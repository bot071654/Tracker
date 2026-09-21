"""Scenario rules: reading a flop situation, and deciding from it."""

import json

import pytest

from poker import scenarios
from poker.board_features import (
    FLUSH_DRAW, GUTSHOT, NO_DRAW, OPEN_STRAIGHT, PAIR_FROM_HOLE, PAIR_ON_BOARD,
    POCKET_PAIR, describe_flop, describe_flop_from_slots, summarise,
)


# -- reading the situation ----------------------------------------------------

def test_four_to_a_flush():
    features = describe_flop(["AH", "KH"], ["2H", "7H", "9C"])
    assert features["hand"] == "High Card"
    assert features["draw"] == FLUSH_DRAW


def test_a_made_flush_is_not_a_draw():
    features = describe_flop(["AH", "KH"], ["2H", "7H", "9H"])
    assert features["hand"] == "Flush"
    assert features["draw"] == NO_DRAW


@pytest.mark.parametrize("hole,board,draw", [
    (["9H", "8D"], ["7C", "6S", "2H"], OPEN_STRAIGHT),   # 5 or 10 completes
    (["JH", "10D"], ["9C", "8S", "2H"], OPEN_STRAIGHT),  # 7 or Q completes
    (["5H", "4D"], ["3C", "2S", "KH"], OPEN_STRAIGHT),   # 6 or the wheel ace
    (["AH", "KD"], ["QC", "JS", "2H"], GUTSHOT),         # only a 10 completes
    (["AH", "2D"], ["3C", "4S", "KH"], GUTSHOT),         # only a 5 completes
    (["9H", "7D"], ["6C", "5S", "KH"], GUTSHOT),         # only an 8 completes
    (["2H", "7D"], ["KC", "9S", "4H"], NO_DRAW),
])
def test_straight_draws(hole, board, draw):
    assert describe_flop(hole, board)["draw"] == draw


def test_a_made_straight_is_not_drawing():
    features = describe_flop(["9H", "8D"], ["7C", "6S", "5H"])
    assert features["hand"] == "Straight"
    assert features["draw"] == NO_DRAW


@pytest.mark.parametrize("hole,board,source", [
    (["8S", "8D"], ["2C", "7H", "KD"], POCKET_PAIR),
    (["8S", "9D"], ["8C", "7H", "KD"], PAIR_FROM_HOLE),
    (["8S", "9D"], ["2C", "2H", "KD"], PAIR_ON_BOARD),
    (["8S", "9D"], ["2C", "7H", "KD"], None),
])
def test_where_a_pair_comes_from(hole, board, source):
    assert describe_flop(hole, board)["pair"] == source


def test_a_pair_on_the_board_is_worth_nothing():
    """Both seats hold it, so the rule builder has to be able to see it."""
    features = describe_flop(["8S", "9D"], ["2C", "2H", "KD"])
    assert features["hand"] == "Pair"
    assert features["pair"] == PAIR_ON_BOARD
    assert "pair is on the board" in summarise(features)


@pytest.mark.parametrize("hole,board,count", [
    (["AS", "KD"], ["2C", "7H", "9D"], 2),
    (["AS", "5D"], ["2C", "7H", "9D"], 1),
    (["3S", "5D"], ["2C", "7H", "9D"], 0),
])
def test_overcards(hole, board, count):
    assert describe_flop(hole, board)["overcards"] == count


def test_nothing_to_read_before_the_flop():
    assert describe_flop(["AS", "KD"], ["2C"]) is None
    assert describe_flop(["AS"], ["2C", "7H", "9D"]) is None


def test_a_card_read_into_two_places_reads_as_nothing():
    assert describe_flop(["AS", "KD"], ["AS", "7H", "9D"]) is None


def test_reading_from_tracker_slots():
    features = describe_flop_from_slots({
        "player_1": "AH", "player_2": "KH",
        "flop_1": "2H", "flop_2": "7H", "flop_3": "9C",
        "turn": None, "river": None,
    })
    assert features["draw"] == FLUSH_DRAW


# -- deciding from the rules --------------------------------------------------

@pytest.fixture
def empty():
    return {"preround": {"default": scenarios.ANTE, "rules": []},
            "flop": {"default": scenarios.PLAY, "rules": []}}


def test_the_default_applies_when_no_rule_matches(empty):
    features = describe_flop(["AS", "KD"], ["2C", "7H", "9D"])
    assert scenarios.decide_flop(empty, features) == (scenarios.PLAY, None)


def test_a_flop_rule_fires(empty):
    empty["flop"]["rules"] = [scenarios.flop_rule("High Card", NO_DRAW, scenarios.FOLD)]
    features = describe_flop(["AS", "KD"], ["2C", "7H", "9D"])
    action, rule = scenarios.decide_flop(empty, features)
    assert action == scenarios.FOLD
    assert rule["name"] == "If High Card with no draw, fold"


def test_the_draw_condition_keeps_a_hand_that_would_otherwise_fold(empty):
    """The whole point of the second dropdown."""
    empty["flop"]["rules"] = [scenarios.flop_rule("High Card", NO_DRAW, scenarios.FOLD)]

    dry = describe_flop(["AS", "KD"], ["2C", "7H", "9D"])
    drawing = describe_flop(["AH", "KH"], ["2H", "7H", "9C"])

    assert scenarios.decide_flop(empty, dry)[0] == scenarios.FOLD
    assert scenarios.decide_flop(empty, drawing)[0] == scenarios.PLAY


