"""The dealer's two cards, followed poll by poll: status, timing and evidence.

In a live session of eleven rounds that reached the river, the dealer's cards
were shown in four and never shown in seven. The gate that guards the
dealer's cards was refusing readings stuck at 0.64-0.82, correctly by its own
rules - but nothing recorded what those readings were read *from*, because the
crops of readings that pass the recogniser are never saved and only refused
ones are. So "why is the dealer slow" could not be answered.

This answers it. For each dealer slot it reports one of

    NOT_DETECTED   the box shows no card face this poll
    UNKNOWN        a card face, but nothing matched well
    AMBIGUOUS      read well, but the two suits of its colour were level
    DETECTED       read confidently, not yet shown (memory or the gate)
    CONFIRMED      shown

records when the card was first boxed, first read, taken into memory and
shown, and keeps the crop - with its box, where the box came from, and every
score - each time the reading changes.

Reporting only. Nothing here changes a reading, memory, the gate or a hand.
"""

import json
import logging
import os
import queue
import threading
import time

import cv2

from config.settings import DEFAULT_CONFIG, ROOT
from recognition.card_recognizer import undecided_half

logger = logging.getLogger(__name__)

DEALER_SLOTS = ("dealer_1", "dealer_2")
READABLE = DEFAULT_CONFIG["confidence_threshold"]
DEBUG_LIVE = os.path.join(ROOT, "debug", "live")

NOT_DETECTED = "NOT_DETECTED"
UNKNOWN = "UNKNOWN"
AMBIGUOUS = "AMBIGUOUS"
DETECTED = "DETECTED"
CONFIRMED = "CONFIRMED"

# Dealer crops saved in one session before saving stops.
MAX_DEALER_SAVES = 3000

# A reading counts as changed - and is saved again - when its card, status or
# box changes, or its confidence moves by at least this much.
CONFIDENCE_STEP = 0.05


def dealer_status(read, shown_card):
    """One of the five statuses for a dealer slot this poll."""
    if shown_card:
        return CONFIRMED
    read = read or {}
    if not read.get("present"):
        return NOT_DETECTED
    if read.get("confident"):
        return DETECTED
    if (read.get("card") and undecided_half(read)
            and read.get("confidence", 0.0) >= READABLE):
        return AMBIGUOUS
    return UNKNOWN


def _ms(start, end):
    return None if start is None or end is None else round((end - start) * 1000.0)


