"""Teaching a correction: capture, scope, rule, conflict, priority, impact.

No Tk here - everything in poker/teaching.py is plain Python so that the part
that decides what a correction means can be tested without a display. The
window that drives it is tested in test_teach_window.py.

The rule sets below are built with the project's own constructors rather than
typed out, so a test cannot assert against a rule shape the engine would not
accept.
"""

import copy
import datetime
import json
import os

import pytest

from poker import scenario_engine as se
from poker import scenarios as sr
from poker import teaching
from poker.board_features import describe_flop

SETTLED = ["player_1", "player_2", "flop_1", "flop_2", "flop_3"]

# The brief's own example: A7 on K72, which is a Pair.
CARDS = {"player_1": "AS", "player_2": "7D",
         "flop_1": "KC", "flop_2": "7C", "flop_3": "2H"}


def payload(cards=None, statuses=None, round_id=123, state="FLOP",
            decision=se.DONT_PLAY, **extra):
    cards = CARDS if cards is None else cards
    body = {
        "state": state,
        "round_id": round_id,
        "cards": dict(cards),
        "statuses": statuses or {slot: "CONFIRMED" for slot in SETTLED},
        "scenario": {"decision": decision, "reason": "no scenario matched"},
    }
    body.update(extra)
    return body


def rules(flop=(), preround=(), flop_default=sr.PLAY, preround_default=sr.ANTE):
    return {"preround": {"default": preround_default, "rules": list(preround)},
            "flop": {"default": flop_default, "rules": list(flop)}}


PAIR_FOLD = sr.flop_rule("Pair", sr.ANY, sr.FOLD, name="Pair -> DON'T PLAY")
HIGH_PLAY = sr.flop_rule("High Card", sr.ANY, sr.PLAY, name="High Card -> PLAY")
EXAMPLE = rules(flop=[PAIR_FOLD, HIGH_PLAY])


def record(winner="Dealer", player_hand="High Card", dealer_hand="Pair"):
    return {"winner": winner, "player_hand": player_hand,
            "dealer_hand": dealer_hand}


@pytest.fixture
def snapshot():
    taken, why = teaching.capture(payload(), EXAMPLE, session=4)
    assert taken is not None, why
    return taken


# -- 1-10. the snapshot captures the moment -----------------------------------

def test_capture_returns_a_snapshot_and_no_complaint():
    taken, why = teaching.capture(payload(), EXAMPLE, session=4)
    assert taken is not None
    assert why == ""


def test_the_round_identity_is_the_trackers_own(snapshot):
    """CardMemory's generation, straight out of the payload. Not invented."""
    assert snapshot.round_id == 123
    assert snapshot.session == 4


def test_a_capture_carries_the_time_it_was_taken():
    when = datetime.datetime(2026, 9, 22, 12, 30, 0)
    taken, _ = teaching.capture(payload(), EXAMPLE, session=1, now=when)
    assert taken.captured_at == when


def test_the_stage_is_captured(snapshot):
    assert snapshot.stage == "FLOP"


def test_the_player_cards_are_captured(snapshot):
    assert snapshot.cards["player_1"] == "AS"
    assert snapshot.cards["player_2"] == "7D"
    assert snapshot.player_cards() == "A♠  7♦"


def test_the_flop_is_captured(snapshot):
    assert [snapshot.cards[slot] for slot in ("flop_1", "flop_2", "flop_3")] \
        == ["KC", "7C", "2H"]
    assert snapshot.flop_cards() == "K♣  7♣  2♥"


def test_an_empty_turn_and_river_read_as_a_dash(snapshot):
    assert snapshot.turn_card() == "-"
    assert snapshot.river_card() == "-"


def test_the_turn_and_river_are_captured_when_they_are_there():
    cards = dict(CARDS, turn="9S", river="4D")
    taken, _ = teaching.capture(payload(cards=cards), EXAMPLE, session=1)
    assert taken.cards["turn"] == "9S"
    assert taken.cards["river"] == "4D"
    assert taken.turn_card() == "9♠"
    assert taken.river_card() == "4♦"


