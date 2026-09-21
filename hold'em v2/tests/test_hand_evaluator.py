"""Hand evaluation tests - every ranking, plus the examples from the brief."""

import pytest

from poker.hand_evaluator import (
    InvalidCardError, evaluate_hand, evaluate_showdown,
)

COMMUNITY = ["2D", "QH", "3D", "3H", "6S"]


def name_of(cards):
    return evaluate_hand(cards)["name"]


# -- the worked example from the specification --------------------------------

def test_example_player_has_a_pair():
    player, dealer = evaluate_showdown(["8S", "9C"], ["8D", "QD"], COMMUNITY)
    assert player["name"] == "Pair"
    assert dealer["name"] == "Two Pair"


def test_example_dealer_two_pair_uses_queens_and_threes():
    _, dealer = evaluate_showdown(["8S", "9C"], ["8D", "QD"], COMMUNITY)
    assert set(dealer["best_five"]) == {"QD", "QH", "3D", "3H", "8D"}


# -- every hand ranking -------------------------------------------------------

def test_high_card():
    assert name_of(["2C", "5D", "9H", "JS", "KD", "3C", "7S"]) == "High Card"


def test_pair():
    assert name_of(["8S", "9C", "2D", "QH", "3D", "3H", "6S"]) == "Pair"


def test_two_pair():
    assert name_of(["8D", "QD", "2D", "QH", "3D", "3H", "6S"]) == "Two Pair"


def test_three_of_a_kind():
    assert name_of(["3C", "3S", "3H", "8D", "QC", "2H", "9S"]) == "Three of a Kind"


def test_straight():
    assert name_of(["5C", "6D", "7H", "8S", "9D", "KC", "2H"]) == "Straight"


def test_wheel_straight_counts_ace_as_low():
    result = evaluate_hand(["AC", "2D", "3H", "4S", "5D", "KC", "9H"])
    assert result["name"] == "Straight"
    assert set(result["best_five"]) == {"AC", "2D", "3H", "4S", "5D"}


def test_flush():
    assert name_of(["2H", "5H", "9H", "JH", "KH", "3C", "7S"]) == "Flush"


def test_full_house():
    assert name_of(["3C", "3S", "3H", "8D", "8C", "2H", "9S"]) == "Full House"


def test_four_of_a_kind():
    assert name_of(["3C", "3S", "3H", "3D", "8C", "2H", "9S"]) == "Four of a Kind"


def test_straight_flush():
    assert name_of(["5H", "6H", "7H", "8H", "9H", "KC", "2S"]) == "Straight Flush"


def test_royal_flush():
    assert name_of(["10S", "JS", "QS", "KS", "AS", "2H", "7D"]) == "Royal Flush"


def test_royal_flush_beats_straight_flush():
    royal = evaluate_hand(["10S", "JS", "QS", "KS", "AS", "2H", "7D"])
    straight_flush = evaluate_hand(["9S", "10S", "JS", "QS", "KS", "2H", "7D"])
    assert royal["score"] > straight_flush["score"]


# -- picking the best five of seven -------------------------------------------

def test_picks_best_five_of_seven():
    result = evaluate_hand(["AS", "AD", "KC", "KD", "KH", "2C", "3S"])
    assert result["name"] == "Full House"
    assert set(result["best_five"]) == {"KC", "KD", "KH", "AS", "AD"}


def test_flush_beats_straight_on_the_same_board():
    result = evaluate_hand(["2H", "3H", "4H", "5H", "9H", "6C", "6D"])
    assert result["name"] == "Flush"


def test_deterministic():
    cards = ["8S", "9C"] + COMMUNITY
    assert evaluate_hand(cards)["score"] == evaluate_hand(list(reversed(cards)))["score"]


# -- input validation ---------------------------------------------------------

def test_rejects_duplicate_cards():
    with pytest.raises(InvalidCardError):
        evaluate_hand(["8S", "8S", "2D", "QH", "3D"])


def test_rejects_too_few_cards():
    with pytest.raises(InvalidCardError):
        evaluate_hand(["8S", "9C", "2D", "QH"])
