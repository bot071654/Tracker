"""Regression tests for the Action Controller failures found while debugging.

Each test names the bug it guards against. Fakes stand in for the mouse and
the table; the Windows window checks are exercised in test_test_poker_ui.py.
"""

import threading
import time

import pytest

from automation import mouse_controller as mc
from poker import scenario_engine as se
from tests_support_action import FakeMouse, FakeTable, Log, make


def result(decision, hand_round=None):
    out = {"decision": decision}
    if hand_round is not None:
        out["round_id"] = hand_round
    return out


def wait_until(predicate, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# -- BUG: queued clicks still ran after the tracker was stopped ------------------------

def test_no_queued_click_runs_after_close():
    controller, table, mouse, log = make(synchronous=False)
    mouse.hold = 0.3
    controller.handle_scenario_result(result(mc.ANTE_REQUIRED), action_round=1)
    wait_until(lambda: mouse.moves)                       # first click in flight
    controller.handle_scenario_result(result(mc.ANTE_REQUIRED), action_round=2)   # queued
    controller.close()
    time.sleep(1.0)
    assert table.counts[mc.ANTE] <= 1, "the queued click for hand 2 ran after close()"
    assert controller.handle_scenario_result(result(mc.ANTE_REQUIRED), action_round=3) is None


def test_close_stops_the_worker_thread():
    controller, _, _, _ = make(synchronous=False)
    controller.handle_scenario_result(result(mc.ANTE_REQUIRED), action_round=1)
    wait_until(lambda: controller.status()["ante"] == mc.CLICKED)
    worker = controller._worker
    controller.close()
    assert wait_until(lambda: not worker.is_alive(), 2.0)


# -- BUG: a new hand starting while the mouse was moving still got the click ------------

def test_no_click_if_the_hand_changes_while_the_mouse_moves():
    controller, table, mouse, log = make(synchronous=False)
    moving = threading.Event()
    release = threading.Event()
    original = mouse.move

    def slow_move(x, y, duration):
        original(x, y, duration)
        moving.set()
        release.wait(2)

    mouse.move = slow_move
    controller.handle_scenario_result(result(mc.ANTE_REQUIRED), action_round=1)
    assert moving.wait(2)
    controller.handle_scenario_result(result(se.WAIT), action_round=2)     # hand 2 begins
    release.set()
    assert wait_until(lambda: any("round changed" in line for line in log.lines))
    assert table.counts[mc.ANTE] == 0 and mouse.presses == []
    controller.close()


# -- BUG: a PLAY computed before the hand began could be acted on in the new hand ------

def test_play_from_a_previous_hands_scenario_is_ignored():
    controller, table, _, log = make()
    # Hand 5 starts while the tracker's scenario round (CardMemory generation) is 40.
    controller.handle_scenario_result(result(mc.ANTE_REQUIRED, 40), action_round=5)
    assert table.counts[mc.ANTE] == 1
    controller.handle_scenario_result(result(se.PLAY, 39), action_round=5)   # stale
    assert table.counts[mc.PLAY_BUTTON] == 0
    assert "belongs to an earlier hand" in log.lines[-1]
    controller.handle_scenario_result(result(se.PLAY, 41), action_round=5)   # this hand
    assert table.counts[mc.PLAY_BUTTON] == 1


# -- BUG: fixed coordinates broke whenever the test table was placed differently -------

def test_button_positions_come_from_the_running_table_when_not_configured():
    controller, table, mouse, _ = make(ante_button=None, play_button=None)
    table.centres = {mc.ANTE: [300, 400], mc.PLAY_BUTTON: [300, 460]}
    mouse.points = dict(table.centres)
    controller.handle_scenario_result(result(mc.ANTE_REQUIRED), action_round=1)
    controller.handle_scenario_result(result(se.PLAY), action_round=1)
    assert mouse.moves == [(300, 400), (300, 460)]
    assert table.counts == {mc.ANTE: 1, mc.PLAY_BUTTON: 1}


def test_no_click_when_the_table_publishes_no_button_position():
    controller, table, mouse, log = make(ante_button=None, play_button=None)
    table.centres = {}
    controller.handle_scenario_result(result(mc.ANTE_REQUIRED), action_round=1)
    assert mouse.moves == [] and "not visible" in log.lines[-1]


# -- the complete requested sequence, hand by hand -----------------------------------------

def test_four_hands_exact_click_counts():
    controller, table, mouse, log = make()

    def hand(n, generation, decisions):
        controller.handle_scenario_result(result(mc.ANTE_REQUIRED, generation), action_round=n)
        for decision in decisions:
            for _ in range(5):
                controller.handle_scenario_result(result(decision, generation), action_round=n)
                controller.handle_scenario_result(result(mc.ANTE_REQUIRED, generation), action_round=n)

    hand(1, 10, [se.WAIT, se.DONT_PLAY])
    assert table.counts == {mc.ANTE: 1, mc.PLAY_BUTTON: 0}
    hand(2, 20, [se.WAIT, se.PLAY])
    assert table.counts == {mc.ANTE: 2, mc.PLAY_BUTTON: 1}
    hand(3, 30, [se.WAIT, se.DONT_PLAY, se.PLAY, se.WAIT, se.PLAY, se.DONT_PLAY])
    assert table.counts == {mc.ANTE: 3, mc.PLAY_BUTTON: 2}
    hand(4, 40, ["BOGUS", None, "LIVE_PLAY"])
    assert table.counts == {mc.ANTE: 4, mc.PLAY_BUTTON: 2}
    assert mouse.presses == [mc.ANTE, mc.ANTE, mc.PLAY_BUTTON, mc.ANTE, mc.PLAY_BUTTON, mc.ANTE]
