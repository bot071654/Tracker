"""Deciding what is worth saying, from what the tracker already emitted.

This is an event CONSUMER. It runs no recognition of its own, keeps no
round id of its own, and evaluates no hand of its own. Every fact it speaks
arrived in a tracker payload that the window was already showing.

WHY IT IS SAFE TO SPEAK WHAT IT SPEAKS

The tracker polls about five times a second, so the same card is in the
payload dozens of times. Two things stop that becoming a stammer:

  * a card is only spoken once its status is CONFIRMED or HELD - the same
    gate the Scenario Engine uses (scenario_engine.ACCEPTED_STATUSES), which
    is itself downstream of CardMemory, RANK_MARGIN, SUIT_MARGIN and
    CORROBORATED_READING. Nothing here bypasses or re-tunes any of that.
  * every announcement carries a key of (round_id, event, value), and the
    announcer refuses a key it has already said this round.

So "Player Pair" seen on forty consecutive polls is said once, and a card
revised by CardMemory mid-round is announced under a new key rather than
suppressed - a revision is news.

WHAT IS ANNOUNCED

    Only when `announce_cards` is on. The window turns it off - the voice
    announces the decision on the banner and nothing else, because a
    commentary on every card talks over the one thing worth hearing. See
    config/settings.py, voice_announce_cards.

    new round        nothing said; the announcer is told to forget the last
    player cards     once both hole cards are settled
    flop             once all three are settled
    turn / river     once settled
    player's hand    when it changes, from the flop onwards
    winner           once, from the stored record - the same row as the
                     database gets, so the voice cannot disagree with it
"""

import logging

from poker.scenario_engine import ACCEPTED_STATUSES
from voice import phrasing
from voice.announcer import FINAL, TRANSIENT

logger = logging.getLogger(__name__)

PLAYER_SLOTS = ["player_1", "player_2"]
FLOP_SLOTS = ["flop_1", "flop_2", "flop_3"]

# The states in which a hand is worth announcing. Before the flop there is no
# hand to speak of, and COMPLETE is covered by the winner.
HAND_STATES = ("FLOP", "TURN", "RIVER")


def settled(payload, slots):
    """The cards in `slots`, but only if the tracker has settled every one.

    Returns None otherwise - which is the answer while a card is still
    arriving, and the reason the voice does not read out a card that is about
    to be revised.
    """
    cards = payload.get("cards") or {}
    statuses = payload.get("statuses") or {}
    chosen = []
    for slot in slots:
        card = cards.get(slot)
        if not card or statuses.get(slot) not in ACCEPTED_STATUSES:
            return None
        chosen.append(card)
    return chosen


class VoiceEvents:
    """Feeds an Announcer from the tracker's own updates.

    Holds no game state: the only thing it remembers between updates is which
    round it last told the announcer about, so that a new round resets the
    announcer's memory of what it has said.
    """

    def __init__(self, announcer, announce_cards=True):
        self.announcer = announcer
        # Whether to read the table out as well as the decision. The window
        # passes config["voice_announce_cards"], which is off: the voice is
        # there to say what the banner says, and a running commentary on every
        # card talks over it. The default here stays True so that the tests
        # below, which are about the reading itself, keep exercising it.
        self.announce_cards = bool(announce_cards)
        self._round_id = None

    # -- the tracker's "update" event -----------------------------------------

    def observe(self, payload, progress=None):
        """One tracker update. Cheap, non-blocking, and never raises.

        Called from the window's event loop, not from the tracker thread, so
        even the queueing happens off the recognition path.

        `progress` is hand_record.hands_so_far for this update - the window
        has already computed it for the Player Hand line, so it is passed in
        rather than worked out again here. The voice evaluates nothing.
        """
        if not self.announcer.enabled or not self.announce_cards:
            return
        try:
            self._observe(payload, progress or {})
        except Exception as exc:            # noqa: BLE001 - voice must not break the UI
            logger.warning("[VOICE] could not read an update: %s", exc)

    def _observe(self, payload, progress):
        round_id = payload.get("round_id")
        if round_id != self._round_id:
            self._round_id = round_id
            self.announcer.set_round(round_id)

        say = self._say(round_id)

        cards = settled(payload, PLAYER_SLOTS)
        if cards:
            say("player", cards, "Your cards: %s" % phrasing.say_cards(cards))

        cards = settled(payload, FLOP_SLOTS)
        if cards:
            say("flop", cards, "Flop: %s" % phrasing.say_cards(cards))

        for slot, label in (("turn", "Turn"), ("river", "River")):
            cards = settled(payload, [slot])
            if cards:
                say(slot, cards, "%s: %s" % (label, phrasing.say_card(cards[0])))

        self._announce_hand(payload, progress, say)

    def _announce_hand(self, payload, progress, say):
        """The player's hand, as the window is already showing it.

        Taken from the tracker's own evaluation of the table, which is
        hand_record.hands_so_far - the same function the Player Hand line
        uses. The voice does not evaluate anything.
        """
        if payload.get("state") not in HAND_STATES:
            return
        hand = (progress or {}).get("player_hand")
        if hand:
            say("hand", hand, phrasing.say_hand(hand))

    def _say(self, round_id):
        """A helper that builds the (round, event, value) key and queues."""
        def announce(event, value, text, kind=TRANSIENT):
            key = (round_id, event, tuple(value) if isinstance(value, list) else value)
            self.announcer.announce(text, key=key, round_id=round_id, kind=kind)
        return announce

    # -- the tracker's "saved" event ------------------------------------------

    def announce_result(self, record):
        """The finished round, from the row that was stored.

        FINAL, so it survives a backlog and is still spoken if the round has
        already moved on - the result is the one thing worth hearing late.
        """
        if not self.announcer.enabled or not record:
            return
        if not self.announce_cards:
            return
        try:
            text = phrasing.say_winner(
                record.get("winner"),
                hand=self._winning_hand(record),
                qualified=record.get("dealer_qualified"))
            if not text:
                return
            # Keyed on the fingerprint: the hand's own identity, already
            # unique in the database, so a result cannot be said twice even if
            # the record arrives again.
            self.announcer.announce(
                text, key=("result", record.get("hand_fingerprint")),
                round_id=self._round_id, kind=FINAL)
        except Exception as exc:            # noqa: BLE001
            logger.warning("[VOICE] could not announce the result: %s", exc)

    @staticmethod
    def _winning_hand(record):
        """The winner's own hand name, or None for a tie."""
        winner = record.get("winner")
        if winner == "Player":
            return record.get("player_hand")
        if winner == "Dealer":
            return record.get("dealer_hand")
        return None
