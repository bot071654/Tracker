"""The tracker's own windows in front of the game: Teach / Correct Scenario, the rule editors, the main window itself.

Clicking Start, Teach or a rule editor puts a tracker window in front of the game, so the title no longer matches. The screen that
is grabbed is the whole monitor, so what is read is what is behind that window - the game, if the game was what was in front a
moment ago. The gate therefore holds its state through the tracker's own windows once the game has been seen, and only then:

* it never RESUMES a paused tracker (only a match does);
* it never hides a real non-match: if something else was in front just before, the own window is judged as before;
* it is not for ever (OWN_WINDOW_HOLD_SECONDS), and a gate that has never seen the game keeps the old rules.
"""

import pytest

import window_focus as wf

TARGET = "Casino"
GAME = "Casino - Google Chrome"
OWN_MAIN = "Poker Hand Tracker"
OWN_OTHER = "Teach / Correct Scenario"          # a tracker window the title rule does not know
OTHER = "Notepad"
OTHER_TAB = "Gmail - Google Chrome"


def sequence(*items):
    """Plays back a fixed list, then repeats the last item."""
    left = list(items)
    return lambda: left[0] if len(left) == 1 else left.pop(0)


def gate_at(clock, titles, own_process=lambda: False, **kwargs):
    return wf.FocusGate(TARGET, read_title=titles, clock=clock.read, own_process=own_process,
                        grace_seconds=3.0, **kwargs)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def read(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def settle_on_game(gate, clock):
    gate.observe()
    clock.advance(10.0)                           # well past the startup grace
    assert gate.state == wf.GAME_ACTIVE


def test_the_main_window_after_the_grace_period_does_not_pause_a_game_that_was_seen():
    clock = Clock()
    titles = sequence(GAME, *([OWN_MAIN] * 40))
    gate = gate_at(clock, titles)
    settle_on_game(gate, clock)
    for _ in range(40):
        clock.advance(1.0)
        assert gate.observe() is None
        assert gate.paused is False


def test_a_tracker_window_with_an_unknown_title_is_recognised_as_the_trackers_own():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, *([OWN_OTHER] * 20)), own_process=lambda: True)
    settle_on_game(gate, clock)
    for _ in range(20):
        clock.advance(1.0)
        gate.observe()
        assert gate.paused is False


def test_returning_to_the_game_after_the_own_window_stays_active():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, OWN_MAIN, OWN_MAIN, OWN_MAIN, GAME))
    settle_on_game(gate, clock)
    for _ in range(4):
        clock.advance(1.0)
        gate.observe()
    assert gate.state == wf.GAME_ACTIVE and gate.paused is False


def test_an_unrelated_window_still_pauses_after_two_looks():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, OTHER, OTHER))
    settle_on_game(gate, clock)
    assert gate.observe() is None                 # one non-match is absorbed
    assert gate.observe() == wf.GAME_INACTIVE


def test_another_browser_tab_still_pauses():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, OTHER_TAB, OTHER_TAB))
    settle_on_game(gate, clock)
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE


def test_an_own_window_after_something_else_was_in_front_does_not_hide_the_pause():
    """Game -> Notepad -> tracker window: the screen behind the tracker window is Notepad, not the game."""
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, OTHER, OWN_MAIN, OWN_MAIN))
    settle_on_game(gate, clock)
    gate.observe()                                # Notepad: one non-match, absorbed
    assert gate.observe() == wf.GAME_INACTIVE     # the own window counts as the second non-match
    assert gate.paused is True


def test_an_own_window_never_resumes_a_paused_tracker():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, OTHER, OTHER, *([OWN_MAIN] * 10), GAME))
    settle_on_game(gate, clock)
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE
    for _ in range(10):
        clock.advance(1.0)
        gate.observe()
        assert gate.paused is True
    assert gate.observe() == wf.GAME_ACTIVE       # only the game itself resumes it


