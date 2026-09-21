"""The local TEST betting screen, and the controller's phase checks and pre-round SKIP."""

import json
import queue
import threading
import time

import pytest

from automation import mouse_controller as mc
from automation import test_betting_screen as bs
from poker import scenarios as scenario_rules
from tests_support_action import Log

FAST = {bs.NEXT_GAME: 1.0, bs.BETTING: 5.0, bs.DEALING: 1.0, bs.DECISION: 5.0,
        bs.REVEAL: 1.0, bs.RESULT: 2.0}


def engine(**kwargs):
    game = bs.GameEngine(seed=kwargs.pop("seed", 3), durations=dict(FAST), **kwargs)
    game.start(0.0)
    return game


def run_to(game, phase, now=0.0, limit=100.0):
    while game.phase != phase and now < limit:
        now += 0.1
        game.tick(now)
    assert game.phase == phase, (game.phase, now)
    return now


# -- the game ---------------------------------------------------------------------------

def test_a_round_goes_through_every_phase_and_deals_on_time():
    game = engine()
    assert game.phase == bs.NEXT_GAME and not any(game.shown.values())
    now = run_to(game, bs.BETTING)
    assert not any(game.shown.values()) and game.message == "PLACE YOUR BETS"
    now = run_to(game, bs.DEALING, now)
    assert all(game.shown[s] for s in bs.PLAYER_SLOTS + bs.FLOP_SLOTS)
    assert not any(game.shown[s] for s in bs.LATER_SLOTS)
    now = run_to(game, bs.REVEAL, now)
    assert all(game.shown.values())
    now = run_to(game, bs.RESULT, now)
    assert game.history[-1]["round"] == 1 and game.history[-1]["ante"] is False
    now = run_to(game, bs.NEXT_GAME, now)
    assert game.round == 2 and not any(game.shown.values())


def test_buttons_only_work_in_their_phase_and_once_per_round():
    game = engine()
    assert game.press(mc.ANTE, 0.1) == (False, "ANTE is closed during NEXT_GAME")
    now = run_to(game, bs.BETTING)
    assert game.press(mc.PLAY_BUTTON, now)[0] is False
    assert game.press(mc.BONUS, now) == (False, "BONUS needs an ANTE first")
    assert game.press(mc.ANTE, now) == (True, "ANTE placed")
    assert game.press(mc.ANTE, now) == (False, "ANTE already placed this round")
    now = run_to(game, bs.DECISION, now)
    assert game.press(mc.ANTE, now)[0] is False
    assert game.press(mc.PLAY_BUTTON, now) == (True, "PLAY placed")
    assert game.bets == {mc.ANTE: 50, mc.BONUS: 0, mc.PLAY_BUTTON: 100}
    assert game.counts == {mc.ANTE: 1, mc.BONUS: 0, mc.PLAY_BUTTON: 1}
    # refused: before betting opened, the duplicate, and after betting closed
    assert game.rejected[mc.ANTE] == 3 and game.rejected[mc.PLAY_BUTTON] == 1


def test_play_without_ante_is_refused():
    game = engine()
    now = run_to(game, bs.DECISION)
    assert game.press(mc.PLAY_BUTTON, now) == (False, "PLAY needs an ANTE this round")
    assert game.message == "NO ANTE THIS ROUND"


def test_settlement_follows_casino_holdem_rules():
    game = engine()
    now = run_to(game, bs.BETTING)
    game.press(mc.ANTE, now)
    now = run_to(game, bs.DECISION, now)
    game.press(mc.PLAY_BUTTON, now)
    now = run_to(game, bs.RESULT, now)
    out = game.history[-1]
    from poker.hand_evaluator import evaluate_hand
    board = [game.deal[s] for s in bs.FLOP_SLOTS + ["turn", "river"]]
    player = evaluate_hand([game.deal["player_1"], game.deal["player_2"]] + board)
    ante_pays = bs.ANTE_PAYS.get(player["category"], 1)
    expected = {("Player", True): 50 * ante_pays + 100, ("Dealer", True): -150, ("Tie", True): 0}
    if out["dealer_qualified"]:
        assert out["net"] == expected[(out["winner"], True)]
    else:
        assert out["net"] == 50 * ante_pays
    assert out["credits"] == bs.STARTING_CREDITS + out["net"]


def test_folding_loses_the_ante():
    game = engine()
    now = run_to(game, bs.BETTING)
    game.press(mc.ANTE, now)
    run_to(game, bs.RESULT, now)
    assert game.history[-1]["folded"] and game.history[-1]["net"] == -50


def test_the_same_seed_deals_the_same_cards():
    assert bs.GameEngine(seed=11).deal == bs.GameEngine(seed=11).deal


