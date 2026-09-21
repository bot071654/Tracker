"""Card memory: a card stays read while the dealer's hands pass over it."""

import pytest

from config.settings import CARD_SLOTS
from tracker import COMPLETE, READABLE, CardMemory, derive_state, vote_score

FULL = {
    "dealer_1": "8D", "dealer_2": "QD",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "player_1": "8S", "player_2": "9C",
}



# A frame with nothing in it: the tracker looks for cards in whatever it
# captures, and finds none here, which is all these tests need.
_BLANK_FRAME = __import__("numpy").zeros((40, 60, 3), dtype="uint8")

def reads_for(cards, covered=(), confidence=0.95):
    """Per-slot reads: a covered slot shows nothing at all, as when a hand is over it."""
    result = {}
    for slot in CARD_SLOTS:
        card = None if slot in covered else cards.get(slot)
        result[slot] = {
            "present": card is not None,
            "ratio": 0.6 if card else 0.05,
            "card": card,
            "confidence": confidence if card else 0.0,
            "confident": card is not None,
        }
    return result


def poll(memory, cards, covered=(), confidence=0.95):
    visible = {slot: (None if slot in covered else cards.get(slot)) for slot in CARD_SLOTS}
    return memory.update(visible, reads_for(cards, covered, confidence))


def test_card_is_remembered_while_it_is_covered():
    memory = CardMemory()
    poll(memory, FULL)
    covered = poll(memory, FULL, covered=["dealer_1", "dealer_2"])

    assert covered["dealer_1"] == "8D"
    assert covered["dealer_2"] == "QD"
    assert memory.held == {"dealer_1", "dealer_2"}


def test_hand_still_completes_when_cards_are_never_all_visible_at_once():
    """The dealer's hands sweep across; no single poll sees all nine cards."""
    memory = CardMemory()
    sweep = [["dealer_1", "dealer_2"], ["flop_1", "flop_2"], ["turn", "river"],
             ["player_1"], ["flop_3", "player_2"]]

    state = None
    for covered in sweep:
        cards = poll(memory, FULL, covered=covered)
        state = derive_state(cards)

    assert state == COMPLETE
    assert cards == FULL


def test_memory_is_dropped_once_the_table_clears():
    memory = CardMemory(clear_frames=3)
    poll(memory, FULL)

    for _ in range(2):
        cards = poll(memory, {})          # every region empty
        assert cards["player_1"] == "8S"  # not yet - could be a passing hand

    cards = poll(memory, {})
    assert cards == {}
    assert memory.cards == {}


def test_a_covered_table_is_not_mistaken_for_a_cleared_one():
    """Hands over every card still leave the memory intact for a poll or two."""
    memory = CardMemory(clear_frames=3)
    poll(memory, FULL)
    cards = poll(memory, FULL, covered=CARD_SLOTS)
    assert cards == FULL


def test_new_player_cards_start_a_new_hand():
    memory = CardMemory(confirm_frames=2)
    poll(memory, FULL)
    generation = memory.generation

    next_deal = {"player_1": "AS", "player_2": "KD"}
    cards = poll(memory, next_deal)
    assert cards["flop_1"] == "2D", "one frame is not enough to abandon the hand"

    cards = poll(memory, next_deal)
    assert cards == next_deal
    assert "flop_1" not in cards
    assert memory.generation > generation


def test_one_misread_player_card_does_not_throw_away_the_hand():
    """A flickering player card must not wipe the community cards mid-hand."""
    memory = CardMemory(confirm_frames=2)
    poll(memory, FULL)

    poll(memory, dict(FULL, player_2="10S"))    # one bad frame
    cards = poll(memory, FULL)                   # reads correctly again

    assert cards == FULL
    assert memory.generation == 0


def test_a_single_misread_cannot_overwrite_a_well_seen_card():
    """Evidence accumulates, so one odd frame cannot outweigh several good ones."""
    memory = CardMemory()
    for _ in range(3):
        poll(memory, FULL)

    cards = poll(memory, dict(FULL, river="7S"))   # one bad frame
    assert cards["river"] == "6S"

    cards = poll(memory, FULL)
    assert cards["river"] == "6S"


def test_the_reading_seen_most_often_wins():
    """A card that keeps reading one way settles that way, whatever came first."""
    memory = CardMemory()
    poll(memory, dict(FULL, river="7S"))           # first sighting is the wrong one
    for _ in range(3):
        poll(memory, FULL)

    cards = poll(memory, FULL)
    assert cards["river"] == "6S"


def test_a_persistent_different_reading_does_take_over():
    memory = CardMemory()
    poll(memory, FULL)
    for _ in range(3):
        cards = poll(memory, dict(FULL, river="7S"))
    assert cards["river"] == "7S"


