"""The user's own rules wait for the same five cards the Scenario Engine does.

The two halves of the app answer the same question from the same table. The
Scenario Engine refuses to answer until the player's two cards and the three
flop cards are CONFIRMED or HELD (scenario_engine.gate_slots). "Your scenarios
say" did not: it was handed whatever CardMemory currently held, which after a
single weak reading is that reading.

That matters because of how a flop arrives. The cards deal in from the left,
so for a poll or two the third box holds a card on its way past - read weakly,
and read repeatedly. Replaying session 20260917_120408 through the rules as
they stood, three of the fourteen rounds fired a rule on a card that was still
CONFIRMING and was revised a moment later, and in each one the rule that fired
was the wrong rule:

    round 37   flop_3 read KD beside KS -> "the flop is paired, play on"
               really 3D                -> no rule applies
    round 60   flop_3 read KS beside KD -> "the flop is paired, play on"
               really 2S                -> no rule applies
    round 194  flop_3 read KD beside KC -> "the flop is paired, play on"
               really JD                -> no rule applies (the player held
                                           2C 9C; under the board-inclusive
                                           reading the rule used at the time,
                                           the king made it "A, K or Q high")

Each of those recommendations was shown, and then replaced a second later.
"""

import pytest

from poker import scenarios
from poker.scenario_engine import ACCEPTED_STATUSES

CONFIRMED, HELD = "CONFIRMED", "HELD"
CONFIRMING, AMBIGUOUS, UNKNOWN, EMPTY = (
    "CONFIRMING", "AMBIGUOUS", "UNKNOWN", "EMPTY")


@pytest.fixture()
def standard():
    empty = {section: {"default": body["default"], "rules": []}
             for section, body in scenarios.DEFAULTS.items()}
    assert scenarios.add_standard_rules(empty) == 5
    return empty


def settled(**overrides):
    """Every decision card confirmed, unless told otherwise."""
    statuses = {slot: CONFIRMED for slot in scenarios.DECISION_SLOTS}
    statuses.update(overrides)
    return statuses


# -- the gate itself ----------------------------------------------------------

def test_the_gate_names_the_same_two_words_the_engine_accepts():
    assert set(ACCEPTED_STATUSES) == {CONFIRMED, HELD}


def test_nothing_is_waited_for_when_all_five_are_settled():
    assert scenarios.unsettled_slots(settled()) == []
    assert scenarios.unsettled_slots(settled(flop_2=HELD)) == []


@pytest.mark.parametrize("status", [CONFIRMING, AMBIGUOUS, UNKNOWN, EMPTY])
def test_any_other_status_is_waited_for(status):
    assert scenarios.unsettled_slots(settled(flop_3=status)) == ["flop_3"]


def test_waiting_slots_come_back_in_dealing_order():
    statuses = settled(player_2=CONFIRMING, flop_1=UNKNOWN, flop_3=EMPTY)
    assert scenarios.unsettled_slots(statuses) == ["player_2", "flop_1", "flop_3"]


def test_a_slot_with_no_status_at_all_is_waited_for():
    """A caller that knows nothing about a slot has not settled it."""
    assert scenarios.unsettled_slots({}) == scenarios.DECISION_SLOTS
    assert scenarios.unsettled_slots(None) == scenarios.DECISION_SLOTS


# -- what the window would have shown -----------------------------------------

ROUND_37 = {"player_1": "10H", "player_2": "KC",
            "flop_1": "9S", "flop_2": "KS", "flop_3": "KD"}
ROUND_37_TRUTH = dict(ROUND_37, flop_3="3D")


def test_an_unsettled_card_produces_no_recommendation(standard):
    decision = scenarios.decide_from_cards(
        standard, ROUND_37, statuses=settled(flop_3=CONFIRMING))
    assert decision["features"] is None
    assert decision["flop_rule"] is None
    assert decision["waiting_for"] == ["flop_3"]


def test_the_wrong_rule_is_no_longer_reached(standard):
    """Round 37: KD in flop_3 beside KS looked like a paired flop."""
    ungated = scenarios.decide_from_cards(standard, ROUND_37)
    assert "flop is paired" in ungated["flop_rule"]["name"]

    truth = scenarios.decide_from_cards(standard, ROUND_37_TRUTH)
    assert truth["flop_rule"] is None          # no rule applies to 9S KS 3D

    gated = scenarios.decide_from_cards(
        standard, ROUND_37, statuses=settled(flop_3=CONFIRMING))
    assert gated["flop_rule"] is None


def test_the_recommendation_arrives_once_the_card_settles(standard):
    decision = scenarios.decide_from_cards(
        standard, ROUND_37_TRUTH, statuses=settled())
    assert decision["waiting_for"] == []
    assert decision["features"]["hand"] == "Pair"
    assert decision["flop_action"] == standard["flop"]["default"]


def test_a_settled_flop_decides_exactly_as_it_always_did(standard):
    paired = {"player_1": "2C", "player_2": "7D",
              "flop_1": "9H", "flop_2": "9S", "flop_3": "4D"}
    gated = scenarios.decide_from_cards(standard, paired, statuses=settled())
    ungated = scenarios.decide_from_cards(standard, paired)
    assert gated["flop_action"] == ungated["flop_action"] == scenarios.PLAY
    assert gated["flop_rule"] == ungated["flop_rule"]


def test_a_card_held_under_the_dealers_hand_still_decides(standard):
    """HELD is a settled card that happens to be covered right now."""
    paired = {"player_1": "2C", "player_2": "7D",
              "flop_1": "9H", "flop_2": "9S", "flop_3": "4D"}
    decision = scenarios.decide_from_cards(
        standard, paired, statuses=settled(flop_2=HELD, player_1=HELD))
    assert decision["waiting_for"] == []
    assert "flop is paired" in decision["flop_rule"]["name"]


# -- the pre-round half is unaffected -----------------------------------------

def test_the_preround_recommendation_does_not_wait_for_the_flop(standard):
    """It is about the round that just finished, which is already recorded."""
    previous = {"winner": scenarios.DEALER,
                "player_hand": "High Card", "dealer_hand": "Pair"}
    decision = scenarios.decide_from_cards(
        standard, {}, previous=previous, statuses={})
    assert decision["features"] is None
    assert decision["preround_action"] == scenarios.SKIP
    assert "dealer won" in decision["preround_rule"]["name"]


# -- callers that have no statuses to give ------------------------------------

def test_stored_hands_are_taken_at_face_value(standard):
    """The backtest replays finished rounds, whose cards are settled by definition."""
    decision = scenarios.decide_from_cards(standard, ROUND_37)
    assert decision["waiting_for"] == []
    assert decision["features"] is not None
