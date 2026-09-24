"""The decision banner belongs to a tracking session, and dies with it.

THE FAILURE THIS IS ABOUT

The window said STOPPED and a green PLAY NOW banner was still sitting on top
of everything else. The banner is an always-on-top frame with no title bar, so
one left behind covers whatever the person does next.

Two paths let it survive, and neither went through the guard that already
existed:

  A. App.stop() closed the banner on its LAST line and set the status on its
     FIRST, with the voice, the scenario panel and the ANTE alert in between.
     Anything that raised there skipped the teardown - and in a Tk button
     callback the traceback goes to stderr, which nobody starting from a
     shortcut sees. The window then said STOPPED under a live banner.

  B. The tracker thread ended on its own. Nothing watches for that, because
     every other consumer is fed by events that simply stop arriving, so the
     banner kept showing the last decision above a window still saying
     RUNNING.

The fix is ownership rather than another hide(): App.tracking says whether a
decision may be shown at all, the banner is closed first and unconditionally,
and a tracker that ends without Stop is reconciled once per poll.
"""

import time

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenario_engine as se                     # noqa: E402
from poker import scenarios as scenario_rules               # noqa: E402
from ui.ante_alert import SHOW                              # noqa: E402
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


def payload(decision, round_id=1, reason="FLOP_PAIR_PLAYER_CARD_PLUS_FLOP_PAIR"):
    """A tracker update carrying a decision, with every key the tracker sends."""
    return {"state": "FLOP", "round_id": round_id, "cards": CARDS,
            "seen": dict(CARDS), "reads": {}, "held": {}, "panels": {},
            "diagnostics": {}, "dealer": {}, "action": None,
            "statuses": {slot: "CONFIRMED" for slot in SETTLED},
            "timing": {}, "emitted_at": time.time(), "uncertain": [],
            "scenario": {"decision": decision, "reason": reason,
                         "round_id": round_id}}


def begin(application):
    """Press Start, and let the tracker appear to be running."""
    application.start()
    application.tracker._fake = True
    application.root.update()


def end(application):
    """Press Stop."""
    application.tracker._fake = False
    application.stop()
    application.root.update()


def deliver(application, decision, round_id=1):
    """Queue an update and let the window's own poll loop drain it."""
    application.events.put(("update", payload(decision, round_id)))
    application._poll_events()
    application.root.update()


def shown(application):
    """What is actually on screen, not merely what the object believes."""
    banner = application.decision_banner
    application.root.update()
    try:
        viewable = bool(banner.window.winfo_viewable())
    except tk.TclError:                         # pragma: no cover
        viewable = False
    return banner.visible, viewable, banner.text


def assert_hidden(application):
    visible, viewable, text = shown(application)
    assert not visible, "banner reports itself visible"
    assert not viewable, "the banner window is still on screen"
    assert text == "", "banner still reads %r" % text
    assert application.decision_banner.window.state() == "withdrawn"


# -- the ten steps ------------------------------------------------------------

def test_1_before_the_tracker_starts_the_banner_is_hidden(application):
    assert_hidden(application)
    assert application.tracking is False


def test_2_starting_leaves_it_hidden_until_a_decision(application):
    begin(application)
    assert application.tracking is True
    assert_hidden(application)


def test_3_a_decision_while_tracking_shows_the_banner(application):
    begin(application)
    deliver(application, se.PLAY)
    visible, viewable, text = shown(application)
    assert visible and viewable
    assert text == "PLAY NOW"


def test_4_stopping_hides_the_banner_immediately(application):
    begin(application)
    deliver(application, se.PLAY)
    end(application)
    assert_hidden(application)
    assert application.tracking is False


def test_5_the_same_payload_after_stop_changes_nothing(application):
    begin(application)
    deliver(application, se.PLAY)
    end(application)
    deliver(application, se.PLAY)               # the very same decision again
    assert_hidden(application)


def test_6_a_backlog_queued_before_the_stop_cannot_bring_it_back(application):
    """The queue outlives the tracker; stopping does not empty it."""
    begin(application)
    deliver(application, se.PLAY)
    application.events.put(("update", payload(se.PLAY)))
    application.events.put(("update", payload(se.DONT_PLAY, round_id=2)))
    end(application)                            # stop with the backlog waiting
    application._poll_events()                  # now let it drain
    assert_hidden(application)


def test_7_starting_again_does_not_restore_the_old_decision(application):
    begin(application)
    deliver(application, se.PLAY)
    end(application)
    begin(application)
    assert_hidden(application)


def test_8_a_new_decision_in_the_new_session_appears(application):
    begin(application)
    deliver(application, se.PLAY)
    end(application)
    begin(application)
    deliver(application, se.DONT_PLAY, round_id=9)
    visible, viewable, text = shown(application)
    assert visible and viewable and text == "DON'T PLAY"


def test_9_stopping_again_hides_it_again(application):
    begin(application)
    deliver(application, se.PLAY)
    end(application)
    begin(application)
    deliver(application, se.DONT_PLAY, round_id=9)
    end(application)
    assert_hidden(application)


