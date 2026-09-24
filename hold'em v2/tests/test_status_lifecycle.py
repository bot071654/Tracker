"""The status line belongs to a tracking session too.

THE FAILURE THIS IS ABOUT

With the banner fixed, a stopped tracker could still leave the window saying

    Status: RUNNING - WAITING

The tracker's queue outlives the tracker. Frames it put there before it
stopped are still drained afterwards, and _show_reading sets the status from
every frame it is given - so one stale frame repainted a line that had
correctly said STOPPED a moment earlier.

Two things let a stale frame through, and they need different answers:

  A. It arrives while stopped. Nothing was watching: _handle_event handled
     every update it was given, whether or not a session was open.

  B. It survives into the NEXT session. A guard on "are we tracking" cannot
     help here - tracking is true again - so the frames themselves have to be
     dropped when a session begins.

Both reuse the session ownership the banner fix introduced rather than adding
a second idea of what a session is.
"""

import time

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenario_engine as se                     # noqa: E402
from voice import engines                                   # noqa: E402

SETTLED = ["player_1", "player_2", "flop_1", "flop_2", "flop_3"]
CARDS = {"player_1": "AS", "player_2": "7D",
         "flop_1": "KC", "flop_2": "7C", "flop_3": "2H"}


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:                  # pragma: no cover - headless
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def application(root, monkeypatch):
    """The real window, with the database, screen reading and voice stubbed."""
    import app as app_module
    import tracker as tracker_module

    monkeypatch.setattr(engines, "Pyttsx3Engine",
                        lambda *_a, **_k: engines.NullEngine())
    monkeypatch.setattr(app_module.App, "_startup_checks", lambda self: None)
    monkeypatch.setattr(app_module.App, "_refresh_statistics", lambda self: None)
    monkeypatch.setattr(app_module.App, "_refresh_scenario_history",
                        lambda self: None)
    monkeypatch.setattr(app_module.db, "check_connection",
                        lambda *_a, **_k: (True, "stubbed"))
    monkeypatch.setattr(app_module.messagebox, "askyesno",
                        lambda *_a, **_k: pytest.fail("a modal was opened"))
    monkeypatch.setattr(tracker_module.Tracker, "start", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "stop", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "is_running",
                        lambda self: getattr(self, "_fake", False))

    made = app_module.App(root)
    root.update()
    try:
        yield made
    finally:
        made.tracker._fake = False
        made.on_close()


def payload(decision=se.PLAY, round_id=1, state="FLOP"):
    """A tracker update, with every key the tracker really sends."""
    return {"state": state, "round_id": round_id, "cards": CARDS,
            "seen": dict(CARDS), "reads": {}, "held": {}, "panels": {},
            "diagnostics": {}, "dealer": {}, "action": None,
            "statuses": {slot: "CONFIRMED" for slot in SETTLED},
            "timing": {}, "emitted_at": time.time(), "uncertain": [],
            "scenario": {"decision": decision, "reason": "FLOP_PAIR",
                         "round_id": round_id}}


def begin(application):
    application.start()
    application.tracker._fake = True
    application.root.update()


def end(application):
    application.tracker._fake = False
    application.stop()
    application.root.update()


def queue_only(application, **kwargs):
    """Put a frame on the queue without letting the window drain it yet."""
    application.events.put(("update", payload(**kwargs)))


def deliver(application, **kwargs):
    """Queue a frame and let the window's own poll loop drain it."""
    queue_only(application, **kwargs)
    application._poll_events()
    application.root.update()


def status(application):
    return application.status_var.get()


# -- the ten steps ------------------------------------------------------------

def test_1_starting_sets_the_status_to_running(application):
    begin(application)
    assert status(application) == "Status: RUNNING"


def test_2_a_frame_from_the_live_tracker_updates_the_status(application):
    begin(application)
    deliver(application, state="FLOP")
    assert status(application) == "Status: RUNNING - FLOP"


def test_3_stopping_sets_the_status_to_stopped(application):
    begin(application)
    deliver(application, state="FLOP")
    end(application)
    assert status(application) == "Status: STOPPED"


