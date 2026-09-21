"""Naming the situation on the table, and counting how those situations went.

Two separate jobs, deliberately kept apart from poker/scenarios.py. That module
decides what to do, from rules the user wrote. This one only describes - it
names what is showing and reports what the recorded rounds did the last time
the table looked like that.

The figure worth reading is not the win rate when a situation applies but the
difference between that and the rate when it does not. On the recorded hands,
"A/K/Q high" wins 31% against 48% for the hands without it: a situation can be
common, easy to detect, and still be worth less than nothing.
"""

import pytest

from poker import analysis
from poker.board_features import describe_flop


def keys(cards, flop):
    return [entry["key"] for entry in analysis.detect(describe_flop(cards, flop))]


# -- naming the situation -----------------------------------------------------

def test_a_pocket_pair_is_named():
    assert "pocket_pair" in keys(["8S", "8H"], ["2D", "7C", "KS"])


def test_a_pair_made_with_a_hole_card_is_named():
    found = keys(["AS", "7H"], ["AD", "4C", "9S"])
    assert "pair_from_hole" in found
    assert "pocket_pair" not in found


def test_a_pair_only_on_the_board_is_named_differently():
    """The distinction that matters: a board pair is shared with the dealer."""
    found = keys(["AS", "KH"], ["7D", "7C", "3S"])
    assert "pair_on_board" in found
    assert "pair_from_hole" not in found
    assert "paired_board" in found


def test_two_pair_and_trips_are_named():
    assert "two_pair" in keys(["AS", "7H"], ["AD", "7C", "KS"])
    assert "three_of_a_kind" in keys(["8S", "8H"], ["8D", "4C", "KS"])


def test_a_straight_and_a_flush_are_named():
    assert "straight" in keys(["8S", "9H"], ["6D", "7C", "10S"])
    assert "flush" in keys(["AS", "7S"], ["2S", "9S", "KS"])


def test_the_draws_are_named():
    assert "four_to_flush" in keys(["AS", "7S"], ["2S", "9S", "KD"])
    assert "open_straight" in keys(["8S", "9H"], ["6D", "7C", "KS"])
    assert "gutshot" in keys(["8S", "9H"], ["6D", "KC", "10S"])


def test_the_overcards_are_named():
    assert "two_overcards" in keys(["AS", "KH"], ["4D", "7C", "9S"])
    assert "one_overcard" in keys(["AS", "6H"], ["4D", "7C", "9S"])
    found = keys(["5S", "6H"], ["8D", "10C", "QS"])
    assert "two_overcards" not in found and "one_overcard" not in found


def test_a_high_card_hand_with_a_big_card_is_named():
    assert "high_card_akq" in keys(["AS", "6H"], ["4D", "7C", "9S"])
    assert "high_card_akq" not in keys(["JS", "6H"], ["4D", "7C", "9S"])


def test_a_big_card_alongside_a_made_pair_is_not_a_high_card_hand():
    assert "high_card_akq" not in keys(["AS", "7H"], ["AD", "4C", "9S"])


def test_several_situations_can_apply_at_once():
    """A flush draw with two overcards is both, and both are worth saying."""
    found = keys(["AS", "KS"], ["2S", "9S", "4D"])
    assert "four_to_flush" in found and "two_overcards" in found


def test_the_strongest_situation_comes_first():
    found = analysis.detect(describe_flop(["AS", "KS"], ["2S", "9S", "4D"]))
    assert found[0]["key"] == "four_to_flush"
    assert [e["priority"] for e in found] == sorted(
        (e["priority"] for e in found), reverse=True)


# -- not guessing -------------------------------------------------------------

def test_an_unreadable_flop_names_nothing():
    """The point of the guard: no cards, no analysis - not a default answer."""
    assert analysis.detect(None) == []
    assert analysis.detect_from_slots({}) == []
    assert analysis.detect_from_slots({"player_1": "AS"}) == []


def test_a_partial_flop_names_nothing():
    assert analysis.detect(describe_flop(["AS", "KH"], ["4D", "7C"])) == []


def test_describe_reads_as_a_line():
    assert analysis.describe([]) == "--"
    found = analysis.detect(describe_flop(["8S", "8H"], ["2D", "7C", "KS"]))
    assert "Pocket Pair" in analysis.describe(found)


