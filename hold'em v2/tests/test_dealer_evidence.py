"""The dealer's cards are held to a higher standard than the rest.

From hand #280, recorded live on 11 September 2026:

    actual table    dealer 8H 6H
    recorded        dealer 3H 6H
    evidence        3H=0.70/2   6H=0.88/3

The dealer's first card was under their hand, and an eight with its left half
covered is a three - not approximately, exactly. Template matching returned the
right answer for the pixels it was given, and 0.70 cleared the 0.62 bar that
every other card uses.

That bar is wrong for these two slots. Measured over 200 recorded hands:

    slot       avg confidence   avg readings
    dealer_1   0.85              2.4
    dealer_2   0.85              2.4
    flop_1     0.90             54.2
    player_1   0.91             59.8

Every other card is seen dozens of times and converges. The dealer's are face
up for a second or two, usually with a hand still over them, and get about two
looks - so a single marginal frame becomes the stored answer. And a wrong
dealer card changes the winner, so it is the most expensive card to get wrong.
"""

import queue

import pytest

import tracker as tracker_module
from tracker import Tracker

FULL = {
    "player_1": "5D", "player_2": "KC",
    "flop_1": "7D", "flop_2": "6S", "flop_3": "8S",
    "turn": "9D", "river": "AS",
    "dealer_1": "8H", "dealer_2": "6H",
}

CONFIG = {"monitor": 1, "latch_cards": True, "stable_frames": 2}


@pytest.fixture()
def tracker():
    return Tracker(dict(CONFIG), queue.Queue())


def support(tracker, slot, card, best, total, readings=2):
    """Put a reading into memory the way the tracker's own polls would."""
    tracker.memory.cards[slot] = card
    tracker.memory.votes.setdefault(slot, {})[card] = [total, readings, best]


def reads_for(cards, confidence=0.9):
    return {slot: {"present": bool(card), "card": card, "confidence": confidence,
                   "confident": bool(card), "ratio": 0.6}
            for slot, card in cards.items()}


# -- the hand that started this ----------------------------------------------

def test_the_8H_misread_as_3H_is_not_accepted(tracker):
    """Hand #280 exactly: 3H at 0.70 over two readings must not be stored."""
    cards = dict(FULL, dealer_1="3H")
    support(tracker, "dealer_1", "3H", best=0.70, total=1.38, readings=2)
    support(tracker, "dealer_2", "6H", best=0.88, total=2.55, readings=3)

    vetted = tracker._vet_dealer_cards(cards, reads_for(cards))
    assert vetted["dealer_1"] is None, "the false 3H was accepted"
    assert vetted["dealer_2"] == "6H", "the good dealer card was thrown away too"


def test_that_hand_does_not_reach_complete(tracker):
    """A hand is only complete when both dealer cards are trustworthy."""
    cards = dict(FULL, dealer_1="3H")
    support(tracker, "dealer_1", "3H", best=0.70, total=1.38, readings=2)
    support(tracker, "dealer_2", "6H", best=0.88, total=2.55, readings=3)

    vetted = tracker._vet_dealer_cards(cards, reads_for(cards))
    assert tracker_module.derive_state(vetted) == tracker_module.RIVER
    assert tracker_module.derive_state(cards) == tracker_module.COMPLETE, (
        "without vetting this hand would have been stored")


def test_the_rejection_is_logged_with_its_evidence(tracker, caplog):
    cards = dict(FULL, dealer_1="3H")
    support(tracker, "dealer_1", "3H", best=0.70, total=1.38, readings=2)
    support(tracker, "dealer_2", "6H", best=0.88, total=2.55, readings=3)

    with caplog.at_level("INFO"):
        tracker._vet_dealer_cards(cards, reads_for(cards))
    logged = " ".join(record.message for record in caplog.records)
    assert "dealer_1" in logged and "3H" in logged
    assert "0.70" in logged


# -- what must still get through ---------------------------------------------

def test_a_clearly_read_dealer_card_is_still_accepted(tracker):
    """The common good case: two solid looks at the showdown."""
    support(tracker, "dealer_1", "8H", best=0.90, total=1.78, readings=2)
    support(tracker, "dealer_2", "6H", best=0.88, total=1.74, readings=2)

    vetted = tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))
    assert vetted["dealer_1"] == "8H"
    assert vetted["dealer_2"] == "6H"
    assert tracker_module.derive_state(vetted) == tracker_module.COMPLETE


def test_a_card_seen_many_times_is_accepted(tracker):
    support(tracker, "dealer_1", "8H", best=0.83, total=4.10, readings=5)
    support(tracker, "dealer_2", "6H", best=0.86, total=3.40, readings=4)
    vetted = tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))
    assert vetted["dealer_1"] == "8H" and vetted["dealer_2"] == "6H"


def test_nothing_but_the_dealer_slots_is_vetted(tracker):
    """Player, flop, turn and river read correctly today and are left alone."""
    for slot in ("player_1", "flop_1", "turn", "river"):
        support(tracker, slot, FULL[slot], best=0.63, total=0.63, readings=1)
    vetted = tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))
    for slot in ("player_1", "flop_1", "turn", "river"):
        assert vetted[slot] == FULL[slot], "%s was vetted but should not be" % slot