def test_support_reports_the_evidence_behind_a_card():
    memory = CardMemory()
    for _ in range(4):
        poll(memory, FULL)

    total, readings, best = memory.support("river")
    assert readings == 4
    assert 0 < best <= 1.0
    assert total == pytest.approx(best * 4)
    # What those four readings are worth when the slot picks a card is how far
    # each one cleared the threshold, which is not the same number.
    assert vote_score((total, readings, best)) == pytest.approx((best - READABLE) * 4)
    assert memory.evidence("river") == "%.2f/4" % best


def test_readings_are_worth_how_far_they_cleared_the_threshold():
    assert vote_score(None) == 0.0
    assert vote_score((READABLE, 1, READABLE)) == pytest.approx(0.0)
    assert vote_score((0.95, 1, 0.95)) == pytest.approx(0.95 - READABLE)
    # five barely-readable readings are worth less than one plain sight
    assert vote_score((5 * 0.65, 5, 0.65)) < vote_score((0.96, 1, 0.96))


def test_one_plain_sight_of_a_card_outweighs_a_run_of_barely_readable_ones():
    """The deal guarantees the wrong card is seen first, and seen often.

    Live (session 20260917_120408, round 60) flop_3 read as a king of spades
    five times at 0.62-0.705 while the card was still arriving, and the two of
    spades that was really there then arrived at 0.957. Adding whole
    confidences, the king held 3.30 against the two's 0.96 and kept the slot
    for four more polls. Counting only what each reading cleared the threshold
    by, the two takes the slot on the poll it is first seen.
    """
    memory = CardMemory()
    for confidence in (0.622, 0.644, 0.654, 0.673, 0.705):
        poll(memory, dict(FULL, flop_3="KS"), confidence=confidence)
    assert memory.cards["flop_3"] == "KS"

    cards = poll(memory, dict(FULL, flop_3="2S"), confidence=0.957)
    assert cards["flop_3"] == "2S"


def test_a_card_seen_clearly_is_still_not_displaced_by_one_bad_frame():
    """The property the tally exists for, which the change must not cost."""
    memory = CardMemory()
    for _ in range(10):
        poll(memory, FULL, confidence=0.93)
    cards = poll(memory, dict(FULL, river="7S"), confidence=0.70)
    assert cards["river"] == FULL["river"]


def test_generation_changes_only_when_memory_is_cleared():
    memory = CardMemory(clear_frames=1)
    start = memory.generation
    poll(memory, FULL)
    assert memory.generation == start

    poll(memory, {})
    assert memory.generation == start + 1


@pytest.fixture
def config():
    return {
        "regions": {},
        "confidence_threshold": 0.62,
        "presence_threshold": 0.35,
        "stable_frames": 1,
        "latch_cards": True,
        "clear_frames": 2,
        "change_confirm_frames": 2,
    }


def test_tracker_saves_one_hand_then_waits_for_the_next_deal(monkeypatch, config):
    """Two deals in a row, with hands covering cards throughout, save two rows."""
    import queue

    import tracker as tracker_module

    stored = []
    monkeypatch.setattr(
        tracker_module.db, "insert_hand",
        lambda record: (True, {"id": len(stored) + 1,
                               "hand_fingerprint": stored.append(record) or
                               record["hand_fingerprint"]}),
    )
    monkeypatch.setattr(tracker_module.excel_export, "append_hand", lambda row: True)

    table = {"cards": dict(FULL), "covered": []}
    # These tests drive the tracker with a stubbed read_table, so the screen is
    # never really looked at. The capture still has to be stood in for, because
    # the tracker now grabs a frame in order to find the cards in it.
    monkeypatch.setattr(tracker_module, "grab_full_screen",
                        lambda monitor=1: _BLANK_FRAME)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(
        tracker_module, "read_table",
        lambda cfg, images=None: (
            {slot: (None if slot in table["covered"] else table["cards"].get(slot))
             for slot in CARD_SLOTS},
            reads_for(table["cards"], table["covered"]),
        ),
    )

    tracker = tracker_module.Tracker(config, queue.Queue())

    for covered in ([], ["dealer_1"], ["flop_2", "turn"], []):
        table["covered"] = covered
        tracker._tick()
    assert len(stored) == 1

    # The table clears, then a different hand is dealt.
    table["cards"] = {}
    table["covered"] = []
    for _ in range(2):
        tracker._tick()

    second = dict(FULL, player_1="AS", player_2="KD", river="7C")
    table["cards"] = second
    for covered in ([], ["river"], []):
        table["covered"] = covered
        tracker._tick()

    assert len(stored) == 2
    assert stored[1]["hand_fingerprint"].startswith("AS-KD-")
