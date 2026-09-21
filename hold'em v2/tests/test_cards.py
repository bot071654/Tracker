"""Card parsing and recognition-output tests."""

import pytest

from poker.hand_evaluator import (
    ALL_CARDS, InvalidCardError, card_name, is_valid_card, normalize_card, parse_card,
)


def test_all_52_cards_are_unique():
    assert len(ALL_CARDS) == 52
    assert len(set(ALL_CARDS)) == 52


@pytest.mark.parametrize("card,rank,suit", [
    ("8D", "8", "D"),
    ("QH", "Q", "H"),
    ("9C", "9", "C"),
    ("AS", "A", "S"),
    ("10H", "10", "H"),
])
def test_parse_card(card, rank, suit):
    assert parse_card(card) == (rank, suit)


def test_parse_is_case_insensitive_and_trims():
    assert parse_card(" qh ") == ("Q", "H")


def test_ten_shorthand_normalises():
    assert normalize_card("TD") == "10D"
    assert normalize_card("10d") == "10D"


@pytest.mark.parametrize("bad", ["", "X", "1S", "8X", "ZZ", "S8", None, 8])
def test_invalid_cards_are_rejected(bad):
    assert is_valid_card(bad) is False
    with pytest.raises(InvalidCardError):
        parse_card(bad)


def test_card_name():
    assert card_name("8D") == "Eight of Diamonds"
    assert card_name("QH") == "Queen of Hearts"


# -- recognition output shape -------------------------------------------------

fake_cards = pytest.importorskip("fake_cards")
cv2 = pytest.importorskip("cv2")

from recognition.card_detector import card_present  # noqa: E402
from recognition.card_recognizer import read_slot, recognize_card  # noqa: E402

pytestmark = pytest.mark.skipif(
    not fake_cards.fonts_available(), reason="no system fonts to draw test cards with"
)


def test_recognition_result_has_the_documented_shape():
    result = recognize_card(fake_cards.render_card("8D"))
    assert result["rank"] == "8"
    assert result["suit"] == "D"
    assert result["card"] == "8D"
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["confident"], bool)


@pytest.mark.parametrize("card", ["AS", "10H", "QD", "2C", "KS", "7H", "JC", "9D"])
def test_recognises_a_spread_of_cards(card):
    assert recognize_card(fake_cards.render_card(card))["card"] == card


@pytest.mark.parametrize("layout", ["side", "stacked"])
def test_recognises_every_card_in_the_deck(layout):
    """Both printed layouts: rank beside the suit, and rank above it."""
    wrong = [
        card for card in ALL_CARDS
        if recognize_card(fake_cards.render_card(card, layout=layout))["card"] != card
    ]
    assert wrong == []


def test_ten_is_not_mistaken_for_a_rank_beside_a_suit():
    """The two digits of a "10" must not read as a rank plus a suit."""
    for card in ["10S", "10H", "10D", "10C"]:
        for layout in ("side", "stacked"):
            assert recognize_card(
                fake_cards.render_card(card, layout=layout))["card"] == card


def test_recognition_survives_smaller_cards():
    image = fake_cards.render_card("QH", width=72, height=100)
    assert recognize_card(image)["card"] == "QH"


def test_empty_region_is_not_a_card():
    present, ratio = card_present(fake_cards.render_empty())
    assert present is False
    assert ratio < 0.35
    assert read_slot(fake_cards.render_empty())["card"] is None


def test_face_down_card_is_not_read_as_a_card():
    assert read_slot(fake_cards.render_card_back())["present"] is False


def test_face_up_card_is_detected_as_present():
    read = read_slot(fake_cards.render_card("3H"))
    assert read["present"] is True
    assert read["card"] == "3H"
    assert read["confident"] is True


def test_missing_image_is_handled():
    read = read_slot(None)
    assert read["present"] is False
    assert read["card"] is None


def test_low_confidence_is_reported_not_raised():
    # A threshold of 1.0 can never be met, so the card must come back unconfident.
    read = read_slot(fake_cards.render_card("5S"), confidence_threshold=1.0)
    assert read["present"] is True
    assert read["confident"] is False
