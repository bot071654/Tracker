"""A result is final only when the casino's panel CONFIRMED it, and a finished hand is announced once (round_lifecycle.py).

THE FINDINGS THIS COVERS (from the review of the round-lifecycle fix)

1. The next-round ANTE / SKIP alert could fire twice for one finished hand: a stray single player-card read between rounds
   re-armed it, and the lifecycle still pointed at the same finished round. The pending / unknown notices could repeat the
   same way.
2. "Known" meant "the table's cards gave a winner and the one-shot panel look did not object", so a missing or late panel
   counted as confirmation, and the window could show a final winner next to SOURCE CONFLICT. Now a result calculated from
   the nine confirmed cards is `unconfirmed` until both panel rows are complete and agree with it, and nothing is final, and
   no ANTE / SKIP is suggested, before then.

Driven through the real Tracker._tick with scripted readings and scripted panel snapshots (no screen, no casino).
"""

import pytest

import round_lifecycle as rl
from poker import scenarios as scenario_rules
from ui import ante_alert
from ui.ante_alert import AnteAlertLogic

from test_round_lifecycle import (FLOP, FULL, NEXT_FLOP, NEXT_PLAYER, PLAYER, RIVER, TURN,   # noqa: F401 - fixtures too
                                  Session, finish_round, life, panel_snapshot, run, settle, shown)

BUILD_UP = {**PLAYER, **FLOP, **TURN, **RIVER}
OTHER = {**NEXT_PLAYER, **NEXT_FLOP, "turn": "QH", "river": "3C", "dealer_1": "AD", "dealer_2": "KC"}


def complete(run, mode, polls=4):
    run.panel_mode = mode
    settle(run, BUILD_UP)
    return settle(run, FULL, polls=polls)


# -- 2. which source a result came from ---------------------------------------------------------------------------------

def test_a_result_calculated_from_the_table_is_not_a_confirmed_result(run):
    payload = complete(run, "missing")
    lifecycle = life(payload)
    assert lifecycle["state"] == rl.COMPLETE
    assert lifecycle["table_result"] in ("Player", "Dealer", "Tie")          # calculated from the nine cards ...
    assert lifecycle["result_status"] == rl.RESULT_UNCONFIRMED              # ... and nothing more than that
    assert lifecycle["result"] is None and lifecycle["result_source"] == rl.SOURCE_TABLE
    assert shown(payload) == (rl.RESULT_PENDING_KEY, False)


def test_a_panel_that_agrees_confirms_the_result_and_makes_the_decision_final(run):
    payload = complete(run, "agree")
    lifecycle = life(payload)
    assert lifecycle["result_status"] == rl.RESULT_CONFIRMED and lifecycle["result_source"] == rl.SOURCE_PANEL
    assert lifecycle["result"] == lifecycle["table_result"]
    assert shown(payload) == (payload["scenario"]["decision"], True)
    assert "PANEL CONFIRMED" in rl.status_suffix(lifecycle)


@pytest.mark.parametrize("mode", ["missing", "partial"])
def test_a_missing_or_unreadable_panel_is_unconfirmed_not_confirmation(run, mode):
    payload = complete(run, mode, polls=8)
    assert life(payload)["result_status"] == rl.RESULT_UNCONFIRMED and life(payload)["result"] is None
    assert shown(payload) == (rl.RESULT_PENDING_KEY, False)
    assert "RESULT PENDING" in rl.status_suffix(life(payload))


def test_storing_or_checking_the_record_is_not_the_panel_confirming_it(run):
    """The old rule: any verdict from the one-shot look at store time counted. It no longer does."""
    run._verify_against_panels = lambda record: ("verified", "the one-shot look liked it")
    payload = complete(run, "missing", polls=8)
    assert run.backend.stored, "the hand is still stored"
    assert life(payload)["result_status"] == rl.RESULT_UNCONFIRMED


def test_a_stale_panel_row_from_an_earlier_round_does_not_confirm(run):
    run.panel_mode = "agree"
    original = run._observe_panels

    def stale(cards):
        snapshot = original(cards)
        for side in snapshot["sides"].values():
            side["stale"] = True
        snapshot["verdicts"] = {side: ("unavailable", "previous round") for side in snapshot["sides"]}
        return snapshot

    run._observe_panels = stale
    settle(run, BUILD_UP)
    payload = settle(run, FULL, polls=6)
    assert life(payload)["result_status"] == rl.RESULT_UNCONFIRMED


