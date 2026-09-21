"""Who won, and what the player holds as each street lands.

The showdown winner is a straight comparison of the two best hands. Whether
the dealer qualifies is recorded separately: Casino Hold'em needs the dealer to
hold a pair of fours or better, and when they do not, the ante pays and the
call is returned whichever hand is stronger.
"""

import pytest

from poker.hand_evaluator import (
    compare_hands, dealer_qualifies, evaluate_hand, evaluate_street,
)
from poker.hand_record import build_hand_record, hands_so_far

EXAMPLE = {
    "player_1": "8S", "player_2": "9C",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "dealer_1": "8D", "dealer_2": "QD",
}


# -- comparing the two hands --------------------------------------------------

def test_the_stronger_hand_wins():
    player = evaluate_hand(["8S", "9C", "2D", "QH", "3D"])          # High Card
    dealer = evaluate_hand(["8D", "QD", "2D", "QH", "3D"])          # Pair
    assert compare_hands(player, dealer) == "Dealer"
    assert compare_hands(dealer, player) == "Player"


def test_identical_hands_are_a_tie():
    board = ["2D", "QH", "3D", "3H", "6S"]
    both = evaluate_hand(["AS", "KC"] + board)
    assert compare_hands(both, both) == "Tie"


def test_playing_the_board_is_a_tie():
    """Both seats play the same five community cards."""
    board = ["AS", "KS", "QS", "JS", "10S"]                        # Royal Flush
    player = evaluate_hand(["2D", "3C"] + board)
    dealer = evaluate_hand(["4H", "5C"] + board)
    assert compare_hands(player, dealer) == "Tie"


def test_the_same_hand_name_can_still_have_a_winner():
    """Two pairs of the same rank are separated by the kicker."""
    board = ["2D", "7H", "9S", "JD", "4C"]
    player = evaluate_hand(["9H", "AC"] + board)                   # nines, ace kicker
    dealer = evaluate_hand(["9D", "KC"] + board)                   # nines, king kicker
    assert player["name"] == dealer["name"] == "Pair"
    assert compare_hands(player, dealer) == "Player"


# -- the dealer's qualifying hand ---------------------------------------------

@pytest.mark.parametrize("hole,board,qualifies", [
    (["4H", "9C"], ["4D", "2S", "7H", "JD", "KC"], True),    # pair of fours - the floor
    (["3H", "9C"], ["3D", "2S", "7H", "JD", "KC"], False),   # pair of threes
    (["2H", "9C"], ["2D", "5S", "7H", "JD", "KC"], False),   # pair of twos
    (["AH", "9C"], ["AD", "5S", "7H", "JD", "KC"], True),    # pair of aces
    (["AH", "KC"], ["2D", "5S", "7H", "JD", "9C"], False),   # ace high, no pair
    (["3H", "3C"], ["3D", "5S", "7H", "JD", "9C"], True),    # trip threes still qualify
    (["2H", "2C"], ["3D", "3S", "7H", "JD", "9C"], True),    # two pair of low cards
])
def test_dealer_qualification(hole, board, qualifies):
    assert dealer_qualifies(evaluate_hand(hole + board)) is qualifies


# -- the recorded hand --------------------------------------------------------

def test_the_example_hand_records_its_winner():
    record = build_hand_record(EXAMPLE)
    assert record["player_hand"] == "Pair"
    assert record["dealer_hand"] == "Two Pair"
    assert record["winner"] == "Dealer"
    assert record["dealer_qualified"] is True


def test_a_hand_the_player_wins():
    record = build_hand_record(dict(EXAMPLE, dealer_1="7C", dealer_2="4S"))
    assert record["winner"] == "Player"


def test_a_win_against_a_dealer_who_does_not_qualify():
    """The comparison and the qualification are recorded separately."""
    record = build_hand_record({
        "player_1": "AS", "player_2": "AD",
        "flop_1": "2C", "flop_2": "7H", "flop_3": "9S",
        "turn": "JD", "river": "KC",
        "dealer_1": "3H", "dealer_2": "5C",
    })
    assert record["winner"] == "Player"
    assert record["dealer_qualified"] is False


# -- the hand as it develops --------------------------------------------------

def test_nothing_to_report_before_the_flop():
    assert evaluate_street(["8S", "9C"], [None, None, None, None, None]) is None
    assert hands_so_far({"player_1": "8S", "player_2": "9C"})["player_hand"] is None


def test_the_player_hand_appears_at_the_flop():
    flop = {"player_1": "8S", "player_2": "9C",
            "flop_1": "2D", "flop_2": "QH", "flop_3": "3D"}
    progress = hands_so_far(flop)
    assert progress["player_hand"] == "High Card"
    assert progress["dealer_hand"] is None
    assert progress["winner"] is None


def test_the_player_hand_improves_through_the_streets():
    table = {"player_1": "8S", "player_2": "9C",
             "flop_1": "2D", "flop_2": "QH", "flop_3": "3D"}
    assert hands_so_far(table)["player_hand"] == "High Card"

    table["turn"] = "3H"
    assert hands_so_far(table)["player_hand"] == "Pair"      # the board pairs

    table["river"] = "9D"
    assert hands_so_far(table)["player_hand"] == "Two Pair"  # nines and threes


def test_the_winner_appears_only_at_the_showdown():
    table = {slot: card for slot, card in EXAMPLE.items() if not slot.startswith("dealer")}
    assert hands_so_far(table)["winner"] is None

    table.update(dealer_1="8D", dealer_2="QD")
    progress = hands_so_far(table)
    assert progress["dealer_hand"] == "Two Pair"
    assert progress["winner"] == "Dealer"
    assert progress["qualified"] is True


def test_no_winner_is_named_when_a_card_was_read_into_two_places():
    """Each seat is valid on its own, but the table as a whole cannot exist."""
    progress = hands_so_far(dict(EXAMPLE, dealer_1="8S"))   # 8S is also player_1
    assert progress["player_hand"] == "Pair"                # still worth showing
    assert progress["winner"] is None


def test_the_board_alone_is_not_reported_as_a_seat_hand():
    """Five community cards with no hole cards must not look like a hand."""
    board_only = {"flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
                  "turn": "3H", "river": "6S"}
    progress = hands_so_far(board_only)
    assert progress["player_hand"] is None
    assert progress["dealer_hand"] is None


def test_one_missing_hole_card_leaves_the_hand_unknown():
    partial = dict(EXAMPLE)
    partial["player_2"] = None
    assert hands_so_far(partial)["player_hand"] is None
    assert hands_so_far(partial)["winner"] is None
