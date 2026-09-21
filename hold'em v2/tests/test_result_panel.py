"""Reading the Dealer/Player result panels as a second opinion.

The cards in the middle of the table are gone within a couple of seconds. The
panels beside it show the same hand as five thumbnails and stay up far longer,
so a round that was missed in the middle can still be checked at the edge.

These thumbnails are about 33x47 pixels and were unreadable until the cause was
found: they are printed stacked - rank above suit - with the suit running to
about nine tenths of the card, and the stacked strip was being cut at 0.72,
taking the foot off every pip. Enlarging the crop did not help and never could;
nothing was missing from the pixels, only from the strip. With a taller strip
offered as an extra reading, all ten thumbnails on the sample frame read
correctly.

Ground truth here is the hand the existing screenshot test already asserts:

    player best five   8S 9C QH 3D 3H
    dealer best five   QD QH 3D 3H 8D
"""

import glob
import os

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from poker.hand_record import build_hand_record  # noqa: E402
from recognition import result_panel  # noqa: E402
from recognition.card_recognizer import read_slot  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))

pytestmark = pytest.mark.skipif(
    not SCREENSHOTS, reason="no casino screenshot in the project folder")

PLAYER_FIVE = {"8S", "9C", "QH", "3D", "3H"}
DEALER_FIVE = {"QD", "QH", "3D", "3H", "8D"}

casino_card = pytest.importorskip("test_tight_index").casino_card


@pytest.fixture(scope="module")
def frame():
    image = cv2.imread(SCREENSHOTS[0])
    assert image is not None
    return image


# -- finding the panels -------------------------------------------------------

def test_both_panels_are_found(frame):
    found = result_panel.locate(frame)
    assert set(found) == {"player", "dealer"}
    assert len(found["player"]) == 5
    assert len(found["dealer"]) == 5


def test_the_thumbnails_are_separate_cards_not_one_block(frame):
    """Five boxes, side by side, none overlapping the next."""
    row = result_panel.locate(frame)["player"]
    lefts = [box[0] for box in row]
    assert lefts == sorted(lefts), "the row is not ordered left to right"
    for before, after in zip(row, row[1:]):
        assert before[0] + before[2] <= after[0] + 2, "two thumbnails overlap"
    widths = [box[2] for box in row]
    assert max(widths) - min(widths) <= 3, "the thumbnails are not one size"


def test_the_panels_are_not_the_table(frame):
    """The table's cards are three times the size and must not be picked up."""
    for row in result_panel.locate(frame).values():
        for box in row:
            assert box[3] < 70, "a table card was read as a thumbnail"


def test_the_dealer_panel_is_the_one_above(frame):
    found = result_panel.locate(frame)
    assert found["dealer"][0][1] < found["player"][0][1]


def test_no_panel_on_an_empty_screen():
    """Absence is the normal state for most of a round, not an error."""
    assert result_panel.locate(np.full((600, 900, 3), 40, np.uint8)) == {}
    assert result_panel.read_panels(np.full((600, 900, 3), 40, np.uint8)) == {}


# -- reading them -------------------------------------------------------------

def test_the_player_panel_reads_all_five(frame):
    player = result_panel.read_panels(frame)["player"]
    assert player["complete"]
    assert set(player["cards"]) == PLAYER_FIVE


def test_the_dealer_panel_reads_all_five(frame):
    dealer = result_panel.read_panels(frame)["dealer"]
    assert dealer["complete"]
    assert set(dealer["cards"]) == DEALER_FIVE


def test_a_club_and_a_spade_are_told_apart_in_the_panel(frame):
    """The pair that has caused trouble on the table, at a third of the size."""
    cards = result_panel.read_panels(frame)["player"]["cards"]
    assert "9C" in cards, "the club was lost"
    assert "8S" in cards, "the spade was lost"


def test_every_thumbnail_carries_its_own_confidence(frame):
    for side in ("player", "dealer"):
        for read in result_panel.read_panels(frame)[side]["reads"]:
            assert 0.0 <= read["confidence"] <= 1.0
            assert read["confident"], read


def test_regions_come_back_in_screen_coordinates(frame):
    plain = result_panel.read_panels(frame)["player"]["reads"][0]["region"]
    shifted = result_panel.read_panels(frame, origin=(1920, 0))["player"]["reads"][0]["region"]
    assert shifted["left"] == plain["left"] + 1920


# -- checking one against the other -------------------------------------------

def test_a_panel_that_agrees_is_verified(frame):
    from tests.test_screenshot import EXPECTED

    record = build_hand_record(EXPECTED)
    for side, five in (("player", record["player_best_five"]),
                       ("dealer", record["dealer_best_five"])):
        cards = result_panel.read_panels(frame)[side]["cards"]
        verdict, message = result_panel.compare(side, cards, five)
        assert verdict == "verified", message


def test_a_panel_that_disagrees_is_a_conflict():
    verdict, message = result_panel.compare(
        "player", ["3D", "3H", "QH", "9C", "8S"],
        ["3D", "3H", "QH", "9C", "8C"])          # a club where a spade was read
    assert verdict == "conflict"
    assert "8S" in message and "8C" in message


def test_a_half_read_panel_claims_nothing():
    verdict, message = result_panel.compare(
        "player", ["3D", None, "QH", None, "8S"], ["3D", "3H", "QH", "9C", "8S"])
    assert verdict == "unknown"
    assert "3 of 5" in message


def test_order_does_not_matter():
    """The casino lists its best five in its own order; a hand is a set."""
    verdict, _ = result_panel.compare(
        "player", ["8S", "9C", "QH", "3D", "3H"], ["3D", "3H", "QH", "9C", "8S"])
    assert verdict == "verified"


# -- the cards that have caused trouble ---------------------------------------

@pytest.mark.parametrize("card", ["10C", "10S", "10H", "10D"])
def test_the_tens_still_read_on_a_full_size_card(card):
    """The extra strip must not disturb the rank that splits awkwardly."""
    assert read_slot(casino_card(card))["card"] == card


@pytest.mark.parametrize("card", ["3C", "3S", "KC", "9C", "8S"])
def test_the_black_cards_still_read_on_a_full_size_card(card):
    assert read_slot(casino_card(card))["card"] == card