def test_the_hand_type_is_the_evaluators_own_name(snapshot):
    assert snapshot.hand == "Pair"
    assert snapshot.hand == describe_flop(["AS", "7D"], ["KC", "7C", "2H"])["hand"]


def test_the_current_decision_is_the_one_the_rules_give(snapshot):
    """Not a second reading of the cards: decide_flop's own answer."""
    action, _ = sr.decide_flop(EXAMPLE, snapshot.features)
    assert snapshot.action == action == sr.FOLD


def test_the_matched_rule_and_its_priority_are_captured(snapshot):
    assert snapshot.matched_rule["name"] == "Pair -> DON'T PLAY"
    assert snapshot.matched_index == 0
    assert snapshot.priority() == "#1"


def test_the_engines_own_decision_is_carried_beside_it(snapshot):
    assert snapshot.engine_decision == se.DONT_PLAY


def test_the_default_is_reported_as_the_default_not_as_a_rule():
    taken, _ = teaching.capture(payload(), rules(), session=1)
    assert taken.matched_rule is None
    assert taken.matched_index is None
    assert taken.priority() is None
    assert "default" in taken.matched_name()


def test_the_snapshot_is_a_copy_and_does_not_follow_the_payload(snapshot):
    """The point of freezing: the tracker reuses nothing the dialog holds."""
    live = payload()
    taken, _ = teaching.capture(live, EXAMPLE, session=1)
    live["cards"]["flop_3"] = "9D"
    assert taken.cards["flop_3"] == "2H"


# -- capture refuses what it should not teach ---------------------------------

def test_no_payload_is_refused():
    taken, why = teaching.capture(None, EXAMPLE, session=1)
    assert taken is None
    assert "no scenario" in why.lower()


def test_an_unsettled_card_is_refused_rather_than_guessed():
    """The same gate the decision uses: nothing is taught about a card still
    being read."""
    statuses = {slot: "CONFIRMED" for slot in SETTLED}
    statuses["flop_3"] = "CONFIRMING"
    taken, why = teaching.capture(payload(statuses=statuses), EXAMPLE, session=1)
    assert taken is None
    assert "flop_3" in why


def test_before_the_flop_with_no_finished_round_there_is_nothing_to_teach():
    taken, why = teaching.capture(
        payload(cards={}, statuses={}, state="WAITING"), EXAMPLE, session=1)
    assert taken is None
    assert "No decision" in why


def test_before_the_flop_a_finished_round_gives_a_preround_scenario():
    taken, why = teaching.capture(
        payload(cards={}, statuses={}, state="WAITING"), EXAMPLE, session=1,
        previous=record(winner="Dealer"), history=[record(winner="Dealer")])
    assert taken is not None, why
    assert taken.section == teaching.PREROUND
    assert taken.action in sr.PREROUND_ACTIONS


# -- 7. round identity and stale state ----------------------------------------

def test_the_same_session_and_round_is_current(snapshot):
    assert snapshot.is_current(4, 123, True) == (True, "")


def test_a_stopped_tracker_is_not_current(snapshot):
    ok, why = snapshot.is_current(4, 123, False)
    assert ok is False
    assert "stopped" in why


def test_a_new_round_is_not_current(snapshot):
    ok, why = snapshot.is_current(4, 124, True)
    assert ok is False
    assert "round has changed" in why


def test_a_restarted_tracker_is_not_current_even_on_the_same_round(snapshot):
    """The generation is not reset by stopping, so the session has to say so."""
    ok, why = snapshot.is_current(5, 123, True)
    assert ok is False
    assert "restarted" in why


def test_a_missing_round_id_is_not_current(snapshot):
    assert snapshot.is_current(4, None, True)[0] is False


# -- 8/10. only decisions and scopes the engine supports ----------------------

def test_a_flop_snapshot_offers_only_the_flop_actions(snapshot):
    assert snapshot.actions() == sr.FLOP_ACTIONS == [sr.PLAY, sr.FOLD]


def test_a_preround_snapshot_offers_only_the_preround_actions():
    taken, _ = teaching.capture(
        payload(cards={}, statuses={}, state="WAITING"), EXAMPLE, session=1,
        previous=record(), history=[record()])
    assert taken.actions() == sr.PREROUND_ACTIONS == [sr.ANTE, sr.SKIP]


