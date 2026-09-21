"""End-to-end test: a rendered table image, read through the real pipeline.

Builds a picture that mimics a Casino Hold'em layout (dealer on top, community
in the middle, player below), points calibrated regions at it, and checks that
the tracker reads the example hand from the brief and saves it exactly once.
"""

import numpy as np
import pytest

from capture.screen_capture import crop
from poker.hand_record import build_hand_record
from tracker import COMPLETE, derive_state, read_table

fake_cards = pytest.importorskip("fake_cards")

pytestmark = pytest.mark.skipif(
    not fake_cards.fonts_available(), reason="no system fonts to draw test cards with"
)

CARD_W, CARD_H = 96, 134

# The example table from the brief.
TABLE = {
    "dealer_1": "8D", "dealer_2": "QD",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "player_1": "8S", "player_2": "9C",
}

# Where each card sits on the fake screen (top-left corner).
POSITIONS = {
    "dealer_1": (700, 60), "dealer_2": (810, 60),
    "flop_1": (480, 330), "flop_2": (590, 330), "flop_3": (700, 330),
    "turn": (810, 330), "river": (920, 330),
    "player_1": (700, 620), "player_2": (810, 620),
}

SCREEN = (1917, 1078)



# A frame with nothing in it: the tracker looks for cards in whatever it
# captures, and finds none here, which is all these tests need.
_BLANK_FRAME = __import__("numpy").zeros((40, 60, 3), dtype="uint8")

def build_screen(cards):
    """Draw a table screenshot showing `cards` (slot -> card or None)."""
    screen = np.full((SCREEN[1], SCREEN[0], 3), (55, 90, 14), dtype=np.uint8)
    for slot, (x, y) in POSITIONS.items():
        card = cards.get(slot)
        image = (fake_cards.render_card(card, CARD_W, CARD_H) if card
                 else fake_cards.render_empty(CARD_W, CARD_H))
        screen[y:y + CARD_H, x:x + CARD_W] = image
    return screen


def regions():
    return {
        slot: {"left": x, "top": y, "width": CARD_W, "height": CARD_H}
        for slot, (x, y) in POSITIONS.items()
    }


@pytest.fixture
def config():
    return {
        "regions": regions(),
        "confidence_threshold": 0.62,
        "presence_threshold": 0.35,
        "stable_frames": 2,
        # These cards are drawn here with system fonts rather than captured
        # from a casino, and the whole table reads at 0.70-0.80 because of it -
        # dealer and player alike. The live dealer bar is set for real artwork,
        # which reads around 0.85, so it is relaxed to this table's own quality.
        # What the dealer bar itself does is covered in test_dealer_evidence.py.
        "dealer_min_confidence": 0.65,
        "dealer_min_total": 1.20,
    }


def read_screen(config, cards):
    screen = build_screen(cards)
    images = {slot: crop(screen, region) for slot, region in config["regions"].items()}
    return read_table(config, images)


def test_reads_the_example_table():
    cards, _ = read_screen(
        {"regions": regions(), "confidence_threshold": 0.62, "presence_threshold": 0.35},
        TABLE,
    )
    assert cards == TABLE


def test_example_table_is_complete_and_evaluates_correctly(config):
    cards, reads = read_screen(config, TABLE)

    assert derive_state(cards) == COMPLETE
    assert all(reads[slot]["confident"] for slot in TABLE)

    record = build_hand_record(cards)
    assert record["player_hand"] == "Pair"
    assert record["dealer_hand"] == "Two Pair"
    assert record["hand_fingerprint"] == "8S-9C-2D-QH-3D-3H-6S-8D-QD"


def test_empty_regions_are_not_read_as_cards(config):
    before_flop = {slot: TABLE[slot] for slot in ("player_1", "player_2")}
    cards, _ = read_screen(config, before_flop)

    assert cards["player_1"] == "8S"
    assert cards["player_2"] == "9C"
    assert cards["flop_1"] is None
    assert cards["turn"] is None
    assert derive_state(cards) != COMPLETE


def test_hand_is_only_complete_once_the_dealer_cards_show(config):
    without_dealer = {slot: card for slot, card in TABLE.items()
                      if not slot.startswith("dealer")}
    cards, _ = read_screen(config, without_dealer)
    assert derive_state(cards) != COMPLETE

    cards, _ = read_screen(config, TABLE)
    assert derive_state(cards) == COMPLETE


def test_repeated_reads_produce_the_same_fingerprint(config):
    first, _ = read_screen(config, TABLE)
    second, _ = read_screen(config, TABLE)
    assert (build_hand_record(first)["hand_fingerprint"]
            == build_hand_record(second)["hand_fingerprint"])


def test_tracker_saves_a_complete_hand_once(monkeypatch, config):
    """The tracker must store one row per hand, however often it sees it."""
    import queue

    import tracker as tracker_module

    inserted = []
    excel_rows = []
    stored_fingerprints = set()

    def fake_insert(record):
        fingerprint = record["hand_fingerprint"]
        if fingerprint in stored_fingerprints:
            return False, None
        stored_fingerprints.add(fingerprint)
        row = {"id": len(stored_fingerprints), "hand_fingerprint": fingerprint}
        inserted.append(row)
        return True, row

    monkeypatch.setattr(tracker_module.db, "insert_hand", fake_insert)
    monkeypatch.setattr(tracker_module.excel_export, "append_hand",
                        lambda row: excel_rows.append(row))

    screen_cards = dict(TABLE)
    # These tests drive the tracker with a stubbed read_table, so the screen is
    # never really looked at. The capture still has to be stood in for, because
    # the tracker now grabs a frame in order to find the cards in it.
    monkeypatch.setattr(tracker_module, "grab_full_screen",
                        lambda monitor=1: _BLANK_FRAME)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(
        tracker_module, "read_table",
        lambda config, images=None: read_screen(config, screen_cards),
    )

    events = queue.Queue()
    tracker = tracker_module.Tracker(config, events)

    for _ in range(5):          # the same finished hand stays on screen
        tracker._tick()

    assert len(inserted) == 1
    assert len(excel_rows) == 1
    assert inserted[0]["hand_fingerprint"] == "8S-9C-2D-QH-3D-3H-6S-8D-QD"

    # The table is cleared between hands, then a new one is dealt.
    screen_cards.clear()
    for _ in range(4):
        tracker._tick()

    screen_cards.update(dict(TABLE, player_1="AS", player_2="KD", river="7C"))
    for _ in range(3):
        tracker._tick()

    assert len(inserted) == 2
    assert len(excel_rows) == 2
    assert inserted[1]["hand_fingerprint"].startswith("AS-KD-")
