"""The ANTE alert: when it appears, what it says, and that it never touches the game."""

import inspect

import pytest

from poker import scenarios as scenario_rules
from ui import ante_alert
from ui.ante_alert import HIDE, SHOW, AnteAlertLogic, preround_decision

EMPTY = {"player_1": None, "player_2": None, "flop_1": None}
DEALT = {"player_1": "7S", "player_2": "7D", "flop_1": None}


def ante():
    return scenario_rules.ANTE, "rule says ante"


def feed(logic, polls, seen, state="WAITING", start=0.0, step=0.2, decide=ante):
    events = []
    for index in range(polls):
        event = logic.observe(state, seen, decide, now=start + index * step)
        if event:
            events.append(event)
    return events


def test_first_empty_table_after_start_alerts_once():
    logic = AnteAlertLogic(empty_polls=3)
    events = feed(logic, 40, EMPTY)
    assert events == [(SHOW, scenario_rules.ANTE, "rule says ante")]


def test_started_mid_hand_waits_for_the_table_to_clear():
    logic = AnteAlertLogic(empty_polls=3)
    assert feed(logic, 10, DEALT, state="PLAYER_CARDS") == []
    assert feed(logic, 5, EMPTY, start=2.0) == [(SHOW, scenario_rules.ANTE, "rule says ante")]


def test_hides_when_the_next_deal_appears_and_alerts_again_next_round():
    logic = AnteAlertLogic(empty_polls=3)
    feed(logic, 5, EMPTY)
    assert feed(logic, 3, DEALT, state="PLAYER_CARDS", start=5.0) == [
        (HIDE, "cards dealt - betting closed")]
    assert feed(logic, 20, EMPTY, start=10.0) == [(SHOW, scenario_rules.ANTE, "rule says ante")]
    assert logic.alerts == 2


def test_a_hand_passing_over_the_table_mid_betting_does_not_alert_twice():
    logic = AnteAlertLogic(empty_polls=3)
    feed(logic, 5, EMPTY)
    # a covered / flickering poll with nothing readable is still an empty table
    assert feed(logic, 30, EMPTY, start=1.0) == []
    assert logic.alerts == 1


def test_times_out():
    logic = AnteAlertLogic(empty_polls=3, timeout=5.0)
    feed(logic, 3, EMPTY)
    assert feed(logic, 1, EMPTY, start=6.0) == [(HIDE, "timed out")]


def test_dismiss_and_reset():
    logic = AnteAlertLogic(empty_polls=1)
    feed(logic, 1, EMPTY)
    assert logic.dismiss() == (HIDE, "dismissed") and logic.showing is None
    assert logic.dismiss() is None
    feed(logic, 1, DEALT, state="PLAYER_CARDS")
    feed(logic, 1, EMPTY, start=1.0)
    assert logic.reset() == (HIDE, "tracker stopped")


def test_skip_is_shown_too_and_unknown_actions_are_not():
    logic = AnteAlertLogic(empty_polls=1)
    assert feed(logic, 1, EMPTY, decide=lambda: (scenario_rules.SKIP, "dealer won")) == [
        (SHOW, scenario_rules.SKIP, "dealer won")]
    other = AnteAlertLogic(empty_polls=1)
    assert feed(other, 1, EMPTY, decide=lambda: ("bet_everything", "?")) == []


def test_the_rules_are_only_asked_when_an_alert_is_due():
    calls = []
    logic = AnteAlertLogic(empty_polls=3)
    feed(logic, 30, EMPTY, decide=lambda: calls.append(1) or ante())
    assert len(calls) == 1


def test_preround_decision_uses_the_users_rules():
    scenarios = {"preround": {"default": scenario_rules.ANTE, "rules": [
        scenario_rules.preround_rule("Any", "Any", scenario_rules.SKIP,
                                     previous_winner=scenario_rules.DEALER,
                                     name="If the dealer won the last round, skip this one")]}}
    dealer_won = {"winner": scenario_rules.DEALER}
    assert preround_decision(scenarios, dealer_won, [dealer_won]) == (
        scenario_rules.SKIP, "If the dealer won the last round, skip this one")
    player_won = {"winner": scenario_rules.PLAYER}
    assert preround_decision(scenarios, player_won, [player_won]) == (
        scenario_rules.ANTE, "default rule")
    assert preround_decision(scenarios, None, []) == (
        scenario_rules.ANTE, "default rule - no finished round recorded yet")


def test_the_alert_never_touches_the_game():
    source = inspect.getsource(ante_alert).lower()
    for forbidden in ("pyautogui", "mouse_controller", "automation", "sendinput",
                      "mouse_event", "keybd_event", "pynput"):
        assert forbidden not in source


# -- the banner and the app ------------------------------------------------------------

@pytest.fixture
def root():
    tk = pytest.importorskip("tkinter")
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


def test_banner_shows_hides_and_dismisses_without_taking_focus(root):
    banner = ante_alert.AnteAlertBanner(root, sound=False)
    dismissed = []
    banner.on_dismiss = lambda: dismissed.append(True)
    banner.show(scenario_rules.ANTE, "If the player won the last round, play")
    root.update()
    assert banner.visible and banner.title_var.get() == "ANTE NOW"
    assert bool(banner.window.attributes("-topmost")) is True
    assert root.grab_current() is None
    banner._clicked()
    root.update()
    assert not banner.visible and dismissed == [True]
    banner.show(scenario_rules.SKIP, "dealer won")
    assert banner.title_var.get() == "SKIP THIS ROUND"
    banner.hide()
    banner.destroy()


def test_app_shows_the_alert_from_the_users_rules(root, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module.AnteAlertBanner, "show",
                        lambda self, action, reason: setattr(self, "shown", (action, reason)))
    application = app_module.App(root)
    application.ante_banner.shown = None
    application.last_record = {"winner": scenario_rules.DEALER}
    application.history = [application.last_record]
    application.scenarios = {"preround": {"default": scenario_rules.ANTE, "rules": [
        scenario_rules.preround_rule("Any", "Any", scenario_rules.SKIP,
                                     previous_winner=scenario_rules.DEALER)]}}
    payload = {"state": "WAITING", "seen": dict(EMPTY)}
    for _ in range(3):
        application._update_ante_alert(payload)
    assert application.ante_banner.shown[0] == scenario_rules.SKIP
    application.on_close()