class DealerWatch:
    """First boxed, first read, into memory and shown - per dealer card, per round."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.generation = None
        self.slots = {}
        self._reset()

    def _reset(self):
        self.slots = {slot: {"boxed": None, "read": None, "memory": None, "shown": None,
                             "card": None, "reported": False} for slot in DEALER_SLOTS}

    def observe(self, generation, reads, memory_cards, shown, now=None):
        """Fold one poll in. Returns latency records for cards shown this poll."""
        now = self.clock() if now is None else now
        if generation != self.generation:
            self.generation = generation
            self._reset()
        records = []
        for slot in DEALER_SLOTS:
            mark = self.slots[slot]
            read = (reads or {}).get(slot) or {}
            if read.get("present") and mark["boxed"] is None:
                mark["boxed"] = now
            if read.get("confident") and mark["read"] is None:
                mark["read"], mark["card"] = now, read.get("card")
            if (memory_cards or {}).get(slot) and mark["memory"] is None:
                mark["memory"] = now
            card = (shown or {}).get(slot)
            if card and mark["shown"] is None:
                mark["shown"] = now
            if mark["shown"] is not None and not mark["reported"]:
                mark["reported"] = True
                records.append({
                    "slot": slot, "card": card,
                    "boxed_to_read_ms": _ms(mark["boxed"], mark["read"]),
                    "read_to_memory_ms": _ms(mark["read"], mark["memory"]),
                    "memory_to_shown_ms": _ms(mark["memory"], mark["shown"]),
                    "boxed_to_shown_ms": _ms(mark["boxed"], mark["shown"]),
                    "first_read_card": mark["card"],
                })
        return records

    def snapshot(self, now=None):
        now = self.clock() if now is None else now
        return {slot: {key: (round((now - value) * 1000.0) if isinstance(value, float) else value)
                       for key, value in mark.items() if key in ("boxed", "read", "memory", "shown")}
                for slot, mark in self.slots.items()}


def slot_evidence(slot, region, source, read, memory_card, shown_card, status):
    """The plain facts behind one dealer slot this poll, for the log, GUI and files."""
    read = read or {}
    detail = read.get("detail") or {}
    return {
        "slot": slot,
        "status": status,
        "box": dict(region) if region else None,
        "box_source": source,
        "present": bool(read.get("present")),
        "card": read.get("card"),
        "confident": bool(read.get("confident")),
        "confidence": read.get("confidence", 0.0),
        "rank_confidence": read.get("rank_confidence"),
        "suit_confidence": read.get("suit_confidence"),
        "suit_margin": read.get("suit_margin"),
        "suit_scores": detail.get("suit_scores"),
        "rank_scores": detail.get("rank_scores"),
        "recognition_ms": read.get("ms"),
        "memory_card": memory_card,
        "shown_card": shown_card,
    }


def describe_slot(evidence):
    """One compact line for a dealer slot."""
    box = evidence["box"]
    where = ("%d,%d %dx%d %s" % (box["left"], box["top"], box["width"], box["height"],
                                 evidence["box_source"] or "?") if box else "no box")
    if evidence["card"]:
        read = "%s %.2f (rank %s suit %s margin %s)" % (
            evidence["card"], evidence["confidence"],
            _num(evidence["rank_confidence"]), _num(evidence["suit_confidence"]),
            _num(evidence["suit_margin"]))
    else:
        read = "--"
    return "%s %s %s | memory %s shown %s | box %s" % (
        evidence["slot"], evidence["status"], read, evidence["memory_card"] or "--",
        evidence["shown_card"] or "--", where)


def _num(value):
    return "--" if value is None else "%.2f" % value


class DealerSaver:
    """Keeps each changed dealer crop, with its evidence, on a background thread."""

    def __init__(self, folder=DEBUG_LIVE, max_saves=MAX_DEALER_SAVES):
        self.folder = folder
        self.max_saves = max_saves
        self.saved = 0
        self.dropped = 0
        self._last = {}
        self._queue = queue.Queue(maxsize=64)
        self._thread = threading.Thread(target=self._run, name="dealer-crops", daemon=True)
        self._thread.start()

    def _key(self, evidence):
        box = evidence["box"] or {}
        return (evidence["status"], evidence["card"],
                int((evidence["confidence"] or 0.0) / CONFIDENCE_STEP),
                box.get("left"), box.get("top"), box.get("width"), box.get("height"))

    def consider(self, evidence, image, extra=None):
        """Save this slot's crop if its reading has changed since the last save."""
        if image is None or not getattr(image, "size", 0) or not evidence["present"]:
            return False
        key = self._key(evidence)
        if self._last.get(evidence["slot"]) == key or self.saved >= self.max_saves:
            return False
        self._last[evidence["slot"]] = key
        # The running number keeps two saves in the same millisecond from
        # sharing a name and one silently overwriting the other.
        stamp = "%s-%03d-%05d" % (time.strftime("%Y%m%d-%H%M%S"),
                                  int(time.time() * 1000) % 1000, self.saved + self.dropped)
        record = dict(evidence, stamp=stamp, **(extra or {}))
        try:
            self._queue.put_nowait((stamp, evidence["slot"], image.copy(), record))
            self.saved += 1
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def flush(self, timeout=10.0):
        deadline = time.time() + timeout
        while self._queue.unfinished_tasks and time.time() < deadline:
            time.sleep(0.02)

    def _run(self):
        while True:
            stamp, slot, image, record = self._queue.get()
            try:
                os.makedirs(self.folder, exist_ok=True)
                base = os.path.join(self.folder, "%s_%s" % (slot, stamp))
                cv2.imwrite(base + ".png", image)
                with open(base + ".json", "w", encoding="utf-8") as handle:
                    json.dump(record, handle, indent=2, default=str)
            except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
                logger.warning("Could not save the %s crop: %s", slot, exc)
            finally:
                self._queue.task_done()
