"""Saying why a slot reads the way it does.

A dash in the card table can mean four different things, and until now they
looked identical:

    nothing is there
    something is there but could not be read
    it was read, and refused because two suits of one colour were too close
    it is unreadable right now but a settled value is remembered

The third is the one worth knowing about. A live six of clubs showed as a dash
and there was no way to tell whether the card had been missed or read into two
minds - the rejection was logged at debug level, and logging runs at INFO, so
it had never once been emitted.

This reports; it does not decide. What the tracker uses is still settled by
CardMemory and the confidence thresholds.
"""

import queue

import pytest

import tracker as tracker_module
from tracker import (
    AMBIGUOUS, CONFIRMED, CONFIRMING, EMPTY, HELD, UNKNOWN, card_status,
)
from recognition.card_recognizer import SUIT_MARGIN


def read(card=None, confidence=0.0, confident=False, present=True, margin=0.30,
         rank_margin=0.30):
    return {"present": present, "card": card, "confidence": confidence,
            "confident": confident, "ratio": 0.6, "suit_margin": margin,
            "rank_margin": rank_margin}


# -- the four meanings of a dash ---------------------------------------------

def test_an_empty_place_is_empty():
    status, why = card_status(read(present=False))
    assert status == EMPTY
    assert "nothing" in why


def test_a_missing_image_is_empty():
    assert card_status(None)[0] == EMPTY


def test_something_unreadable_is_unknown():
    status, why = card_status(read(card=None, confidence=0.2))
    assert status == UNKNOWN
    assert "not readable" in why


def test_a_suit_too_close_to_call_is_ambiguous():
    """The six of clubs case: read, but the two black suits were level."""
    status, why = card_status(read(card="6C", confidence=0.66, margin=0.004))
    assert status == AMBIGUOUS
    assert "6C" in why and "0.004" in why


def test_a_card_kept_from_memory_says_so():
    status, why = card_status(read(present=False), support=(1.8, 2, 0.9))
    assert status == HELD
    assert "2" in why


def test_a_card_unreadable_this_poll_but_known_is_held():
    status, _ = card_status(read(card=None, confidence=0.1), support=(1.8, 2, 0.9))
    assert status == HELD


# -- a card earning its place ------------------------------------------------

def test_a_first_confident_reading_is_only_confirming():
    """One frame is a sighting, not a confirmation."""
    status, why = card_status(read("6C", 0.88, True), support=(0.88, 1, 0.88))
    assert status == CONFIRMING
    assert "not yet seen twice" in why


def test_a_repeated_reading_is_confirmed():
    status, why = card_status(read("6C", 0.91, True), support=(2.6, 3, 0.91))
    assert status == CONFIRMED
    assert "seen 3 times" in why


def test_the_sequence_a_card_goes_through():
    """Frame by frame, as the prompt describes it."""
    assert card_status(read("6C", 0.82, True), (0.82, 1, 0.82))[0] == CONFIRMING
    assert card_status(read("6C", 0.91, True), (1.73, 2, 0.91))[0] == CONFIRMED
    assert card_status(read(present=False), (1.73, 2, 0.91))[0] == HELD
    assert card_status(read("6C", 0.89, True), (2.62, 3, 0.91))[0] == CONFIRMED


def test_ambiguity_is_reported_even_with_a_high_score():
    """Scoring well and being decidable are different questions."""
    status, _ = card_status(read(card="10S", confidence=0.72, margin=0.02))
    assert status == AMBIGUOUS


def test_a_comfortable_margin_is_not_ambiguous():
    status, _ = card_status(read(card="10S", confidence=0.55,
                                 margin=SUIT_MARGIN + 0.1))
    assert status == UNKNOWN, "low confidence is not the same as ambiguity"


# -- what the tracker logs ----------------------------------------------------

@pytest.fixture()
def tracker():
    return tracker_module.Tracker({"monitor": 1}, queue.Queue())


def test_an_ambiguous_slot_is_logged_once(tracker, caplog):
    reads = {slot: read(present=False) for slot in tracker_module.CARD_SLOTS}
    reads["flop_1"] = read(card="6C", confidence=0.66, margin=0.004)

    with caplog.at_level("INFO"):
        tracker._note_statuses(reads)
        tracker._note_statuses(reads)          # unchanged: must not repeat
    ambiguous = [r for r in caplog.records if "AMBIGUOUS" in r.message]
    assert len(ambiguous) == 1, [r.message for r in ambiguous]
    assert "6C" in ambiguous[0].message


def test_a_slot_that_recovers_stops_being_logged(tracker, caplog):
    reads = {slot: read(present=False) for slot in tracker_module.CARD_SLOTS}
    reads["flop_1"] = read(card="6C", confidence=0.66, margin=0.004)
    with caplog.at_level("INFO"):
        tracker._note_statuses(reads)
        reads["flop_1"] = read(card="6C", confidence=0.91, confident=True)
        tracker._note_statuses(reads)
        reads["flop_1"] = read(card="6C", confidence=0.66, margin=0.004)
        tracker._note_statuses(reads)
    assert len([r for r in caplog.records if "AMBIGUOUS" in r.message]) == 2


def test_a_readable_table_logs_nothing(tracker, caplog):
    reads = {slot: read("6C", 0.91, True) for slot in tracker_module.CARD_SLOTS}
    with caplog.at_level("INFO"):
        tracker._note_statuses(reads)
    assert not [r for r in caplog.records if "AMBIGUOUS" in r.message]


def test_reporting_does_not_change_what_is_read(tracker):
    """The status is commentary; the cards are decided elsewhere."""
    reads = {slot: read(present=False) for slot in tracker_module.CARD_SLOTS}
    reads["flop_1"] = read(card="6C", confidence=0.66, margin=0.004)
    before = dict(reads["flop_1"])
    tracker._note_statuses(reads)
    assert reads["flop_1"] == before