def test_session_expiry_closes_everything_and_logging_in_resumes():
    game = engine(expire_round=2, expire_seconds=0)
    now = run_to(game, bs.SESSION_EXPIRED)
    assert game.round == 2 and not any(game.shown.values())
    assert game.press(mc.ANTE, now) == (False, "session expired")
    game.tick(now + 60)
    assert game.phase == bs.SESSION_EXPIRED, "stays expired until logged in again"
    game.log_in_again(now + 60)
    assert game.phase == bs.NEXT_GAME
    run_to(game, bs.BETTING, now + 60, limit=200)


def test_session_expiry_can_log_back_in_by_itself():
    game = engine(expire_round=2, expire_seconds=3)
    now = run_to(game, bs.SESSION_EXPIRED)
    game.tick(now + 3.1)
    assert game.phase == bs.NEXT_GAME


# -- the controller against a simulated screen's status ------------------------------------

HWND, PID = 777, 4321


@pytest.fixture
def screen(tmp_path, monkeypatch):
    path = str(tmp_path / "status.json")
    state = {"phase": bs.NEXT_GAME, "counts": {mc.ANTE: 0, mc.BONUS: 0, mc.PLAY_BUTTON: 0}}
    rects = {mc.ANTE: [100, 400, 180, 440], mc.BONUS: [200, 400, 280, 440],
             mc.PLAY_BUTTON: [300, 400, 380, 440]}

    def write():
        with open(path, "w") as handle:
            json.dump({"title": mc.TEST_WINDOW_TITLE, "pid": PID, "hwnd": HWND,
                       "heartbeat": time.time(), "buttons": rects, **state}, handle)

    write()
    monkeypatch.setattr(mc, "_window_is_shown", lambda hwnd, title: hwnd == HWND)
    monkeypatch.setattr(mc, "_window_pid", lambda hwnd: PID)
    monkeypatch.setattr(mc, "_window_at", lambda x, y: HWND)
    monkeypatch.setattr(mc, "_raise_without_activating", lambda hwnd: None)

    class Mouse:
        def __init__(self):
            self.moves, self.presses, self.at = [], [], None

        def move(self, x, y, duration):
            self.at = (x, y)
            self.moves.append((x, y))

        def press(self, hold):
            for name, (l, t, r, b) in rects.items():
                if l <= self.at[0] < r and t <= self.at[1] < b and \
                        state["phase"] == mc.OPEN_PHASE.get(name):
                    state["counts"][name] += 1
                    self.presses.append(name)
            write()

    def make(**overrides):
        values = dict(automation_enabled=True, status_file=path, verify_timeout=0.5,
                      phase_wait=3.0)
        values.update(overrides)
        config = mc.MouseControllerConfig(**values).validate()
        mouse, log = Mouse(), Log()
        return mc.ActionController(config, mouse=mouse, log=log, synchronous=True), mouse, log

    def set_phase(phase):
        state["phase"] = phase
        write()

    return state, make, set_phase


def ante_required(action="ante", rule="If the player won the last round, play"):
    return {"decision": mc.ANTE_REQUIRED, "round_id": 5, "preround_action": action,
            "preround_rule": rule}


def test_the_ante_waits_for_betting_to_open_then_clicks(screen):
    state, make, set_phase = screen
    controller, mouse, log = make()
    timer = threading.Timer(0.4, lambda: set_phase(bs.BETTING))
    timer.start()
    started = time.time()
    controller.handle_scenario_result(ante_required(), action_round=1)
    assert state["counts"][mc.ANTE] == 1 and mouse.presses == [mc.ANTE]
    assert time.time() - started >= 0.35, "it waited for betting to open"


def test_no_click_when_betting_never_opens(screen):
    state, make, set_phase = screen
    controller, mouse, log = make(phase_wait=0.3)
    controller.handle_scenario_result(ante_required(), action_round=1)
    assert mouse.moves == [] and state["counts"][mc.ANTE] == 0
    assert "did not open" in log.lines[-1]


def test_no_click_while_the_session_is_expired(screen):
    state, make, set_phase = screen
    set_phase(bs.SESSION_EXPIRED)
    controller, mouse, log = make(phase_wait=0.3)
    controller.handle_scenario_result(ante_required(), action_round=1)
    assert mouse.moves == [] and "phase SESSION_EXPIRED" in log.lines[-1]


def test_betting_closes_while_the_mouse_moves(screen):
    state, make, set_phase = screen
    set_phase(bs.BETTING)
    controller, mouse, log = make()
    original = mouse.move

    def move_then_close(x, y, duration):
        original(x, y, duration)
        set_phase(bs.DEALING)

    mouse.move = move_then_close
    controller.handle_scenario_result(ante_required(), action_round=1)
    assert mouse.presses == [] and "closed (phase DEALING)" in log.lines[-1]