def test_a_panel_that_contradicts_the_table_is_a_conflict_with_no_winner(run):
    payload = complete(run, "disagree", polls=6)
    lifecycle = life(payload)
    assert lifecycle["result_status"] == rl.RESULT_CONFLICT
    assert lifecycle["result"] is None                                       # neither source is trusted
    assert shown(payload) == (rl.RESULT_UNKNOWN_KEY, False)
    assert "UNABLE TO DETERMINE RESULT" in rl.status_suffix(lifecycle)


def test_both_rows_must_be_complete_a_single_agreeing_row_is_not_enough(run):
    payload = complete(run, "partial", polls=6)                              # the player row is complete and consistent
    verdicts = payload["panels"]["verdicts"]
    assert verdicts["player"][0] == "consistent"
    assert life(payload)["result_status"] == rl.RESULT_UNCONFIRMED


def test_panel_evidence_needs_the_winner_the_panels_imply_to_match_the_table():
    snapshot = panel_snapshot("agree", FULL, 1)
    assert rl.panel_evidence(snapshot, snapshot["outcome"]["winner"]) == rl.PANEL_CONFIRMED
    other = "Player" if snapshot["outcome"]["winner"] != "Player" else "Dealer"
    assert rl.panel_evidence(snapshot, other) == rl.PANEL_CONFLICT
    assert rl.panel_evidence(snapshot, None) == rl.PANEL_UNCONFIRMED
    assert rl.panel_evidence(None, "Dealer") == rl.PANEL_UNCONFIRMED
    assert rl.panel_evidence({}, "Dealer") == rl.PANEL_UNCONFIRMED


# -- 2. a delayed panel resolves the status when reliable evidence arrives -------------------------------------------------

def test_a_delayed_panel_moves_pending_to_confirmed_and_the_decision_to_final(run):
    settle(run, BUILD_UP)
    seen = []
    for mode in ("missing", "missing", "partial", "agree", "agree"):
        run.panel_mode = mode
        payload = run.poll(FULL)
        seen.append((life(payload)["result_status"], shown(payload)))
    decision = payload["scenario"]["decision"]
    assert [status for status, _ in seen] == [rl.RESULT_UNCONFIRMED] * 3 + [rl.RESULT_CONFIRMED] * 2
    assert seen[0][1] == (rl.RESULT_PENDING_KEY, False) and seen[-1][1] == (decision, True)


def test_nothing_is_final_before_the_panel_arrives_even_after_many_polls(run):
    settle(run, BUILD_UP)
    run.panel_mode = "missing"
    for _ in range(30):
        payload = run.poll(FULL)
    assert life(payload)["result_status"] == rl.RESULT_UNCONFIRMED and shown(payload)[1] is False


def test_a_poll_with_no_look_at_the_panels_changes_nothing(run):
    settle(run, BUILD_UP)
    run.panel_mode = "agree"
    run.poll(FULL)
    run.poll(FULL)
    run.panel_mode = None                                                     # the panels were not looked at this poll
    assert life(run.poll(FULL))["result_status"] == rl.RESULT_CONFIRMED
    run.panel_mode = "missing"
    run.poll(FULL)
    run.panel_mode = None
    assert life(run.poll(FULL))["result_status"] == rl.RESULT_CONFIRMED       # latched: a vanished panel does not undo it


def test_a_confirmed_result_stays_confirmed_if_the_panel_disappears(run):
    complete(run, "agree")
    run.panel_mode = "missing"
    payload = settle(run, FULL, polls=5)
    assert life(payload)["result_status"] == rl.RESULT_CONFIRMED


def test_a_panel_that_later_contradicts_the_table_overrides_the_confirmation_and_can_clear(run):
    complete(run, "agree")
    run.panel_mode = "disagree"
    assert life(run.poll(FULL))["result_status"] == rl.RESULT_CONFLICT
    run.panel_mode = "agree"
    assert life(run.poll(FULL))["result_status"] == rl.RESULT_CONFIRMED


