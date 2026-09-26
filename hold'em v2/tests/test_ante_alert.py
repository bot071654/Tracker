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


def test_cold_start_never_alerts_no_matter_how_long_the_table_stays_empty():
    """The casino lobby, a table still loading, and the tracker's own cold
    start all look exactly like an empty table too - none of them is proof a
    hand just ended, so none of them may guess ANTE or SKIP.
    """
    logic = AnteAlertLogic(empty_polls=3)
    assert feed(logic, 200, EMPTY) == []
    assert logic.alerts == 0


def test_the_first_alert_of_a_session_needs_a_real_hand_first():
    logic = AnteAlertLogic(empty_polls=3)
    assert feed(logic, 40, EMPTY) == []                        # cold start: nothing
    assert feed(logic, 10, DEALT, state="PLAYER_CARDS", start=8.0) == []
    assert feed(logic, 5, EMPTY, start=10.0) == [
        (SHOW, scenario_rules.ANTE, "rule says ante")]


def test_started_mid_hand_waits_for_the_table_to_clear():
    logic = AnteAlertLogic(empty_polls=3)
    assert feed(logic, 10, DEALT, state="PLAYER_CARDS") == []
    assert feed(logic, 5, EMPTY, start=2.0) == [(SHOW, scenario_rules.ANTE, "rule says ante")]


def test_hides_when_the_next_deal_appears_and_alerts_again_next_round():
    logic = AnteAlertLogic(empty_polls=3)
    feed(logic, 10, DEALT, state="PLAYER_CARDS")               # a real hand, first
    feed(logic, 5, EMPTY, start=2.0)                           # that hand ends: first alert
    assert feed(logic, 3, DEALT, state="PLAYER_CARDS", start=5.0) == [
        (HIDE, "cards dealt - betting closed")]
    assert feed(logic, 20, EMPTY, start=10.0) == [(SHOW, scenario_rules.ANTE, "rule says ante")]
    assert logic.alerts == 2


def test_a_hand_passing_over_the_table_mid_betting_does_not_alert_twice():
    logic = AnteAlertLogic(empty_polls=3)
    feed(logic, 10, DEALT, state="PLAYER_CARDS")
    feed(logic, 5, EMPTY, start=2.0)                           # first alert
    # a covered / flickering poll with nothing readable is still an empty table
    assert feed(logic, 30, EMPTY, start=3.0) == []
    assert logic.alerts == 1


def test_times_out():
    logic = AnteAlertLogic(empty_polls=3, timeout=5.0)
    feed(logic, 10, DEALT, state="PLAYER_CARDS", start=-2.0)
    feed(logic, 3, EMPTY)
    assert feed(logic, 1, EMPTY, start=6.0) == [(HIDE, "timed out")]


def test_dismiss_and_reset():
    logic = AnteAlertLogic(empty_polls=1)
    feed(logic, 1, DEALT, state="PLAYER_CARDS", start=-1.0)
    feed(logic, 1, EMPTY)
    assert logic.dismiss() == (HIDE, "dismissed") and logic.showing is None
    assert logic.dismiss() is None
    feed(logic, 1, DEALT, state="PLAYER_CARDS")
    feed(logic, 1, EMPTY, start=1.0)
    assert logic.reset() == (HIDE, "tracker stopped")


def test_skip_is_shown_too_and_unknown_actions_are_not():
    logic = AnteAlertLogic(empty_polls=1)
    feed(logic, 1, DEALT, state="PLAYER_CARDS", start=-1.0)
    assert feed(logic, 1, EMPTY, decide=lambda: (scenario_rules.SKIP, "dealer won")) == [
        (SHOW, scenario_rules.SKIP, "dealer won")]
    other = AnteAlertLogic(empty_polls=1)
    feed(other, 1, DEALT, state="PLAYER_CARDS", start=-1.0)
    assert feed(other, 1, EMPTY, decide=lambda: ("bet_everything", "?")) == []


def test_the_rules_are_only_asked_when_an_alert_is_due():
    calls = []
    logic = AnteAlertLogic(empty_polls=3)
    feed(logic, 10, DEALT, state="PLAYER_CARDS", start=-4.0)
    feed(logic, 30, EMPTY, decide=lambda: calls.append(1) or ante())
    assert len(calls) == 1


# -- pre-round decision latency ------------------------------------------------
#
# A real session (logs/tracker.log, 2026-09-26) measured 0.47-1.78s between
# the tracker's own state reaching WAITING (the player's seat already
# confirmed empty - CardMemory's own clear_frames debounce) and the ANTE
# alert firing. Both contributing causes are covered below: the alert's own
# empty_polls re-debounce on top of an already-settled state, and requiring
# every one of the nine slots - not just the player's - to read empty.

STILL_REVEALING = {"player_1": None, "player_2": None,
                   "dealer_1": "2D", "dealer_2": "QD", "river": "5D"}


