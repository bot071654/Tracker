"""The High Card A/K/Q rule asks about the player's own two cards.

It used to ask about the highest card showing, which counts the flop. An ace
on the flop is the dealer's ace as well and plays the same for both seats, so
a hand could be "A high" with nothing in it at all. Over 197 recorded hands,
High Card hands with an A/K/Q in the hole won 39.1% (18 of 46); the ones where
the big card was only on the board won 19.2% (10 of 52).

The Scenario Engine already asked the hole-card question
(config/scenario_engine.json: high_card_source = "player"), so the two halves
of the window disagreed. They now ask the same thing.

Both readings are still defined and both are still offered to the rule builder
and scored by the statistics - what changed is which one the shipped rule and
the saved rule use.
"""

import json
import os

import pytest

from poker import scenarios
from poker.board_features import HIGH_AKQ, HIGH_AKQ_HOLE, describe_flop
from poker.scenario_engine import (
    HIGH_CARD_AKQ, detect_high_card_scenario, load_engine_config,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED = os.path.join(ROOT, "config", "scenarios.json")


@pytest.fixture()
def standard():
    """A fresh rule set holding exactly the shipped rules."""
    empty = {section: {"default": body["default"], "rules": []}
             for section, body in scenarios.DEFAULTS.items()}
    assert scenarios.add_standard_rules(empty) == 5
    return empty


def decide(standard, hole, board):
    """(action, rule name) the shipped rules give for one flop."""
    features = describe_flop(hole, board)
    action, rule = scenarios.decide_flop(standard, features)
    return action, (rule or {}).get("name")


def high_card_rule(rules):
    return [rule for rule in rules["flop"]["rules"]
            if rule["hand"] == scenarios.HIGH_CARD][0]


# -- A/K/Q in either hole card fires the rule --------------------------------

@pytest.mark.parametrize("hole", [
    ["AH", "5C"],          # ace first
    ["5C", "AH"],          # ace second
    ["KS", "7D"],          # king first
    ["7D", "KS"],          # king second
    ["QC", "3H"],          # queen first
    ["3H", "QC"],          # queen second
])
def test_a_big_card_in_either_hole_card_plays_on(standard, hole):
    """Either position counts - the rule is about the two cards, not the order."""
    action, name = decide(standard, hole, ["9D", "6S", "2H"])
    assert action == scenarios.PLAY
    assert "one of my two cards is A, K or Q" in name


def test_two_big_cards_in_hand_still_play_on(standard):
    action, name = decide(standard, ["AH", "KD"], ["9D", "6S", "2H"])
    assert action == scenarios.PLAY
    assert "one of my two cards is A, K or Q" in name


# -- A/K/Q only on the flop does not ------------------------------------------

@pytest.mark.parametrize("board", [
    ["AS", "6S", "2H"],    # ace on the board
    ["KS", "5D", "9H"],    # king on the board
    ["QD", "7C", "3S"],    # queen on the board
    ["AS", "KD", "QC"],    # all three on the board
])
def test_a_big_card_only_on_the_flop_does_not_fire_the_rule(standard, board):
    action, name = decide(standard, ["7D", "4C"], board)
    assert name != high_card_rule(standard)["name"]
    assert action == standard["flop"]["default"]      # falls through to the default


def test_the_board_reading_would_have_fired_on_those(standard):
    """The rule that was there before. Kept as a condition, not used by the rule."""
    features = describe_flop(["7D", "4C"], ["AS", "6S", "2H"])
    assert scenarios._matches_condition(HIGH_AKQ, features)
    assert not scenarios._matches_condition(HIGH_AKQ_HOLE, features)


# -- no A/K/Q anywhere in the hole does not -----------------------------------

@pytest.mark.parametrize("hole", [
    ["JS", "10D"],         # the next two ranks down
    ["9C", "2H"],
    ["7D", "4C"],
])
def test_no_big_card_in_hand_does_not_fire_the_rule(standard, hole):
    action, name = decide(standard, hole, ["8S", "5D", "3H"])
    assert name != high_card_rule(standard)["name"]
    assert action == standard["flop"]["default"]


def test_a_jack_is_not_a_big_card(standard):
    """The rank list is A/K/Q; the jack is the nearest thing that is not."""
    features = describe_flop(["JS", "JD"], ["8S", "5D", "3H"])
    assert not scenarios._matches_condition(HIGH_AKQ_HOLE, features)


# -- the two halves of the window now agree ------------------------------------

@pytest.mark.parametrize("hole,board", [
    (["JS", "3D"], ["KS", "5D", "9H"]),       # king on the board only
    (["AH", "5C"], ["KC", "2D", "9H"]),       # ace in the hand
    (["QC", "5S"], ["JC", "4S", "2H"]),       # queen in the hand
    (["7C", "2D"], ["9H", "5S", "4D"]),       # nothing anywhere
    (["7C", "2D"], ["AH", "KS", "QD"]),       # everything on the board
])
def test_the_rule_and_the_scenario_engine_answer_the_same_question(hole, board):
    features = describe_flop(hole, board)
    engine = detect_high_card_scenario(hole, board)[HIGH_CARD_AKQ]
    assert scenarios._matches_condition(HIGH_AKQ_HOLE, features) == engine


def test_the_engine_is_still_configured_for_the_players_own_cards():
    config = load_engine_config()
    assert config.high_card_source == "player"
    assert config.high_card_min_count == 1
    assert config.high_card_ranks == ["A", "K", "Q"]


# -- the shipped default and the saved rule -----------------------------------

def test_the_shipped_rule_asks_about_the_hole_cards(standard):
    assert high_card_rule(standard)["condition"] == HIGH_AKQ_HOLE


def test_the_saved_production_rule_asks_about_the_hole_cards():
    saved = json.load(open(SAVED, encoding="utf-8"))
    rules = [rule for rule in saved["flop"]["rules"]
             if rule["hand"] == scenarios.HIGH_CARD]
    assert [rule["condition"] for rule in rules] == [HIGH_AKQ_HOLE]


def test_the_saved_rule_and_the_shipped_default_are_the_same_question():
    """Otherwise "add standard rules" would offer a second, contradictory rule."""
    saved = scenarios.load(SAVED)
    assert scenarios.add_standard_rules(saved) == 0


def test_both_readings_are_still_available_to_the_rule_builder():
    assert HIGH_AKQ in scenarios.CONDITION_CHOICES
    assert HIGH_AKQ_HOLE in scenarios.CONDITION_CHOICES


def test_both_readings_are_still_scored_by_the_statistics():
    from poker import analysis

    keys = {key for key, _, _, _, _ in analysis.CATALOGUE}
    assert {"high_card_akq", "high_card_akq_hole"} <= keys

    # and they really do separate: a king on the board only is one, not both
    found = {entry["key"] for entry in
             analysis.detect(describe_flop(["JS", "3D"], ["KS", "5D", "9H"]))}
    assert "high_card_akq" in found
    assert "high_card_akq_hole" not in found


# -- every other rule is untouched ---------------------------------------------

def test_the_paired_flop_rule_is_unchanged(standard):
    action, name = decide(standard, ["2C", "7D"], ["9H", "9S", "4D"])
    assert action == scenarios.PLAY
    assert "flop is paired" in name


def test_the_preround_rules_are_unchanged(standard):
    def round_record(winner):
        return {"winner": winner, "player_hand": "High Card",
                "dealer_hand": "High Card"}

    action, rule = scenarios.decide_preround(standard, round_record(scenarios.DEALER))
    assert (action, "dealer won" in rule["name"]) == (scenarios.SKIP, True)

    action, rule = scenarios.decide_preround(standard, round_record(scenarios.PLAYER))
    assert (action, "player won the last round" in rule["name"]) == (scenarios.ANTE, True)

    action, rule = scenarios.decide_preround(
        standard, round_record(scenarios.PLAYER),
        history=[round_record(scenarios.PLAYER), round_record(scenarios.PLAYER)])
    assert (action, "last 2 rounds" in rule["name"]) == (scenarios.ANTE, True)


@pytest.mark.parametrize("hole,board,hand", [
    (["9C", "9D"], ["2H", "5S", "KD"], "Pair"),              # pocket pair
    (["9C", "7D"], ["9H", "5S", "4D"], "Pair"),              # pair with a hole card
    (["9C", "7D"], ["9H", "7S", "4D"], "Two Pair"),
    (["9C", "9D"], ["9H", "5S", "4D"], "Three of a Kind"),
    (["6C", "7D"], ["8H", "9S", "10D"], "Straight"),
])
def test_made_hands_are_decided_by_the_same_rules_as_before(standard, hole, board, hand):
    """None of these is a High Card hand, so the rule under test cannot reach them."""
    features = describe_flop(hole, board)
    assert features["hand"] == hand
    action, name = scenarios.decide_flop(standard, features)
    # either the paired-flop rule or the default - never the High Card rule
    assert name != high_card_rule(standard)["name"]
    assert action == scenarios.PLAY


def test_the_scenario_engine_rules_are_untouched():
    """The engine's own scenarios decide PLAY exactly as they did."""
    config = load_engine_config()
    assert set(config.play_scenarios) == {
        "HIGH_CARD_AKQ", "PLAYER_PAIR", "COMBINED_PAIR", "FLOP_PAIR",
        "PLAYER_PAIR_PLUS_FLOP_PAIR", "COMBINED_TWO_PAIR",
        "PLAYER_PAIR_CARD_PLUS_FLOP_PAIR", "PLAYER_PAIR_PLUS_FLOP_MATCH",
        "PLAYER_CARD_PLUS_FLOP_PAIR", "STRAIGHT",
    }
