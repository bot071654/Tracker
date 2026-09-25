"""Tracker lifecycle visibility: RUNNING/STOPPED, and mid-hand starts.

THE FAILURE THIS IS ABOUT

Real gameplay logs (logs/tracker.log, 2026-09-24/25) showed operational
problems behind "the scenarios are not working":

  A. The tracker was not running for a real 35-minute stretch - the app
     window was relaunched about 20 times, but Start Tracker was never
     clicked again, and nothing made "not tracking" visually obvious.
  B. Five real hands were started mid-hand (cards already on screen at the
     first poll) and never reached a classified decision before the hand
     ended - correct, conservative WAIT behaviour, but not explained to the
     person watching.

A third issue - a screen-resolution/calibration mismatch warning that was
easy to miss - was investigated too, but its UI fix was reverted at the
person's request: they wanted _check_resolution()'s previous behaviour left
exactly as it was, with no new Calibrate requirement.

None of these are Scenario Engine bugs - classification, rule matching,
confirmation and FocusGate are unchanged and untested here; see
test_scenario_engine.py, test_scenarios.py, test_window_focus.py and
test_ante_alert.py for that coverage, still green.
"""

import queue

import numpy
import pytest

from config.settings import CARD_SLOTS
import tracker as tracker_module
from tracker import PLAYER_CARDS, WAITING, Tracker

# -- Tier 1: Tracker, no Tk ---------------------------------------------------


def _config():
    return {"monitor": 1, "screen_size": [1366, 768], "clear_frames": 3,
            "change_confirm_frames": 2}


@pytest.fixture
def wired(monkeypatch):
    """A Tracker whose screen and table are whatever the test sets."""
    screen = {}

    def fake_read_table(config, images=None):
        reads, seen = {}, {}
        for slot in CARD_SLOTS:
            card = screen.get(slot)
            reads[slot] = {"card": card, "present": bool(card), "confident": bool(card),
                           "confidence": 0.9 if card else 0.0, "suit_margin": 0.2}
            seen[slot] = card
        return seen, reads

    monkeypatch.setattr(tracker_module, "grab_full_screen",
                        lambda monitor=1: numpy.zeros((10, 10, 3), "uint8"))
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "read_table", fake_read_table)
    monkeypatch.setattr(tracker_module.db, "insert_hand", lambda record: (False, None))

    made = Tracker(_config(), queue.Queue())
    return made, screen


def _drain_updates(made):
    events = []
    try:
        while True:
            kind, payload = made.events.get_nowait()
            if kind == "update":
                events.append(payload)
    except queue.Empty:
        pass
    return events


def test_started_mid_hand_true_when_the_first_poll_already_shows_a_hand(wired):
    made, screen = wired
    screen.update(player_1="AS", player_2="7D")   # cards on screen from tick 1
    made._tick()
    [payload] = _drain_updates(made)
    assert payload["state"] == PLAYER_CARDS
    assert payload["started_mid_hand"] is True


def test_started_mid_hand_stays_true_for_the_rest_of_that_same_round(wired):
    made, screen = wired
    screen.update(player_1="AS", player_2="7D")
    made._tick()
    made._tick()
    round_id = made.memory.generation
    payloads = [p for p in _drain_updates(made) if p["round_id"] == round_id]
    assert all(p["started_mid_hand"] for p in payloads)


def test_started_mid_hand_is_false_for_a_hand_that_started_from_its_own_deal(wired):
    made, screen = wired
    made._tick()                       # table empty: an ordinary start
    screen.update(player_1="AS", player_2="7D")   # dealt normally, after tracking began
    made._tick()
    payloads = _drain_updates(made)
    assert all(p["started_mid_hand"] is False for p in payloads)
    assert payloads[0]["state"] == WAITING


def test_started_mid_hand_clears_once_the_table_goes_empty_and_a_new_hand_begins(wired):
    made, screen = wired
    screen.update(player_1="AS", player_2="7D")
    made._tick()
    screen.clear()
    for _ in range(_config()["clear_frames"]):
        made._tick()                   # enough empty polls: this hand is over
    screen.update(player_1="KC", player_2="KD")    # a genuinely new deal
    made._tick()
    payloads = _drain_updates(made)
    last = payloads[-1]
    assert last["state"] == PLAYER_CARDS
    assert last["started_mid_hand"] is False


