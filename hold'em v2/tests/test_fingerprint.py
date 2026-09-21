"""Fingerprint, hand-record and duplicate-detection tests."""

import pytest

from poker.hand_record import build_fingerprint, build_hand_record
from tracker import COMPLETE, FLOP, PLAYER_CARDS, RIVER, TURN, WAITING, derive_state

EXAMPLE = {
    "player_1": "8S", "player_2": "9C",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "dealer_1": "8D", "dealer_2": "QD",
}


def test_fingerprint_matches_the_documented_format():
    assert build_fingerprint(EXAMPLE) == "8S-9C-2D-QH-3D-3H-6S-8D-QD"


def test_fingerprint_is_stable_across_reads():
    assert build_fingerprint(EXAMPLE) == build_fingerprint(dict(EXAMPLE))


def test_fingerprint_normalises_notation():
    hand = dict(EXAMPLE, player_1="td")
    assert build_fingerprint(hand).startswith("10D-9C-")


def test_fingerprint_differs_when_a_card_differs():
    other = dict(EXAMPLE, river="7S")
    assert build_fingerprint(EXAMPLE) != build_fingerprint(other)


def test_incomplete_hand_has_no_fingerprint():
    incomplete = dict(EXAMPLE)
    incomplete["river"] = None
    with pytest.raises(ValueError):
        build_fingerprint(incomplete)


def test_hand_record_contains_both_evaluated_hands():
    record = build_hand_record(EXAMPLE)
    assert record["player_hand"] == "Pair"
    assert record["dealer_hand"] == "Two Pair"
    assert record["hand_fingerprint"] == "8S-9C-2D-QH-3D-3H-6S-8D-QD"


def test_duplicate_detection_is_fingerprint_equality():
    seen = {build_fingerprint(EXAMPLE)}
    assert build_fingerprint(dict(EXAMPLE)) in seen
    assert build_fingerprint(dict(EXAMPLE, turn="4H")) not in seen


# -- game state ---------------------------------------------------------------

def empty():
    return {slot: None for slot in EXAMPLE}


def test_state_waiting_when_table_is_empty():
    assert derive_state(empty()) == WAITING


def test_state_progresses_through_the_street():
    cards = empty()
    cards.update(player_1="8S", player_2="9C")
    assert derive_state(cards) == PLAYER_CARDS

    cards.update(flop_1="2D", flop_2="QH", flop_3="3D")
    assert derive_state(cards) == FLOP

    cards["turn"] = "3H"
    assert derive_state(cards) == TURN

    cards["river"] = "6S"
    assert derive_state(cards) == RIVER

    cards.update(dealer_1="8D", dealer_2="QD")
    assert derive_state(cards) == COMPLETE


def test_partial_flop_is_not_treated_as_a_flop():
    cards = empty()
    cards.update(player_1="8S", player_2="9C", flop_1="2D", flop_2="QH")
    assert derive_state(cards) == PLAYER_CARDS


def test_complete_only_when_all_nine_cards_are_known():
    cards = dict(EXAMPLE)
    cards["dealer_2"] = None
    assert derive_state(cards) != COMPLETE
    assert derive_state(EXAMPLE) == COMPLETE
