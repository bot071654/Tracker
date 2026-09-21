"""The TEST action flow, end to end through the controller's REAL click checks.

    NEW HAND -> ANTE_REQUIRED -> TEST ANTE -> WAIT -> PLAY -> TEST PLAY

Unlike test_action_controller.py (which fakes the whole target), these tests use
the real TestTableTarget: a heartbeat file in a temp folder describes a test
table, and only the three Windows calls it makes (is the window shown, which
process owns it, which window is under a point) are stood in for. So the status
file, title, pid, button rectangle and round checks all run as they do live.
No real mouse moves.
"""

import json
import os
import queue
import threading
import time

import pytest

from automation import mouse_controller as mc
from poker import scenario_engine as se
from tests_support_action import Log

HWND, PID = 4242, 9191
RECTS = {mc.ANTE: [100, 100, 220, 150], mc.BONUS: [100, 160, 220, 210],
         mc.PLAY_BUTTON: [100, 220, 220, 270]}
WINDOW = [90, 60, 230, 300]


class SimTable:
    """A test table that exists only as its heartbeat file."""

    def __init__(self, path):
        self.path = path
        self.window_open = True
        self.pid = PID
        self.rects = {name: list(rect) for name, rect in RECTS.items()}
        self.counts = {mc.ANTE: 0, mc.BONUS: 0, mc.PLAY_BUTTON: 0}
        self.write()

    def write(self):
        status = {"title": mc.TEST_WINDOW_TITLE, "pid": self.pid, "hwnd": HWND,
                  "heartbeat": time.time(), "counts": dict(self.counts),
                  "buttons": self.rects}
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(status, handle)

    def remove(self):
        os.remove(self.path)

    def press_at(self, x, y):
        for name, (left, top, right, bottom) in self.rects.items():
            if left <= x < right and top <= y < bottom:
                self.counts[name] += 1
                self.write()
                return name
        return None


class SimMouse:
    def __init__(self, table):
        self.table = table
        self.at = None
        self.moves, self.presses = [], []
        self.before_press = None          # hook: runs after moving, before pressing

    def move(self, x, y, duration):
        self.at = (x, y)
        self.moves.append((x, y))
        if self.before_press:
            self.before_press()

    def press(self, hold):
        self.presses.append(self.table.press_at(*self.at))


@pytest.fixture
def sim(tmp_path, monkeypatch):
    table = SimTable(str(tmp_path / "status.json"))
    monkeypatch.setattr(mc, "_window_is_shown",
                        lambda hwnd, title: table.window_open and hwnd == HWND
                        and title == mc.TEST_WINDOW_TITLE)
    monkeypatch.setattr(mc, "_window_pid", lambda hwnd: PID if hwnd == HWND else None)

    def window_at(x, y):
        left, top, right, bottom = WINDOW
        return HWND if table.window_open and left <= x < right and top <= y < bottom else 1

    monkeypatch.setattr(mc, "_window_at", window_at)
    monkeypatch.setattr(mc, "_raise_without_activating", lambda hwnd: None)

    def make(synchronous=True, **overrides):
        values = dict(automation_enabled=True, status_file=table.path, verify_timeout=0.3)
        values.update(overrides)
        config = mc.MouseControllerConfig(**values).validate()
        mouse = SimMouse(table)
        log = Log()
        controller = mc.ActionController(config, mouse=mouse, log=log, synchronous=synchronous)
        return controller, mouse, log

    return table, make


def payload(decision, tracker_round=10, player=("7S", "7D"), flop=("7H", "KC", "3S")):
    """What the Scenario Engine sends (only the keys the controller reads)."""
    return {"decision": decision, "round_id": tracker_round, "player_cards": list(player),
            "flop_cards": list(flop), "primary_scenario": "THREE_OF_A_KIND",
            "matched_scenarios": ["PLAYER_PAIR"]}


def ante(controller, hand=1, tracker_round=10):
    return controller.handle_scenario_result({"decision": mc.ANTE_REQUIRED,
                                              "round_id": tracker_round}, action_round=hand)