def test_a_new_round_never_inherits_the_last_rounds_confirmation(run):
    complete(run, "agree")
    settle(run, {}, polls=5)
    payload = settle(run, NEXT_PLAYER, polls=4)
    assert life(payload)["state"] == rl.IN_PROGRESS and life(payload)["result_status"] == rl.RESULT_NONE
    assert life(payload)["result"] is None


def test_the_finished_rounds_status_is_kept_when_the_table_clears(run):
    complete(run, "agree")
    previous = life(settle(run, {}, polls=5))["previous"]
    assert previous["result_status"] == rl.RESULT_CONFIRMED and previous["result_source"] == rl.SOURCE_PANEL
    run.panel_mode = "disagree"                                               # a panel of the next round proves nothing here
    assert life(settle(run, {}, polls=3))["previous"]["result_status"] == rl.RESULT_CONFIRMED


# -- 2. the next-round suggestion only follows a confirmed result ---------------------------------------------------------

def finish_and_clear(run, session, mode):
    complete(run, mode)
    session.feed(run.poll(FULL))
    for _ in range(6):
        session.feed(run.poll({}))


@pytest.mark.parametrize("mode", ["missing", "partial", "disagree"])
def test_no_ante_or_skip_while_the_result_is_unconfirmed_or_conflicting(run, mode):
    session = Session()
    finish_and_clear(run, session, mode)
    session.feed(run.poll({}), step=40.0)                                      # well past the notice's timeout
    for _ in range(4):
        session.feed(run.poll({}))
    assert scenario_rules.ANTE not in session.actions() and scenario_rules.SKIP not in session.actions()
    expected = [rl.RESULT_UNKNOWN_KEY] if mode == "disagree" else [rl.RESULT_PENDING_KEY, rl.RESULT_UNKNOWN_KEY]
    assert session.actions() == expected


def test_the_suggestion_follows_a_panel_confirmed_result(run):
    session = Session()
    finish_and_clear(run, session, "agree")
    assert session.actions() in ([scenario_rules.ANTE], [scenario_rules.SKIP])


# -- 1. one alert per finished hand ------------------------------------------------------------------------------------------

def test_the_review_sequence_cannot_alert_twice_for_the_same_hand():
    """Alert, banner times out, a stray one-card read, an empty poll: the second alert is the defect that was found."""
    logic = AnteAlertLogic(empty_polls=1, timeout=25.0)
    clock = [0.0]

    def step(state, seen, dt=0.2):
        clock[0] += dt
        return logic.observe(state, seen, lambda: (scenario_rules.SKIP, "rule"), now=clock[0],
                             ready=lambda: (ante_alert.READY, ""), key=("session", 7))

    step("FLOP", {"player_1": "9H", "player_2": "JH"})
    assert step("WAITING", {})[:2] == (ante_alert.SHOW, scenario_rules.SKIP)
    assert step("WAITING", {}, dt=30)[0] == ante_alert.HIDE
    assert step("WAITING", {"player_1": "2C"}) is None
    assert step("WAITING", {}) is None                                         # the same hand: no second alert
    for _ in range(5):
        assert step("WAITING", {}) is None
    assert logic.alerts == 1


def test_without_a_key_the_old_behaviour_is_unchanged():
    """No key (an update that carries no lifecycle): the alert is as it always was, so the key is what protects the hand."""
    logic = AnteAlertLogic(empty_polls=1, timeout=25.0)
    ready = lambda: (ante_alert.READY, "")                                        # noqa: E731
    decide = lambda: (scenario_rules.SKIP, "rule")                                # noqa: E731
    logic.observe("FLOP", {"player_1": "9H"}, decide, now=1.0, ready=ready)
    assert logic.observe("WAITING", {}, decide, now=2.0, ready=ready) is not None
    logic.observe("WAITING", {}, decide, now=40.0, ready=ready)
    logic.observe("WAITING", {"player_1": "2C"}, decide, now=41.0, ready=ready)
    assert logic.observe("WAITING", {}, decide, now=42.0, ready=ready) is not None


def test_the_same_sequence_through_the_real_tracker_alerts_once(run):
    session = Session()
    finish_and_clear(run, session, "agree")
    assert len(session.actions()) == 1
    session.feed(run.poll({}), step=30.0)                                      # the alert's banner has gone
    for noise in ({"player_1": "2C"}, {"player_2": "5D"}, {"player_1": "2C"}):
        session.feed(run.poll(noise))                                          # a stray single card, state still WAITING
        for _ in range(5):
            session.feed(run.poll({}))
    assert len(session.actions()) == 1, session.events