def test_play_is_only_clicked_in_the_decision_phase(screen):
    state, make, set_phase = screen
    set_phase(bs.BETTING)
    controller, mouse, log = make()
    controller.handle_scenario_result(ante_required(), action_round=1)
    set_phase(bs.DEALING)
    threading.Timer(0.3, lambda: set_phase(bs.DECISION)).start()
    controller.handle_scenario_result({"decision": "PLAY", "round_id": 6}, action_round=1)
    assert mouse.presses == [mc.ANTE, mc.PLAY_BUTTON]


def test_preround_skip_clicks_nothing_and_blocks_play(screen):
    state, make, set_phase = screen
    set_phase(bs.BETTING)
    controller, mouse, log = make()
    controller.handle_scenario_result(
        ante_required("skip", "If the dealer won the last round, skip this one"), action_round=1)
    assert mouse.moves == []
    assert "Pre-round rule says SKIP: If the dealer won the last round, skip this one" in log.lines[-1]
    set_phase(bs.DECISION)
    controller.handle_scenario_result({"decision": "PLAY", "round_id": 6}, action_round=1)
    assert mouse.moves == [] and state["counts"] == {mc.ANTE: 0, mc.BONUS: 0, mc.PLAY_BUTTON: 0}
    controller.close()
    assert "skipped=If the dealer won the last round, skip this one" in \
        [l for l in log.lines if l.startswith("[TEST HAND SUMMARY]")][-1]


def test_an_unknown_preround_action_clicks_nothing(screen):
    state, make, set_phase = screen
    set_phase(bs.BETTING)
    controller, mouse, log = make()
    controller.handle_scenario_result(ante_required("double"), action_round=1)
    assert mouse.moves == [] and "Invalid pre-round action" in log.lines[-1]


def test_bonus_is_never_clicked_by_the_controller(screen):
    state, make, set_phase = screen
    set_phase(bs.BETTING)
    controller, mouse, log = make()
    ok, reason = controller.target.validate(mc.BONUS, [240, 420])
    assert not ok and "not a TEST action button" in reason


# -- the tracker asks the pre-round rules ------------------------------------------------------

def test_tracker_sends_the_preround_rules_decision(monkeypatch):
    import tracker as tracker_module

    tr = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    tr.preround_scenarios = {"preround": {"default": scenario_rules.ANTE, "rules": [
        scenario_rules.preround_rule("Any", "Any", scenario_rules.SKIP,
                                     previous_winner=scenario_rules.DEALER,
                                     name="If the dealer won the last round, skip this one")]}}
    assert tr._preround_decision() == (scenario_rules.ANTE, "default rule")
    tr._remember_round({"hand_fingerprint": "a", "winner": scenario_rules.DEALER})
    tr._remember_round({"hand_fingerprint": "a", "winner": scenario_rules.DEALER})
    assert len(tr.recent_rounds) == 1
    assert tr._preround_decision() == (scenario_rules.SKIP,
                                       "If the dealer won the last round, skip this one")
    monkeypatch.setattr(scenario_rules, "decide_preround",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("broken")))
    assert tr._preround_decision()[0] == scenario_rules.SKIP, "unreadable rules click nothing"


# -- the window ---------------------------------------------------------------------------------

def test_the_window_publishes_phase_and_enables_buttons_only_when_open(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover
        pytest.skip("no display available: %s" % exc)
    config = mc.MouseControllerConfig(status_file=str(tmp_path / "s.json"),
                                      test_window_position=[40, 40]).validate()
    game = bs.GameEngine(seed=1, durations=dict(FAST))
    screen_ = bs.BettingScreen(root, config, game)
    try:
        root.update()
        screen_.write_status()
        status = json.load(open(config.status_file))
        assert status["title"] == mc.TEST_WINDOW_TITLE and status["phase"] == bs.NEXT_GAME
        assert set(status["buttons"]) == {mc.ANTE, mc.BONUS, mc.PLAY_BUTTON}
        assert str(screen_.buttons[mc.ANTE]["state"]) == "disabled"
        game.phase_started -= FAST[bs.NEXT_GAME]            # jump to betting
        game.tick(time.time())
        screen_.refresh()
        assert game.phase == bs.BETTING and str(screen_.buttons[mc.ANTE]["state"]) == "normal"
        screen_.buttons[mc.ANTE].invoke()
        screen_.write_status()
        status = json.load(open(config.status_file))
        assert status["counts"][mc.ANTE] == 1 and status["bets"][mc.ANTE] == 50
        assert str(screen_.buttons[mc.ANTE]["state"]) == "disabled"
    finally:
        screen_.close()
