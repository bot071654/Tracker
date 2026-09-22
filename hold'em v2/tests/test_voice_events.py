"""What the voice says, from the tracker payloads it is given.

These drive VoiceEvents with payloads shaped exactly like the tracker's own
"update" event, through a real Announcer whose engine is a recording stub. So
the deduplication, the settled-card gate and the round lifecycle are the real
ones; only the speaker is a stand-in.

The point most of these make is the same one: the tracker polls about five
times a second, and the voice must not stammer.
"""

import pytest

from voice.announcer import Announcer
from voice.engines import NullEngine
from voice.events import VoiceEvents, settled

CONFIRMED, HELD, CONFIRMING = "CONFIRMED", "HELD", "CONFIRMING"

HOLE = {"player_1": "AS", "player_2": "KH"}
FLOP = {"flop_1": "7D", "flop_2": "2C", "flop_3": "10S"}


def payload(state="PLAYER_CARDS", round_id=1, cards=None, statuses=None):
    """One tracker update, as the window receives it."""
    cards = dict(cards or {})
    if statuses is None:
        statuses = {slot: CONFIRMED for slot in cards}
    return {"state": state, "round_id": round_id,
            "cards": cards, "statuses": statuses}


@pytest.fixture
def engine():
    return NullEngine()


@pytest.fixture
def voice(engine):
    made = Announcer(enabled=True, engine_factory=lambda: engine)
    yield made
    made.shutdown()


@pytest.fixture
def events(voice):
    return VoiceEvents(voice)


def spoken(voice, engine):
    assert voice.wait_until_idle(5)
    return list(engine.spoken)


# -- the settled-card gate ----------------------------------------------------

def test_a_card_is_only_read_out_once_it_has_settled():
    """The same gate the Scenario Engine uses; nothing here re-tunes it."""
    assert settled(payload(cards=HOLE), ["player_1", "player_2"]) == ["AS", "KH"]

    still_arriving = payload(cards=HOLE,
                             statuses={"player_1": CONFIRMED, "player_2": CONFIRMING})
    assert settled(still_arriving, ["player_1", "player_2"]) is None


def test_a_card_held_under_the_dealers_hand_still_counts():
    held = payload(cards=HOLE, statuses={"player_1": CONFIRMED, "player_2": HELD})
    assert settled(held, ["player_1", "player_2"]) == ["AS", "KH"]


def test_a_missing_card_is_not_settled():
    assert settled(payload(cards={"player_1": "AS"}), ["player_1", "player_2"]) is None


def test_an_unsettled_card_is_not_announced(events, voice, engine):
    events.observe(payload(cards=HOLE,
                           statuses={"player_1": CONFIRMED, "player_2": CONFIRMING}))
    assert spoken(voice, engine) == []


# -- 21: repeated polling says it once ----------------------------------------

def test_forty_identical_polls_produce_one_announcement(events, voice, engine):
    for _ in range(40):
        events.observe(payload(cards=HOLE))
    said = spoken(voice, engine)
    assert said == ["Your cards: Ace of Spades, King of Hearts"]


def test_the_hand_is_announced_once_however_often_it_is_seen(events, voice, engine):
    update = payload(state="FLOP", cards=dict(HOLE, **FLOP))
    for _ in range(30):
        events.observe(update, {"player_hand": "Pair"})
    assert spoken(voice, engine).count("pair") == 1


def test_a_changed_hand_is_announced_again(events, voice, engine):
    update = payload(state="FLOP", cards=dict(HOLE, **FLOP))
    events.observe(update, {"player_hand": "Pair"})
    events.observe(update, {"player_hand": "Two Pair"})
    said = spoken(voice, engine)
    assert "pair" in said and "two pair" in said


# -- the streets --------------------------------------------------------------

def test_the_flop_is_announced_once(events, voice, engine):
    update = payload(state="FLOP", cards=dict(HOLE, **FLOP))
    for _ in range(10):
        events.observe(update)
    said = spoken(voice, engine)
    assert said.count("Flop: 7 of Diamonds, 2 of Clubs, 10 of Spades") == 1


def test_the_turn_and_river_are_announced_once_each(events, voice, engine):
    cards = dict(HOLE, **FLOP)
    cards.update({"turn": "QD", "river": "2S"})
    for _ in range(10):
        events.observe(payload(state="RIVER", cards=cards))
    said = spoken(voice, engine)
    assert said.count("Turn: Queen of Diamonds") == 1
    assert said.count("River: 2 of Spades") == 1


def test_a_hand_is_not_announced_before_the_flop(events, voice, engine):
    events.observe(payload(state="PLAYER_CARDS", cards=HOLE),
                   {"player_hand": "High Card"})
    assert "high card" not in spoken(voice, engine)


# -- 22: the round lifecycle --------------------------------------------------

def test_a_new_round_lets_the_same_cards_be_announced_again(events, voice, engine):
    """The same two cards dealt again later is news, not a repeat."""
    events.observe(payload(round_id=1, cards=HOLE))
    assert voice.wait_until_idle(5)          # rounds are ~20s apart in practice
    events.observe(payload(round_id=2, cards=HOLE))
    said = spoken(voice, engine)
    assert said.count("Your cards: Ace of Spades, King of Hearts") == 2


def test_a_card_still_unspoken_when_the_round_ends_is_dropped(events, voice, engine):
    """The other side of it: a card from a finished round is stale, not news.

    Only reachable when the voice is behind - which is exactly when saying it
    would be wrong.
    """
    voice.pause()                            # nothing can be spoken meanwhile
    events.observe(payload(round_id=1, cards=HOLE))
    events.observe(payload(round_id=2, cards={}))
    voice.resume()
    assert spoken(voice, engine) == []


def test_the_round_id_comes_from_the_tracker(events, voice):
    """Not invented here - it is CardMemory's generation, passed through."""
    events.observe(payload(round_id=7, cards=HOLE))
    assert voice._round_id == 7


