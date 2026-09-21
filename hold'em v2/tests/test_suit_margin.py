"""Telling the two black suits apart, and admitting when it cannot be done.

A ten of clubs on the live table was recorded as a ten of spades, while the
three, king and nine of clubs on the same flop all read correctly. The suit was
not lost because clubs and spades are hard to tell apart in general - it was
lost because the ten's pip was clipped.

Colour comes from the ink and is never in doubt. Which of the two suits of that
colour it is comes down to the shape of the pip alone, and a clipped pip has
lost the shape that distinguishes them. The ten is the card that clips: two
digits push its pip hard against the edge of the index strip, where
card_detector's edge filtering trims it.

The measurements these tests are built on, taken from this casino's own cards:

    a correct reading wins its suit by at least 0.09
    a clipped pip wins by as little as 0.004 - a coin toss

So the fix is not a better template or a higher threshold. It is to notice when
the two suits are too close to call and decline, because a wrong suit is
recorded and a missing one is not.
"""

import glob
import os

import pytest

cv2 = pytest.importorskip("cv2")

from recognition.card_recognizer import (  # noqa: E402
    SUIT_MARGIN, read_slot, recognize_card,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))

casino_card = pytest.importorskip("test_tight_index").casino_card

BLACK = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


# -- the cards from the failing flop ------------------------------------------

@pytest.mark.parametrize("card", ["10C", "3C", "KC", "9C"])
def test_the_clubs_from_the_failing_flop(card):
    """10C was recorded as 10S; the other three were already correct."""
    assert recognize_card(casino_card(card))["card"] == card


@pytest.mark.parametrize("card", ["10S", "9S", "KS"])
def test_spades_are_not_broken_by_the_fix(card):
    assert recognize_card(casino_card(card))["card"] == card


def test_every_black_card_reads_correctly():
    wrong = [rank + suit for rank in BLACK for suit in "CS"
             if recognize_card(casino_card(rank + suit))["card"] != rank + suit]
    assert wrong == []


def test_red_suits_are_untouched():
    wrong = [rank + suit for rank in BLACK for suit in "HD"
             if recognize_card(casino_card(rank + suit))["card"] != rank + suit]
    assert wrong == []


# -- the margin itself --------------------------------------------------------

def test_a_clear_card_wins_its_suit_by_a_wide_margin():
    result = recognize_card(casino_card("9C"))
    assert result["suit_margin"] > SUIT_MARGIN
    assert result["confident"]


def test_every_correct_reading_clears_the_margin_comfortably():
    """The bar has to sit below every honest reading or it would reject them.

    The smallest honest margin measured here is what makes 0.05 safe; if a
    change to the detector narrows that, this test is where it shows up.
    """
    margins = [recognize_card(casino_card(rank + suit))["suit_margin"]
               for rank in BLACK for suit in "CS"]
    assert min(margins) > SUIT_MARGIN, (
        "the closest correct reading is %.3f, too near the %.2f bar"
        % (min(margins), SUIT_MARGIN))


@pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project")
def test_a_clipped_pip_is_refused_rather_than_guessed():
    """The real failure, reproduced on real artwork.

    Trimming the right edge off a real nine of clubs imitates what the ten's
    wider index does to its own pip. The suit becomes a coin toss - and must
    then be declined, not reported.
    """
    from capture.screen_capture import crop
    from tests.test_screenshot import REGIONS

    frame = cv2.imread(SCREENSHOTS[0])
    club = crop(frame, REGIONS["player_2"])          # a real 9C

    intact = read_slot(club)
    assert intact["card"] == "9C" and intact["confident"]
    assert intact["suit_margin"] > SUIT_MARGIN

    clipped = read_slot(club[:, :club.shape[1] - 12])
    assert clipped["suit_margin"] < SUIT_MARGIN, "the suit should be in doubt"
    assert not clipped["confident"], "a coin-toss suit was reported as certain"


@pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project")
def test_the_real_table_still_reads_in_full():
    """Nothing on a good frame is lost to the new bar."""
    from capture.screen_capture import crop
    from tests.test_screenshot import EXPECTED, REGIONS

    frame = cv2.imread(SCREENSHOTS[0])
    for slot, want in EXPECTED.items():
        result = read_slot(crop(frame, REGIONS[slot]))
        assert result["confident"] and result["card"] == want, slot


@pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project")
def test_a_clipped_pip_is_never_reported_as_the_other_black_suit():
    """Whatever else happens, a club must not be recorded as a spade.

    Every trim is checked, because the margin does not fall smoothly - it is
    the point at which the reading stops being trustworthy that matters, not
    the exact pixel.
    """
    from capture.screen_capture import crop
    from tests.test_screenshot import REGIONS

    frame = cv2.imread(SCREENSHOTS[0])
    club = crop(frame, REGIONS["player_2"])

    for trim in range(0, 20):
        result = read_slot(club[:, :club.shape[1] - trim] if trim else club)
        if result["confident"]:
            assert result["card"] == "9C", (
                "a trimmed club was confidently read as %s at -%dpx"
                % (result["card"], trim))


def test_the_margin_is_reported_for_diagnosis():
    """read_slot carries it out so a misread can be explained afterwards."""
    assert "suit_margin" in read_slot(casino_card("KS"))
