"""The dealer's cards, cut to their own face before they are read.

The fixtures are real dealer crops from a live run of twelve rounds, each one
named for the card actually on it - checked by eye, not taken from the reader.
In that run the dealer's boxes were wider than the card, the felt beside the
card was read as part of the index, and the results were a king read as a ten,
a two as a seven, correct cards held at 0.75-0.78, and one suit score falling
from 0.98 to 0.36 on a card that had not moved.

What these hold:

    the cards that were misread or held low now read correctly, high enough
    for the dealer gate
    the cards that cannot be read - a thumb over the index, a finger over the
    pip - still cannot, and nothing they produce clears the gate
    only the dealer's slots are cut; board and player cards read as before
    a dealer card lying still is not read again for video noise
"""

import glob
import os
import queue

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module  # noqa: E402
from poker.hand_evaluator import RANKS, SUITS  # noqa: E402
from recognition import dealer_crop  # noqa: E402
from recognition.card_recognizer import SUIT_MARGIN, read_slot  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "dealer_live")
needs_fixtures = pytest.mark.skipif(not glob.glob(os.path.join(FIXTURES, "*.png")),
                                    reason="no live dealer fixtures")

# The dealer gate's floor: nothing below this is shown however often it is read.
GATE_FLOOR = 0.80
GATE_ALONE = 0.85


def fixture(name):
    image = cv2.imread(os.path.join(FIXTURES, name + ".png"))
    assert image is not None, name
    return image


def dealer_read(image):
    return read_slot(dealer_crop.cut(image)[0])


# -- the failures from the live run ------------------------------------------------------

@needs_fixtures
@pytest.mark.parametrize("name,card,was", [
    ("KC_read-as-10C", "KC", "10C"),
    ("2D_read-as-7D", "2D", "7D"),
])
def test_a_rank_misread_through_the_felt_now_reads_correctly(name, card, was):
    image = fixture(name)
    before = read_slot(image)
    # Live, the uncut box read `was`. Index extraction has improved since (felt
    # outside the card is painted out), so the exact wrong answer has changed -
    # the king now reads AC unconfirmed, the two 3D - but the uncut box still
    # does not read the true card, which is the failure the dealer cut fixes.
    assert not (before["confident"] and before["card"] == card), \
        "the fixture no longer shows the live failure"
    after = dealer_read(image)
    assert after["confident"] and after["card"] == card, after
    assert after["confidence"] >= GATE_ALONE, "strong enough to be shown at once"


@needs_fixtures
@pytest.mark.parametrize("name,card,before_at_most,after_at_least", [
    ("9H_078", "9H", 0.79, GATE_ALONE),        # 0.78 -> 0.95
    ("5H_075", "5H", 0.76, GATE_FLOOR),        # 0.75 -> 0.83
    ("5H_036", "5H", 0.40, GATE_FLOOR),        # suit 0.36 -> 0.98
    ("5H_081", "5H", 0.82, GATE_FLOOR),
])
def test_a_correct_card_held_low_is_raised_over_the_gate_floor(name, card, before_at_most,
                                                               after_at_least):
    image = fixture(name)
    assert read_slot(image)["confidence"] <= before_at_most
    after = dealer_read(image)
    assert after["confident"] and after["card"] == card, after
    assert after["confidence"] >= after_at_least, after


@needs_fixtures
def test_a_five_of_hearts_reads_the_same_either_side_of_a_suit_score_collapse():
    """Two crops of one card, a moment apart: 0.81 and 0.36 as read live."""
    first = dealer_read(fixture("5H_081"))
    second = dealer_read(fixture("5H_036"))
    assert first["card"] == second["card"] == "5H"
    assert abs(first["confidence"] - second["confidence"]) < 0.05


@needs_fixtures
def test_a_suit_hidden_by_a_finger_stays_ambiguous():
    after = dealer_read(fixture("AS_ambiguous-finger"))
    assert not after["confident"]
    assert after["card"] == "AS" and after["suit_margin"] < SUIT_MARGIN


@needs_fixtures
@pytest.mark.parametrize("name", ["4H_read-as-9H-thumb", "5H_thumb-read-as-AH", "JH_read-as-JD"])
def test_what_still_cannot_be_read_never_clears_the_dealer_gate(name):
    """A thumb over the index, and the jack of hearts whose suit is still read
    as diamonds. Not fixed - but nothing they produce can be shown."""
    after = dealer_read(fixture(name))
    true_card = name.split("_")[0]
    if after["confident"] and after["card"] != true_card:
        assert after["confidence"] < GATE_FLOOR, after


# -- the cut itself -----------------------------------------------------------------------