def test_round_id_churn_does_not_repeat_an_announcement(events, voice, engine):
    """The same round seen many times is still one round."""
    for _ in range(20):
        events.observe(payload(round_id=3, cards=HOLE))
    assert len(spoken(voice, engine)) == 1


# -- 23: the winner -----------------------------------------------------------

RECORD = {"winner": "Player", "player_hand": "Two Pair", "dealer_hand": "Pair",
          "dealer_qualified": True, "hand_fingerprint": "AS-KH-7D-2C-10S-QD-2S-3H-4C"}


def test_the_winner_is_announced_once(events, voice, engine):
    for _ in range(5):
        events.announce_result(RECORD)
    assert spoken(voice, engine) == ["Player wins with two pair"]


def test_the_winner_names_the_winning_seats_hand(events, voice, engine):
    events.announce_result(dict(RECORD, winner="Dealer"))
    assert spoken(voice, engine) == ["Dealer wins with pair"]


def test_a_dealer_who_did_not_qualify_is_mentioned(events, voice, engine):
    events.announce_result(dict(RECORD, dealer_qualified=False))
    assert spoken(voice, engine) == ["Player wins with two pair, dealer did not qualify"]


def test_a_tie_is_announced(events, voice, engine):
    events.announce_result(dict(RECORD, winner="Tie"))
    assert spoken(voice, engine) == ["Tie"]


def test_a_record_without_a_winner_says_nothing(events, voice, engine):
    events.announce_result(dict(RECORD, winner=None))
    assert spoken(voice, engine) == []


def test_the_result_survives_the_round_moving_on(events, voice, engine):
    """The one thing worth hearing slightly late."""
    events.observe(payload(round_id=1, cards=HOLE))
    events.announce_result(RECORD)
    events.observe(payload(round_id=2, cards={}))     # the next round begins
    assert "Player wins with two pair" in spoken(voice, engine)


# -- 15, 16, 17: the tracker is never affected --------------------------------

def test_voice_disabled_consumes_nothing_and_says_nothing(engine):
    off = Announcer(enabled=False, engine_factory=lambda: engine)
    events = VoiceEvents(off)
    for _ in range(10):
        events.observe(payload(cards=HOLE))
    events.announce_result(RECORD)
    assert engine.spoken == []
    assert off.pending() == 0
    off.shutdown()


def test_a_broken_payload_does_not_raise(events, voice, engine):
    """The window must not fall over because the voice could not read it."""
    for bad in ({}, {"cards": None}, {"cards": {"player_1": object()}},
                {"state": "FLOP", "cards": HOLE, "statuses": None}):
        events.observe(bad)
    events.announce_result({"winner": "Player"})
    assert voice.wait_until_idle(5)


def test_a_broken_record_does_not_raise(events):
    for bad in (None, {}, {"winner": object()}):
        events.announce_result(bad)


def test_observing_while_paused_says_nothing(events, voice, engine):
    voice.pause()
    for _ in range(10):
        events.observe(payload(cards=HOLE))
    assert spoken(voice, engine) == []
    # ...and the events kept being consumed without error, which is the point:
    # the window's update loop is unaffected by the voice being paused.
    assert voice.is_paused()


def test_observing_while_muted_says_nothing(events, voice, engine):
    voice.mute()
    for _ in range(10):
        events.observe(payload(cards=HOLE))
    assert spoken(voice, engine) == []
    assert voice.is_muted()
