"""The tracking loop: read the calibrated regions, follow the hand, store it.

Runs on a background thread and reports to the UI through a queue, so Tkinter
only ever touches its own widgets on the main thread.
"""

import logging
import os
import threading
import time
from datetime import datetime

import numpy

from capture.screen_capture import (
    CaptureError, crop, grab_full_screen, grab_regions, grab_regions_fast,
    monitor_origin, screen_size,
)
from config.settings import (
    CARD_SLOTS, DEFAULT_CONFIG, ROOT as LOG_ROOT, SLOT_LABELS,
)
from database import db
from export import excel_export
from poker import scenario_engine
from poker import scenarios as scenario_rules
from poker.hand_record import build_hand_record, hands_so_far
from recognition import result_panel, table_layout
from recognition.live_diagnostics import LiveDiagnostics
from recognition import dealer_crop, dealer_watch
import window_focus
from recognition.card_recognizer import (
    TemplatesMissingError, read_slot, undecided_half,
)

logger = logging.getLogger(__name__)

WAITING = "WAITING"
PLAYER_CARDS = "PLAYER_CARDS"
FLOP = "FLOP"
TURN = "TURN"
RIVER = "RIVER"
COMPLETE = "COMPLETE"

PLAYER_SLOTS = ["player_1", "player_2"]
FLOP_SLOTS = ["flop_1", "flop_2", "flop_3"]
DEALER_SLOTS = ["dealer_1", "dealer_2"]

# Wait this long before retrying a hand that failed to store (e.g. database down).
RETRY_SECONDS = 30


def _have(cards, slots):
    return all(cards.get(slot) for slot in slots)


def derive_state(cards):
    """Current game state from the set of recognised cards.

    `cards` is slot -> card string or None.
    """
    if not _have(cards, PLAYER_SLOTS):
        return WAITING
    if not _have(cards, FLOP_SLOTS):
        return PLAYER_CARDS
    if not cards.get("turn"):
        return FLOP
    if not cards.get("river"):
        return TURN
    if not _have(cards, DEALER_SLOTS):
        return RIVER
    return COMPLETE


