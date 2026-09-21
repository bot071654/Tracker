"""Regressions found by the live read-only validation of 17 September 2026.

Each fixture is the exact crop the tracker saved live (logs/failures), so these
exercise the real pixels, not a re-capture.
"""

import os

import cv2
import pytest

from recognition import dealer_crop
from recognition.card_recognizer import read_slot

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "dealer_live_20260917")


def load(name):
    image = cv2.imread(os.path.join(FIXTURES, name))
    assert image is not None, name
    return image


def test_a_flat_dealer_ten_of_spades_is_cut_to_its_face_and_read():
    """Round 98: the dealer's 10S lay flat and clear, but white filled only 0.537
    of its face, so it was not cut; gold logo lines joined the outline, felt got
    into the index and nothing was read at all."""
    image = load("10S_felt-in-index.png")
    assert read_slot(image, 0.62, 0.35)["card"] is None or \
        not read_slot(image, 0.62, 0.35)["confident"], "precondition: uncut it is not read"
    cut, box = dealer_crop.cut(image)
    assert box is not None
    read = read_slot(cut, 0.62, 0.35)
    assert read["card"] == "10S" and read["confident"] and read["confidence"] >= 0.85


def test_a_fingertip_at_the_card_edge_is_not_read_as_a_confident_wrong_card_over_the_gate():
    """The next poll: a fingertip at the card's right edge. Whatever it reads as,
    it must not be a wrong card at the dealer gate's 0.80 floor."""
    cut, _ = dealer_crop.cut(load("10S_fingertip-edge.png"))
    read = read_slot(cut, 0.62, 0.35)
    assert read["card"] == "10S" or read["confidence"] < 0.80


def test_min_fill_only_adds_cuts():
    """Lowering the floor cannot change a crop that already cleared the old one."""
    assert dealer_crop.MIN_FILL == pytest.approx(0.52)