def wait_until(predicate, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def centre(name):
    left, top, right, bottom = RECTS[name]
    return ((left + right) // 2, (top + bottom) // 2)


# -- the happy path ---------------------------------------------------------------------

def test_full_flow_new_hand_ante_wait_play(sim):
    table, make = sim
    controller, mouse, log = make()
    ante(controller)
    controller.handle_scenario_result(payload(se.WAIT), action_round=1)
    assert table.counts[mc.PLAY_BUTTON] == 0
    controller.handle_scenario_result(payload(se.PLAY), action_round=1)
    assert table.counts == {mc.ANTE: 1, mc.BONUS: 0, mc.PLAY_BUTTON: 1}
    # positions came from the table's own published geometry, not constants
    assert mouse.moves == [centre(mc.ANTE), centre(mc.PLAY_BUTTON)]


# -- 1. ANTE exactly once -------------------------------------------------------------------

def test_regression_ante_exactly_once(sim):
    table, make = sim
    controller, mouse, _ = make()
    for _ in range(30):
        ante(controller)
        controller.handle_scenario_result(payload(se.WAIT), action_round=1)
    assert table.counts[mc.ANTE] == 1 and mouse.presses == [mc.ANTE]


# -- 2. PLAY exactly once -------------------------------------------------------------------

def test_regression_play_exactly_once(sim):
    table, make = sim
    controller, mouse, _ = make()
    ante(controller)
    for _ in range(30):
        controller.handle_scenario_result(payload(se.PLAY), action_round=1)
    assert table.counts[mc.PLAY_BUTTON] == 1
    assert mouse.presses == [mc.ANTE, mc.PLAY_BUTTON]


def test_regression_play_exactly_once_while_the_click_is_still_queued(sim):
    table, make = sim
    controller, mouse, log = make(synchronous=False)
    ante(controller)
    assert wait_until(lambda: controller.status()["ante"] == mc.CLICKED)
    for _ in range(50):                                   # polls arriving mid-click
        controller.handle_scenario_result(payload(se.PLAY), action_round=1)
    assert wait_until(lambda: controller.status()["play"] == mc.CLICKED)
    time.sleep(0.2)
    controller.close()
    assert table.counts[mc.PLAY_BUTTON] == 1
    assert any("Already queued for round 1" in line or "Already executed for round 1" in line
               for line in log.lines)


# -- 3. no PLAY before ANTE ------------------------------------------------------------------

def test_regression_no_play_before_ante(sim):
    table, make = sim
    controller, mouse, log = make()
    controller.handle_scenario_result(payload(se.PLAY), action_round=1)
    assert mouse.moves == [] and table.counts[mc.PLAY_BUTTON] == 0
    assert "ANTE has not been clicked for round 1" in log.lines[-1]


def test_regression_no_play_when_the_ante_click_failed(sim):
    table, make = sim
    controller, mouse, _ = make()
    table.window_open = False
    ante(controller)                                       # refused: window not shown
    table.window_open = True
    table.write()
    controller.handle_scenario_result(payload(se.PLAY), action_round=1)
    assert table.counts == {mc.ANTE: 0, mc.BONUS: 0, mc.PLAY_BUTTON: 0}
    assert controller.status()["ante"] == mc.FAILED


# -- 4. DON'T_PLAY ----------------------------------------------------------------------------

def test_regression_dont_play_never_clicks_play(sim):
    table, make = sim
    controller, mouse, log = make()
    ante(controller)
    for _ in range(20):
        controller.handle_scenario_result(payload(se.DONT_PLAY, player=("JS", "9D"),
                                                  flop=("7C", "5H", "2S")), action_round=1)
    assert table.counts[mc.PLAY_BUTTON] == 0 and mouse.presses == [mc.ANTE]
    controller.close()
    summary = [line for line in log.lines if line.startswith("[TEST HAND SUMMARY]")]
    assert summary and "scenario=DON'T_PLAY\nante=1\nplay=0" in summary[-1]


# -- 5. WAIT -----------------------------------------------------------------------------------

def test_regression_wait_never_clicks_play(sim):
    table, make = sim
    controller, mouse, _ = make()
    ante(controller)
    for _ in range(20):
        controller.handle_scenario_result(payload(se.WAIT), action_round=1)
    assert table.counts[mc.PLAY_BUTTON] == 0 and mouse.presses == [mc.ANTE]


def test_regression_wait_alone_does_not_ante(sim):
    table, make = sim
    controller, mouse, _ = make()
    controller.handle_scenario_result(payload(se.WAIT), action_round=1)
    assert mouse.moves == [] and table.counts[mc.ANTE] == 0


# -- 6. stale action after a round change ------------------------------------------------------

def test_regression_queued_ante_for_an_old_hand_is_invalidated(sim):
    table, make = sim
    controller, mouse, log = make(synchronous=False)
    gate = threading.Event()
    real_validate = controller.target.validate
    controller.target.validate = lambda b, p: (gate.wait(2), real_validate(b, p))[1]
    ante(controller, hand=1)
    controller.handle_scenario_result(payload(se.WAIT, tracker_round=11), action_round=2)
    gate.set()
    assert wait_until(lambda: any("round changed" in line for line in log.lines))
    controller.close()
    assert table.counts[mc.ANTE] == 0 and mouse.presses == []


def test_regression_hand_changes_during_validation_does_not_move_the_mouse(sim):
    table, make = sim
    controller, mouse, log = make()
    real_validate = controller.target.validate

    def validate_then_new_hand(button, point):
        outcome = real_validate(button, point)
        controller.handle_scenario_result(payload(se.WAIT, tracker_round=11), action_round=2)
        return outcome

    controller.target.validate = validate_then_new_hand
    ante(controller, hand=1)
    assert mouse.moves == [] and mouse.presses == []
    assert "round changed" in log.lines[-1]


def test_regression_hand_changes_between_move_and_press(sim):
    table, make = sim
    controller, mouse, log = make()
    mouse.before_press = lambda: controller.handle_scenario_result(
        payload(se.WAIT, tracker_round=11), action_round=2)
    ante(controller, hand=1)
    assert mouse.moves == [centre(mc.ANTE)] and mouse.presses == []
    assert "round changed" in log.lines[-1]


def test_regression_play_scenario_from_an_earlier_tracker_round_is_ignored(sim):
    table, make = sim
    controller, _, log = make()
    ante(controller, hand=1, tracker_round=40)
    controller.handle_scenario_result(payload(se.PLAY, tracker_round=39), action_round=1)
    assert table.counts[mc.PLAY_BUTTON] == 0
    assert "belongs to an earlier hand" in log.lines[-1]


# -- 7. queued action after the tracker stops --------------------------------------------------

@pytest.fixture
def scripted_tracker(sim, monkeypatch):
    import numpy

    import tracker as tracker_module
    from config.settings import CARD_SLOTS

    table, make = sim
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
                        lambda monitor=1: numpy.zeros((40, 60, 3), "uint8"))
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "read_table", fake_read_table)
    monkeypatch.setattr(tracker_module.db, "insert_hand", lambda record: (False, None))
    monkeypatch.setattr(se, "load_engine_config", lambda: se.EngineConfig())

    controller, mouse, log = make(synchronous=False)
    monkeypatch.setattr(mc, "load_mouse_config", lambda: controller.config)
    monkeypatch.setattr(mc, "ActionController", lambda config: controller)
    tr = tracker_module.Tracker({"monitor": 1, "clear_frames": 3}, queue.Queue())
    return tr, screen, table, controller, mouse, log


