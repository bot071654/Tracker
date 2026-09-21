"""A card must never be recorded in two places at once.

Nine cards come from one deck. If two slots read as the same card, one of them
was misread, so the hand must not be stored - and the tracker has to recover
rather than hold the bad value for the rest of the hand.
"""

import queue

import pytest

from config.settings import CARD_SLOTS
from poker.hand_record import build_hand_record
from tracker import CardMemory, Tracker, repeated_cards

FULL = {
    "dealer_1": "8D", "dealer_2": "QD",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "player_1": "8S", "player_2": "9C",
}

CONFIG = {
    "regions": {},
    "confidence_threshold": 0.62,
    "presence_threshold": 0.35,
    "stable_frames": 1,
    "latch_cards": True,
    "clear_frames": 3,
    "change_confirm_frames": 2,
}


def reads_for(cards, confidences=None):
    confidences = confidences or {}
    return {
        slot: {
            "present": cards.get(slot) is not None,
            "ratio": 0.6 if cards.get(slot) else 0.05,
            "card": cards.get(slot),
            "confidence": confidences.get(slot, 0.95 if cards.get(slot) else 0.0),
            "confident": cards.get(slot) is not None,
        }
        for slot in CARD_SLOTS
    }


# -- the record itself --------------------------------------------------------

def test_hand_record_rejects_the_same_card_in_two_seats():
    """The case that slipped through: a card repeated across player and dealer.

    Each seat is evaluated against the board separately, so neither evaluation
    ever sees the repeat.
    """
    hand = dict(FULL, player_2="10H", dealer_1="10H")
    with pytest.raises(ValueError) as error:
        build_hand_record(hand)
    assert "10H" in str(error.value)


def test_hand_record_rejects_a_repeat_inside_one_seat():
    with pytest.raises(ValueError):
        build_hand_record(dict(FULL, river="8S"))   # 8S is also player_1


def test_a_valid_hand_is_still_accepted():
    assert build_hand_record(FULL)["hand_fingerprint"] == "8S-9C-2D-QH-3D-3H-6S-8D-QD"


def test_repeated_cards_reports_where_the_clash_is():
    clash = repeated_cards(dict(FULL, player_2="10H", dealer_1="10H"))
    assert clash == {"10H": ["dealer_1", "player_2"]}


def test_no_clash_on_a_clean_table():
    assert repeated_cards(FULL) == {}


# A frame with nothing in it: the tracker looks for cards in whatever it
# captures, and finds none here, which is all these tests need.
_BLANK_FRAME = __import__("numpy").zeros((40, 60, 3), dtype="uint8")

# -- tracker behaviour --------------------------------------------------------

def _tracker(monkeypatch, table):
    import tracker as tracker_module

    stored = []

    def fake_insert(record):
        stored.append(record)
        return True, {"id": len(stored), "hand_fingerprint": record["hand_fingerprint"]}

    monkeypatch.setattr(tracker_module.db, "insert_hand", fake_insert)
    monkeypatch.setattr(tracker_module.excel_export, "append_hand", lambda row: True)
    # These tests drive the tracker with a stubbed read_table, so the screen is
    # never really looked at. The capture still has to be stood in for, because
    # the tracker now grabs a frame in order to find the cards in it.
    monkeypatch.setattr(tracker_module, "grab_full_screen",
                        lambda monitor=1: _BLANK_FRAME)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(
        tracker_module, "read_table",
        lambda cfg, images=None: (dict(table["cards"]),
                                  reads_for(table["cards"], table.get("confidence"))),
    )
    return Tracker(CONFIG, queue.Queue()), stored


def test_a_hand_with_a_repeated_card_is_not_stored(monkeypatch):
    table = {"cards": dict(FULL, dealer_1="10H", player_2="10H")}
    tracker, stored = _tracker(monkeypatch, table)

    for _ in range(4):
        tracker._tick()

    assert stored == []


def test_the_weaker_reading_is_dropped_so_it_can_be_read_again(monkeypatch):
    """The confident slot is kept; the doubtful one is re-read."""
    table = {
        "cards": dict(FULL, dealer_1="10H", player_2="10H"),
        "confidence": {"dealer_1": 0.95, "player_2": 0.66},
    }
    tracker, _ = _tracker(monkeypatch, table)

    for _ in range(2):
        tracker._tick()

    assert tracker.memory.cards.get("dealer_1") == "10H"
    assert "player_2" not in tracker.memory.cards


def test_the_hand_is_stored_once_the_misread_slot_reads_correctly(monkeypatch):
    table = {"cards": dict(FULL, dealer_1="10H", player_2="10H"),
             "confidence": {"dealer_1": 0.95, "player_2": 0.66}}
    tracker, stored = _tracker(monkeypatch, table)

    for _ in range(2):
        tracker._tick()
    assert stored == []

    # The dealer's hand moves away and the player card reads properly.
    table["cards"] = dict(FULL, dealer_1="10H")
    table["confidence"] = {}
    for _ in range(2):
        tracker._tick()

    assert len(stored) == 1
    assert stored[0]["hand_fingerprint"] == "8S-9C-2D-QH-3D-3H-6S-10H-QD"


def test_the_warning_is_not_repeated_every_poll(monkeypatch):
    table = {"cards": dict(FULL, dealer_1="10H", player_2="10H")}
    tracker, _ = _tracker(monkeypatch, table)

    for _ in range(6):
        tracker._tick()

    warnings = [payload for kind, payload in list(tracker.events.queue) if kind == "warning"]
    assert len(warnings) == 1
    assert "10H" in warnings[0]


def test_memory_forget_only_drops_the_named_slots():
    memory = CardMemory()
    memory.update(FULL, reads_for(FULL))
    memory.forget(["river"], "test")

    assert "river" not in memory.cards
    assert memory.cards["turn"] == "3H"
    assert len(memory.cards) == len(FULL) - 1