def read_table(config, images=None, regions=None):
    """Read every card region once.

    Returns (cards, reads) where `cards` is slot -> card string or None (None
    when absent or not confidently recognised) and `reads` holds the full
    per-slot detail including confidence.

    `regions` is where to look. The tracker passes the boxes it found in the
    current frame; left out, the calibrated ones are used, which is what a
    one-off read from a saved screenshot still wants.
    """
    if regions is None:
        regions = config.get("regions") or {}
    confidence_threshold = config.get("confidence_threshold", 0.62)
    presence_threshold = config.get("presence_threshold", 0.35)

    if images is None:
        images = grab_regions_fast(
            {slot: regions[slot] for slot in CARD_SLOTS if slot in regions}
        )

    # The live diagnostics want to see how each card was read. Asking for that
    # changes no answer - checked on 1,380 images - it only keeps the working.
    detail = bool(config.get("live_diagnostics"))
    # The dealer's cards are cut to their own face before they are read; see
    # recognition/dealer_crop.py for the live evidence. No other slot is.
    dealer_cut = config.get("dealer_face_cut", True)
    cards, reads = {}, {}
    for slot in CARD_SLOTS:
        started = time.perf_counter()
        image, face = images.get(slot), None
        if dealer_cut and slot in DEALER_SLOTS and image is not None:
            image, face = dealer_crop.cut(image)
        read = read_slot(image, confidence_threshold, presence_threshold, detail=detail)
        read["ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        if slot in DEALER_SLOTS:
            read["dealer_face"] = list(face) if face else None
        reads[slot] = read
        cards[slot] = read["card"] if read["confident"] else None
    return cards, reads


# How a slot's reading stands, for the log and for Test Recognition. Reported
# rather than acted on: the tracker already decides what to use through
# CardMemory and the confidence thresholds, and this only says why.
EMPTY = "EMPTY"              # no card in that place
UNKNOWN = "UNKNOWN"          # a card is there but could not be read
AMBIGUOUS = "AMBIGUOUS"      # read, but two suits of one colour were too close
CONFIRMING = "CONFIRMING"    # read once, not yet corroborated
CONFIRMED = "CONFIRMED"      # read the same way more than once
HELD = "HELD"                # unreadable now, but a settled value is remembered

CONFIRMING_READINGS = 2      # readings that make a card confirmed rather than seen

# What "well enough matched to be worth arguing about" means. Only a reading
# that clears this is capable of being ambiguous: below it the glyph matched
# nothing in particular, and which of two suits it least badly resembled is
# not a meaningful question.
READABLE = DEFAULT_CONFIG["confidence_threshold"]

# Two readings confirm a card only if one of them was clearly good. Live, a
# card still sliding into place on the flop read AH at 0.69 and 0.64 on two
# polls - two agreeing readings, both weak - and was reported CONFIRMED half a
# second before it settled and read 6H at 0.91. Every correct card in that
# session had either a reading at this strength or a third reading.
STRONG_READING = 0.75
CORROBORATING_READINGS = 3

# A third reading stands in for some of that strength, but not for all of it.
#
# It was standing in for all of it, which is how a card that is not there gets
# confirmed: the flop deals from the left, so for a few polls the third box
# holds a card on its way past, read weakly and read repeatedly. In session
# 20260917_120408 that put a king of spades in flop_3 for five polls at
# nothing better than 0.705, and it was reported CONFIRMED - the two of spades
# that was really there had not been read yet.
#
# Over that session's 165 accepted cards the two questions separate cleanly.
# Every one of the 125 cards that was really there peaked at 0.818 or better.
# Of the 40 that were not, only two lasted three polls, peaking at 0.705 and
# 0.672. 0.72 sits in the gap: it refuses both, and refuses no real card.
CORROBORATED_READING = 0.72


def vote_score(support):
    """How much the readings behind a card are worth when a slot picks one.

    The sum of how far each reading cleared the threshold, rather than the sum
    of the confidences themselves, so that support counts as evidence rather
    than as persistence. A reading that only just cleared the threshold is
    worth almost nothing, which is what it is worth.

    Nothing extra is stored for this: summing (confidence - threshold) over
    the readings is the same as the total less the threshold once per reading,
    and the tally already holds both. `support` is CardMemory's
    (total, readings, best).
    """
    if not support:
        return 0.0
    total, readings = support[0], support[1]
    return total - READABLE * readings


def is_settled(support):
    """Whether CardMemory's (total, readings, best) is enough to confirm a card."""
    if not support:
        return False
    readings, best = support[1], support[2]
    return (readings >= CORROBORATING_READINGS and best >= CORROBORATED_READING) or (
        readings >= CONFIRMING_READINGS and best >= STRONG_READING)


# How long a result panel may disagree with the table in strength before it is
# called a conflict. The panel and the table each catch up with a new card on
# their own schedule - the table needs two agreeing polls to confirm one - so a
# difference that lasts a poll or two is one of them being behind. Two seconds
# of it is not.
PANEL_MISMATCH_POLLS = 10


def card_status(read, support=None, readable=READABLE, card=None):
    """How a slot's reading stands, as (status, note).

    `read` is what read_slot returned for this poll and `support` is
    CardMemory's (total, readings, best) for the slot, where there is one. The
    distinction that matters is between a card that could not be read and one
    that was read into two minds: the first is a gap and the second is a
    warning, and they look identical in a table full of dashes.

    Those two were being confused. The suit margin was tested before anything
    asked whether the reading was any good, so a glyph that matched its best
    template at 0.20 - which is to say, matched nothing - was reported as
    "two suits too close to call". Every one of the 2795 crops kept from one
    live session carries that label, and their median confidence is 0.35: the
    real story in almost all of them is that nothing was recognised, which is
    a different fault with a different cause.

    So ambiguity is now only claimed where it is meaningful: the reading was
    good enough to accept, and the *only* thing standing in its way was that
    one half of the card was level with what it was being chosen between -
    the two suits of its colour, or the next rank down. The note says which.

    `card` is the card memory holds for the slot, which `support` describes.
    A confident read of a *different* card is not a confirmation of the
    remembered one: live, the first poll of a new deal read 10H/3H/2D at 0.86+
    while memory still held the last hand's KC/7H/6D, and those were reported
    CONFIRMED for a poll. Nothing is confirmed, held or trusted on one reading
    or on weak ones either (see is_settled).
    """
    readings = support[1] if support else 0
    remembered = readings > 0
    settled = is_settled(support)

    if read is None or not read.get("present"):
        if settled:
            return HELD, "kept from %d earlier reading(s)" % readings
        if remembered:
            return CONFIRMING, "not there now, seen %d time(s) - not settled" % readings
        return EMPTY, "nothing there"

    if read.get("confident"):
        if card and read.get("card") and read["card"] != card:
            return CONFIRMING, "reads %s at %.2f, memory still holds %s" % (
                read["card"], read["confidence"], card)
        if settled:
            return CONFIRMED, "%s seen %d times, best %.2f" % (
                read["card"], readings, support[2])
        if readings >= CONFIRMING_READINGS:
            return CONFIRMING, "%s seen %d times but best only %.2f" % (
                read["card"], readings, support[2])
        return CONFIRMING, "%s at %.2f, not yet seen twice" % (
            read["card"], read["confidence"])

    confidence = read.get("confidence", 0.0)
    # Which half of the card - the rank or the suit - was too close to call,
    # where there was one. Either one undecided means the reading is not a
    # card, so both are reported the same way and only the wording differs.
    undecided = undecided_half(read) if read.get("card") else None
    if settled:
        # A confirmed card is not un-confirmed by one poll that could not
        # settle its suit - a hand passing over it, a blurred frame. Reporting
        # it AMBIGUOUS sent the scenario back to WAIT mid-hand while the card
        # itself had not changed. Memory still holds the card, so it is held.
        doubt = ("ambiguous this poll" if undecided and confidence >= readable
                 else "unreadable this poll")
        return HELD, "%s, kept from %d reading(s)" % (doubt, readings)
    if undecided and confidence >= readable:
        return AMBIGUOUS, "%s at %.2f, but %s" % (
            read["card"], confidence, undecided)
    if remembered:
        return CONFIRMING, "unreadable this poll, seen %d time(s) - not settled" % readings
    if read.get("card"):
        return UNKNOWN, "nothing matched well - closest was %s at %.2f" % (
            read["card"], confidence)
    return UNKNOWN, "present but not readable (%.2f)" % confidence


def uncertain_slots(reads):
    """Slots showing a card that could not be recognised confidently."""
    return [slot for slot, read in reads.items() if read["present"] and not read["confident"]]


def describe(cards):
    """One-line summary of the table, for the log."""
    return " ".join("%s=%s" % (slot, cards.get(slot) or "--") for slot in CARD_SLOTS)


def repeated_cards(cards):
    """Cards appearing in more than one slot: {card: [slots]}.

    Nine cards dealt from one deck are always different, so a repeat means one
    of those slots was misread.
    """
    places = {}
    for slot in CARD_SLOTS:
        card = cards.get(slot)
        if card:
            places.setdefault(card, []).append(slot)
    return {card: slots for card, slots in places.items() if len(slots) > 1}


def validate_against_panels(frame, record, origin=(0, 0)):
    """Cross-check a finished round against the casino's own result panels.

    The panels are found in the frame rather than at calibrated coordinates,
    so they survive the browser moving. They show the best five of the seven
    cards, which is what this app has just worked out for itself - so they can
    confirm a round or contradict it, but never supply the two hole cards.

    Returns (verdict, message): "verified", "conflict", or "unknown" when no
    panel was legible, which is the ordinary case mid-round.
    """
    panels = result_panel.read_panels(frame, origin)
    if not panels:
        return "unknown", "no result panel found in the frame"

    cards = {slot: record.get(slot) for slot in CARD_SLOTS}
    verdicts, messages = [], []
    for side in ("player", "dealer"):
        if side not in panels:
            messages.append("%s panel not shown" % side)
            continue
        data = panels[side]
        if not data["complete"]:
            verdict, message = result_panel.compare(
                side, data["cards"], record["%s_best_five" % side])
        else:
            # The same hand counts as agreement even where the casino chose a
            # different but equal kicker, which a plain set comparison would
            # report as a conflict.
            found, message = result_panel.verify(side, {"confirmed": data["cards"]}, cards)
            verdict = {"consistent": "verified", "conflict": "conflict",
                       "mismatch": "conflict"}.get(found, "unknown")
        verdicts.append(verdict)
        messages.append(message)

    if not verdicts:
        return "unknown", "; ".join(messages)
    if "conflict" in verdicts:
        return "conflict", "; ".join(messages)
    if all(v == "verified" for v in verdicts):
        return "verified", "; ".join(messages)
    return "unknown", "; ".join(messages)


def validate_against_result_boxes(config, record):
    """Older cross-check against result boxes fixed by calibration.

    Kept for anyone who calibrated them; validate_against_panels finds the
    same panels without needing coordinates.
    """
    if not config.get("validate_with_result_boxes"):
        return True, None
    boxes = config.get("result_boxes") or {}
    if not boxes:
        return True, None

    confidence_threshold = config.get("confidence_threshold", 0.62)
    presence_threshold = config.get("presence_threshold", 0.35)
    images = grab_regions(boxes)

    problems = []
    for side in ("player", "dealer"):
        seen = set()
        for index in range(1, 6):
            read = read_slot(
                images.get("%s_result_%d" % (side, index)),
                confidence_threshold, presence_threshold,
            )
            if read["confident"] and read["card"]:
                seen.add(read["card"])
        if len(seen) < 5:
            continue  # could not read the box clearly; not a mismatch
        expected = set(record["%s_best_five" % side])
        if seen != expected:
            problems.append(
                "%s box shows %s but this app calculated %s"
                % (side, " ".join(sorted(seen)), " ".join(sorted(expected)))
            )

    if problems:
        return False, "WARNING: Hand validation mismatch - " + "; ".join(problems)
    return True, None


class RoundTimer:
    """Times the round as the tracker watches it happen.

    Four moments matter, and each is recorded the first time it is reached so
    that a flickering read cannot move it:

        started      the deal - cards appear on an empty table
        flop         all three community cards are readable
        showdown     the dealer's cards are turned over
        cleared      the table empties again, ready for the next deal

    Durations come from a monotonic clock so that a system clock adjustment
    cannot produce a negative round; the recorded start time is wall clock,
    because that is what is useful in a spreadsheet.
    """

    def __init__(self, clock=time.monotonic, wall_clock=None):
        self._clock = clock
        self._wall_clock = wall_clock or datetime.now
        self.started_at = None
        self.started_wall = None
        self.flop_at = None
        self.showdown_at = None
        self.previous_showdown_at = None
        self.generation = None
        self._idle = True

    def observe(self, state, generation=None):
        """Fold one poll into the timings."""
        now = self._clock()

        if generation is not None and generation != self.generation:
            self.generation = generation
            self._begin_new_round()
            # The table need never have looked empty - a new deal can be
            # recognised straight from new player cards - so the round is
            # re-armed here and starts on the cards seen in this same poll.
            self._idle = True

        if state == WAITING:
            self._idle = True
            return

        if self._idle:                      # first sight of cards on the table
            self._begin_new_round()
            self._idle = False
            self.started_at = now
            self.started_wall = self._wall_clock()

        if self.flop_at is None and state in (FLOP, TURN, RIVER, COMPLETE):
            self.flop_at = now
        if self.showdown_at is None and state == COMPLETE:
            self.showdown_at = now

    def _begin_new_round(self):
        if self.showdown_at is not None:
            self.previous_showdown_at = self.showdown_at
        self.started_at = None
        self.started_wall = None
        self.flop_at = None
        self.showdown_at = None

    @staticmethod
    def _gap(earlier, later):
        if earlier is None or later is None:
            return None
        return round(max(0.0, later - earlier), 2)

    def timings(self):
        """The round's timings in seconds, with None for anything not seen."""
        return {
            "round_started_at": self.started_wall,
            "deal_to_flop_seconds": self._gap(self.started_at, self.flop_at),
            "flop_to_showdown_seconds": self._gap(self.flop_at, self.showdown_at),
            "round_seconds": self._gap(self.started_at, self.showdown_at),
            "seconds_since_previous_round": self._gap(
                self.previous_showdown_at, self.started_at),
        }

    def describe(self):
        """One line for the log."""
        timings = self.timings()
        parts = []
        for key, label in (("round_seconds", "round"),
                           ("deal_to_flop_seconds", "deal->flop"),
                           ("flop_to_showdown_seconds", "flop->showdown"),
                           ("seconds_since_previous_round", "since last round")):
            if timings[key] is not None:
                parts.append("%s %.1fs" % (label, timings[key]))
        return ", ".join(parts) or "no timings yet"


class CardMemory:
    """Accumulates what each slot has been read as over the course of a hand.

    Two things make a single reading untrustworthy. The dealer's hands pass
    over the table constantly, so a covered card reads as absent; and two suits
    of the same colour can be confused on a blurred or half-covered frame, so
    the same card may read as the seven of hearts on one poll and the seven of
    diamonds on the next.

    So a slot is not latched to its first reading. Every confident reading adds
    to a tally for that card, and the slot reports whichever card has the most
    support so far. A card seen clearly ten times cannot be displaced by one
    bad frame, and a slot that genuinely flickers between two readings settles
    on whichever the recogniser believes more, more often.

    What a reading is worth to that choice is how far it cleared the threshold,
    not its whole confidence (see vote_score). Whole confidences let a long run
    of barely-readable readings outweigh one plain sight of the card, and the
    deal guarantees exactly that run: a card sliding past the box reads weakly
    for several polls before the card that belongs there is dealt. Live
    (20260917_120408 round 60), flop_3 read as a king of spades five times at
    0.62-0.705 - a total of 3.30 - and the two of spades that was really there
    arrived at 0.957 and could not displace it for another four polls. Counting
    only the part above the threshold, the king is worth 0.198 and the two
    takes the slot on the poll it is first seen.

    The tally itself is unchanged, so what the dealer's cards are vetted on -
    how much evidence there is, in whole confidences - is unchanged too.

    The tally is dropped when the hand is over:

    * every region has been empty for `clear_frames` polls - the table has been
      cleared, so the next deal starts from nothing;
    * the player's own seat has been empty for twice that long - the hand is
      over even if something else on the table is still showing; or
    * both player cards read as a different pair on `confirm_frames` polls in a
      row - a new deal that never showed an empty table.
    """

    def __init__(self, clear_frames=10, confirm_frames=2):
        self.clear_frames = max(1, int(clear_frames))
        self.confirm_frames = max(1, int(confirm_frames))
        self.votes = {}            # slot -> {card: [total confidence, readings, best]}
        self.cards = {}            # slot -> best-supported card
        self.held = set()          # slots currently supplied from memory
        self.generation = 0        # bumped on every clear, so hands can't blend
        self._empty_streak = 0
        self._seat_empty_streak = 0
        self._new_deal_streak = 0

    def support(self, slot):
        """(total confidence, readings, best single reading) behind `slot`."""
        card = self.cards.get(slot)
        tally = self.votes.get(slot, {}).get(card) if card else None
        return tuple(tally) if tally else (0.0, 0, 0.0)

    def evidence(self, slot):
        """Short description of why a slot reads the way it does, for the log."""
        total, readings, best = self.support(slot)
        return "%.2f/%d" % (best, readings) if readings else "-"

    def forget(self, slots, reason):
        """Drop specific slots so they are read again from scratch."""
        dropped = [slot for slot in slots if slot in self.cards]
        if not dropped:
            return
        for slot in dropped:
            self.cards.pop(slot, None)
            self.votes.pop(slot, None)
        self.held -= set(dropped)
        logger.info("Forgot %s (%s)", ", ".join(sorted(dropped)), reason)

    def clear(self, reason):
        if self.cards:
            logger.info("Card memory cleared (%s)", reason)
        self.votes = {}
        self.cards = {}
        self.held = set()
        self._empty_streak = 0
        self._seat_empty_streak = 0
        self._new_deal_streak = 0
        self.generation += 1

    def _is_new_deal(self, cards):
        """True when both player cards have read as a different pair repeatedly.

        One frame is not enough: a misread player card would otherwise throw
        away every community card in the middle of a hand.
        """
        fresh = [cards.get("player_1"), cards.get("player_2")]
        known = [self.cards.get("player_1"), self.cards.get("player_2")]
        # Both cards must be different. A real new deal replaces both hole
        # cards (and nearly always passes through an empty table, which clears
        # memory anyway); one card reading differently is a misread. Live, a
        # nine of clubs under the showdown banner read as a queen on two polls,
        # and a six of hearts as a queen, and each time the hand was thrown away
        # and restarted on the wrong card.
        if not all(fresh) or not all(known) or set(fresh) & set(known):
            self._new_deal_streak = 0
            return False
        self._new_deal_streak += 1
        return self._new_deal_streak >= self.confirm_frames

    def update(self, cards, reads):
        """Fold this poll into memory and return the effective cards."""
        if not any(read["present"] for read in reads.values()):
            self._empty_streak += 1
        else:
            self._empty_streak = 0

        if any(reads[slot]["present"] for slot in PLAYER_SLOTS):
            self._seat_empty_streak = 0
        else:
            self._seat_empty_streak += 1

        if self._empty_streak >= self.clear_frames:
            self.clear("table empty")
        elif self._seat_empty_streak >= self.clear_frames * 2:
            # Belt and braces: if the table never quite reads as empty, the
            # player's seat emptying still ends the hand, so the tracker can
            # never get stuck holding one hand forever.
            self.clear("player seat empty")

        if self._is_new_deal(cards):
            self.clear("new player cards")

        for slot in CARD_SLOTS:
            card = cards.get(slot)
            if not card:
                continue
            confidence = max(0.01, reads[slot]["confidence"])
            tally = self.votes.setdefault(slot, {})
            entry = tally.setdefault(card, [0.0, 0, 0.0])
            entry[0] += confidence
            entry[1] += 1
            entry[2] = max(entry[2], confidence)

            winner = max(tally, key=lambda option: vote_score(tally[option]))
            if winner != self.cards.get(slot):
                if slot in self.cards:
                    logger.info(
                        "%s now reads %s (%.2f over %d) rather than %s (%.2f over %d)",
                        slot, winner, vote_score(tally[winner]), tally[winner][1],
                        self.cards[slot], vote_score(tally[self.cards[slot]]),
                        tally[self.cards[slot]][1],
                    )
                self.cards[slot] = winner

        self.held = {slot for slot in self.cards if not cards.get(slot)}
        return dict(self.cards)


class Tracker:
    """Background screen-reading loop. Pushes events onto the events queue."""

    def __init__(self, config, events):
        self.config = config
        self.events = events
        self._thread = None
        self._stop = threading.Event()
        self._last_saved_fingerprint = None
        self._last_state = None
        # Set once, the first time this session's very first poll already
        # finds a state past WAITING - a hand already under way before
        # tracking began. Kept only for that one round (see _tick): a hand
        # started from its own deal is never mid-hand, whatever its state.
        self._started_mid_hand_round = None
        self._pending = None          # (fingerprint, timestamp of next retry)
        self._stable = {"cards": None, "count": 0}
        self._saved_generation = None  # memory generation the last hand came from
        self._conflict_note = None     # last "same card twice" message, to avoid repeats
        # Where the cards were last seen, and where they were found from. The
        # table is located in the frame rather than assumed, but a frame with a
        # hand across it finds nothing, so the last good layout is kept to read
        # from until one works again.
        self._layout = None
        self._layout_source = None
        self._dealer_note = {}         # last rejection logged per dealer slot
        self._status_note = {}         # last ambiguity logged per slot
        # The pixels each slot showed last poll, and what they were read as.
        # A card that has not moved is not read again.
        self._last_crop = {}
        self._last_read = {}
        # The result panels, followed from poll to poll. They are the source for
        # each seat's final best five and hand; the table stays the source for
        # which card is where. Nothing read from a panel is written into a slot.
        self.panels = result_panel.PanelTracker()
        self._frame = None               # (frame, origin) of the current poll
        self._panel_note = {}            # last panel state/verdict logged per side
        self._panel_mismatch = {}        # polls a side has stayed "mismatch"
        self._panel_failure_note = {}    # last thumbnail failure logged per position
        # Live recognition diagnostics: reporting only, pictures only when
        # config["live_diagnostics"] is on.
        self.diagnostics = LiveDiagnostics()
        self._last_regions = {}
        self._last_images = {}
        self._region_sources = {}
        # Dealer-card status, timing and crops (config["dealer_debug"]).
        self.dealer_watch = dealer_watch.DealerWatch()
        self.dealer_saver = None
        self._timing = {}
        # The Scenario Engine, dry run: works out PLAY / DON'T_PLAY / WAIT from
        # the confirmed player and flop cards and logs why. It never acts.
        self.scenario_monitor = None
        self.timer = RoundTimer()
        self.memory = CardMemory(
            clear_frames=config.get("clear_frames", 3),
            confirm_frames=config.get("change_confirm_frames", 2),
        )
        # Pause while the game window is not in front. Off unless
        # config["game_window_title"] names it, in which case _run skips the
        # whole poll rather than reading somebody else's screen. See
        # window_focus.py for what a window title can and cannot tell you.
        self.focus = window_focus.FocusGate(config.get("game_window_title"))

    # -- lifecycle ---------------------------------------------------------

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tracker", daemon=True)
        self._thread.start()
        logger.info("Tracker started")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        logger.info("Tracker stopped")

    # -- internals ---------------------------------------------------------

    def _emit(self, kind, payload):
        self.events.put((kind, payload))

    def _check_resolution(self):
        """Warn if the screen no longer matches the size recorded at calibration."""
        expected = self.config.get("screen_size")
        current = screen_size()
        if expected and current and list(current) != list(expected):
            message = (
                "Screen size changed since calibration (%sx%s now %sx%s). "
                "Recalibrate if cards are not read correctly."
                % (expected[0], expected[1], current[0], current[1])
            )
            logger.warning(message)
            self._emit("warning", message)

    def _run(self):
        interval = float(self.config.get("poll_interval_seconds", 1.0))
        self._check_resolution()
        while not self._stop.is_set():
            started = time.time()
            # The one gate, in front of the one method that does the work.
            # _tick is where the screen is read, where CardMemory is advanced,
            # where the round timer moves, where the scenario engine decides
            # and where the update every consumer downstream is fed from is
            # emitted - so not calling it is the whole of the pause. Nothing
            # downstream needs a second check.
            moved = self.focus.observe()
            if moved is not None:
                self._emit("focus", moved)
            if self.focus.paused:
                self._stop.wait(interval)
                continue
            try:
                self._tick()
            except TemplatesMissingError as exc:
                logger.error("%s", exc)
                self._emit("error", str(exc))
                break
            except CaptureError as exc:
                logger.error("Capture problem: %s", exc)
                self._emit("warning", "Screen capture problem: %s" % exc)
            except Exception as exc:  # noqa: BLE001 - the loop must survive bad frames
                logger.exception("Unexpected tracker error")
                self._emit("warning", "Tracker error: %s" % exc)
            elapsed = time.time() - started
            self._stop.wait(max(0.1, interval - elapsed))

    def _locate(self, frame, origin):
        """Where to read the cards from in this frame.

        The table is found in the frame first. When it cannot be - a hand
        sweeping across merges three cards into one shape that fits nothing -
        the last layout that worked is used instead, so a covered table reads
        as covered rather than as a table that has moved. Calibrated boxes are
        the floor under both, for the frames before any layout has been found.
        """
        layout = table_layout.locate(frame, origin=origin)
        if table_layout.looks_plausible(layout):
            self._layout = layout
            self._layout_source = "dynamic"
        elif self._layout is not None:
            self._layout_source = "cached"
        else:
            # Nothing found and nothing found before, so the calibrated boxes
            # are all that is left - and when there are none of those either,
            # say so rather than blaming a calibration that was never done.
            self._layout_source = ("calibrated" if self.config.get("regions")
                                   else "none")
        regions = table_layout.merge(
            self._layout if self._layout_source != "calibrated" else None,
            self.config.get("regions") or {},
        )
        # Where each box came from, for the live diagnostics: found in this
        # frame, kept from the last frame the table was found in, or calibrated.
        # A kept or calibrated box reads whatever that part of the screen shows
        # now - which, with the game window away in a live run, was another
        # application's text.
        kept = ((self._layout or {}).get("regions") or {}
                if self._layout_source in ("dynamic", "cached") else {})
        found = "this frame" if self._layout_source == "dynamic" else "last good layout"
        self._region_sources = {slot: (found if slot in kept else "calibrated")
                                for slot in regions}
        return regions

    def _read_table(self):
        """Capture the screen, find the cards in it, and read them.

        One grab of the whole monitor rather than nine small ones: the cards
        have to be found before they can be read, and finding them needs the
        picture they are in.
        """
        monitor = int(self.config.get("monitor", 1) or 1)
        clock = time.perf_counter
        started = clock()
        frame = grab_full_screen(monitor)
        origin = monitor_origin(monitor)
        captured = clock()
        self._frame = (frame, origin)
        regions = self._locate(frame, origin)
        located = clock()
        images = {slot: crop(frame, regions[slot], origin)
                  for slot in CARD_SLOTS if slot in regions}
        cropped = clock()
        self._last_regions, self._last_images = regions, images
        result = self._read_changed(images)
        self._timing.update(capture=(captured - started) * 1000.0,
                            roi=(located - captured) * 1000.0,
                            crop=(cropped - located) * 1000.0,
                            recognition=(clock() - cropped) * 1000.0)
        return result

    def _read_changed(self, images):
        """Read the slots whose pixels have changed since the last poll.

        Recognising a card costs about 20ms and deciding that its pixels are
        untouched costs about 0.02ms, so a table where two cards have just been
        dealt is read in 41ms instead of 185ms. The saving is the whole point:
        the community cards are on screen briefly and the dealer's briefly
        still, and the time not spent re-reading the seven cards that have not
        moved is time available to sample the two that just appeared.

        Nothing about the reading changes. A still card is recognised every
        poll today and votes in CardMemory every poll; it still votes every
        poll, from the answer already worked out for those exact pixels.
        """
        fresh = {slot: images.get(slot) for slot in CARD_SLOTS
                 if not self._unchanged(slot, images.get(slot))}
        self._timing["fresh"] = set(fresh)
        cards, reads = read_table(self.config, fresh)

        for slot in CARD_SLOTS:
            if slot in fresh:
                self._last_crop[slot] = images.get(slot)
                self._last_read[slot] = reads[slot]
            elif slot in self._last_read:
                read = self._last_read[slot]
                reads[slot] = read
                cards[slot] = read["card"] if read["confident"] else None
        return cards, reads

    def _unchanged(self, slot, image):
        """True when this slot shows exactly the pixels it showed last poll.

        Only a real picture can be unchanged. A slot with no image is read
        again every time, because "nothing there" is cheap to establish and
        the alternative is a stale answer outliving the card.
        """
        previous = self._last_crop.get(slot)
        if not (image is not None and previous is not None
                and slot in self._last_read
                and image.shape == previous.shape):
            return False
        if numpy.array_equal(image, previous):
            return True
        # A dealer card lying still is the same picture from poll to poll give
        # or take video noise, and is not read again for it. Compared with the
        # crop that was last *read*, so a card that creeps counts as moved.
        return slot in DEALER_SLOTS and dealer_crop.still(image, previous)

    def _verify_against_panels(self, record):
        """Read the result panels now and compare them with the finished round.

        A fresh grab rather than the frame the round was built from: the
        panels are filled in at the showdown, which is often a moment after
        the last card was read.
        """
        monitor = int(self.config.get("monitor", 1) or 1)
        frame = grab_full_screen(monitor)
        return validate_against_panels(frame, record, monitor_origin(monitor))

    def _observe_panels(self, cards):
        """Read the result panels in this poll's frame and check them against the table.

        Reporting only: the table's cards are passed in and never changed, no
        slot is filled from a panel, and a failure here never stops a poll.
        Panel evidence is tagged with the card memory's generation so one
        round's panel cannot be counted as the next round's.
        """
        if self._frame is None:
            return None
        frame, origin = self._frame
        self._frame = None
        try:
            snapshot = self.panels.observe(frame, origin, self._layout,
                                           round_id=self.memory.generation,
                                           cards=cards)
        except Exception as exc:  # noqa: BLE001 - the panel is a second source, never a blocker
            if self._panel_note.get("error") != str(exc):
                self._panel_note["error"] = str(exc)
                logger.warning("Result panel reading failed: %s", exc)
            return None
        if self.panels.last_retire:
            logger.info("Result panel evidence retired: %s", self.panels.last_retire)

        verdicts = {}
        for side in result_panel.SIDES:
            verdict, message = result_panel.verify(side, snapshot[side], cards)
            if verdict == "mismatch":
                count = self._panel_mismatch.get(side, 0) + 1
                self._panel_mismatch[side] = count
                if count >= PANEL_MISMATCH_POLLS:
                    verdict = "conflict"
                    message = "%s (for %d polls)" % (message, count)
            else:
                self._panel_mismatch[side] = 0
            verdicts[side] = (verdict, message)
            self._note_panel(side, snapshot[side], verdict, message, cards)
        self._note_panel_failures(snapshot)
        return {"sides": snapshot, "verdicts": verdicts,
                "outcome": result_panel.outcome(snapshot)}

    def _watch_dealer(self, reads, cards, state):
        """The dealer's two cards this poll: status, timing and crops.

        Reporting only, and only while config["dealer_debug"] is on. Logs a
        DEALER_TIMING line every poll at the river - where the dealer's cards
        are revealed - and a DEALER_LATENCY line when each card is shown, and
        keeps every changed dealer crop. A failure here never stops a poll.
        """
        if not self.config.get("dealer_debug"):
            return None
        try:
            regions = self._last_regions or {}
            sources = self._region_sources or {}
            fresh = self._timing.get("fresh", set())
            evidence = {}
            for slot in dealer_watch.DEALER_SLOTS:
                read = reads.get(slot)
                found = dealer_watch.slot_evidence(
                    slot, regions.get(slot), sources.get(slot), read,
                    self.memory.cards.get(slot), cards.get(slot),
                    dealer_watch.dealer_status(read, cards.get(slot)))
                # A card read on an earlier poll and unchanged cost nothing now.
                found["recognition_ms"] = (read or {}).get("ms", 0.0) if slot in fresh else 0.0
                evidence[slot] = found
            records = self.dealer_watch.observe(self.memory.generation, reads,
                                                self.memory.cards, cards)
            timing = self._timing
            if state == RIVER:
                logger.info(
                    "DEALER_TIMING capture=%.0fms roi=%.0fms crop=%.1fms recognition=%.0fms "
                    "(dealer_1 %.0f, dealer_2 %.0f) memory=%.2fms gate=%.2fms panel=%.0fms "
                    "diagnostics=%.1fms total=%.0fms | %s | %s",
                    timing.get("capture", 0.0), timing.get("roi", 0.0), timing.get("crop", 0.0),
                    timing.get("recognition", 0.0),
                    evidence["dealer_1"]["recognition_ms"] or 0.0,
                    evidence["dealer_2"]["recognition_ms"] or 0.0,
                    timing.get("memory", 0.0), timing.get("gate", 0.0), timing.get("panel", 0.0),
                    timing.get("diagnostics", 0.0), timing.get("total", 0.0),
                    dealer_watch.describe_slot(evidence["dealer_1"]),
                    dealer_watch.describe_slot(evidence["dealer_2"]))
            for record in records:
                logger.info(
                    "DEALER_LATENCY %s=%s boxed->read=%s read->memory=%s memory->shown=%s "
                    "boxed->shown=%s ms (first confident read %s)",
                    record["slot"], record["card"], record["boxed_to_read_ms"],
                    record["read_to_memory_ms"], record["memory_to_shown_ms"],
                    record["boxed_to_shown_ms"], record["first_read_card"])
            if self.dealer_saver is None:
                self.dealer_saver = dealer_watch.DealerSaver()
            extra = {"generation": self.memory.generation, "state": state,
                     "timing_ms": {key: round(value, 2) for key, value in timing.items()
                                   if key != "fresh"}}
            for slot in dealer_watch.DEALER_SLOTS:
                self.dealer_saver.consider(evidence[slot], (self._last_images or {}).get(slot),
                                           extra)
            return {"slots": evidence, "latency": records,
                    "marks_ms_ago": self.dealer_watch.snapshot(),
                    "saved": self.dealer_saver.saved}
        except Exception as exc:  # noqa: BLE001 - diagnostics must never block tracking
            if self._panel_note.get("dealer_error") != str(exc):
                self._panel_note["dealer_error"] = str(exc)
                logger.warning("Dealer diagnostics failed: %s", exc)
            return None

    def _run_scenario_engine(self, cards, reads):
        """The Scenario Engine's dry-run decision for this poll.

        Reporting only, like the diagnostics: the engine is handed copies of
        what the tracker already decided - each slot's card, its card_status
        and CardMemory's reading count - and nothing it returns is written
        back. It never clicks, bets or sends anything. A failure here never
        stops a poll. Off with config["scenario_engine"] = False.
        """
        if not self.config.get("scenario_engine", True):
            return None
        try:
            if self.scenario_monitor is None:
                self.scenario_monitor = scenario_engine.ScenarioMonitor(
                    scenario_engine.load_engine_config())
            slots = {}
            for slot in CARD_SLOTS:
                support = self.memory.support(slot)
                slots[slot] = {"card": cards.get(slot),
                               "status": card_status(reads.get(slot), support,
                                                         card=self.memory.cards.get(slot))[0],
                               "readings": support[1]}
            # The dealer's final hand needs the whole board confirmed; the
            # dealer's own two are taken as the tracker already vetted them.
            board_ready = not any(
                scenario_engine.slot_problem(slot, slots[slot],
                                             self.scenario_monitor.config)
                for slot in scenario_engine.BOARD_SLOTS)
            return self.scenario_monitor.observe(
                slots, self.memory.generation,
                dealer_cards=[cards.get(slot) for slot in DEALER_SLOTS],
                board_ready=board_ready)
        except Exception as exc:  # noqa: BLE001 - the engine must never block tracking
            if self._panel_note.get("scenario_error") != str(exc):
                self._panel_note["scenario_error"] = str(exc)
                logger.warning("Scenario engine failed: %s", exc)
            return None

    def _diagnose(self, captured, reads, cards, state, panels):
        """Compare this frame's raw readings with memory, the panel and each other.

        Reporting only: nothing read here changes a slot, memory or a stored
        hand. Pictures are saved only while config["live_diagnostics"] is on,
        and a failure here never stops a poll.
        """
        try:
            frame, origin = captured if captured else (None, (0, 0))
            support = {slot: self.memory.support(slot) for slot in CARD_SLOTS}
            statuses = {slot: card_status(reads.get(slot), support[slot],
                                                card=self.memory.cards.get(slot))[0]
                        for slot in CARD_SLOTS}
            return self.diagnostics.poll(
                frame=frame, origin=origin, regions=self._last_regions,
                region_sources=self._region_sources, images=self._last_images,
                reads=reads, memory_cards=dict(self.memory.cards),
                memory_support=support, shown=cards, statuses=statuses,
                generation=self.memory.generation, state=state,
                layout_source=self._layout_source, panels=panels,
                save=bool(self.config.get("live_diagnostics")),
                panel_crops=dict(self.panels.crops))
        except Exception as exc:  # noqa: BLE001 - diagnostics must never block tracking
            if self._panel_note.get("diagnostics_error") != str(exc):
                self._panel_note["diagnostics_error"] = str(exc)
                logger.warning("Live diagnostics failed: %s", exc)
            return None

    def _note_panel(self, side, data, verdict, message, cards):
        """Log a side's panel whenever what it shows, or how it fits, changes."""
        key = (data["state"], tuple(data["confirmed"] or ()), data["stale"], verdict)
        if self._panel_note.get(side) == key:
            return
        self._panel_note[side] = key
        logger.info("Result panel %s: %s %s%s [round %s] | table check %s: %s",
                    side, data["state"], " ".join(data["confirmed"] or []) or "--",
                    " (previous round, ignored)" if data["stale"] else "",
                    data["round_id"], verdict.upper(), message)
        if verdict == "conflict":
            logger.warning("SOURCE CONFLICT (%s): %s | table %s", side, message,
                           describe(cards))
            self._emit("warning", "Result panel disagrees with the table (%s): %s"
                       % (side, message))

    def _note_panel_failures(self, snapshot):
        """Record a thumbnail that was seen but not read, once per change."""
        live = set()
        for side in result_panel.SIDES:
            for index, read in enumerate(snapshot[side]["reads"]):
                status = read["status"]
                if status not in (result_panel.UNKNOWN, result_panel.AMBIGUOUS):
                    continue
                position = (side, index)
                live.add(position)
                note = (status, read["card"], round(read["confidence"], 2))
                if self._panel_failure_note.get(position) == note:
                    continue
                self._panel_failure_note[position] = note
                region = read["region"] or {}
                text = ("card=%s confidence=%.2f rank=%s suit=%s margin=%s "
                        "region=%s,%s,%sx%s round=%s source=panel" % (
                            read["card"] or "--", read["confidence"],
                            read["rank_confidence"], read["suit_confidence"],
                            read["suit_margin"], region.get("left"), region.get("top"),
                            region.get("width"), region.get("height"),
                            snapshot[side]["round_id"]))
                logger.info("Result panel %s card %d %s: %s",
                            side, index + 1, status, text)
                self._save_failure("panel_%s_%d" % (side, index + 1), status, text,
                                   self.panels.crops.get(position))
        for position in [p for p in self._panel_failure_note if p not in live]:
            del self._panel_failure_note[position]

    def _save_failure(self, slot, status, note, image):
        """Keep the pixels of a card that was seen but not accepted.

        A dash in the log says a card was refused but not what it looked like,
        and these failures are intermittent - by the time they are noticed the
        frame is long gone. One file per failure, only while the flag is on.
        """
        if image is None or not self.config.get("debug_save_failures"):
            return
        try:
            import cv2

            folder = os.path.join(LOG_ROOT, "logs", "failures")
            os.makedirs(folder, exist_ok=True)
            name = "%s_%s_%s.png" % (
                time.strftime("%Y%m%d-%H%M%S"), slot, status.lower())
            cv2.imwrite(os.path.join(folder, name), image)
            logger.info("Saved the refused %s crop: %s (%s)", slot, name, note)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
            logger.debug("Could not save the %s crop: %s", slot, exc)

    def _note_statuses(self, reads, images=None):
        """Log a slot whose card was seen but refused, once per change.

        Both ways of refusing are reported, because they have different
        causes and want different fixes: AMBIGUOUS is a card read well enough
        to accept whose suit could not be settled, and UNKNOWN is a card that
        matched nothing.

        UNKNOWN used to be left out of this entirely, and of the crops kept
        with it. That made the saved evidence answer a question nobody asked:
        every one of the 2795 crops from one live session was an AMBIGUOUS
        one, not because the others do not happen but because they were never
        kept, and reading that pile as "the suit margin is the problem" is a
        mistake the pile itself invites. A slot that is holding a settled
        card is not reported - it is covered, not failing.

        Only changes are logged: this runs several times a second.
        """
        for slot in CARD_SLOTS:
            status, note = card_status(reads.get(slot), self.memory.support(slot),
                                       card=self.memory.cards.get(slot))
            if status not in (AMBIGUOUS, UNKNOWN):
                self._status_note.pop(slot, None)
                continue
            if self._status_note.get(slot) != note:
                self._status_note[slot] = note
                logger.info("%s %s: %s", SLOT_LABELS[slot], status, note)
                self._save_failure(slot, status, note, (images or {}).get(slot))

    def _dealer_evidence(self, slot, reads):
        """(best single reading, total across readings) behind a dealer slot.

        Memory is where the readings accumulate, so that is asked first. With
        latching turned off there is no tally to ask, and all the evidence
        there will ever be is this one frame.
        """
        total, readings, best = self.memory.support(slot)
        if readings:
            return best, total
        read = reads.get(slot) or {}
        confidence = read.get("confidence", 0.0) if read.get("confident") else 0.0
        return confidence, confidence

    def _vet_dealer_cards(self, cards, reads):
        """Drop dealer cards that are not backed by enough evidence.

        The dealer's two cards are face up for a moment at the showdown, and
        the dealer's hands are usually still over them. Every other card on the
        table is seen dozens of times and converges; these are seen about twice,
        so the usual threshold - written for a card that gets another forty
        chances to correct itself - is the wrong bar for them.

        The cost of being wrong is also different. A missed dealer card leaves
        the hand incomplete and nothing is recorded. A wrong one is recorded,
        and since it decides the winner it is recorded wrongly.

        Two conditions, because confidence alone is not enough: an eight with
        its left half under a finger really does look like a three, and matched
        like one at 0.70. `best` demands one good look and `total` demands that
        more than one frame agreed - a single reading cannot reach it.
        """
        min_best = float(self.config.get("dealer_min_confidence", 0.80))
        min_total = float(self.config.get("dealer_min_total", 1.50))
        strong = float(self.config.get("dealer_strong_confidence", 0.85))

        vetted = dict(cards)
        for slot in DEALER_SLOTS:
            card = cards.get(slot)
            if not card:
                continue
            best, total = self._dealer_evidence(slot, reads)
            # Either one look good enough to stand alone, or two decent looks
            # that agree. Requiring the second look regardless threw away
            # cards that were read plainly but only shown once.
            if best >= strong or (best >= min_best and total >= min_total):
                continue
            vetted[slot] = None
            note = "%s: rejected %s confidence=%.2f total=%.2f" % (
                slot, card, best, total)
            if note != self._dealer_note.get(slot):
                # Once per slot per round: this runs several times a second.
                self._dealer_note[slot] = note
                logger.info(
                    "%s (needs confidence>=%.2f alone, or >=%.2f with total>=%.2f)",
                    note, strong, min_best, min_total)
        return vetted

    def _tick(self):
        clock = time.perf_counter
        self._timing = {}
        started = clock()
        seen, reads = self._read_table()

        mark = clock()
        if self.config.get("latch_cards", True):
            cards = self.memory.update(seen, reads)
            held = set(self.memory.held)
        else:
            cards = seen
            held = set()
        self._timing["memory"] = (clock() - mark) * 1000.0

        # Vetted after memory has had its say, so the judgement is made on all
        # the readings behind a card rather than just this frame's.
        mark = clock()
        cards = self._vet_dealer_cards(cards, reads)
        self._timing["gate"] = (clock() - mark) * 1000.0
        self._note_statuses(reads, self._last_crop)

        state = derive_state(cards)
        self.timer.observe(state, self.memory.generation)

        if self._last_state is None and state != WAITING:
            self._started_mid_hand_round = self.memory.generation

        if state != self._last_state:
            # From the flop onwards the player's hand can be worked out, so the
            # log records how it developed street by street.
            progress = hands_so_far(cards)
            summary = describe(cards)
            if progress["player_hand"]:
                summary += " | player has %s" % progress["player_hand"]
            if progress["winner"]:
                summary += ", dealer has %s -> %s" % (
                    progress["dealer_hand"], progress["winner"])
            logger.info("State %s -> %s | %s", self._last_state, state, summary)
            if state == COMPLETE:
                logger.info("Round timing: %s", self.timer.describe())
            self._last_state = state

        captured = self._frame
        mark = clock()
        panels = self._observe_panels(cards)
        self._timing["panel"] = (clock() - mark) * 1000.0
        mark = clock()
        diagnostics = self._diagnose(captured, reads, cards, state, panels)
        self._timing["diagnostics"] = (clock() - mark) * 1000.0
        self._timing["total"] = (clock() - started) * 1000.0
        dealer = self._watch_dealer(reads, cards, state)
        scenario = self._run_scenario_engine(cards, reads)
        # No automation: read-only. Kept in the payload as None, exactly the
        # value it already carried whenever automation was configured off.
        action = None

        self._emit("update", {
            "state": state,
            # CardMemory's generation: the round identity the dealer watch,
            # the scenario engine and the diagnostics already use. Exposed
            # here so a consumer does not have to invent a second one.
            "round_id": self.memory.generation,
            "cards": cards,
            "seen": seen,
            "reads": reads,
            "held": held,
            "panels": panels,
            "diagnostics": diagnostics,
            "dealer": dealer,
            "scenario": scenario,
            "action": action,
            # True only for the round tracking happened to start inside -
            # cards already on screen before the first poll, not dealt since.
            # Display only: WAIT still waits for the same confirmations: this
            # just tells the person why it might take a whole hand to answer.
            "started_mid_hand": (self._started_mid_hand_round is not None
                                  and self.memory.generation == self._started_mid_hand_round),
            # Each slot's recognition status (CONFIRMED, HELD, CONFIRMING,
            # AMBIGUOUS, UNKNOWN, EMPTY), so the window can say which card is
            # missing and why. Reporting only.
            "statuses": {slot: card_status(reads.get(slot), self.memory.support(slot),
                                     card=self.memory.cards.get(slot))[0]
                         for slot in CARD_SLOTS},
            "timing": {key: value for key, value in self._timing.items() if key != "fresh"},
            "emitted_at": time.time(),
            # A slot the memory already holds is not "uncertain" - it is just
            # covered at this instant.
            "uncertain": [slot for slot in uncertain_slots(reads)
                          if not cards.get(slot)],
        })

        if state != COMPLETE:
            self._stable = {"cards": None, "count": 0}
            return

        # Require the same nine cards on consecutive frames: a card caught
        # mid-animation can otherwise be misread once and stored.
        snapshot = tuple(cards[slot] for slot in CARD_SLOTS)
        if snapshot == self._stable["cards"]:
            self._stable["count"] += 1
        else:
            self._stable = {"cards": snapshot, "count": 1}

        if self._stable["count"] < int(self.config.get("stable_frames", 2)):
            return

        conflicts = repeated_cards(cards)
        if conflicts:
            self._resolve_conflicts(conflicts, reads)
            return

        self._conflict_note = None
        self._store(cards, reads)

    def _resolve_conflicts(self, conflicts, reads):
        """One card was read into two slots, so the hand cannot be right.

        The hand is not saved. The slots involved are forgotten so they are
        read again rather than the wrong value being held for the rest of the
        hand - which would otherwise block this hand from ever being stored.
        """
        note = "; ".join(
            "%s in %s" % (card, " and ".join(SLOT_LABELS[slot] for slot in slots))
            for card, slots in sorted(conflicts.items())
        )
        if note != self._conflict_note:
            logger.warning("Same card read in two places, not saving: %s", note)
            self._emit("warning", "Same card read twice (%s) - hand not saved." % note)
            self._conflict_note = note

        # Keep the reading the recogniser is most sure of right now; re-read
        # the others. When every one of them is covered, re-read them all.
        for slots in conflicts.values():
            best = max(slots, key=lambda slot: reads[slot]["confidence"])
            drop = [slot for slot in slots
                    if slot != best or reads[best]["confidence"] <= 0]
            self.memory.forget(drop, "same card as another slot")
        self._stable = {"cards": None, "count": 0}

    def _store(self, cards, reads=None):
        try:
            record = build_hand_record(cards)
        except ValueError as exc:
            logger.error("Could not build hand record: %s", exc)
            return

        fingerprint = record["hand_fingerprint"]
        if fingerprint == self._last_saved_fingerprint:
            return  # the same completed hand is still on screen

        if self._saved_generation == self.memory.generation:
            # A hand was already stored from this set of remembered cards. Wait
            # for the table to clear (or a new deal) rather than storing a blend
            # of the old cards and the new ones.
            return

        if self._pending and self._pending[0] == fingerprint and time.time() < self._pending[1]:
            return  # a recent attempt failed; back off before retrying

        record.update(self.timer.timings())
        confidences = " | best/readings " + " ".join(
            "%s=%s" % (cards[slot], self.memory.evidence(slot))
            for slot in CARD_SLOTS
        )
        logger.info(
            "Completed hand %s | Player: %s | Dealer: %s | Winner: %s%s%s",
            fingerprint, record["player_hand"], record["dealer_hand"], record["winner"],
            "" if record["dealer_qualified"] else " (dealer did not qualify)",
            confidences,
        )
        logger.info("Timing: %s", self.timer.describe())

        try:
            # The panels beside the table outlive the cards in the middle of
            # it, so a round that has just been worked out can be checked
            # against the casino's own account of it while it is still up.
            verdict, message = self._verify_against_panels(record)
            if verdict == "verified":
                logger.info("ROUND VERIFIED against the result panels: %s", message)
            elif verdict == "conflict":
                logger.warning("ROUND CONFLICT: %s", message)
                self._emit("warning", "Result panel disagrees: %s" % message)
            else:
                # Said out loud: this used to be silent, which left a live run
                # with no verified hands and no way to tell why.
                logger.info("Result panels could not check this hand: %s",
                            message or "no result panel found")
            ok, message = validate_against_result_boxes(self.config, record)
            if not ok:
                logger.warning("%s", message)
                self._emit("warning", message)
        except Exception as exc:  # noqa: BLE001 - validation must never block a save
            logger.warning("Result validation failed: %s", exc)

        try:
            inserted, row = db.insert_hand(record)
        except db.DatabaseError as exc:
            logger.error("Database error: %s", exc)
            self._pending = (fingerprint, time.time() + RETRY_SECONDS)
            self._emit("error", "Database error: %s" % exc)
            return

        self._pending = None
        self._last_saved_fingerprint = fingerprint
        self._saved_generation = self.memory.generation

        if not inserted:
            self._emit("duplicate", record)
            return

        excel_error = None
        try:
            excel_export.append_hand(row)
        except excel_export.ExcelError as exc:
            logger.error("Excel error: %s", exc)
            excel_error = str(exc)

        self._emit("saved", {"record": record, "row": row, "excel_error": excel_error})