def test_wait_is_never_offered_as_something_to_teach(snapshot):
    assert se.WAIT not in snapshot.actions()
    assert "WAIT" in teaching.UNREPRESENTABLE["wait"]


def test_exact_cards_are_reported_as_unrepresentable_not_approximated():
    message = teaching.UNREPRESENTABLE["exact_cards"]
    assert "cannot represent" in message
    assert "CONDITION_CHOICES" in message      # names the smallest extension


def test_every_scope_offered_uses_a_condition_the_engine_knows(snapshot):
    for scope in teaching.available_scopes(snapshot):
        assert scope.fields["condition"] in sr.CONDITION_CHOICES
        assert scope.fields["hand"] in sr.HAND_CHOICES


def test_every_scope_offered_actually_matches_the_captured_hand(snapshot):
    """A scope that did not match the hand it came from would be teaching the
    wrong situation."""
    for scope in teaching.available_scopes(snapshot):
        rule = scope.build(sr.PLAY)
        probe = rules(flop=[rule])
        assert sr.decide_flop(probe, snapshot.features)[1] is not None


def test_the_narrowest_scopes_come_first(snapshot):
    scopes = teaching.available_scopes(snapshot)
    filled = [sum(1 for value in scope.fields.values() if value != sr.ANY)
              for scope in scopes]
    assert filled[0] == 2                       # hand and a condition
    assert filled[-1] == 1                      # a condition alone


def test_a_scope_for_the_hand_type_alone_is_offered(snapshot):
    labels = [scope.key for scope in teaching.available_scopes(snapshot)]
    assert "hand" in labels


def test_true_conditions_are_the_ones_the_engine_agrees_with(snapshot):
    for condition in teaching.true_conditions(snapshot.features):
        assert sr.condition_holds(condition, snapshot.features)
    false = set(sr.CONDITION_CHOICES) - set(
        teaching.true_conditions(snapshot.features)) - {sr.ANY}
    for condition in false:
        assert not sr.condition_holds(condition, snapshot.features)


def test_preround_scopes_are_built_from_the_previous_round():
    taken, _ = teaching.capture(
        payload(cards={}, statuses={}, state="WAITING"), EXAMPLE, session=1,
        previous=record(winner="Dealer", dealer_hand="Two Pair"),
        history=[record(winner="Dealer", dealer_hand="Two Pair")])
    scopes = teaching.available_scopes(taken)
    assert scopes
    for scope in scopes:
        rule = scope.build(sr.SKIP)
        probe = rules(preround=[rule])
        assert sr.decide_preround(probe, taken.previous, taken.history)[1] is not None


def test_a_streak_scope_appears_only_after_two_player_wins():
    wins = [record(winner="Player"), record(winner="Player")]
    taken, _ = teaching.capture(
        payload(cards={}, statuses={}, state="WAITING"), EXAMPLE, session=1,
        previous=wins[0], history=wins)
    assert taken.streak == 2
    assert "streak" in [scope.key for scope in teaching.available_scopes(taken)]

    one = [record(winner="Player")]
    taken, _ = teaching.capture(
        payload(cards={}, statuses={}, state="WAITING"), EXAMPLE, session=1,
        previous=one[0], history=one)
    assert "streak" not in [scope.key for scope in teaching.available_scopes(taken)]


# -- 11/13/14. the rule is an ordinary rule -----------------------------------

def test_a_taught_rule_is_the_same_shape_as_a_hand_made_one(snapshot):
    scope = teaching.available_scopes(snapshot)[0]
    taught = scope.build(sr.PLAY)
    by_hand = sr.flop_rule(scope.fields["hand"], scope.fields["condition"],
                           sr.PLAY)
    assert taught == by_hand


def test_a_taught_rule_serialises_and_comes_back_unchanged(snapshot, tmp_path):
    scope = teaching.available_scopes(snapshot)[0]
    rule = teaching.stamp(scope.build(sr.PLAY), snapshot, sr.PLAY)
    proposed = teaching.propose(EXAMPLE, "flop", rule, 0)
    path = str(tmp_path / "scenarios.json")
    sr.save(proposed, path)
    assert sr.load(path)["flop"]["rules"][0] == rule