def test_regression_queued_action_is_cancelled_when_the_tracker_stops(scripted_tracker):
    tr, screen, table, controller, mouse, log = scripted_tracker
    in_move, release = threading.Event(), threading.Event()

    def held_move(x, y, duration):
        mouse.at = (x, y)
        mouse.moves.append((x, y))
        in_move.set()
        release.wait(3)

    mouse.move = held_move
    for _ in range(12):                                     # empty table -> hand 1 -> ANTE
        tr._tick()
    assert in_move.wait(2), "the ANTE click should be under way"
    stopper = threading.Thread(target=tr.stop)
    stopper.start()                                         # Tracker.stop -> controller.close
    assert wait_until(lambda: controller.closed, 2)
    release.set()
    stopper.join(5)
    assert table.counts[mc.ANTE] == 0 and mouse.presses == []
    assert any("controller was stopped" in line for line in log.lines)
    # and nothing new is accepted afterwards
    assert ante(controller, hand=99) is None


def test_regression_jobs_still_in_the_queue_are_dropped_on_close(sim):
    table, make = sim
    controller, mouse, log = make(synchronous=False)
    gate = threading.Event()
    mouse.before_press = lambda: gate.wait(2)
    ante(controller, hand=1)
    assert wait_until(lambda: mouse.moves)
    ante(controller, hand=2)                                # sits in the queue
    closer = threading.Thread(target=controller.close)
    closer.start()
    assert wait_until(lambda: controller.closed)
    gate.set()
    closer.join(5)
    assert table.counts[mc.ANTE] == 0
    assert mouse.moves == [centre(mc.ANTE)], "the queued hand-2 ANTE must never move the mouse"


# -- 8. duplicate scenario updates -------------------------------------------------------------

def test_regression_duplicate_scenario_updates(sim):
    table, make = sim
    controller, mouse, log = make()
    ante(controller)
    same = payload(se.PLAY)
    for _ in range(40):
        controller.handle_scenario_result(dict(same), action_round=1)
        ante(controller)
    assert table.counts == {mc.ANTE: 1, mc.BONUS: 0, mc.PLAY_BUTTON: 1}
    assert len([line for line in log.lines if line.startswith("[TEST HAND]\n")]) == 1
    assert len([line for line in log.lines if "Already executed for round 1" in line]) == 2


# -- 9. invalid scenario ------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, {}, "PLAY", 7, ["PLAY"],
                                 {"decision": "FOLD", "round_id": 1},
                                 {"decision": None, "round_id": 1},
                                 {"decision": "BONUS", "round_id": 1},
                                 {"decision": "LIVE_PLAY", "round_id": 1},
                                 {"decision": "play", "round_id": 1}])