def test_half_of_the_next_deal_does_not_re_alert_the_finished_hand(run):
    session = Session()
    finish_and_clear(run, session, "agree")
    session.feed(run.poll({}), step=30.0)
    for _ in range(3):
        session.feed(run.poll({"player_1": "2D"}))                             # one hole card of the next deal ...
    for _ in range(6):
        session.feed(run.poll({}))                                             # ... then it goes again
    assert len(session.actions()) == 1


def test_repeated_pending_and_unknown_notices_are_said_once_per_round(run):
    session = Session()
    settle(run, {**PLAYER, **FLOP, **TURN})
    for _ in range(3):
        session.feed(run.poll({**PLAYER, **FLOP, **TURN}))
    for _ in range(6):
        session.feed(run.poll({}))
    assert session.actions() == [rl.RESULT_PENDING_KEY]
    session.feed(run.poll({}), step=30.0)
    assert session.actions() == [rl.RESULT_PENDING_KEY, rl.RESULT_UNKNOWN_KEY]
    for noise in range(4):
        session.feed(run.poll({"player_1": "2C"}))
        for _ in range(6):
            session.feed(run.poll({}), step=30.0)
    assert session.actions() == [rl.RESULT_PENDING_KEY, rl.RESULT_UNKNOWN_KEY]


def test_a_genuinely_new_completed_hand_with_its_own_result_alerts_again(run):
    session = Session()
    finish_and_clear(run, session, "agree")
    first = list(session.actions())
    assert len(first) == 1
    run.panel_mode = "agree"
    settle(run, {**NEXT_PLAYER, **NEXT_FLOP, "turn": "QH", "river": "3C"})
    done = settle(run, OTHER, polls=5)
    assert life(done)["result_status"] == rl.RESULT_CONFIRMED
    session.feed(done)
    for _ in range(6):
        session.feed(run.poll({}))
    assert len(session.actions()) == 2


def test_a_second_hand_whose_result_is_unconfirmed_does_not_borrow_the_first_ones(run):
    session = Session()
    finish_and_clear(run, session, "agree")
    assert len(session.actions()) == 1
    run.panel_mode = "missing"
    settle(run, {**NEXT_PLAYER, **NEXT_FLOP, "turn": "QH", "river": "3C"})
    session.feed(settle(run, OTHER, polls=5))
    for _ in range(6):
        session.feed(run.poll({}))
    assert session.actions()[1:] == [rl.RESULT_PENDING_KEY]                    # its own notice, not the first hand's alert


def test_once_a_round_is_declared_unknown_the_alert_never_follows_for_it():
    logic = AnteAlertLogic(empty_polls=1, timeout=25.0)
    key = ("session", 3)
    logic.observe("FLOP", {"player_1": "9H"}, lambda: None, now=1.0, ready=lambda: (ante_alert.READY, ""), key=key)
    first = logic.observe("WAITING", {}, lambda: None, now=2.0, ready=lambda: (ante_alert.UNKNOWN, "disagree"), key=key)
    assert first[:2] == (ante_alert.SHOW, rl.RESULT_UNKNOWN_KEY)
    logic.observe("WAITING", {"player_1": "2C"}, lambda: None, now=3.0, key=key)
    assert logic.observe("WAITING", {}, lambda: (scenario_rules.ANTE, "rule"), now=4.0,
                         ready=lambda: (ante_alert.READY, ""), key=key) is None


def test_pending_may_still_be_followed_by_the_alert_for_the_same_round_once_confirmed():
    logic = AnteAlertLogic(empty_polls=1, timeout=25.0)
    key = ("session", 4)
    logic.observe("FLOP", {"player_1": "9H"}, lambda: None, now=1.0, ready=lambda: (ante_alert.READY, ""), key=key)
    answers = iter([(ante_alert.PENDING, "wait"), (ante_alert.PENDING, "wait"), (ante_alert.READY, "")])
    events = [logic.observe("WAITING", {}, lambda: (scenario_rules.ANTE, "rule"), now=2.0 + step, ready=lambda: next(answers),
                            key=key) for step in range(3)]
    assert events[0][:2] == (ante_alert.SHOW, rl.RESULT_PENDING_KEY) and events[1] is None
    assert events[2][:2] == (ante_alert.SHOW, scenario_rules.ANTE)
    assert logic.observe("WAITING", {}, lambda: None, now=9.0, ready=lambda: (ante_alert.READY, ""), key=key) is None


