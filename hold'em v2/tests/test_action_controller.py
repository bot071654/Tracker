"""The Action Controller: one click per action per hand, TEST table only.

No real mouse moves here: a fake table and a fake mouse stand in, so what is
checked is the decision to click, the duplicate prevention and the validation.
The real clicks are demonstrated against the local test table separately.
"""

import inspect
import queue
import threading
import time

import pytest

from automation import mouse_controller as mc
from poker import scenario_engine as se

from tests_support_action import make


def result(decision, hand=100):
    return {"decision": decision, "round_id": hand}


def ante(controller, hand=100):
    return controller.handle_scenario_result(result(mc.ANTE_REQUIRED, hand))


# -- 1-2: PLAY once per hand ------------------------------------------------------

def test_1_play_produces_one_test_play_action():
    controller, table, mouse, log = make()
    ante(controller)
    assert controller.handle_scenario_result(result(se.PLAY)) == mc.PLAY_BUTTON
    assert table.counts == {mc.ANTE: 1, mc.PLAY_BUTTON: 1}
    assert controller.status()["play"] == mc.CLICKED
    assert "[TEST ACTION]\nPLAY\nround=100\ndecision=PLAY\nresult=CLICKED" == log.lines[-1]


def test_1b_play_without_the_ante_rule():
    controller, table, _, _ = make(require_ante_before_play=False)
    controller.handle_scenario_result(result(se.PLAY))
    assert table.counts[mc.PLAY_BUTTON] == 1


def test_2_repeated_play_polls_produce_only_one_action():
    controller, table, mouse, log = make()
    ante(controller)
    for _ in range(20):
        controller.handle_scenario_result(result(se.PLAY))
    assert table.counts[mc.PLAY_BUTTON] == 1
    assert mouse.presses.count(mc.PLAY_BUTTON) == 1
    duplicates = [line for line in log.lines if "Already executed for round 100" in line]
    assert len(duplicates) == 1, "the duplicate is logged once, not every poll"


# -- 3-4: no action ------------------------------------------------------------------

def test_3_dont_play_produces_no_action():
    controller, table, mouse, log = make()
    ante(controller)
    for _ in range(5):
        assert controller.handle_scenario_result(result(se.DONT_PLAY)) is None
    assert table.counts[mc.PLAY_BUTTON] == 0
    assert mouse.presses == [mc.ANTE]
    assert any("NONE\nround=100\ndecision=DON'T_PLAY\nreason=Scenario did not qualify" in line
               for line in log.lines)


def test_4_wait_produces_no_action():
    controller, table, mouse, log = make()
    for _ in range(5):
        assert controller.handle_scenario_result(result(se.WAIT)) is None
    assert mouse.moves == [] and mouse.presses == []
    assert any("NONE\nround=100\ndecision=WAIT\nreason=Waiting for confirmed cards" in line
               for line in log.lines)


def test_4b_wait_from_the_real_engine_for_unconfirmed_cards():
    controller, _, mouse, _ = make()
    ante(controller)
    slots = {"player_1": {"card": "7S", "status": "CONFIRMING", "readings": 1}}
    controller.handle_scenario_result(se.evaluate_round(slots, round_id=100))
    assert mouse.presses == [mc.ANTE]


# -- 5-6: ANTE once per hand ----------------------------------------------------------

def test_5_ante_required_produces_one_test_ante_action():
    controller, table, _, log = make()
    assert ante(controller) == mc.ANTE
    assert table.counts == {mc.ANTE: 1, mc.PLAY_BUTTON: 0}
    assert "[TEST ACTION]\nANTE\nround=100\ndecision=ANTE_REQUIRED\nresult=CLICKED" == log.lines[-1]


def test_6_repeated_ante_required_polls_produce_only_one_action():
    controller, table, _, _ = make()
    for _ in range(20):
        ante(controller)
    assert table.counts[mc.ANTE] == 1


# -- 7-8: a new hand ---------------------------------------------------------------------

def test_7_new_round_resets_action_state():
    controller, table, _, log = make()
    ante(controller, 100)
    controller.handle_scenario_result(result(se.PLAY, 100))
    assert controller.status()["play"] == mc.CLICKED
    ante(controller, 101)
    status = controller.status()
    assert status["round"] == 101 and status["play"] is None and status["ante"] == mc.CLICKED
    assert any(line.startswith("[TEST HAND START]\nround=101") for line in log.lines)


