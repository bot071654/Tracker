"""Opening, using and closing Teach / Correct Scenario must not pause the tracker.

THE REPORT

"When I open Teach / Correct Scenario and return to the main tracker, the tracker becomes paused."

Pressing the button puts one of the tracker's own windows in front of the game. Its title is not the game's, so the focus gate
judged it like any unrelated application; once the startup grace was over, two looks later the tracker paused (the installed
build's log shows `foreground_title='Teach / Correct Scenario' ... state=GAME_INACTIVE`).

The gate now holds its state through the tracker's own windows once the game has been seen in front and nothing else has been
since (see window_focus.FocusGate.look). These tests drive the real application window, the real Teach window and the real gate with
scripted foreground titles, and also check the safety rules that must NOT change: a game that really left the front still pauses,
an own window never resumes a paused tracker, and a scenario captured before a real pause is not treated as current.
"""

import ast
import os
import re

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenario_engine as se                      # noqa: E402
import window_focus as wf                                    # noqa: E402
from test_banner_lifecycle import application, begin, deliver  # noqa: E402,F401 - fixture and helpers

@pytest.fixture
def root():
    """A hidden Tk root. Tk on this machine now and then fails to start ("can't find a usable init.tcl") for no reason that
    lasts; a few tries make these lifecycle tests run instead of skipping, and a machine with no display still skips."""
    import time
    window, error = None, None
    for _ in range(6):
        try:
            window = tk.Tk()
            break
        except tk.TclError as exc:
            error = exc
            time.sleep(0.4)
    if window is None:
        pytest.skip("no display available: %s" % error)
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


GAME = "Casino - Google Chrome"
MAIN = "Poker Hand Tracker"
TEACH = "Teach / Correct Scenario"


class Scene:
    """The real window and the real gate, with the foreground title and the clock under the test's control."""

    def __init__(self, application, monkeypatch):
        import app as app_module
        self.app = application
        self.title, self.now = GAME, 1000.0
        self.told = []
        monkeypatch.setattr(app_module.messagebox, "showinfo", lambda title, message, **_k: self.told.append(message))
        self.gate = wf.FocusGate(GAME, read_title=lambda: self.title, clock=lambda: self.now,
                                 own_process=lambda: False,        # the title list alone must be enough
                                 grace_seconds=3.0)
        application.tracker.focus = self.gate
        self.events = []

    def look(self, title, seconds=1.0):
        """The person brings `title` to the front; the tracker takes one look, as _run does."""
        self.title = title
        self.now += seconds
        moved = self.gate.observe()
        if moved is not None:                                   # the tracker's own "focus" event reaching the window
            self.events.append(moved)
            self.app._show_focus(moved)
        self.app.root.update()
        return moved

    def teach(self):
        self.app.teach_scenario()
        windows = [w for w in self.app.root.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == TEACH]
        return windows[0] if windows else None


@pytest.fixture
def scene(application, monkeypatch):
    begin(application)
    made = Scene(application, monkeypatch)
    made.look(GAME, seconds=0.0)
    made.now += 10.0                                            # past the startup grace
    deliver(application, se.PLAY)
    yield made
    for window in list(application.root.winfo_children()):
        if isinstance(window, tk.Toplevel) and window.title() == TEACH:
            window.destroy()


def test_opening_using_and_closing_teach_never_pauses_the_tracker(scene):
    scene.look(MAIN)                                            # the button is in the main window
    window = scene.teach()
    assert window is not None, scene.told
    for _ in range(6):                                          # working in the Teach window for a while
        scene.look(TEACH, seconds=8.0)
        assert scene.gate.paused is False
        assert window.is_current()[0] is True                   # Save & Test and Activate would be allowed
    window.destroy()
    scene.look(MAIN)                                            # closing hands the foreground back to the main window
    scene.look(GAME)                                            # and the person returns to the game
    assert scene.gate.paused is False and scene.gate.state == wf.GAME_ACTIVE
    assert wf.GAME_INACTIVE not in scene.events                 # not one pause along the way
    assert scene.app._payload_stale is False


def test_the_tracker_keeps_feeding_the_window_while_teach_is_open(scene):
    scene.look(MAIN)
    window = scene.teach()
    scene.look(TEACH, seconds=5.0)
    deliver(scene.app, se.PLAY)                                 # updates are still accepted, not dropped as paused
    assert scene.app._payload_stale is False
    assert window.is_current()[0] is True


def test_a_real_loss_of_the_game_still_pauses_with_teach_open(scene):
    scene.look(MAIN)
    window = scene.teach()
    scene.look(TEACH, seconds=5.0)
    scene.look("Notepad")                                       # something else really comes to the front ...
    assert scene.look("Notepad") == wf.GAME_INACTIVE            # ... and two looks later the tracker pauses
    assert scene.app._payload_stale is True
    assert window.is_current()[0] is False                      # the captured scenario is no longer trusted


def test_the_teach_window_never_resumes_a_paused_tracker(scene):
    scene.look("Notepad")
    scene.look("Notepad")
    assert scene.gate.paused is True
    for _ in range(10):
        scene.look(TEACH, seconds=5.0)                          # only the game itself can resume it
        assert scene.gate.paused is True


def test_teach_is_refused_after_a_real_pause_until_the_game_is_back_with_a_fresh_update(scene):
    scene.look("Notepad")
    scene.look("Notepad")
    scene.look(MAIN)
    assert scene.teach() is None and scene.told                 # refused, with the reason given
    scene.look(GAME)
    assert scene.gate.paused is False
    deliver(scene.app, se.PLAY)                                 # a fresh update after the pause
    assert scene.teach() is not None


def test_the_pause_after_a_game_never_seen_is_unchanged(application, monkeypatch):
    """Start Tracker pressed with the tracker window in front and the game never seen: still paused after the grace."""
    begin(application)
    made = Scene(application, monkeypatch)
    made.now += 10.0
    made.look(MAIN)
    made.look(MAIN)
    assert made.gate.paused is True


def test_every_window_title_the_application_uses_is_known_to_the_focus_gate():
    """A new window added without its title here would pause the tracker the first time it took the foreground."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    known = [title.lower() for title in wf.APP_WINDOW_TITLES]
    missing = []
    for relative in ("app.py", os.path.join("ui", "rule_windows.py"), os.path.join("ui", "decision_banner.py"),
                     os.path.join("desktop", "bootstrap.py")):
        source = open(os.path.join(root, relative), encoding="utf-8").read()
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "title"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                text = node.args[0].value.lower()
                if not any(entry in text for entry in known):
                    missing.append((relative, node.args[0].value))
        for match in re.finditer(r'heading = "([^"]+)"', source):
            if not any(entry in match.group(1).lower() for entry in known):
                missing.append((relative, match.group(1)))
    assert missing == []