def test_regression_invalid_scenario(sim, bad):
    table, make = sim
    controller, mouse, log = make()
    ante(controller)
    assert controller.handle_scenario_result(bad, action_round=1) is None
    assert table.counts[mc.PLAY_BUTTON] == 0 and mouse.presses == [mc.ANTE]
    assert "Invalid state" in log.lines[-1]


def test_regression_invalid_scenario_without_any_round(sim):
    table, make = sim
    controller, mouse, log = make()
    assert controller.handle_scenario_result({"decision": se.PLAY}) is None
    assert mouse.moves == [] and "Invalid state" in log.lines[-1]


def test_regression_bonus_is_never_clicked(sim):
    table, make = sim
    controller, mouse, _ = make()
    ok, reason = controller.target.validate(mc.BONUS, centre(mc.BONUS))
    assert not ok and "not a TEST action button" in reason
    ante(controller)
    controller.handle_scenario_result(payload(se.PLAY), action_round=1)
    assert table.counts[mc.BONUS] == 0


# -- 10. missing test window -----------------------------------------------------------------------

def test_regression_missing_status_file(sim):
    table, make = sim
    controller, mouse, log = make()
    table.remove()
    ante(controller)
    assert mouse.moves == [] and "not running" in log.lines[-1]


def test_regression_stale_heartbeat(sim):
    table, make = sim
    controller, mouse, log = make(status_max_age=0.5)
    time.sleep(0.7)
    ante(controller)
    assert mouse.moves == [] and "stale heartbeat" in log.lines[-1]


def test_regression_window_closed(sim):
    table, make = sim
    controller, mouse, log = make()
    table.window_open = False
    ante(controller)
    assert mouse.moves == [] and "not visible" in log.lines[-1]


def test_regression_window_of_another_process(sim):
    table, make = sim
    controller, mouse, log = make()
    table.pid = PID + 1                  # the heartbeat claims a process that does not own it
    table.write()
    ante(controller)
    assert mouse.moves == [] and "does not belong" in log.lines[-1]


def test_regression_window_disappears_while_the_mouse_moves(sim):
    table, make = sim
    controller, mouse, log = make()

    def close_window():
        table.window_open = False

    mouse.before_press = close_window
    ante(controller)
    assert mouse.presses == [] and "after moving" in log.lines[-1]


# -- 11. invalid button position --------------------------------------------------------------------

def test_regression_configured_point_outside_the_button(sim):
    table, make = sim
    controller, mouse, log = make(ante_button=[400, 400])
    ante(controller)
    assert mouse.moves == [] and "outside the TEST ANTE button" in log.lines[-1]


def test_regression_play_point_on_the_bonus_button_is_refused(sim):
    table, make = sim
    controller, mouse, log = make(play_button=list(centre(mc.BONUS)))
    ante(controller)
    controller.handle_scenario_result(payload(se.PLAY), action_round=1)
    assert mouse.presses == [mc.ANTE] and table.counts[mc.BONUS] == 0
    assert "outside the TEST PLAY button" in log.lines[-1]


def test_regression_button_moved_after_the_position_was_read(sim):
    table, make = sim
    controller, mouse, log = make()

    def move_table():
        table.rects[mc.ANTE] = [500, 500, 600, 550]
        table.write()

    mouse.before_press = move_table
    ante(controller)
    assert mouse.presses == [] and "outside" in log.lines[-1]


def test_regression_button_covered_by_another_window(sim, monkeypatch):
    table, make = sim
    controller, mouse, log = make()
    monkeypatch.setattr(mc, "_window_at", lambda x, y: 1)
    ante(controller)
    assert mouse.moves == [] and "covers" in log.lines[-1]


# -- logs ---------------------------------------------------------------------------------------------

def test_logs_test_hand_action_and_summary(sim):
    table, make = sim
    controller, _, log = make()
    ante(controller, hand=3, tracker_round=12)
    controller.handle_scenario_result(payload(se.PLAY, tracker_round=12), action_round=3)
    controller.close()
    text = "\n\n".join(log.lines)
    assert ("[TEST HAND]\nround=3\ntracker_round=12\nplayer=7S 7D\nflop=7H KC 3S\n"
            "scenario=PLAY") in text
    assert "[TEST ACTION]\nANTE\nround=3\ndecision=ANTE_REQUIRED\nresult=CLICKED" in text
    assert "[TEST ACTION]\nPLAY\nround=3\ndecision=PLAY\nresult=CLICKED" in text
    assert "[TEST HAND SUMMARY]\nround=3\nplayer=7S 7D\nflop=7H KC 3S\nscenario=PLAY\nante=1\nplay=1" in text
    assert text.index("[TEST ACTION]\nANTE") < text.index("[TEST ACTION]\nPLAY") \
        < text.index("[TEST HAND SUMMARY]")


def test_shipped_config_is_disabled_and_test_only():
    config = mc.load_mouse_config()
    assert config.automation_enabled is False and config.mode == "TEST"