def test_8_play_in_a_new_round_can_execute_again():
    controller, table, _, _ = make()
    for hand in (100, 101):
        ante(controller, hand)
        for _ in range(3):
            controller.handle_scenario_result(result(se.PLAY, hand))
    assert table.counts == {mc.ANTE: 2, mc.PLAY_BUTTON: 2}


# -- 9-11: validation and safety -----------------------------------------------------------

def test_9_missing_test_button_produces_no_action():
    controller, table, mouse, log = make()
    table.visible[mc.ANTE] = False
    ante(controller)
    assert mouse.moves == [] and mouse.presses == []
    assert controller.status()["ante"] == mc.FAILED
    assert "reason=TEST ANTE button is not visible" in log.lines[-1]


def test_9b_button_the_table_does_not_publish_produces_no_action():
    controller, table, mouse, log = make()
    controller.config.play_button = None          # use the table's own position...
    del table.centres[mc.PLAY_BUTTON]             # ...which it does not report
    ante(controller)
    controller.handle_scenario_result(result(se.PLAY))
    assert mouse.presses == [mc.ANTE]
    assert "TEST PLAY button is not visible" in log.lines[-1]


def test_9c_test_table_not_running_produces_no_action():
    controller, table, mouse, _ = make()
    table.running = False
    ante(controller)
    assert mouse.moves == []


def test_9d_a_click_the_table_did_not_register_is_not_counted_as_clicked():
    controller, table, _, log = make()
    table.register = False
    ante(controller)
    assert controller.status()["ante"] == mc.FAILED
    assert "did not register" in log.lines[-1]
    ante(controller)                                   # and it is not retried
    assert table.counts[mc.ANTE] == 0 and len([l for l in log.lines if l.startswith("[TEST ACTION]\nANTE\n") and "result=" in l]) == 1


@pytest.mark.parametrize("bad", [{"decision": "FOLD", "round_id": 1}, {"decision": None, "round_id": 1},
                                 {"decision": se.PLAY}, None, {}])
def test_10_invalid_state_produces_no_action(bad):
    controller, _, mouse, log = make()
    assert controller.handle_scenario_result(bad) is None
    assert mouse.moves == []
    assert "Invalid state" in log.lines[-1]


def test_10b_play_before_ante_is_not_valid():
    controller, _, mouse, log = make()
    controller.handle_scenario_result(result(se.PLAY))
    assert mouse.moves == []
    assert "ANTE has not been clicked for round 100" in log.lines[-1]


def test_10c_ante_after_play_in_the_same_hand_is_not_valid():
    controller, table, _, log = make(require_ante_before_play=False)
    controller.handle_scenario_result(result(se.PLAY))
    ante(controller)
    assert table.counts[mc.ANTE] == 0


def test_11_automation_disabled_produces_no_mouse_action():
    controller, table, mouse, log = make(enabled=False)
    for decision in (mc.ANTE_REQUIRED, se.PLAY, se.DONT_PLAY, se.WAIT):
        assert controller.handle_scenario_result(result(decision)) is None
    assert mouse.moves == [] and mouse.presses == [] and log.lines == []


def test_11b_the_default_config_is_disabled_and_test_only():
    config = mc.MouseControllerConfig().validate()
    assert config.automation_enabled is False and config.mode == mc.TEST
    assert mc.load_mouse_config().automation_enabled is False    # the shipped file
    assert mc.load_mouse_config().mode == mc.TEST


def test_11c_there_is_no_live_mode():
    assert mc.MODES == (mc.TEST,)
    with pytest.raises(ValueError):
        mc.MouseControllerConfig(mode="LIVE").validate()


def test_11d_a_bad_config_file_leaves_automation_off(tmp_path):
    path = tmp_path / "mouse.json"
    path.write_text('{"automation_enabled": true, "mode": "LIVE"}', encoding="utf-8")
    assert mc.load_mouse_config(str(path)).automation_enabled is False