def test_the_first_matching_rule_wins(empty):
    empty["flop"]["rules"] = [
        scenarios.flop_rule("High Card", FLUSH_DRAW, scenarios.PLAY),
        scenarios.flop_rule("High Card", scenarios.ANY, scenarios.FOLD),
    ]
    drawing = describe_flop(["AH", "KH"], ["2H", "7H", "9C"])
    assert scenarios.decide_flop(empty, drawing)[0] == scenarios.PLAY


def test_a_rule_for_any_hand_with_a_condition(empty):
    empty["flop"]["rules"] = [
        scenarios.flop_rule(scenarios.ANY, PAIR_ON_BOARD, scenarios.FOLD)
    ]
    board_pair = describe_flop(["8S", "9D"], ["2C", "2H", "KD"])
    assert scenarios.decide_flop(empty, board_pair)[0] == scenarios.FOLD


def test_no_decision_before_the_flop(empty):
    assert scenarios.decide_flop(empty, None) == (None, None)


def test_preround_rule_matches_the_previous_hand(empty):
    empty["preround"]["rules"] = [
        scenarios.preround_rule(scenarios.ANY, "High Card", scenarios.SKIP)
    ]
    previous = {"player_hand": "High Card", "dealer_hand": "Pair"}
    assert scenarios.decide_preround(empty, previous)[0] == scenarios.SKIP

    previous = {"player_hand": "Pair", "dealer_hand": "Pair"}
    assert scenarios.decide_preround(empty, previous)[0] == scenarios.ANTE


def test_preround_rule_can_ask_about_both_seats(empty):
    empty["preround"]["rules"] = [
        scenarios.preround_rule("Two Pair", "High Card", scenarios.SKIP)
    ]
    assert scenarios.decide_preround(
        empty, {"player_hand": "High Card", "dealer_hand": "Two Pair"})[0] == scenarios.SKIP
    # only one side matching is not enough
    assert scenarios.decide_preround(
        empty, {"player_hand": "High Card", "dealer_hand": "Pair"})[0] == scenarios.ANTE


def test_the_first_round_has_no_previous_hand(empty):
    empty["preround"]["rules"] = [
        scenarios.preround_rule(scenarios.ANY, "High Card", scenarios.SKIP)
    ]
    assert scenarios.decide_preround(empty, None)[0] == scenarios.ANTE


def test_an_unknown_condition_does_not_fire(empty):
    empty["flop"]["rules"] = [
        {"hand": scenarios.ANY, "condition": "something else", "action": scenarios.FOLD,
         "name": "odd"}
    ]
    features = describe_flop(["AS", "KD"], ["2C", "7H", "9D"])
    assert scenarios.decide_flop(empty, features)[0] == scenarios.PLAY


def test_rules_reject_an_unknown_action():
    with pytest.raises(ValueError):
        scenarios.flop_rule("High Card", NO_DRAW, "ante")
    with pytest.raises(ValueError):
        scenarios.preround_rule(scenarios.ANY, "Pair", "fold")


# -- storage ------------------------------------------------------------------

def test_saving_and_loading(tmp_path, empty):
    path = str(tmp_path / "scenarios.json")
    empty["flop"]["rules"] = [scenarios.flop_rule("Pair", PAIR_ON_BOARD, scenarios.FOLD)]
    scenarios.save(empty, path)

    loaded = scenarios.load(path)
    assert loaded["flop"]["rules"][0]["condition"] == PAIR_ON_BOARD
    assert loaded["flop"]["default"] == scenarios.PLAY


def test_a_missing_file_gives_empty_rule_sets(tmp_path):
    loaded = scenarios.load(str(tmp_path / "nothing.json"))
    assert loaded["flop"]["rules"] == []
    assert loaded["preround"]["default"] == scenarios.ANTE


def test_a_damaged_file_does_not_stop_the_app(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    loaded = scenarios.load(str(path))
    assert loaded["flop"]["rules"] == []


def test_choices_offered_to_the_dropdowns():
    assert "Any" in scenarios.HAND_CHOICES
    for name in ("High Card", "Pair", "Royal Flush"):
        assert name in scenarios.HAND_CHOICES
    for condition in (FLUSH_DRAW, OPEN_STRAIGHT, GUTSHOT, PAIR_ON_BOARD):
        assert condition in scenarios.CONDITION_CHOICES


# -- replaying against recorded hands -----------------------------------------

def test_backtest_replays_recorded_hands(empty):
    from tools.backtest import replay

    rows = [{
        "player_card_1": "AS", "player_card_2": "KD",
        "flop_card_1": "2C", "flop_card_2": "7H", "flop_card_3": "9D",
        "turn_card": "3S", "river_card": "4H",
        "dealer_card_1": "8C", "dealer_card_2": "8H",
        "player_hand": "High Card", "dealer_hand": "Pair", "winner": "Dealer",
    }]
    empty["flop"]["rules"] = [scenarios.flop_rule("High Card", NO_DRAW, scenarios.FOLD)]

    tally = replay(empty, rows)
    assert tally["played"] == 1
    assert tally["folded"] == 1
    assert tally["folded_results"]["Dealer"] == 1


def test_backtest_counts_a_skipped_round(empty):
    from tools.backtest import replay

    row = {
        "player_card_1": "AS", "player_card_2": "KD",
        "flop_card_1": "2C", "flop_card_2": "7H", "flop_card_3": "9D",
        "turn_card": "3S", "river_card": "4H",
        "dealer_card_1": "8C", "dealer_card_2": "8H",
        "player_hand": "High Card", "dealer_hand": "Pair", "winner": "Dealer",
    }
    empty["preround"]["rules"] = [
        scenarios.preround_rule(scenarios.ANY, "High Card", scenarios.SKIP)
    ]
    tally = replay(empty, [row, dict(row)])
    assert tally["played"] == 1        # the first round has no previous hand
    assert tally["skipped"] == 1