def test_a_gate_that_has_never_seen_the_game_keeps_the_old_rule():
    """Start Tracker pressed with the game nowhere in front: nothing says what is behind the tracker window."""
    clock = Clock()
    gate = gate_at(clock, sequence(*([OWN_MAIN] * 10)))
    gate.observe()
    clock.advance(10.0)
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE


def test_the_own_window_allowance_is_not_for_ever():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, *([OWN_MAIN] * 10)), own_hold_seconds=60.0)
    settle_on_game(gate, clock)
    clock.advance(1.0)
    gate.observe()                                # the own window arrives
    assert gate.paused is False
    clock.advance(120.0)
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE


def test_the_hold_starts_again_each_time_the_own_window_arrives():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, OWN_MAIN, GAME, OWN_MAIN, OWN_MAIN), own_hold_seconds=60.0)
    settle_on_game(gate, clock)
    clock.advance(50.0)
    gate.observe()                                # own window, 50 s in... counted from here
    clock.advance(1.0)
    gate.observe()                                # back to the game: the count is cleared
    clock.advance(55.0)
    gate.observe()                                # own window again
    clock.advance(30.0)
    gate.observe()
    assert gate.paused is False


# -- asking Windows whose window it is ----------------------------------------------------------------

def test_the_process_check_is_false_when_it_cannot_be_asked(monkeypatch):
    monkeypatch.setattr(wf, "_user32", lambda: None)
    assert wf.foreground_is_own_process() is False


def test_the_process_check_compares_the_foreground_windows_process_id(monkeypatch):
    import ctypes
    import os

    class Function:
        restype = None

    class FakeUser32:
        GetForegroundWindow = Function()

        def __init__(self, pid):
            self.pid = pid

            def thread_process(handle, pointer):
                pointer._obj.value = self.pid
                return 1
            self.GetWindowThreadProcessId = thread_process
            self.GetForegroundWindow = lambda: 4242
            self.GetForegroundWindow.restype = None

    monkeypatch.setattr(wf, "_user32", lambda: FakeUser32(os.getpid()))
    assert wf.foreground_is_own_process() is True
    monkeypatch.setattr(wf, "_user32", lambda: FakeUser32(os.getpid() + 1))
    assert wf.foreground_is_own_process() is False
    assert isinstance(ctypes.c_ulong(0).value, int)


# -- the hold's flag, its cap and its clock ---------------------------------------------------------------------

def test_the_hold_is_capped_at_two_minutes():
    import inspect
    assert wf.OWN_WINDOW_HOLD_SECONDS == 120.0
    assert inspect.signature(wf.FocusGate.__init__).parameters["own_hold_seconds"].default == 120.0


def test_the_flag_is_true_only_while_the_hold_is_in_effect():
    clock = Clock()
    gate = gate_at(clock, sequence(OWN_MAIN, GAME, OWN_MAIN, OWN_MAIN, GAME, OTHER, OTHER))
    assert gate.holding_own_window is False
    gate.observe()                                    # own window inside the startup grace: not the hold
    assert gate.holding_own_window is False
    gate.observe()                                    # the game
    assert gate.holding_own_window is False
    clock.advance(10.0)
    gate.observe()                                    # own window, game seen, past the grace: the hold
    assert gate.holding_own_window is True
    clock.advance(1.0)
    gate.observe()
    assert gate.holding_own_window is True
    gate.observe()                                    # the game again
    assert gate.holding_own_window is False
    gate.observe()                                    # an unrelated window
    assert gate.holding_own_window is False
    gate.observe()
    assert gate.paused is True and gate.holding_own_window is False


def test_the_hold_ends_at_the_cap_and_repeated_looks_do_not_extend_it():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, *([OWN_MAIN] * 40)))
    settle_on_game(gate, clock)
    clock.advance(1.0)
    gate.observe()                                    # the own window arrives: the 120 s count starts here
    for _ in range(11):
        clock.advance(10.0)                           # looks every 10 s, up to 110 s in
        gate.observe()
        assert gate.holding_own_window is True and gate.paused is False
    clock.advance(10.0)
    gate.observe()                                    # 120 s in: the hold is over, however many looks came before
    assert gate.holding_own_window is False
    assert gate.observe() == wf.GAME_INACTIVE
    assert gate.holding_own_window is False


