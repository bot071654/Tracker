"""SHADOW MODE: would a table slot's last read have been safe to reuse? Measured, never acted on.

The tracker reads the board's and the player's cards on almost every poll, because under video noise their pixels are almost never
byte-identical (about 8.6 of 9 slots a poll live, roughly 48 ms each). Skipping a read when the picture "has not changed" would save
most of that time - but only if the rule that decides "has not changed" never skips a read that mattered. A mean grey-level difference
cannot decide it: on a rendered deck two different cards (a spade and a club of one rank) differ by a mean of 0.36, less than ordinary
noise. The candidate rule here uses the largest difference and the share of pixels that moved, plus the safeguards below.

This module only MEASURES that rule. The tracker still reads every slot exactly as before and hands this module what it saw; the module
returns nothing the tracker uses. For each board / player slot on each poll it records one outcome - the reason reuse would have been
refused, or "eligible" - and, when eligible, whether the fresh read agreed with the read that would have been reused. It keeps only
counts and histogram bins: no picture, crop or screenshot is ever stored, written or logged (the grey reference it compares against lives
in memory for one slot and is replaced on the next read).

The rule it measures (a slot is eligible only when ALL hold):
  * the layout was found in this very frame (not remembered, lost or covered), and the slot is not covered by a tracker window;
  * a reference exists: the picture and read at the last poll where the rule would NOT have reused (so the reference only ever moves
    when a real read would have happened), taken in the same memory generation, from the same box, at the same size;
  * the reference read was confident, card memory holds that same card for the slot, settled, and not merely held;
  * the reference is younger than MAX_AGE_POLLS reuses and MAX_AGE_SECONDS;
  * the largest grey difference is at most MAX_DIFF_LIMIT, no more than PIXEL_SHARE_LIMIT of the pixels differ by more than
    PIXEL_LEVEL, and the mean difference is at most MEAN_LIMIT.
Dealer slots are not looked at: their behaviour is unchanged.
"""

import json
import time

import cv2
import numpy as np

BOARD_SLOTS = ("flop_1", "flop_2", "flop_3", "turn", "river")
PLAYER_SLOTS = ("player_1", "player_2")
SHADOW_SLOTS = BOARD_SLOTS + PLAYER_SLOTS

MAX_DIFF_LIMIT = 32          # grey levels: the largest per-pixel change allowed (rendered noise of sigma 8 peaks at 32; different cards at 177+)
PIXEL_LEVEL = 12             # a pixel "moved" when it changed by more than this ...
PIXEL_SHARE_LIMIT = 0.005    # ... and no more than this share of the picture may have moved
MEAN_LIMIT = 4.0             # mean grey change, a secondary check only (it cannot tell two cards apart on its own)
MAX_AGE_POLLS = 5            # a reference is reused at most this many polls in a row ...
MAX_AGE_SECONDS = 1.5        # ... and for at most this long

MAX_BINS = (4, 8, 16, 24, 32, 48, 64, 128, 256)
MEAN_BINS = (0.5, 1, 2, 4, 8, 16, 32, 64, 256)
SHARE_BINS = (0.0, 0.1, 0.5, 1, 2, 5, 10, 25, 101)       # percent of pixels over PIXEL_LEVEL
AGE_BINS = (0, 1, 2, 3, 4, 5, 6, 999)
SHIFT_BINS = (1, 2, 3, 4, 8, 16, 9999)                  # pixels a box moved between two polls (largest of left, top, width, height)

# Reasons a poll's slot is not eligible. Exactly one is recorded per slot per poll (or ELIGIBLE).
ELIGIBLE = "eligible"
R_COVERED = "covered by a tracker window"
R_NO_PICTURE = "no picture for the slot"
R_LAYOUT = "layout not found in this frame"
R_NO_REFERENCE = "no reference yet"
R_RESET = "new round or reset"
R_MOVED = "box moved"
R_SIZE = "picture size changed"
R_NOT_DECISIVE = "reference read not confident"
R_UNSETTLED = "slot not settled in memory"
R_MEMORY_DIFFERS = "memory holds another card"
R_HELD = "card only held, not read now"
R_AGE = "reference too old"
R_CHANGED_MAX = "picture changed (largest difference)"
R_CHANGED_SHARE = "picture changed (share of pixels)"
R_CHANGED_MEAN = "picture changed (mean difference)"

# What the fresh read made of the slot, compared with the reference's card.
O_SAME = "same card"
O_CHANGED = "different card"
O_EMPTY = "slot empty now"
O_UNSURE = "read not confident"


def slot_type(slot):
    return "board" if slot in BOARD_SLOTS else "player"