@needs_fixtures
def test_the_cut_keeps_the_whole_card_and_drops_the_felt_beside_it():
    image = fixture("KC_read-as-10C")
    cut, box = dealer_crop.cut(image)
    assert box is not None
    x, y, width, height = box
    assert x > 10, "the felt at the left of the box was kept"
    assert cut.shape[1] < image.shape[1] and cut.shape[0] <= image.shape[0]


def test_a_sleeve_or_a_hand_is_not_cut():
    """Wider than a card, so not a card face: the box is read as it was."""
    sleeve = np.full((123, 112, 3), (40, 90, 50), np.uint8)
    sleeve[10:80, 0:112] = (245, 245, 245)                       # wide white band
    cut, box = dealer_crop.cut(sleeve)
    assert box is None and cut is sleeve


def test_nothing_white_is_not_cut():
    felt = np.full((123, 112, 3), (40, 90, 50), np.uint8)
    assert dealer_crop.cut(felt) == (felt, None)


def test_every_card_still_reads_after_the_cut():
    """All 52, drawn the casino's way, in a box with felt around them. This
    does not reproduce the live failure - drawn cards read either way - it
    checks the cut costs nothing on any rank or suit."""
    casino_card = pytest.importorskip("test_tight_index").casino_card
    wrong = []
    for card in [rank + suit for rank in RANKS for suit in SUITS]:
        box = np.full((136, 118, 3), (60, 95, 45), np.uint8)
        box[8:132, 18:113] = casino_card(card)
        read = dealer_read(box)
        if not (read["confident"] and read["card"] == card):
            wrong.append("%s -> %s %.2f" % (card, read["card"], read["confidence"]))
    assert not wrong, wrong


# -- still pictures ---------------------------------------------------------------------------

@needs_fixtures
def test_video_noise_is_still_the_same_picture():
    image = fixture("9H_078")
    noise = np.random.default_rng(1).integers(-2, 3, image.shape)
    noisy = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    assert dealer_crop.still(noisy, image)


@needs_fixtures
def test_a_hand_arriving_is_not_the_same_picture():
    image = fixture("9H_078")
    covered = image.copy()
    covered[:, :60] = (150, 178, 214)
    assert not dealer_crop.still(covered, image)
    assert not dealer_crop.still(image[:, :100], image), "a different size is a different box"


# -- in the tracker --------------------------------------------------------------------------

@needs_fixtures
def test_only_the_dealer_slots_are_cut():
    image = fixture("KC_read-as-10C")
    cards, reads = tracker_module.read_table({"monitor": 1},
                                             {"dealer_1": image, "flop_1": image})
    assert cards["dealer_1"] == "KC"
    assert reads["dealer_1"]["dealer_face"] is not None
    assert reads["flop_1"]["card"] == read_slot(image)["card"], "a board card was cut"
    assert "dealer_face" not in reads["flop_1"]


@needs_fixtures
def test_the_cut_can_be_switched_off():
    image = fixture("KC_read-as-10C")
    cards, _ = tracker_module.read_table({"monitor": 1, "dealer_face_cut": False},
                                         {"dealer_1": image})
    assert cards["dealer_1"] != "KC"


@needs_fixtures
@pytest.mark.parametrize("name,shown", [
    ("9H_078", "9H"),               # 0.95 now: shown on the first poll
    ("KC_read-as-10C", "KC"),       # 0.89
    ("4H_read-as-9H-thumb", None),  # unreadable: nothing shown
    ("JH_read-as-JD", None),        # still the wrong suit, below the floor
    ("5H_thumb-read-as-AH", None),
])
def test_the_dealer_gate_shows_strong_cards_at_once_and_nothing_false(name, shown):
    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    seen, reads = tracker._read_changed({"dealer_1": fixture(name)})
    cards = tracker._vet_dealer_cards(tracker.memory.update(seen, reads), reads)
    assert cards.get("dealer_1") == shown


@needs_fixtures
def test_a_still_dealer_card_is_not_read_again():
    image = fixture("9H_078")
    noisy = np.clip(image.astype(np.int16) + np.random.default_rng(2).integers(-2, 3, image.shape),
                    0, 255).astype(np.uint8)
    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    tracker._read_changed({"dealer_1": image, "flop_1": image})
    tracker._read_changed({"dealer_1": noisy, "flop_1": noisy})
    fresh = tracker._timing.get("fresh", set())
    assert "dealer_1" not in fresh, "a still dealer card was read again"
    assert "flop_1" in fresh, "board cards keep the exact-match rule"


@needs_fixtures
def test_a_dealer_card_that_changes_is_read_again():
    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    tracker._read_changed({"dealer_1": fixture("5H_075")})
    first = tracker._last_read["dealer_1"]["card"]
    other = cv2.resize(fixture("KC_read-as-10C"), fixture("5H_075").shape[1::-1])
    tracker._read_changed({"dealer_1": other})
    assert "dealer_1" in tracker._timing.get("fresh", set())
    assert tracker._last_read["dealer_1"]["card"] != first