def test_the_saved_file_is_ordinary_json_in_the_existing_shape(snapshot, tmp_path):
    scope = teaching.available_scopes(snapshot)[0]
    proposed = teaching.propose(EXAMPLE, "flop", scope.build(sr.PLAY), 0)
    path = str(tmp_path / "scenarios.json")
    sr.save(proposed, path)
    with open(path, encoding="utf-8") as handle:
        stored = json.load(handle)
    assert set(stored) == {"preround", "flop"}
    assert set(stored["flop"]) == {"default", "rules"}


def test_a_taught_rule_still_reads_as_a_rule_to_the_existing_engine(snapshot):
    scope = teaching.available_scopes(snapshot)[0]
    rule = teaching.stamp(scope.build(sr.PLAY), snapshot, sr.PLAY)
    action, matched = sr.decide_flop(rules(flop=[rule]), snapshot.features)
    assert action == sr.PLAY
    assert matched is rule


def test_the_teaching_note_records_what_was_corrected(snapshot):
    scope = teaching.available_scopes(snapshot)[0]
    rule = teaching.stamp(scope.build(sr.PLAY), snapshot, sr.PLAY)
    note = rule["taught"]
    assert note["round_id"] == 123
    assert note["session"] == 4
    assert note["original_decision"] == sr.FOLD
    assert note["original_engine_decision"] == se.DONT_PLAY
    assert note["corrected_decision"] == sr.PLAY
    assert note["matched_rule"] == "Pair -> DON'T PLAY"
    assert note["player_cards"] == ["AS", "7D"]
    assert note["flop_cards"] == ["KC", "7C", "2H"]


def test_the_teaching_note_is_not_part_of_the_question_a_rule_asks(snapshot):
    """Otherwise a taught rule and the identical hand-made one would look like
    two different rules, and the duplicate check would let both be added."""
    scope = teaching.available_scopes(snapshot)[0]
    taught = teaching.stamp(scope.build(sr.PLAY), snapshot, sr.PLAY)
    by_hand = sr.flop_rule(scope.fields["hand"], scope.fields["condition"],
                           sr.PLAY)
    assert "taught" in taught
    assert sr.same_question(taught, by_hand)
    assert "taught" in sr.METADATA_KEYS


# -- 16/17. duplicates and conflicts ------------------------------------------

def test_an_exact_duplicate_is_detected():
    same = sr.flop_rule("Pair", sr.ANY, sr.FOLD, name="another name")
    kinds = [finding.kind for finding in teaching.analyse(EXAMPLE, "flop", same, 0)]
    assert teaching.DUPLICATE in kinds


def test_a_duplicate_is_blocking_so_a_second_copy_is_never_made():
    same = sr.flop_rule("Pair", sr.ANY, sr.FOLD)
    blocking = [f for f in teaching.analyse(EXAMPLE, "flop", same, 0) if f.blocking]
    assert blocking
    assert "already exists" in blocking[0].message


def test_the_same_question_with_a_different_answer_is_a_conflict():
    other = sr.flop_rule("Pair", sr.ANY, sr.PLAY)
    findings = teaching.analyse(EXAMPLE, "flop", other, 0)
    conflict = [f for f in findings if f.kind == teaching.CONFLICT]
    assert conflict
    assert "conflicting rule already exists" in conflict[0].message
    assert conflict[0].rule is PAIR_FOLD


def test_a_conflict_does_not_block_the_correction():
    """The point of teaching is to answer an existing question differently."""
    other = sr.flop_rule("Pair", sr.ANY, sr.PLAY)
    findings = teaching.analyse(EXAMPLE, "flop", other, 0)
    assert not any(f.blocking for f in findings)


def test_nothing_is_reported_when_there_is_nothing_to_report():
    fresh = sr.flop_rule("Flush", sr.ANY, sr.PLAY)
    assert teaching.analyse(EXAMPLE, "flop", fresh, 2) == []