# -- keeping the evidence -----------------------------------------------------

def test_a_refused_card_can_be_saved_for_inspection(tmp_path, monkeypatch):
    """The failures are intermittent, so the pixels have to be kept at the
    moment they are refused or there is nothing left to look at."""
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(tracker_module, "LOG_ROOT", str(tmp_path))

    tracker = tracker_module.Tracker(
        {"monitor": 1, "debug_save_failures": True}, queue.Queue())
    image = np.full((40, 30, 3), 200, dtype="uint8")
    reads = {slot: read(present=False) for slot in tracker_module.CARD_SLOTS}
    reads["flop_1"] = read(card="6C", confidence=0.66, margin=0.004)

    tracker._note_statuses(reads, {"flop_1": image})
    saved = list((tmp_path / "logs" / "failures").glob("*.png"))
    assert len(saved) == 1, saved
    assert "flop_1" in saved[0].name and "ambiguous" in saved[0].name


def test_nothing_is_written_when_the_flag_is_off(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(tracker_module, "LOG_ROOT", str(tmp_path))

    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    reads = {slot: read(present=False) for slot in tracker_module.CARD_SLOTS}
    reads["flop_1"] = read(card="6C", confidence=0.66, margin=0.004)
    tracker._note_statuses(reads, {"flop_1": np.zeros((40, 30, 3), "uint8")})
    assert not (tmp_path / "logs" / "failures").exists()


def test_a_readable_card_is_never_saved(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(tracker_module, "LOG_ROOT", str(tmp_path))

    tracker = tracker_module.Tracker(
        {"monitor": 1, "debug_save_failures": True}, queue.Queue())
    reads = {slot: read("6C", 0.91, True) for slot in tracker_module.CARD_SLOTS}
    tracker._note_statuses(reads, {"flop_1": np.zeros((40, 30, 3), "uint8")})
    assert not (tmp_path / "logs" / "failures").exists()


# -- live verification, 2026-09-17 ---------------------------------------------

def test_a_confident_read_of_a_different_card_does_not_confirm_the_remembered_one():
    # 11:43:54: memory still held the last hand's KC (3 readings) while this
    # poll read 10H at 0.86. The slot was reported CONFIRMED for KC.
    status, why = card_status(read("10H", 0.864, True), (2.7, 3, 0.93), card="KC")
    assert status == CONFIRMING and "KC" in why and "10H" in why
    assert card_status(read("KC", 0.9, True), (2.7, 3, 0.93), card="KC")[0] == CONFIRMED


def test_a_single_sighting_is_never_held():
    # 11:37:51: river AS read once at 0.64 on an emptying table, then shown held.
    assert card_status(read(present=False), (0.64, 1, 0.64))[0] == CONFIRMING
    assert card_status(read(card=None, confidence=0.2), (0.64, 1, 0.64))[0] == CONFIRMING


def test_two_weak_readings_do_not_confirm_a_card_being_dealt():
    # 11:22:48.98-49.23: flop_3 read AH at 0.69 then 0.64 while sliding into
    # place, then settled as 6H at 0.91.
    assert card_status(read("AH", 0.64, True), (1.33, 2, 0.69), card="AH")[0] == CONFIRMING
    assert card_status(read(present=False), (1.33, 2, 0.69), card="AH")[0] == CONFIRMING
    # Nor does a third. The ace was never there: a card sliding past the box
    # is read weakly and read repeatedly, so counting readings without asking
    # how good any of them were confirms the card that is passing. The six of
    # hearts that really was there reads at 0.91 and settles on two.
    assert card_status(read("AH", 0.66, True), (1.99, 3, 0.69), card="AH")[0] == CONFIRMING
    assert card_status(read("6H", 0.91, True), (1.55, 2, 0.91), card="6H")[0] == CONFIRMED


def test_a_third_reading_stands_in_for_strength_but_not_for_all_of_it():
    # Three readings still confirm a card, so long as one of them rose above
    # CORROBORATED_READING. Over session 20260917_120408 every card that was
    # really there peaked at 0.818 or better, and the only two wrong cards
    # that lasted three polls peaked at 0.705 and 0.672.
    weak, good = (1.99, 3, 0.705), (1.99, 3, 0.818)
    assert card_status(read("KS", 0.705, True), weak, card="KS")[0] == CONFIRMING
    assert card_status(read("2S", 0.818, True), good, card="2S")[0] == CONFIRMED
    # and a card kept from memory follows the same rule
    assert card_status(read(present=False), weak, card="KS")[0] == CONFIRMING
    assert card_status(read(present=False), good, card="2S")[0] == HELD


def test_a_rank_too_close_to_call_is_ambiguous_not_unknown():
    """The suit has always been guarded this way; the rank had nothing.

    A nine read as a queen at 0.79 with the nine right behind it is not a
    queen, and saying so is different from saying the glyph matched nothing.
    """
    status, why = card_status(read("QC", 0.79, False, rank_margin=0.02))
    assert status == AMBIGUOUS
    assert "next rank" in why and "QC" in why
    # the suit keeps its own wording
    status, why = card_status(read("QC", 0.79, False, margin=0.01))
    assert status == AMBIGUOUS and "other suit of its colour" in why


def test_weak_cards_keep_the_scenario_waiting():
    from poker import scenario_engine as se

    info = {"card": "AH", "status": card_status(read("AH", 0.64, True), (1.33, 2, 0.69),
                                                card="AH")[0], "readings": 2}
    assert se.slot_problem("flop_3", info) is not None