def test_started_mid_hand_never_changes_the_scenario_decision(monkeypatch):
    """Display only: the same confirmed cards give the same decision either way.

    Two independent trackers reach the identical final table - one sees it
    from the first poll (mid-hand start), the other watches it get dealt
    (ordinary start). started_mid_hand differs; the scenario they emit must
    not.
    """
    def make_tracker():
        screen = {}

        def fake_read_table(config, images=None):
            reads, seen = {}, {}
            for slot in CARD_SLOTS:
                card = screen.get(slot)
                reads[slot] = {"card": card, "present": bool(card), "confident": bool(card),
                               "confidence": 0.9 if card else 0.0, "suit_margin": 0.2}
                seen[slot] = card
            return seen, reads

        monkeypatch.setattr(tracker_module, "grab_full_screen",
                            lambda monitor=1: numpy.zeros((10, 10, 3), "uint8"))
        monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
        monkeypatch.setattr(tracker_module, "read_table", fake_read_table)
        monkeypatch.setattr(tracker_module.db, "insert_hand", lambda record: (False, None))
        return Tracker(_config(), queue.Queue()), screen

    hand = dict(player_1="AS", player_2="7D", flop_1="KC", flop_2="7C", flop_3="2H")

    mid_hand, mid_screen = make_tracker()
    mid_screen.update(hand)             # already on screen at the first poll
    for _ in range(4):
        mid_hand._tick()
    mid_payloads = _drain_updates(mid_hand)
    assert mid_payloads[0]["started_mid_hand"] is True

    ordinary, ordinary_screen = make_tracker()
    ordinary._tick()                    # starts empty, like a normal session
    ordinary_screen.update(hand)        # dealt afterwards
    for _ in range(4):
        ordinary._tick()
    ordinary_payloads = _drain_updates(ordinary)
    assert ordinary_payloads[-1]["started_mid_hand"] is False

    assert mid_payloads[-1]["scenario"] == ordinary_payloads[-1]["scenario"]


# -- Tier 2: App, the window itself -------------------------------------------

tk = pytest.importorskip("tkinter")

from poker import scenario_engine as se                     # noqa: E402
from voice import engines                                   # noqa: E402

SETTLED = ["player_1", "player_2", "flop_1", "flop_2", "flop_3"]
UI_CARDS = {"player_1": "AS", "player_2": "7D",
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


def ui_payload(started_mid_hand=False, uncertain=None, decision=se.WAIT, round_id=1):
    """A tracker update, with every key the tracker really sends."""
    return {"state": "FLOP", "round_id": round_id, "cards": UI_CARDS,
            "seen": dict(UI_CARDS), "reads": {}, "held": {}, "panels": {},
            "diagnostics": {}, "dealer": {}, "action": None,
            "statuses": {slot: "CONFIRMED" for slot in SETTLED},
            "timing": {}, "emitted_at": None, "uncertain": uncertain or [],
            "started_mid_hand": started_mid_hand,
            "scenario": {"decision": decision, "reason": "FLOP_PAIR",
                         "round_id": round_id}}


def begin(application):
    application.start()
    application.tracker._fake = True
    application.root.update()


def deliver(application, **kwargs):
    application.events.put(("update", ui_payload(**kwargs)))
    application._poll_events()
    application.root.update()


# -- A. RUNNING / STOPPED must be visually obvious ----------------------------


def test_running_gets_its_own_colour(application):
    import app as app_module

    begin(application)
    deliver(application)
    assert application.status_var.get().startswith("Status: RUNNING")
    assert str(application.status_label.cget("foreground")) == app_module.ACTIVE_GREEN


def test_stopped_gets_its_own_colour_distinct_from_running(application):
    import app as app_module

    begin(application)
    deliver(application)
    application.tracker._fake = False
    application.stop()
    application.root.update()
    assert application.status_var.get() == "Status: STOPPED"
    assert str(application.status_label.cget("foreground")) == app_module.STOPPED_RED
    assert app_module.STOPPED_RED != app_module.ACTIVE_GREEN


# -- B. mid-hand start must be explained, without touching the decision ------


def test_mid_hand_start_shows_the_waiting_notice(application):
    import app as app_module

    begin(application)
    deliver(application, started_mid_hand=True)
    assert application.message_var.get() == app_module.MID_HAND_NOTICE


def test_the_mid_hand_notice_clears_once_the_flag_goes_away(application):
    import app as app_module

    begin(application)
    deliver(application, started_mid_hand=True)
    assert application.message_var.get() == app_module.MID_HAND_NOTICE
    deliver(application, started_mid_hand=False)
    assert application.message_var.get() != app_module.MID_HAND_NOTICE


def test_mid_hand_start_does_not_change_what_the_scenario_panel_shows(application):
    """Same scenario payload either way - this is display only."""
    begin(application)
    deliver(application, started_mid_hand=False, decision=se.PLAY)
    normal_decision = application.scenario_panel.decision_var.get()
    application.tracker._fake = False
    application.stop()
    application.root.update()

    begin(application)
    deliver(application, started_mid_hand=True, decision=se.PLAY)
    mid_hand_decision = application.scenario_panel.decision_var.get()
    assert normal_decision == mid_hand_decision