# -- confidence and consistency, not either alone ----------------------------

def test_one_excellent_look_is_enough(tracker):
    """The dealer's cards are sometimes face up for a single poll before the
    table clears. A jack and a queen of clubs read at 0.86 and 0.88 were being
    refused for want of a second look, and the round was lost with them."""
    support(tracker, "dealer_1", "JC", best=0.86, total=0.86, readings=1)
    support(tracker, "dealer_2", "QC", best=0.88, total=0.88, readings=1)
    vetted = tracker._vet_dealer_cards(dict(FULL, dealer_1="JC", dealer_2="QC"),
                                       reads_for(FULL))
    assert vetted["dealer_1"] == "JC"
    assert vetted["dealer_2"] == "QC"


def test_one_mediocre_look_is_still_not_enough(tracker):
    """What the second look is really for: a reading that is only passable
    must be corroborated before it counts."""
    support(tracker, "dealer_1", "8H", best=0.82, total=0.82, readings=1)
    assert tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))["dealer_1"] is None


def test_many_poor_looks_are_not_enough_either(tracker):
    """Repetition does not make a bad reading good: a covered eight read as a
    three every frame would still be a three."""
    support(tracker, "dealer_1", "3H", best=0.71, total=3.55, readings=5)
    cards = dict(FULL, dealer_1="3H")
    assert tracker._vet_dealer_cards(cards, reads_for(cards))["dealer_1"] is None


@pytest.mark.parametrize("best,total,readings,accepted", [
    (0.70, 1.38, 2, False),   # hand #280
    (0.79, 1.58, 2, False),   # just short on confidence
    (0.80, 1.50, 2, True),    # exactly the bar
    (0.95, 0.95, 1, True),    # one look, but an unmistakable one
    (0.82, 0.82, 1, False),   # one look, and only a passable one
    (0.90, 1.78, 2, True),    # the ordinary good showdown
    (0.62, 1.86, 3, False),   # clears the old global bar, still refused here
])
def test_the_acceptance_bar(tracker, best, total, readings, accepted):
    support(tracker, "dealer_1", "8H", best=best, total=total, readings=readings)
    vetted = tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))
    assert (vetted["dealer_1"] == "8H") is accepted


# -- keeping a card once it has earned its place -----------------------------

def test_a_trusted_dealer_card_survives_a_poor_later_frame(tracker):
    """Memory keeps the tally, so a card that has been seen well stays seen
    well even when a hand passes over it afterwards."""
    support(tracker, "dealer_1", "8H", best=0.91, total=1.80, readings=2)
    support(tracker, "dealer_2", "6H", best=0.89, total=1.76, readings=2)
    assert tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))["dealer_1"] == "8H"

    # A later frame where the card cannot be read at all: the tally is unchanged.
    covered = dict(FULL)
    poor = reads_for(covered)
    poor["dealer_1"] = {"present": True, "card": None, "confidence": 0.2,
                        "confident": False, "ratio": 0.5}
    assert tracker._vet_dealer_cards(covered, poor)["dealer_1"] == "8H"


def test_an_empty_dealer_slot_is_left_alone(tracker):
    """Nothing read is already the safe answer; there is nothing to reject."""
    cards = dict(FULL, dealer_1=None, dealer_2=None)
    vetted = tracker._vet_dealer_cards(cards, reads_for(cards))
    assert vetted["dealer_1"] is None and vetted["dealer_2"] is None


def test_without_latching_the_frame_itself_is_the_evidence(tracker):
    """With latching off there is no tally, so the single frame has to carry
    the card on its own - which a clear reading now can, and a weak one
    still cannot."""
    tracker.config["latch_cards"] = False
    assert tracker._vet_dealer_cards(
        dict(FULL), reads_for(FULL, confidence=0.95))["dealer_1"] == "8H"
    assert tracker._vet_dealer_cards(
        dict(FULL), reads_for(FULL, confidence=0.70))["dealer_1"] is None


def test_the_misread_is_still_refused_under_the_new_rule(tracker):
    """Loosening the second-look requirement must not readmit the 8H->3H bug."""
    cards = dict(FULL, dealer_1="3H")
    support(tracker, "dealer_1", "3H", best=0.70, total=1.38, readings=2)
    assert tracker._vet_dealer_cards(cards, reads_for(cards))["dealer_1"] is None


def test_repetition_still_cannot_rescue_a_poor_reading(tracker):
    """Seen five times at 0.75 is five poor looks, not one good one."""
    support(tracker, "dealer_1", "5C", best=0.75, total=3.67, readings=5)
    cards = dict(FULL, dealer_1="5C")
    assert tracker._vet_dealer_cards(cards, reads_for(cards))["dealer_1"] is None


def test_the_bar_can_be_tuned_from_the_config(tracker):
    """So it can be loosened or tightened without touching the code."""
    support(tracker, "dealer_1", "8H", best=0.70, total=1.38, readings=2)
    assert tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))["dealer_1"] is None

    tracker.config["dealer_min_confidence"] = 0.65
    tracker.config["dealer_min_total"] = 1.30
    assert tracker._vet_dealer_cards(dict(FULL), reads_for(FULL))["dealer_1"] == "8H"
