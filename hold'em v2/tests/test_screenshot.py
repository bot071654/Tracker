"""Regression test against the real casino screenshot.

Reads the supplied 1917x1078 screenshot of the Casino Hold'em table and checks
it produces the hand from the brief. The regions live here rather than in
config.json so the test keeps working after the user recalibrates.

Skips itself if the screenshot is not in the project folder.
"""

import glob
import os

import pytest

cv2 = pytest.importorskip("cv2")

from capture.screen_capture import crop  # noqa: E402
from poker.hand_record import build_hand_record  # noqa: E402
from tracker import COMPLETE, derive_state, read_table  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))

pytestmark = pytest.mark.skipif(
    not SCREENSHOTS, reason="no casino screenshot in the project folder"
)

# Where the cards sit in that screenshot.
REGIONS = {
    "dealer_1": {"left": 854, "top": 329, "width": 95, "height": 124},
    "dealer_2": {"left": 961, "top": 329, "width": 95, "height": 124},
    "flop_1": {"left": 690, "top": 451, "width": 102, "height": 124},
    "flop_2": {"left": 794, "top": 451, "width": 102, "height": 124},
    "flop_3": {"left": 903, "top": 451, "width": 99, "height": 124},
    "turn": {"left": 1005, "top": 451, "width": 102, "height": 124},
    "river": {"left": 1114, "top": 451, "width": 103, "height": 124},
    "player_1": {"left": 845, "top": 605, "width": 93, "height": 121},
    "player_2": {"left": 958, "top": 605, "width": 92, "height": 121},
}

EXPECTED = {
    "dealer_1": "8D", "dealer_2": "QD",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "player_1": "8S", "player_2": "9C",
}

CONFIG = {
    "regions": REGIONS,
    "confidence_threshold": 0.62,
    "presence_threshold": 0.35,
    "stable_frames": 2,
}


@pytest.fixture(scope="module")
def reading():
    image = cv2.imread(SCREENSHOTS[0])
    assert image is not None, "could not read %s" % SCREENSHOTS[0]
    images = {slot: crop(image, region) for slot, region in REGIONS.items()}
    return read_table(CONFIG, images)


def test_all_nine_cards_are_read(reading):
    cards, _ = reading
    assert cards == EXPECTED


def test_every_card_is_confident(reading):
    _, reads = reading
    unsure = [slot for slot, read in reads.items() if not read["confident"]]
    assert unsure == []


def test_every_region_is_seen_as_a_card(reading):
    _, reads = reading
    assert all(read["present"] for read in reads.values())


def test_the_hand_is_complete_and_evaluates_as_in_the_brief(reading):
    cards, _ = reading
    assert derive_state(cards) == COMPLETE

    record = build_hand_record(cards)
    assert record["player_hand"] == "Pair"
    assert record["dealer_hand"] == "Two Pair"
    assert record["hand_fingerprint"] == "8S-9C-2D-QH-3D-3H-6S-8D-QD"


def test_hand_completes_while_the_dealers_hands_cover_the_cards():
    """The real failure mode: hands sweeping over the table hide cards.

    No frame in this sweep shows all nine cards, so without card memory the
    hand would never look complete.
    """
    from tracker import CardMemory

    base = cv2.imread(SCREENSHOTS[0])
    sweep = [
        ["dealer_1", "dealer_2"], ["dealer_2", "flop_1"], ["flop_1", "flop_2"],
        ["flop_2", "flop_3"], ["flop_3", "turn"], ["turn", "river"],
        ["river", "player_1"], ["player_1", "player_2"],
    ]

    def covered_screen(slots):
        image = base.copy()
        for slot in slots:
            region = REGIONS[slot]
            centre = (region["left"] + region["width"] // 2,
                      region["top"] + region["height"] // 2)
            axes = (region["width"] // 2 + 14, region["height"] // 2 + 10)
            cv2.ellipse(image, centre, axes, 12, 0, 360, (150, 178, 214), -1)
        return image

    memory = CardMemory()
    states, cards = [], {}
    for slots in sweep:
        image = covered_screen(slots)
        images = {slot: crop(image, region) for slot, region in REGIONS.items()}
        seen, reads = read_table(CONFIG, images)
        assert not all(seen.values()), "this frame should have a card covered"
        cards = memory.update(seen, reads)
        states.append(derive_state(cards))

    assert COMPLETE in states
    assert cards == EXPECTED
    assert build_hand_record(cards)["player_hand"] == "Pair"


def test_matches_the_casinos_own_result_boxes(reading):
    """The table itself shows Dealer "Two Pairs" and Player "Pair"."""
    cards, _ = reading
    record = build_hand_record(cards)
    assert set(record["dealer_best_five"]) == {"QD", "QH", "3D", "3H", "8D"}
    assert set(record["player_best_five"]) == {"8S", "9C", "QH", "3D", "3H"}