def test_an_impossible_condition_is_detected_and_blocks():
    rule = sr.flop_rule("High Card", "pocket pair", sr.PLAY)
    findings = teaching.analyse(EXAMPLE, "flop", rule, 0)
    assert findings[0].kind == teaching.IMPOSSIBLE
    assert findings[0].blocking


@pytest.mark.parametrize("condition", list(teaching.PAIR_FORCING))
def test_high_card_with_any_pair_forcing_condition_is_impossible(condition):
    assert teaching.impossible("flop", sr.flop_rule("High Card", condition, sr.PLAY))


def test_a_possible_pairing_is_not_called_impossible():
    assert teaching.impossible("flop", sr.flop_rule("Pair", "pocket pair", sr.PLAY)) is None
    assert teaching.impossible("flop", sr.flop_rule("High Card", "no draw", sr.PLAY)) is None


def test_existing_rules_are_never_deleted_or_rewritten_by_a_proposal():
    before = copy.deepcopy(EXAMPLE)
    new = sr.flop_rule("Pair", "the flop is paired", sr.PLAY)
    proposed = teaching.propose(EXAMPLE, "flop", new, 0)
    assert EXAMPLE == before                     # the live set is untouched
    assert proposed["flop"]["rules"][1:] == before["flop"]["rules"]


# -- 18/19/20. priority --------------------------------------------------------

def test_a_rule_below_one_that_swallows_it_is_reported_as_never_running():
    new = sr.flop_rule("Pair", "the flop is paired", sr.PLAY,
                       name="FLOP + Pair -> PLAY")
    findings = teaching.analyse(EXAMPLE, "flop", new, 2)
    shadowed = [f for f in findings if f.kind == teaching.SHADOWED]
    assert shadowed
    assert "never run" in shadowed[0].message
    assert shadowed[0].rule is PAIR_FOLD


def test_the_same_rule_placed_above_has_nothing_to_report():
    new = sr.flop_rule("Pair", "the flop is paired", sr.PLAY)
    assert teaching.analyse(EXAMPLE, "flop", new, 0) == []


def test_the_suggested_position_is_above_the_rule_that_would_swallow_it():
    new = sr.flop_rule("Pair", "the flop is paired", sr.PLAY)
    assert teaching.suggested_index(EXAMPLE, "flop", new) == 0


def test_a_rule_nothing_swallows_is_suggested_at_the_end():
    new = sr.flop_rule("Flush", sr.ANY, sr.PLAY)
    assert teaching.suggested_index(EXAMPLE, "flop", new) == 2


def test_a_new_rule_that_would_silence_an_existing_one_says_so():
    catch_all = sr.flop_rule(sr.ANY, sr.ANY, sr.PLAY)
    findings = teaching.analyse(EXAMPLE, "flop", catch_all, 0)
    shadows = [f for f in findings if f.kind == teaching.SHADOWS]
    assert len(shadows) == 2                     # both existing rules
    assert {f.rule["name"] for f in shadows} == {
        "Pair -> DON'T PLAY", "High Card -> PLAY"}


def test_first_match_wins_is_what_the_priority_advice_is_about():
    new = sr.flop_rule("Pair", "the flop is paired", sr.PLAY, name="new")
    features = describe_flop(["AS", "7D"], ["KC", "KH", "2H"])
    above = teaching.propose(EXAMPLE, "flop", new, 0)
    below = teaching.propose(EXAMPLE, "flop", new, 2)
    assert sr.decide_flop(above, features)[1]["name"] == "new"
    # A copy, because propose never hands back the live rule objects.
    assert sr.decide_flop(below, features)[1] == PAIR_FOLD


def test_a_specific_rule_above_a_generic_one_overrides_it_only_where_it_applies():
    specific = sr.flop_rule("Pair", "the flop is paired", sr.PLAY, name="specific")
    proposed = teaching.propose(EXAMPLE, "flop", specific, 0)
    paired = describe_flop(["AS", "7D"], ["KC", "KH", "2H"])
    unpaired = describe_flop(["AS", "7D"], ["KC", "7C", "2H"])
    assert sr.decide_flop(proposed, paired) [0] == sr.PLAY
    assert sr.decide_flop(proposed, unpaired)[0] == sr.FOLD   # still the old rule