def test_a_new_session_with_the_same_round_number_has_its_own_alert():
    logic = AnteAlertLogic(empty_polls=1, timeout=25.0)
    ready = lambda: (ante_alert.READY, "")                                        # noqa: E731
    for session in (1, 2):
        for tick in range(2):                                                     # (the first poll hides session 1's banner)
            logic.observe("FLOP", {"player_1": "9H"}, lambda: None, now=session * 100.0 + tick / 10, ready=ready, key=(session, 5))
        event = logic.observe("WAITING", {}, lambda: (scenario_rules.ANTE, "rule"), now=session * 100.0 + 1, ready=ready,
                              key=(session, 5))
        assert event is not None and event[0] == ante_alert.SHOW


def test_the_window_builds_the_alert_key_from_the_session_and_the_finished_round():
    from types import SimpleNamespace

    from app import App
    window = SimpleNamespace(teach_session=4)
    assert App._preround_key(window, {}) is None                               # no lifecycle: legacy behaviour
    assert App._preround_key(window, {"lifecycle": {"previous": {"round_id": 9}}}) == (4, 9)
    assert App._preround_key(window, {"lifecycle": {"previous": None}}) == (4, None)


# -- a confirmed result is not undone by the NEXT deal's partial panel row ---------------------------------------------------

def test_a_confirmed_result_survives_the_panel_showing_the_next_deals_hole_cards(run):
    """Live: "player panel shows 3C 4D, but the table has only 0 unread place(s)" - the next hand, not a wrong result."""
    complete(run, "agree")
    run.panel_mode = "next_deal"
    payload = settle(run, FULL, polls=4)
    assert payload["panels"]["verdicts"]["player"][0] == "conflict"             # the tracker does see it as a conflict ...
    lifecycle = life(payload)
    assert lifecycle["result_status"] == rl.RESULT_CONFIRMED and lifecycle["result"] == lifecycle["table_result"]
    assert lifecycle["panel_conflict_ignored"] is True                          # ... and the lifecycle says it ignored it
    assert shown(payload) == (payload["scenario"]["decision"], True)            # still final


def test_an_unconfirmed_result_is_not_called_a_conflict_by_a_partial_row(run):
    run.panel_mode = "next_deal"
    settle(run, BUILD_UP)
    payload = settle(run, FULL, polls=4)
    assert life(payload)["result_status"] == rl.RESULT_UNCONFIRMED              # pending, not unknown
    assert shown(payload) == (rl.RESULT_PENDING_KEY, False)


def test_a_complete_contradicting_row_still_overrides_a_confirmed_result(run):
    complete(run, "agree")
    run.panel_mode = "disagree"
    payload = run.poll(FULL)
    assert life(payload)["result_status"] == rl.RESULT_CONFLICT and life(payload)["panel_conflict_ignored"] is False
    assert shown(payload) == (rl.RESULT_UNKNOWN_KEY, False)


def test_the_partial_row_conflict_is_only_ignored_never_counted_as_confirmation(run):
    payload = complete(run, "next_deal")
    assert life(payload)["result_status"] == rl.RESULT_UNCONFIRMED and life(payload)["result"] is None
    assert rl.ignored_conflict(payload["panels"]) is True
    assert rl.ignored_conflict(panel_snapshot("agree", FULL, 1)) is False
    assert rl.ignored_conflict(panel_snapshot("disagree", FULL, 1)) is False   # a complete row's conflict is counted, not ignored


def test_the_alert_still_fires_once_after_a_confirmed_round_whose_panel_moved_on(run):
    session = Session()
    complete(run, "agree")
    run.panel_mode = "next_deal"
    for _ in range(3):
        session.feed(run.poll(FULL))
    run.panel_mode = "missing"
    for _ in range(6):
        session.feed(run.poll({}))
    assert len(session.actions()) == 1 and session.actions()[0] in (scenario_rules.ANTE, scenario_rules.SKIP)