def test_4_a_frame_queued_before_the_stop_cannot_repaint_the_status(application):
    """The reported bug: STOPPED, then RUNNING - FLOP again a moment later."""
    begin(application)
    deliver(application, state="FLOP")
    queue_only(application, state="WAITING")     # in flight when Stop is pressed
    end(application)
    assert status(application) == "Status: STOPPED"

    application._poll_events()                   # now the backlog drains
    application.root.update()
    assert status(application) == "Status: STOPPED"


def test_5_a_frame_after_the_stop_leaves_the_banner_hidden(application):
    begin(application)
    deliver(application, decision=se.PLAY)
    end(application)
    deliver(application, decision=se.PLAY)
    banner = application.decision_banner
    assert not banner.visible
    assert not banner.window.winfo_viewable()
    assert banner.text == ""


def test_6_a_tracker_that_exits_on_its_own_sets_the_status_to_stopped(application):
    begin(application)
    deliver(application, state="FLOP")
    assert status(application) == "Status: RUNNING - FLOP"

    application.tracker._fake = False             # the thread has ended
    application._poll_events()                    # the window's own 200ms tick
    application.root.update()
    assert status(application) == "Status: STOPPED"
    assert application.tracking is False


def test_7_a_late_frame_after_an_unexpected_exit_cannot_repaint_it(application):
    begin(application)
    deliver(application, state="FLOP")
    application.tracker._fake = False
    application._poll_events()                    # notices and ends the session
    application.root.update()
    assert status(application) == "Status: STOPPED"

    deliver(application, state="WAITING")         # the thread's last frame
    assert status(application) == "Status: STOPPED"


def test_8_a_new_session_can_update_the_status_again(application):
    begin(application)
    deliver(application, state="FLOP")
    end(application)
    begin(application)
    assert status(application) == "Status: RUNNING"
    deliver(application, state="RIVER")
    assert status(application) == "Status: RUNNING - RIVER"


def test_9_a_frame_from_the_old_session_cannot_reach_the_new_one(application):
    """Tracking is true again here, so the guard alone could not help.

    The frame has to be gone before the new session can be told about it.
    """
    begin(application)
    deliver(application, state="FLOP")
    queue_only(application, state="TURN", decision=se.PLAY)
    end(application)
    begin(application)                            # a new session, same queue

    application._poll_events()
    application.root.update()
    assert status(application) == "Status: RUNNING", (
        "a frame from the previous session repainted the new session's status")
    assert not application.decision_banner.visible


def test_10_the_banner_lifecycle_is_unchanged(application):
    """The guard must not have cost the banner its own behaviour."""
    begin(application)
    assert not application.decision_banner.visible
    deliver(application, decision=se.PLAY)
    assert application.decision_banner.visible
    assert application.decision_banner.text == "PLAY NOW"
    end(application)
    assert not application.decision_banner.visible
    assert application.decision_banner.window.state() == "withdrawn"


# -- the drop must take the session's frames and nothing else -----------------

def test_dropping_a_sessions_frames_keeps_everything_else_queued(application):
    """Statistics and saved hands are answers to something the window asked.

    They are not about a tracking session and must survive a restart, or a
    Start pressed at the wrong moment would swallow a saved hand.
    """
    begin(application)
    end(application)
    application.events.put(("statistics", {"hands": 3}))
    application.events.put(("update", payload()))
    application.events.put(("saved", {"round_id": 7}))
    application.events.put(("startup", "a note"))

    application._drop_queued_updates()

    remaining = []
    while not application.events.empty():
        remaining.append(application.events.get_nowait()[0])
    assert remaining == ["statistics", "saved", "startup"]


def test_the_drop_is_safe_on_an_empty_queue(application):
    while not application.events.empty():
        application.events.get_nowait()
    application._drop_queued_updates()
    assert application.events.empty()


def test_a_frame_while_stopped_never_reaches_the_reading_or_the_voice(
        application, monkeypatch):
    """One guard, above the three consumers, rather than one guard each."""
    reached = []
    monkeypatch.setattr(type(application), "_show_reading",
                        lambda self, p: reached.append("reading"))
    monkeypatch.setattr(type(application), "_update_ante_alert",
                        lambda self, p: reached.append("ante"))

    application.events.put(("update", payload()))
    application._poll_events()                    # never started
    assert reached == []

    begin(application)
    application.events.put(("update", payload()))
    application._poll_events()
    assert reached == ["reading", "ante"]