def test_11e_failsafe_halts_the_controller():
    controller, table, mouse, log = make()

    class FailSafe(Exception):
        pass

    def boom(*args):
        raise FailSafe("corner")

    mouse.FailSafeException = FailSafe
    mouse.move = boom
    ante(controller)
    assert controller.halted is True
    assert "fail-safe triggered" in log.lines[-1]
    ante(controller, 101)
    controller.handle_scenario_result(result(se.PLAY, 101))
    assert table.counts == {mc.ANTE: 0, mc.PLAY_BUTTON: 0}


def test_11f_the_failsafe_is_switched_on():
    pyautogui = pytest.importorskip("pyautogui")
    mc.PyAutoGuiMouse()
    assert pyautogui.FAILSAFE is True


# -- 12-13: separation of concerns -----------------------------------------------------------

def test_12_scenario_engine_remains_independent():
    source = inspect.getsource(se)
    assert "automation" not in source and "pyautogui" not in source.lower()
    slots = {s: {"card": c, "status": "CONFIRMED", "readings": 3} for s, c in
             zip(se.PLAYER_SLOTS + se.FLOP_SLOTS, ["7S", "7D", "7H", "KC", "3S"])}
    before = se.evaluate_round(slots, round_id=1)
    controller, _, _, _ = make()
    ante(controller, 1)
    controller.handle_scenario_result(before, action_round=1)
    after = se.evaluate_round(slots, round_id=1)
    assert before == after and after["decision"] == se.PLAY


def test_13_action_controller_does_not_calculate_poker_hands():
    source = inspect.getsource(mc)
    for name in ("hand_evaluator", "evaluate_hand", "evaluate_round", "detect_player_decision",
                 "dealer_qualifies", "evaluate_dealer_qualification", "parse_card"):
        assert name not in source
    # It trusts the decision it is given: trips with DON'T_PLAY is not played.
    controller, table, _, _ = make()
    ante(controller)
    controller.handle_scenario_result({"decision": se.DONT_PLAY, "round_id": 100,
                                       "player_cards": ["7S", "7D"],
                                       "flop_cards": ["7H", "KC", "3S"]})
    assert table.counts[mc.PLAY_BUTTON] == 0


def test_the_tracker_never_loads_pyautogui_when_disabled():
    import subprocess
    import sys

    code = ("import sys, queue, tracker; t = tracker.Tracker({'monitor': 1}, queue.Queue()); "
            "t._run_action_controller('WAITING', {}, None); "
            "print('pyautogui' in sys.modules, t.action_controller is None)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=__import__("os").path.dirname(__import__("os").path.dirname(
                             __import__("os").path.abspath(__file__))))
    assert out.stdout.strip().splitlines()[-1] == "False True", out.stderr[-500:]


# -- worker thread: the poll is never held up ---------------------------------------------------

def test_clicks_run_off_the_polling_thread():
    controller, table, mouse, log = make(synchronous=False)
    mouse.hold = 0.3                          # a slow mouse
    started = time.perf_counter()
    ante(controller)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.05, "handle_scenario_result must only queue the click"
    for _ in range(50):                        # polls while the click is in flight
        ante(controller)
    deadline = time.time() + 3
    while controller.status()["ante"] != mc.CLICKED and time.time() < deadline:
        time.sleep(0.01)
    assert table.counts[mc.ANTE] == 1
    controller.close()


def test_a_queued_click_for_an_old_hand_is_dropped():
    controller, table, mouse, log = make()
    controller.synchronous = False
    gate = threading.Event()
    original = table.validate
    table.validate = lambda b, p: (gate.wait(2), original(b, p))[1]
    ante(controller, 100)
    controller.handle_scenario_result(result(se.WAIT, 101))     # next hand begins
    gate.set()
    deadline = time.time() + 2
    while not any("round changed" in line for line in log.lines) and time.time() < deadline:
        time.sleep(0.01)
    assert table.counts[mc.ANTE] == 0
    controller.close()


# -- HandCycle: when a hand starts ----------------------------------------------------------------