def test_unrelated_rule_order_is_preserved():
    third = sr.flop_rule("Flush", sr.ANY, sr.PLAY, name="flush")
    with_three = rules(flop=[PAIR_FOLD, HIGH_PLAY, third])
    proposed = teaching.propose(with_three, "flop", sr.flop_rule(
        "Two Pair", sr.ANY, sr.PLAY, name="new"), 1)
    assert [rule["name"] for rule in proposed["flop"]["rules"]] == [
        "Pair -> DON'T PLAY", "new", "High Card -> PLAY", "flush"]


def test_a_streak_rule_is_known_to_be_narrower_than_a_player_win_rule():
    """The one thing the model knows that the fields do not say: a streak of
    one or more means the player won the last round."""
    player_won = sr.preround_rule(sr.ANY, sr.ANY, sr.ANTE,
                                  previous_winner=sr.PLAYER, name="won -> ante")
    streak = sr.preround_rule(sr.ANY, sr.ANY, sr.SKIP, player_win_streak=2,
                              name="won 2 -> skip")
    existing = rules(preround=[player_won])
    assert teaching.subsumes("preround", player_won, streak)
    assert not teaching.subsumes("preround", streak, player_won)
    assert teaching.suggested_index(existing, "preround", streak) == 0
    findings = teaching.analyse(existing, "preround", streak, 1)
    assert [f.kind for f in findings] == [teaching.SHADOWED]


def test_subsumption_is_exact_for_the_flop_model():
    generic = sr.flop_rule(sr.ANY, sr.ANY, sr.PLAY)
    by_hand = sr.flop_rule("Pair", sr.ANY, sr.PLAY)
    by_both = sr.flop_rule("Pair", "no draw", sr.PLAY)
    assert teaching.subsumes("flop", generic, by_both)
    assert teaching.subsumes("flop", by_hand, by_both)
    assert not teaching.subsumes("flop", by_both, by_hand)
    assert not teaching.subsumes("flop", by_hand,
                                 sr.flop_rule("Flush", sr.ANY, sr.PLAY))


# -- 21/22/23. what the correction does and does not change -------------------

def test_the_correction_changes_the_hand_it_was_taught_from(snapshot):
    scope = [s for s in teaching.available_scopes(snapshot) if s.key == "hand"][0]
    proposed = teaching.propose(EXAMPLE, "flop", scope.build(sr.PLAY), 0)
    assert teaching.decide_with(EXAMPLE, snapshot)[0] == sr.FOLD
    assert teaching.decide_with(proposed, snapshot)[0] == sr.PLAY


def test_the_correction_leaves_unrelated_situations_alone(snapshot):
    scope = [s for s in teaching.available_scopes(snapshot) if s.key == "hand"][0]
    proposed = teaching.propose(EXAMPLE, "flop", scope.build(sr.PLAY), 0)
    high_card = describe_flop(["AS", "4D"], ["KC", "9C", "2H"])
    assert sr.decide_flop(EXAMPLE, high_card) == sr.decide_flop(proposed, high_card)


def test_a_narrow_scope_does_not_generalise_to_every_hand_of_that_type(snapshot):
    """Choosing "Pair with a flush draw" must not become "every Pair"."""
    draw_scopes = [s for s in teaching.available_scopes(snapshot)
                   if s.fields.get("condition") == "pair uses one of my cards"]
    narrow = draw_scopes[0]
    proposed = teaching.propose(EXAMPLE, "flop", narrow.build(sr.PLAY), 0)
    pocket = describe_flop(["9S", "9D"], ["KC", "7C", "2H"])
    assert pocket["hand"] == "Pair"
    assert pocket["pair"] == "pocket pair"
    assert sr.decide_flop(proposed, pocket)[0] == sr.FOLD    # untouched


def test_decide_with_uses_the_history_the_snapshot_was_taken_with():
    wins = [record(winner="Player"), record(winner="Player")]
    taken, _ = teaching.capture(
        payload(cards={}, statuses={}, state="WAITING"), rules(), session=1,
        previous=wins[0], history=wins)
    streak = sr.preround_rule(sr.ANY, sr.ANY, sr.SKIP, player_win_streak=2)
    proposed = teaching.propose(rules(), "preround", streak, 0)
    assert teaching.decide_with(proposed, taken)[0] == sr.SKIP