def test_a_lingering_dealer_or_board_reveal_does_not_delay_the_alert():
    """The dealer's cards/board can stay correctly, confidently read on
    screen for a moment after the player's seat is empty - a normal casino
    reveal, not a misread. Before this fix, table_empty checked all nine
    slots and this held the alert back for as long as the reveal lasted.
    """
    logic = AnteAlertLogic(empty_polls=1)
    feed(logic, 1, DEALT, state="PLAYER_CARDS", start=-1.0)    # a real hand, first
    assert feed(logic, 1, STILL_REVEALING) == [
        (SHOW, scenario_rules.ANTE, "rule says ante")]


def test_the_alert_no_longer_waits_several_polls_once_the_seat_is_confirmed_empty():
    """state WAITING is already the debounced signal (CardMemory's
    clear_frames). Re-confirming it for several more polls here was pure
    latency: this is the exact behaviour change, measured in simulated time.
    """
    old_behaviour = AnteAlertLogic(empty_polls=3)          # the previous default
    new_behaviour = AnteAlertLogic(empty_polls=1)           # the current default
    poll_interval = 0.2                                     # config poll_interval_seconds

    # A real hand first, for both - cold start is covered separately above.
    old_behaviour.observe("PLAYER_CARDS", DEALT, ante, now=-poll_interval)
    new_behaviour.observe("PLAYER_CARDS", DEALT, ante, now=-poll_interval)

    old_events, new_events = [], []
    for index in range(5):
        now = index * poll_interval
        old_events.append((now, old_behaviour.observe("WAITING", EMPTY, ante, now=now)))
        new_events.append((now, new_behaviour.observe("WAITING", EMPTY, ante, now=now)))

    old_fired_at = next(t for t, e in old_events if e)
    new_fired_at = next(t for t, e in new_events if e)
    assert new_fired_at == 0.0                              # fires on the first poll
    assert old_fired_at == pytest.approx(0.4)                # the old, measured latency
    assert old_fired_at - new_fired_at == pytest.approx(0.4)  # 400ms recovered, this scenario


def test_default_construction_still_needs_a_real_hand_before_it_can_alert():
    """AnteAlertLogic() with no arguments is the class's own recommended
    default (empty_polls=1) - so a future caller cannot regress the latency
    fix by forgetting to pass empty_polls. Cold start is still guarded even
    at that default: it still needs a real hand seen first.
    """
    logic = AnteAlertLogic()
    assert feed(logic, 40, EMPTY) == []                        # cold start: nothing
    feed(logic, 1, DEALT, state="PLAYER_CARDS", start=8.0)
    assert feed(logic, 1, EMPTY, start=8.2) == [
        (SHOW, scenario_rules.ANTE, "rule says ante")]


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
    # The banner shows nothing until it is told the tracker is running; the
    # window does that in App.start().
    banner.start()
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
    # The banner now shows one decision at a time under one set of labels
    # (ui/decision_banner.DECISION_LABELS), and skip's is "SKIP ROUND".
    assert banner.title_var.get() == "SKIP ROUND"
    banner.hide()
    banner.destroy()


def test_app_shows_the_alert_from_the_users_rules(root, monkeypatch):
    import app as app_module

    # show() now also takes the round the decision belongs to, so that a
    # decision cannot outlive its round on screen.
    monkeypatch.setattr(
        app_module.AnteAlertBanner, "show",
        lambda self, action, reason="", round_id=None: setattr(
            self, "shown", (action, reason)))
    application = app_module.App(root)
    # A banner may only be shown while a tracking session is open: that is the
    # whole of the lifecycle fix, and showing one otherwise is the bug it
    # exists to prevent. start() itself is not called here because it would
    # build a real Tracker and ask the database; this flag is what
    # _publish_decision consults.
    application.tracking = True
    application.ante_banner.shown = None
    application.last_record = {"winner": scenario_rules.DEALER}
    application.history = [application.last_record]
    application.scenarios = {"preround": {"default": scenario_rules.ANTE, "rules": [
        scenario_rules.preround_rule("Any", "Any", scenario_rules.SKIP,
                                     previous_winner=scenario_rules.DEALER)]}}
    # A real hand first: an empty table alone (cold start) is no longer
    # enough to alert - see test_cold_start_never_alerts_no_matter_how_long...
    application._update_ante_alert({"state": "PLAYER_CARDS", "seen": dict(DEALT)})
    payload = {"state": "WAITING", "seen": dict(EMPTY)}
    for _ in range(3):
        application._update_ante_alert(payload)
    assert application.ante_banner.shown[0] == scenario_rules.SKIP
    # One banner: the pre-round alert and the Scenario Engine share it, and
    # the app exposes it under both names.
    assert application.decision_banner is application.ante_banner
    application.on_close()