def test_10_start_decision_stop_start_with_no_decision_stays_hidden(application):
    begin(application)
    deliver(application, se.PLAY)
    end(application)
    begin(application)
    for _ in range(5):                          # the poll loop keeps running
        application._poll_events()
        application.root.update()
    assert_hidden(application)


# -- an old session's work may never reach the banner -------------------------

def test_a_decision_published_after_stop_is_refused(application):
    begin(application)
    deliver(application, se.PLAY)
    end(application)
    assert application._publish_decision(se.PLAY, "stale", 1) is False
    assert_hidden(application)


def test_a_stale_ante_alert_event_after_stop_is_refused(application):
    """The pre-round path reaches the banner separately from the engine one."""
    begin(application)
    end(application)
    application._apply_ante_event((SHOW, scenario_rules.ANTE, "stale"))
    assert_hidden(application)


def test_an_old_sessions_round_id_cannot_show_a_banner_after_stop(application):
    begin(application)
    deliver(application, se.PLAY, round_id=41)
    first_session = application.teach_session
    end(application)
    deliver(application, se.PLAY, round_id=41)  # same round id, session over
    assert_hidden(application)
    begin(application)
    assert application.teach_session == first_session + 1
    assert_hidden(application)                  # and the restart restored nothing


def test_the_banner_is_never_shown_while_not_tracking(application):
    """The window's own flag, independent of the banner's guard."""
    assert application.tracking is False
    assert application._publish_decision(se.PLAY, "", 1) is False
    begin(application)
    assert application._publish_decision(se.PLAY, "", 1) is True
    end(application)
    assert application._publish_decision(se.PLAY, "", 1) is False


# -- path A: something in the stop sequence fails -----------------------------

def test_the_banner_closes_even_if_the_rest_of_stop_fails(application,
                                                          monkeypatch):
    """The reported bug: STOPPED on the status line, PLAY NOW on the screen.

    The teardown used to be the last statement in stop(), so anything raising
    before it left the banner up.
    """
    begin(application)
    deliver(application, se.PLAY)
    assert shown(application)[0] is True

    def explode(self):
        raise RuntimeError("the voice state refresh failed")

    monkeypatch.setattr(type(application), "_refresh_voice_state", explode)
    application.tracker._fake = False
    application.stop()                          # must not raise
    application.root.update()

    assert_hidden(application)
    assert application.status_var.get() == "Status: STOPPED"
    assert application.tracking is False


@pytest.mark.parametrize("failing", [
    "_refresh_voice_state", "_apply_ante_event",
])
def test_no_single_failure_in_stop_can_leave_the_banner_up(application,
                                                           monkeypatch, failing):
    begin(application)
    deliver(application, se.PLAY)

    def explode(self, *args, **kwargs):
        raise RuntimeError("%s failed" % failing)

    monkeypatch.setattr(type(application), failing, explode)
    application.tracker._fake = False
    application.stop()
    assert_hidden(application)


# -- path B: the tracker ends without anyone pressing Stop --------------------

def test_a_tracker_that_ends_on_its_own_takes_the_banner_with_it(application):
    """Nothing else notices: the other consumers just stop being fed."""
    begin(application)
    deliver(application, se.PLAY)
    assert shown(application)[0] is True

    application.tracker._fake = False           # the thread has ended
    application._poll_events()                  # the window's own 200ms tick
    application.root.update()

    assert_hidden(application)
    assert application.tracking is False
    assert application.status_var.get() == "Status: STOPPED"


def test_the_session_is_not_ended_before_the_thread_is_ever_seen_running(
        application):
    """start() returns before the thread is necessarily scheduled.

    Treating that instant as "it has ended" would stop the session that had
    just begun, so the check waits until the tracker has actually been seen
    alive.
    """
    application.start()                         # is_running() is still False
    application.root.update()
    application._poll_events()
    assert application.tracking is True, "the new session was ended immediately"

    application.tracker._fake = True
    application._poll_events()
    deliver(application, se.PLAY)
    assert shown(application)[0] is True


# -- the voice is silenced with the banner, and just as unconditionally -------

def test_stopping_silences_a_queued_decision(application):
    """Item 12: stopping must not let an old decision be announced."""
    begin(application)
    application.announcer.pause()               # hold the worker off
    application.announcer.resume()
    deliver(application, se.PLAY)
    end(application)
    assert application.announcer.pending() == 0
    assert application._spoken_decision is None


def test_the_voice_is_silenced_even_if_the_rest_of_stop_fails(application,
                                                              monkeypatch):
    """It sits beside the banner teardown, not behind the guard."""
    begin(application)
    deliver(application, se.PLAY)

    def explode(self):
        raise RuntimeError("the tracker refused to stop")

    monkeypatch.setattr(type(application).__mro__[0], "_refresh_voice_state",
                        explode)
    application.tracker._fake = False
    application.stop()
    assert application.announcer.pending() == 0
    assert application._spoken_decision is None
    assert_hidden(application)