def test_a_look_at_the_game_is_what_starts_a_new_hold():
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, OWN_MAIN, OWN_MAIN, GAME, OWN_MAIN, OWN_MAIN), own_hold_seconds=60.0)
    settle_on_game(gate, clock)
    clock.advance(1.0)
    gate.observe()                                    # own window arrives
    clock.advance(50.0)
    gate.observe()                                    # 50 s in, still held
    assert gate.holding_own_window is True
    clock.advance(1.0)
    gate.observe()                                    # the game is in front again
    clock.advance(30.0)
    gate.observe()                                    # own window again: a fresh 60 s
    clock.advance(30.0)
    gate.observe()
    assert gate.holding_own_window is True and gate.paused is False


def test_the_default_clock_is_monotonic_and_ignores_wall_clock_changes(monkeypatch):
    import inspect
    import time

    assert inspect.signature(wf.FocusGate.__init__).parameters["clock"].default is time.monotonic
    gate = wf.FocusGate(TARGET, read_title=sequence(GAME, OWN_MAIN), own_process=lambda: False)
    gate.observe()
    monkeypatch.setattr(time, "time", lambda: 10 ** 9)          # the system clock jumps forward
    gate.observe()
    assert gate.paused is False and gate.holding_own_window is False     # still inside the startup grace


# -- a pause can be explained from the log -------------------------------------------------------------------

def focus_lines(caplog):
    return [r.getMessage() for r in caplog.records if "[FOCUS]" in r.getMessage()]


def test_the_pause_line_says_why_the_own_window_did_not_hold(caplog):
    import logging
    caplog.set_level(logging.INFO)
    clock = Clock()
    gate = gate_at(clock, sequence(OWN_MAIN, OWN_MAIN, OWN_MAIN))
    gate.observe()
    clock.advance(10.0)
    gate.observe()
    gate.observe()                                    # the game was never seen: both looks count against it
    last = focus_lines(caplog)[-1]
    assert "state=GAME_INACTIVE" in last and "own-not-held(game-never-seen)" in last


def test_the_log_names_a_non_match_that_came_before_an_own_window(caplog):
    import logging
    caplog.set_level(logging.INFO)
    clock = Clock()
    holder = {}
    gate = gate_at(clock, sequence(GAME, "", OWN_OTHER, OWN_OTHER),
                   own_process=lambda: holder["gate"].title == OWN_OTHER)   # only the tracker's own window is "own"
    holder["gate"] = gate
    settle_on_game(gate, clock)
    gate.observe()                                    # an empty title, absorbed
    gate.observe()                                    # then a tracker window: judged with it
    last = focus_lines(caplog)[-1]
    assert "own-not-held(after-a-non-match)" in last and "other-empty" in last


def test_the_log_names_an_expired_hold(caplog):
    import logging
    caplog.set_level(logging.INFO)
    clock = Clock()
    gate = gate_at(clock, sequence(GAME, *([OWN_MAIN] * 10)), own_hold_seconds=30.0)
    settle_on_game(gate, clock)
    clock.advance(1.0)
    gate.observe()
    clock.advance(40.0)
    gate.observe()
    gate.observe()
    assert "own-not-held(hold-expired)" in focus_lines(caplog)[-1]


def test_the_log_carries_labels_only_never_other_windows_titles(caplog):
    import logging
    caplog.set_level(logging.INFO)
    clock = Clock()
    secret_title = "Inbox (3) - private-person@example.com - Gmail"
    gate = gate_at(clock, sequence(GAME, secret_title, secret_title))
    settle_on_game(gate, clock)
    gate.observe()
    gate.observe()
    recent = focus_lines(caplog)[-1].split("| recent looks:")[1]
    assert "other" in recent and "private-person" not in recent and "Gmail" not in recent