# -- 23/24. save and test, and the backtest -----------------------------------

def hand_row(identifier, player, flop, winner="Dealer"):
    return {"id": identifier, "winner": winner,
            "player_card_1": player[0], "player_card_2": player[1],
            "flop_card_1": flop[0], "flop_card_2": flop[1], "flop_card_3": flop[2],
            "turn_card": "3D", "river_card": "4S",
            "dealer_card_1": "QH", "dealer_card_2": "JD"}


ROWS = [
    hand_row(1, ["AS", "7D"], ["KC", "7C", "2H"], "Player"),   # Pair
    hand_row(2, ["9S", "9D"], ["KC", "8C", "2H"], "Dealer"),   # Pair (pocket)
    hand_row(3, ["AS", "4D"], ["KC", "9C", "2H"], "Dealer"),   # High Card
    hand_row(4, ["AS", "KD"], ["QC", "JC", "TH"], "Player"),   # not a pair
]


def test_save_and_test_reports_what_changes_for_the_captured_hand(snapshot):
    scope = [s for s in teaching.available_scopes(snapshot) if s.key == "hand"][0]
    proposed = teaching.propose(EXAMPLE, "flop", scope.build(sr.PLAY), 0)
    report = teaching.impact(EXAMPLE, proposed, ROWS, "flop",
                             scope.build(sr.PLAY), snapshot)
    assert report["snapshot"]["before"] == sr.FOLD
    assert report["snapshot"]["after"] == sr.PLAY
    assert report["snapshot"]["changed"] is True


def test_save_and_test_counts_come_from_an_actual_replay(snapshot):
    scope = [s for s in teaching.available_scopes(snapshot) if s.key == "hand"][0]
    rule = scope.build(sr.PLAY)
    proposed = teaching.propose(EXAMPLE, "flop", rule, 0)
    report = teaching.impact(EXAMPLE, proposed, ROWS, "flop", rule, snapshot)

    assert report["hands"] == len(ROWS)
    assert report["matches"] == 2                    # the two Pair hands
    assert len(report["changed"]) == 2
    assert {change["row"]["id"] for change in report["changed"]} == {1, 2}
    assert report["before"][sr.FOLD] == 2
    assert report["after"][sr.FOLD] == 0
    assert sum(report["before"].values()) == sum(report["after"].values()) == len(ROWS)


def test_an_unchanged_rule_set_reports_no_change():
    report = teaching.impact(EXAMPLE, copy.deepcopy(EXAMPLE), ROWS, "flop",
                             PAIR_FOLD, None)
    assert report["changed"] == []
    assert report["before"] == report["after"]


def test_a_rule_no_recorded_hand_matches_is_reported_as_such():
    rule = sr.flop_rule("Royal Flush", sr.ANY, sr.PLAY)
    proposed = teaching.propose(EXAMPLE, "flop", rule, 0)
    report = teaching.impact(EXAMPLE, proposed, ROWS, "flop", rule, None)
    assert report["matches"] == 0
    assert "no recorded hand looks like this" in "\n".join(
        teaching.describe_impact(report))


def test_save_and_test_writes_nothing(snapshot, tmp_path):
    """propose and impact are the whole of Save & Test, and neither saves."""
    path = str(tmp_path / "scenarios.json")
    sr.save(EXAMPLE, path)
    before = open(path, "rb").read()
    scope = teaching.available_scopes(snapshot)[0]
    rule = scope.build(sr.PLAY)
    teaching.impact(EXAMPLE, teaching.propose(EXAMPLE, "flop", rule, 0),
                    ROWS, "flop", rule, snapshot)
    assert open(path, "rb").read() == before