# -- counting how they went ---------------------------------------------------

def hand(player, flop, winner):
    return {"player_card_1": player[0], "player_card_2": player[1],
            "flop_card_1": flop[0], "flop_card_2": flop[1], "flop_card_3": flop[2],
            "winner": winner}


def test_a_situation_is_counted_against_the_hands_without_it():
    rows = [
        hand(["8S", "8H"], ["2D", "7C", "KS"], "Player"),   # pocket pair, won
        hand(["8S", "8D"], ["2H", "7C", "KS"], "Player"),   # pocket pair, won
        hand(["AS", "KH"], ["4D", "7C", "9S"], "Dealer"),   # no pair, lost
        hand(["QS", "JH"], ["4D", "7C", "9S"], "Dealer"),   # no pair, lost
    ]
    report = analysis.statistics(rows)
    pocket = next(e for e in report["scenarios"] if e["key"] == "pocket_pair")
    assert pocket["triggered"]["rounds"] == 2
    assert pocket["triggered"]["player_percent"] == 100.0
    assert pocket["not_triggered"]["rounds"] == 2
    assert pocket["not_triggered"]["player_percent"] == 0.0
    assert pocket["advantage"] == 100.0


def test_a_situation_that_does_not_help_shows_no_advantage():
    """The reason for measuring at all: a situation can win often and still be
    winning no more often than everything else."""
    rows = [hand(["8S", "8H"], ["2D", "7C", "KS"], "Player"),
            hand(["8C", "8D"], ["2H", "7S", "KS"], "Dealer"),
            hand(["AS", "KH"], ["4D", "7C", "9S"], "Player"),
            hand(["QS", "JH"], ["4D", "7C", "9S"], "Dealer")]
    pocket = next(e for e in analysis.statistics(rows)["scenarios"]
                  if e["key"] == "pocket_pair")
    assert pocket["triggered"]["player_percent"] == 50.0
    assert pocket["advantage"] == 0.0


def test_a_small_sample_is_flagged():
    rows = [hand(["8S", "8H"], ["2D", "7C", "KS"], "Player")]
    pocket = next(e for e in analysis.statistics(rows)["scenarios"]
                  if e["key"] == "pocket_pair")
    assert pocket["triggered"]["player_percent"] == 100.0
    assert pocket["small_sample"], "one hand at 100% must be flagged"


def test_the_sample_size_bar_can_be_moved():
    rows = [hand(["8S", "8H"], ["2D", "7C", "KS"], "Player")] * 5
    entry = next(e for e in analysis.statistics(rows, small_sample=3)["scenarios"]
                 if e["key"] == "pocket_pair")
    assert not entry["small_sample"]


def test_rounds_that_never_completed_are_not_counted():
    rows = [hand(["8S", "8H"], ["2D", "7C", "KS"], "Player"),
            hand(["8C", "8D"], ["2H", "7S", "KS"], None),
            hand(["8C", "8D"], ["2H", "7S", "KS"], "")]
    assert analysis.statistics(rows)["hands"] == 1


def test_a_hand_whose_flop_cannot_be_read_is_skipped():
    rows = [hand(["8S", "8H"], ["2D", "7C", "KS"], "Player"),
            {"player_card_1": None, "player_card_2": None, "winner": "Player"}]
    assert analysis.statistics(rows)["hands"] == 1


def test_no_hands_at_all_does_not_divide_by_zero():
    report = analysis.statistics([])
    assert report["hands"] == 0
    for entry in report["scenarios"]:
        assert entry["triggered"]["player_percent"] == 0.0
        assert entry["advantage"] == 0.0


def test_every_catalogued_situation_is_reported():
    """Even the ones that never came up, so a missing row means a missing key."""
    report = analysis.statistics([])
    assert len(report["scenarios"]) == len(analysis.CATALOGUE)
    assert {e["key"] for e in report["scenarios"]} == set(analysis.BY_KEY)


def test_the_report_is_ordered_by_precedence():
    scenarios = analysis.statistics([])["scenarios"]
    assert [e["priority"] for e in scenarios] == sorted(
        (e["priority"] for e in scenarios), reverse=True)