def _bin(value, edges):
    for index, edge in enumerate(edges):
        if value <= edge:
            return index
    return len(edges) - 1


def _label(index, edges):
    low = "0" if index == 0 else str(edges[index - 1])
    return "%s-%s" % (low, edges[index])


def grey(image):
    """The grey picture used for every comparison (a new array; the caller's crop is never touched)."""
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image.copy()


def difference(now, before, level=PIXEL_LEVEL):
    """(largest, mean, percent of pixels over `level`) between two grey pictures of one size."""
    delta = cv2.absdiff(now, before)
    return int(delta.max()), float(delta.mean()), float(np.count_nonzero(delta > level)) * 100.0 / delta.size


class _Histogram:
    def __init__(self, edges):
        self.edges = edges
        self.counts = [0] * len(edges)

    def add(self, value):
        self.counts[_bin(value, self.edges)] += 1

    def as_dict(self):
        return {_label(i, self.edges): n for i, n in enumerate(self.counts) if n}


class ShadowReuse:
    """Follows the board and player slots and counts what a reuse rule would have done. Observation only."""

    EXAMPLES = 20

    def __init__(self, clock=time.monotonic, max_diff=MAX_DIFF_LIMIT, pixel_level=PIXEL_LEVEL, pixel_share=PIXEL_SHARE_LIMIT,
                 mean_limit=MEAN_LIMIT, max_age_polls=MAX_AGE_POLLS, max_age_seconds=MAX_AGE_SECONDS):
        self._clock = clock
        self.max_diff, self.pixel_level, self.pixel_share, self.mean_limit = max_diff, pixel_level, pixel_share, mean_limit
        self.max_age_polls, self.max_age_seconds = max_age_polls, max_age_seconds
        self._refs = {}
        self.polls = 0
        self.checks = {"board": 0, "player": 0}
        self.reasons = {}                    # reason -> count, over every (poll, slot) check
        self.eligible = {"board": 0, "player": 0}
        self.outcomes = {}                   # for eligible checks: agree / disagree / ...
        self.fresh_decisive = 0              # checks whose fresh read was confident
        self.compared = {}                   # (class of fresh result) -> {"max": hist, "mean": hist, "share": hist}
        self.eligible_age = _Histogram(AGE_BINS)
        self.box_shift = _Histogram(SHIFT_BINS)
        self.examples = []                   # a few disagreements: numbers only
        self.errors = 0
        self.seconds = 0.0

    # -- the one entry point ---------------------------------------------------------------------------------------------------------

    def observe(self, images, covered, reads, layout_fresh, generation, memory, boxes):
        """Fold in one poll. Never raises, never modifies anything it is given, and returns nothing.

        images   slot -> the BGR crop read this poll (covered slots have none)      covered  slots under a tracker window
        reads    slot -> the read the tracker is actually using this poll             layout_fresh  the table was located in this frame
        generation  card memory's generation (a new round or a clear changes it)      memory  slot -> {"card", "settled", "held"}
        boxes    slot -> (left, top, width, height) the crop was cut from
        """
        started = time.perf_counter()
        try:
            self.polls += 1
            for slot in SHADOW_SLOTS:
                self._slot(slot, images.get(slot), slot in covered, reads.get(slot), layout_fresh, generation,
                           memory.get(slot) or {}, boxes.get(slot))
        except Exception:                    # noqa: BLE001 - a measurement must never stop a poll
            self.errors += 1
        self.seconds += time.perf_counter() - started

    def forget_all(self):
        """Drop every reference (the table was covered, lost or restarted): nothing seen before it counts afterwards."""
        self._refs.clear()

    # -- per slot --------------------------------------------------------------------------------------------------------------------

    def _count(self, slot, reason):
        kind = slot_type(slot)
        self.checks[kind] += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        if reason == ELIGIBLE:
            self.eligible[kind] += 1

    def _slot(self, slot, image, covered, read, layout_fresh, generation, state, box):
        if covered:
            self._refs.pop(slot, None)       # what it showed under the window is not a reference
            self._count(slot, R_COVERED)
            return
        if image is None:
            self._refs.pop(slot, None)
            self._count(slot, R_NO_PICTURE)
            return
        if not layout_fresh:
            self._refs.pop(slot, None)       # boxes that were remembered, not found, are not a basis for a reference
            self._count(slot, R_LAYOUT)
            return
        now_grey = grey(image)
        present = bool(read and read.get("present"))
        confident = bool(read and read.get("confident"))
        card = read.get("card") if confident else None
        if confident:
            self.fresh_decisive += 1
        ref = self._refs.get(slot)
        reason, metrics = self._decide(ref, now_grey, layout_fresh, generation, state, box)
        outcome = None
        if metrics is not None and ref is not None:
            outcome = (O_SAME if confident and card == ref["card"] else O_CHANGED if confident else
                       O_UNSURE if present else O_EMPTY)
            bucket = self.compared.setdefault(outcome, {"max": _Histogram(MAX_BINS), "mean": _Histogram(MEAN_BINS), "share": _Histogram(SHARE_BINS)})
            bucket["max"].add(metrics[0])
            bucket["mean"].add(metrics[1])
            bucket["share"].add(metrics[2])
        self._count(slot, reason)
        if reason == ELIGIBLE:
            self.eligible_age.add(ref["age"])
            kind = "agree" if outcome == O_SAME else "disagree" if outcome == O_CHANGED else "empty now" if outcome == O_EMPTY else "fresh not confident"
            self.outcomes[kind] = self.outcomes.get(kind, 0) + 1
            if kind in ("disagree", "empty now") and len(self.examples) < self.EXAMPLES:
                self.examples.append({"slot_type": slot_type(slot), "outcome": kind, "max": metrics[0], "mean": round(metrics[1], 2),
                                      "share_pct": round(metrics[2], 3), "age_polls": ref["age"]})
            ref["age"] += 1                  # reused: the reference stays
        else:
            self._refs[slot] = {"grey": now_grey, "box": box, "generation": generation, "card": card, "confident": confident,
                                "born": self._clock(), "age": 0}

    def _decide(self, ref, now_grey, layout_fresh, generation, state, box):
        """(reason, metrics-or-None). Metrics are computed whenever the two pictures are comparable, whatever the reason."""
        if ref is None:
            return R_NO_REFERENCE, None
        if ref["generation"] != generation:
            return R_RESET, None
        if box is None or ref["box"] != box:
            if box is not None:
                self.box_shift.add(max(abs(a - b) for a, b in zip(box, ref["box"])))
            return R_MOVED, None
        if ref["grey"].shape != now_grey.shape:
            return R_SIZE, None
        metrics = difference(now_grey, ref["grey"], self.pixel_level)
        if not ref["confident"]:
            return R_NOT_DECISIVE, metrics
        if not state.get("settled"):
            return R_UNSETTLED, metrics
        if state.get("card") != ref["card"]:
            return R_MEMORY_DIFFERS, metrics
        if state.get("held"):
            return R_HELD, metrics
        if ref["age"] >= self.max_age_polls or self._clock() - ref["born"] > self.max_age_seconds:
            return R_AGE, metrics
        largest, mean, share = metrics
        if largest > self.max_diff:
            return R_CHANGED_MAX, metrics
        if share > self.pixel_share * 100.0:
            return R_CHANGED_SHARE, metrics
        if mean > self.mean_limit:
            return R_CHANGED_MEAN, metrics
        return ELIGIBLE, metrics

    # -- reporting -------------------------------------------------------------------------------------------------------------------

    def summary(self):
        """Counts and histogram bins only - numbers and fixed words, nothing about a picture."""
        checks = sum(self.checks.values())
        eligible = sum(self.eligible.values())
        return {
            "rule": {"max_diff": self.max_diff, "pixel_level": self.pixel_level, "pixel_share_pct": self.pixel_share * 100.0,
                     "mean_limit": self.mean_limit, "max_age_polls": self.max_age_polls, "max_age_seconds": self.max_age_seconds},
            "polls": self.polls, "slot_checks": checks, "by_slot_type": dict(self.checks),
            "eligible": eligible, "eligible_by_slot_type": dict(self.eligible),
            "eligibility_rate": round(eligible / checks, 4) if checks else None,
            "eligible_outcomes": dict(self.outcomes),
            "disagreements": self.outcomes.get("disagree", 0) + self.outcomes.get("empty now", 0),
            "fresh_reads_that_were_decisive": self.fresh_decisive,
            "refusal_reasons": dict(sorted(((k, v) for k, v in self.reasons.items() if k != ELIGIBLE), key=lambda item: -item[1])),
            "differences_by_fresh_result": {name: {key: hist.as_dict() for key, hist in bucket.items()} for name, bucket in self.compared.items()},
            "age_when_eligible": self.eligible_age.as_dict(),
            "box_shift_when_moved_px": self.box_shift.as_dict(),
            "disagreement_examples": list(self.examples),
            "errors": self.errors, "overhead_ms_per_poll": round(self.seconds * 1000.0 / self.polls, 3) if self.polls else None,
        }

    def log_line(self):
        return "SHADOW_REUSE " + json.dumps(self.summary(), sort_keys=True, separators=(",", ":"))