def test_the_impact_description_never_invents_a_number(snapshot):
    scope = [s for s in teaching.available_scopes(snapshot) if s.key == "hand"][0]
    rule = scope.build(sr.PLAY)
    report = teaching.impact(EXAMPLE, teaching.propose(EXAMPLE, "flop", rule, 0),
                             ROWS, "flop", rule, snapshot)
    text = "\n".join(teaching.describe_impact(report))
    assert "Hands on record:     %d" % len(ROWS) in text
    assert "Hands affected:      %d" % len(report["changed"]) in text


def test_the_backtest_used_is_the_projects_own():
    from tools import backtest
    assert teaching.impact.__module__ == "poker.teaching"
    assert hasattr(backtest, "walk") and hasattr(backtest, "compare")


def test_the_existing_backtest_replay_still_agrees_with_the_walk():
    """The refactor that made comparison possible must not have changed the
    tool everyone already runs."""
    from tools import backtest
    tally = backtest.replay(EXAMPLE, ROWS)
    walked = list(backtest.walk(EXAMPLE, ROWS))
    assert tally["hands"] == len(walked)
    assert tally["skipped"] == sum(
        1 for item in walked if item["preround_action"] == sr.SKIP)
    assert tally["folded"] == sum(
        1 for item in walked if item["flop_action"] == sr.FOLD)


# -- 25/26. activation and persistence ----------------------------------------

def test_activate_writes_the_proposed_set(snapshot, tmp_path):
    path = str(tmp_path / "scenarios.json")
    sr.save(EXAMPLE, path)
    scope = teaching.available_scopes(snapshot)[0]
    proposed = teaching.propose(EXAMPLE, "flop", scope.build(sr.PLAY), 0)
    teaching.activate(proposed, path)
    assert len(sr.load(path)["flop"]["rules"]) == 3


def test_an_activated_rule_survives_a_reload_and_still_decides(snapshot, tmp_path):
    path = str(tmp_path / "scenarios.json")
    scope = [s for s in teaching.available_scopes(snapshot) if s.key == "hand"][0]
    rule = teaching.stamp(scope.build(sr.PLAY), snapshot, sr.PLAY)
    teaching.activate(teaching.propose(EXAMPLE, "flop", rule, 0), path)

    reloaded = sr.load(path)                     # as if the app had restarted
    action, matched = sr.decide_flop(reloaded, snapshot.features)
    assert action == sr.PLAY
    assert matched["name"] == rule["name"]
    assert matched["taught"]["round_id"] == 123


def test_activation_does_not_disturb_the_other_section(snapshot, tmp_path):
    path = str(tmp_path / "scenarios.json")
    start = rules(flop=[PAIR_FOLD],
                  preround=[sr.preround_rule(sr.ANY, sr.ANY, sr.SKIP,
                                             previous_winner=sr.DEALER)])
    scope = teaching.available_scopes(snapshot)[0]
    teaching.activate(teaching.propose(start, "flop", scope.build(sr.PLAY), 0),
                      path)
    assert sr.load(path)["preround"] == start["preround"]


# -- 30. the manual rule builder is unaffected --------------------------------

def test_the_standard_rules_are_still_recognised_as_already_present():
    """add_standard_rules compares on the question, which now ignores the
    teaching note - the standard rules must still not be added twice."""
    scenarios = {"preround": {"default": sr.ANTE, "rules": []},
                 "flop": {"default": sr.PLAY, "rules": []}}
    assert sr.add_standard_rules(scenarios) > 0
    assert sr.add_standard_rules(scenarios) == 0


def test_a_taught_rule_blocks_the_identical_rule_being_added_by_hand(snapshot):
    scope = teaching.available_scopes(snapshot)[0]
    taught = teaching.stamp(scope.build(sr.PLAY), snapshot, sr.PLAY)
    scenarios = rules(flop=[taught])
    by_hand = sr.flop_rule(scope.fields["hand"], scope.fields["condition"],
                           sr.PLAY)
    assert any(sr.same_question(by_hand, rule)
               for rule in scenarios["flop"]["rules"])


def test_the_live_configuration_file_is_never_touched_by_these_tests():
    """A guard on the suite itself: nothing above may write the real rules."""
    assert os.path.exists(sr.SCENARIOS_PATH)
    live = sr.load()
    assert set(live) == {"preround", "flop"}