def test_hand_cycle_starts_once_per_empty_table_after_cards():
    cycle = mc.HandCycle(empty_polls=3)
    starts = []
    # startup mid-hand: cards showing -> no hand yet
    for empty in [False, False]:
        starts.append(cycle.observe(empty))
    assert starts == [(None, False), (None, False)]
    # table empties for a long time (CardMemory's round id churns meanwhile)
    events = [cycle.observe(True) for _ in range(40)]
    assert [e for e in events if e[1]] == [(1, True)]
    # cards dealt, a covered poll mid-hand, hand ends
    for empty in [False, False, True, False, False]:
        assert cycle.observe(empty)[1] is False
    events = [cycle.observe(True) for _ in range(40)]
    assert [e for e in events if e[1]] == [(2, True)]


# -- 18: a simulated session through the real Tracker ---------------------------------------------

@pytest.fixture
def tracker_with_controller(monkeypatch):
    import numpy

    import tracker as tracker_module
    from config.settings import CARD_SLOTS

    screen = {}

    def fake_read_table(config, images=None):
        reads, seen = {}, {}
        for slot in CARD_SLOTS:
            card = screen.get(slot)
            reads[slot] = {"card": card, "present": bool(card), "confident": bool(card),
                           "confidence": 0.9 if card else 0.0, "suit_margin": 0.2}
            seen[slot] = card
        return seen, reads

    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: numpy.zeros((40, 60, 3), "uint8"))
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "read_table", fake_read_table)
    monkeypatch.setattr(tracker_module.db, "insert_hand", lambda record: (False, None))
    monkeypatch.setattr(se, "load_engine_config", lambda: se.EngineConfig())

    controller, table, mouse, log = make()
    monkeypatch.setattr(mc, "load_mouse_config", lambda: controller.config)
    monkeypatch.setattr(mc, "ActionController", lambda config: controller)
    events = queue.Queue()
    tr = tracker_module.Tracker({"monitor": 1, "clear_frames": 3}, events)
    return tr, screen, table, log, events


def run(tr, n):
    for _ in range(n):
        tr._tick()


def test_18_a_simulated_session(tracker_with_controller):
    tr, screen, table, log, events = tracker_with_controller

    # Hand 1: empty table -> ANTE once; PLAY hand -> PLAY once across many polls.
    run(tr, 12)
    assert table.counts == {mc.ANTE: 1, mc.PLAY_BUTTON: 0}
    screen.update({"player_1": "7S", "player_2": "7D", "flop_1": "7H", "flop_2": "KC", "flop_3": "3S"})
    run(tr, 25)
    assert table.counts == {mc.ANTE: 1, mc.PLAY_BUTTON: 1}

    # Hand 2: DON'T_PLAY -> ANTE once, no PLAY.
    screen.clear(); run(tr, 12)
    assert table.counts == {mc.ANTE: 2, mc.PLAY_BUTTON: 1}
    screen.update({"player_1": "JS", "player_2": "9D", "flop_1": "7C", "flop_2": "5H", "flop_3": "2S"})
    run(tr, 25)
    assert table.counts == {mc.ANTE: 2, mc.PLAY_BUTTON: 1}

    # Hand 3: cards never confirmed (WAIT) -> ANTE once, no PLAY.
    screen.clear(); run(tr, 12)
    screen.update({"player_1": "AS"})
    run(tr, 20)
    assert table.counts == {mc.ANTE: 3, mc.PLAY_BUTTON: 1}

    update = None
    while not events.empty():
        kind, payload = events.get_nowait()
        if kind == "update":
            update = payload
    assert update["action"]["round"] == 3
    assert update["scenario"]["decision"] == se.WAIT


def test_disabled_controller_leaves_the_tracker_payload_as_before(monkeypatch):
    import numpy
    import tracker as tracker_module

    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: numpy.zeros((40, 60, 3), "uint8"))
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    from config.settings import CARD_SLOTS

    empty = {slot: {"card": None, "present": False, "confident": False, "confidence": 0.0}
             for slot in CARD_SLOTS}
    monkeypatch.setattr(tracker_module, "read_table",
                        lambda config, images=None: ({s: None for s in CARD_SLOTS}, dict(empty)))
    events = queue.Queue()
    tr = tracker_module.Tracker({"monitor": 1}, events)
    tr._tick()
    kind, payload = events.get_nowait()
    assert payload["action"] is None and tr.action_controller is None
