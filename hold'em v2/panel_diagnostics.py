"""Result-panel thumbnail measurements across polls, for the validation harness. DIAGNOSTICS ONLY: it watches, it never decides.

WHY

The panel reader keeps a cache of thumbnails it has already read, and treats a new picture as "already read" when its largest
per-pixel difference from a cached picture is at most `PanelTracker.CACHE_TOLERANCE`. Whether that number is safe - small enough
never to reuse the read of a different card, large enough to absorb the casino's video noise - was judged from 215 refused black-suit
thumbnails and one pair of frames. This module measures it from a live session, without changing the cache, the reader or any threshold.

WHAT IT RECORDS

For every thumbnail the tracker's panel snapshot reports, every poll: the card the reader proposed, whether it accepted it, the largest
per-pixel difference (the same statistic the cache uses: `cv2.absdiff(...).max()` over all three channels) from the previous poll's
picture at that position, from the first picture seen at that position since it appeared, and from every picture a mirror of the
tracker's cache holds. The mirror follows the tracker's rule exactly - a picture within the tolerance of a cached one is a hit, any other
is a miss and, unless it was queued behind the reading budget, becomes a new cached picture, the oldest going when the cache is full - and
takes its tolerance and size from the caller, so it describes what the real cache saw.

LABELS: WHAT IS KNOWN AND WHAT IS NOT

Nobody tells this module what card a thumbnail shows. A sample is labelled with a card only when ALL of these hold, decided when its
round has ended and never before:

  * the round's result ended `confirmed` (the casino's panel agreed with the table);
  * the reader accepted the thumbnail this poll and proposed card Y;
  * the panel row's FINAL confirmed row (five cards) has exactly Y at exactly this position, and Y appears once in that row;
  * Y is one of the table's own confirmed cards for that seat (the two hole cards and the five board cards) - the table reader, which is
    independent of the panel reader, saw it too;
  * the row was not marked stale (a leftover from an earlier round).

That is agreement of two readers about a card at a position, not inspection by eye, and it is not ground truth. A round being confirmed
does NOT label its thumbnails; every other sample stays unlabelled and is reported as such, with the reason. Pictures are compared
as they are; nothing is inferred about a card from its picture.

READ-ONLY

It reads the update the tracker already emitted and the frame the tracker already captured and changes nothing. It captures nothing,
sleeps never and writes no file itself: thumbnails (at most `MAX_THUMBNAIL_SIDE` pixels a side, never a whole frame) go to the saver the
harness gives it, in a folder of its own, at most `max_captures` samples a run. Every part is guarded so a failure is counted and the
poll's line still comes back.
"""

import array
import collections
import copy
import math

import cv2
import numpy as np

import round_lifecycle as rl
from read_diagnostics import _box, _slice

SIDES = ("player", "dealer")
BOARD = ("flop_1", "flop_2", "flop_3", "turn", "river")
SEATS = {"player": ("player_1", "player_2") + BOARD, "dealer": ("dealer_1", "dealer_2") + BOARD}
ROW_LENGTH = 5                                           # cards in a complete panel row
MAX_THUMBNAIL_SIDE = 120                                 # a picture larger than this on either side is not a thumbnail: not measured, never saved
DEFAULT_MAX_CAPTURES = 80                                # samples saved per run (up to three small pictures each)
KIND_SHARE = {"changed": 0.5, "first_sight": 0.2, "refused": 0.2, "steady": 0.1}    # of the cap, so one kind cannot use it all
STEADY_EVERY = 25                                        # of the polls that look unchanged, one in this many is kept as a steady sample
DIST_LIMIT = 20000                                       # values kept per distribution for the percentiles (every value is still counted)
BINS = ((0, 0, "0"), (1, 3, "1-3"), (4, 7, "4-7"), (8, 15, "8-15"), (16, 31, "16-31"), (32, 63, "32-63"), (64, 127, "64-127"), (128, 255, "128+"))
NO_DIFF = 0xFFFF                                         # in a difference vector: a cached picture of another size, which cannot be compared
EXAMPLE_LIMIT = 20
LABEL_RULE = ("labelled only when the round ended confirmed AND the reader accepted this thumbnail AND the panel row's final confirmed row "
              "has the same card at this same position, once, AND the table's own confirmed cards for that seat include it AND the row was "
              "not stale: agreement of two readers, not ground truth. Everything else is unlabelled.")


def colour_of(card):
    """"red", "black" or "unknown" for a card string like "10H". For an unlabelled sample it is the colour of the card the reader PROPOSED."""
    if not card or not isinstance(card, str):
        return "unknown"
    return "red" if card[-1] in "HD" else "black" if card[-1] in "SC" else "unknown"


def max_diff(a, b):
    """The cache's own statistic: the largest per-pixel, per-channel difference of two same-sized pictures; None for different sizes."""
    if a is None or b is None or a.shape != b.shape:
        return None
    return int(cv2.absdiff(a, b).max())


def _percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))] if ordered else None


class Dist:
    """A distribution of differences (0-255): every value counted in fixed bins, the first DIST_LIMIT kept for the percentiles."""

    def __init__(self):
        self.n = 0
        self.values = []
        self.bins = [0] * len(BINS)

    def add(self, value):
        if value is None:
            return
        self.n += 1
        if len(self.values) < DIST_LIMIT:
            self.values.append(int(value))
        for number, (low, high, _name) in enumerate(BINS):
            if low <= value <= high:
                self.bins[number] += 1
                break

    def summary(self):
        if not self.n:
            return None
        return {"n": self.n, "min": min(self.values), "median": _percentile(self.values, 0.5), "p90": _percentile(self.values, 0.9),
                "max": max(self.values), "bins": {name: self.bins[number] for number, (_l, _h, name) in enumerate(BINS)}}


class _Stats:
    """Everything tallied when a round is evaluated. Holds numbers only, so a provisional copy is cheap."""

    def __init__(self):
        self.dists = collections.defaultdict(lambda: collections.defaultdict(Dist))      # name -> colour -> Dist
        self.labelled = 0
        self.unlabelled_reasons = collections.Counter()
        self.rounds = 0
        self.rounds_confirmed = 0
        self.reuse_correct = 0
        self.reuse_wrong = 0
        self.wrong_examples = []
        self.reread_with_same_card_cached = 0


class _Sample:
    __slots__ = ("id", "t", "round", "side", "index", "card", "confident", "queued", "status", "stale", "prev_diff", "initial_diff", "nearest",
                 "nearest_entry", "hit", "ids", "diffs", "prev", "label", "basis", "entry_id", "saved", "kind")

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))


class _Entry:
    __slots__ = ("id", "pixels")

    def __init__(self, ident, pixels):
        self.id, self.pixels = ident, pixels


class PanelWatch:
    """What RoundRecorder calls once per update. `saver(name, images, info)` stores {"cur", "prev", "initial"} pictures and returns the name used."""

    def __init__(self, saver=None, max_captures=DEFAULT_MAX_CAPTURES, tolerance=None, pool_size=None):
        if tolerance is None or pool_size is None:
            from recognition import result_panel            # the tracker's own numbers, read not changed
            tolerance = result_panel.PanelTracker.CACHE_TOLERANCE if tolerance is None else tolerance
            pool_size = result_panel.PanelTracker.CACHE_SIZE if pool_size is None else pool_size
        self.saver, self.max_captures = saver, int(max_captures)
        self.tolerance, self.pool_size = int(tolerance), int(pool_size)
        self.polls = 0
        self.samples = 0
        self.errors = 0
        self.first_error = None
        self.oversize = 0
        self.shape_changed = 0
        self.capture_failed = 0
        self.skipped_by_limit = collections.Counter()
        self.saved_by_kind = collections.Counter()
        self.reads = collections.Counter()                  # (accepted | refused | queued, colour of the proposed card) -> samples
        self._pool = collections.deque(maxlen=self.pool_size)
        self._next_entry = 0
        self._next_sample = 0
        self._entry_label = {}                              # cached picture id -> label of the sample that made it (None: unlabelled)
        self._prev = {}                                     # (side, index) -> (pixels, sample) of the previous poll
        self._initial = {}                                  # (side, index) -> pixels of the first poll since that position appeared
        self._rounds = {}                                   # round id -> its samples, table cards, panel rows and result status
        self._open = None
        self._steady_seen = 0
        self._saved = []
        self._stats = _Stats()

    # -- one poll ---------------------------------------------------------------------------------------------------------------

    def observe(self, t, round_id, payload, extras):
        """The compact per-poll entry. Never raises: a failure is counted and the entry says so."""
        self.polls += 1
        try:
            return self._observe(t, payload or {}, extras or {})
        except Exception as exc:  # noqa: BLE001 - a diagnostic must never stop a poll
            self.errors += 1
            if self.first_error is None:
                self.first_error = "%s: %s" % (type(exc).__name__, exc)
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

    def _observe(self, t, payload, extras):
        lifecycle = payload.get("lifecycle") or {}
        current = self._attribute(lifecycle, payload)
        sides = (payload.get("panels") or {}).get("sides") or {}
        frame, origin = extras.get("read_frame"), extras.get("origin") or (0, 0)
        entry, seen = {}, set()
        for side in SIDES:
            data = sides.get(side) or {}
            round_data = self._rounds.get(current)
            confirmed = list(data.get("confirmed") or ())
            if round_data is not None and len(confirmed) == ROW_LENGTH and not data.get("stale"):
                round_data["rows"][side] = confirmed
            for index, read in enumerate(data.get("reads") or ()):
                box = _box((read or {}).get("region"))
                crop = _slice(frame, box, origin)[0] if (frame is not None and box is not None) else None
                if crop is None or crop.size == 0:
                    continue
                if crop.shape[0] > MAX_THUMBNAIL_SIDE or crop.shape[1] > MAX_THUMBNAIL_SIDE:
                    self.oversize += 1
                    continue
                key = (side, index)
                seen.add(key)
                sample = self._measure(t, current, side, index, read, crop.copy(), bool(data.get("stale")))
                entry["%s_%d" % (side, index)] = {"card": sample.card, "ok": sample.confident, "pd": sample.prev_diff, "id": sample.initial_diff,
                                                  "nd": sample.nearest, "hit": sample.hit, "k": sample.kind}
                if round_data is not None:
                    round_data["samples"].append(sample)
                else:
                    self._evaluate_samples([sample], None, self._stats, self._entry_label, "outside any round", final=True)
        for key in [key for key in self._prev if key not in seen]:
            del self._prev[key]                              # a position that vanished has nothing to be compared with when it returns
            self._initial.pop(key, None)
        return entry

    def _attribute(self, lifecycle, payload):
        """The round the samples of this poll belong to: the open round, kept while the table is empty and the panels linger."""
        state, round_id = lifecycle.get("state"), lifecycle.get("round_id")
        if state in (rl.IN_PROGRESS, rl.COMPLETE):
            if self._open is None or round_id != self._open:
                if self._open is not None:
                    self._close(self._open)
                self._open = round_id
                self._rounds[round_id] = {"status": None, "cards": {}, "cards_n": 0, "rows": {side: None for side in SIDES}, "samples": []}
            data = self._rounds[round_id]
            data["status"] = lifecycle.get("result_status")
            cards = payload.get("cards") or {}
            filled = sum(1 for value in cards.values() if value)
            if filled >= data["cards_n"]:
                data["cards"], data["cards_n"] = dict(cards), filled
        return self._open

    def _measure(self, t, round_id, side, index, read, pixels, stale):
        key = (side, index)
        previous, initial = self._prev.get(key), self._initial.get(key)
        prev_diff = initial_diff = None
        if previous is not None:
            prev_diff = max_diff(pixels, previous[0])
            if prev_diff is None:
                self.shape_changed += 1
        if initial is not None:
            initial_diff = max_diff(pixels, initial)
        ids = tuple(entry.id for entry in self._pool)
        diffs = array.array("H")
        nearest = nearest_entry = None
        for entry in self._pool:
            value = max_diff(pixels, entry.pixels)
            diffs.append(NO_DIFF if value is None else value)
            if value is not None and (nearest is None or value < nearest):
                nearest, nearest_entry = value, entry.id
        hit = nearest is not None and nearest <= self.tolerance
        card = read.get("card") or None
        confident = bool(read.get("confident")) and card is not None
        queued = bool(read.get("queued"))
        sample = _Sample(id=self._next_sample, t=t, round=round_id, side=side, index=index, card=card, confident=confident, queued=queued,
                         status=read.get("status"), stale=stale, prev_diff=prev_diff, initial_diff=initial_diff, nearest=nearest,
                         nearest_entry=nearest_entry, hit=hit, ids=ids, diffs=diffs, prev=previous[1] if previous else None)
        self._next_sample += 1
        self.samples += 1
        self.reads[("queued" if queued else "accepted" if confident else "refused", colour_of(card))] += 1
        if not hit and not queued:                           # a miss that was read: the tracker would now cache this picture
            sample.entry_id = self._next_entry
            self._pool.append(_Entry(self._next_entry, pixels))
            self._next_entry += 1
        self._prev[key] = (pixels, sample)
        if initial is None:
            self._initial[key] = pixels
        self._maybe_save(sample, pixels, previous[0] if previous else None, initial)
        return sample

    # -- bounded evidence -----------------------------------------------------------------------------------------------------------

    def _kind(self, sample, previous):
        if previous is None:
            return "first_sight"
        if sample.prev_diff is None or sample.prev_diff > self.tolerance or (
                sample.prev is not None and sample.prev.confident and sample.confident and sample.prev.card != sample.card):
            return "changed"
        if not sample.confident and not sample.queued:
            return "refused"
        self._steady_seen += 1
        return "steady" if self._steady_seen % STEADY_EVERY == 1 else None

    def _maybe_save(self, sample, pixels, previous, initial):
        kind = self._kind(sample, previous)
        sample.kind = None
        if kind is None or self.saver is None or self.max_captures <= 0:
            return
        quota = max(1, math.ceil(self.max_captures * KIND_SHARE[kind]))
        if sum(self.saved_by_kind.values()) >= self.max_captures or self.saved_by_kind[kind] >= quota:
            self.skipped_by_limit[kind] += 1
            return
        images = {"cur": pixels}
        if previous is not None:
            images["prev"] = previous
        if initial is not None and initial is not previous and initial is not pixels:
            images["initial"] = initial
        name = "%04d_%s_%s%d_round%s_t%.1f" % (sample.id, kind, sample.side, sample.index, sample.round, sample.t)
        info = {"kind": kind, "sample": sample.id, "round": sample.round, "side": sample.side, "position": sample.index, "t": round(sample.t, 3),
                "read_card": sample.card, "accepted": sample.confident, "queued": sample.queued, "status": sample.status,
                "difference_from_previous_poll": sample.prev_diff, "difference_from_first_picture_at_position": sample.initial_diff,
                "nearest_cached_picture_difference": sample.nearest, "cache_hit_at_tolerance": sample.hit, "tolerance": self.tolerance,
                "label": "decided when the round ends; see summary.json panel_diagnostics.saved", "label_rule": LABEL_RULE}
        try:
            sample.saved = self.saver(name, images, info)
        except Exception:  # noqa: BLE001 - failing to save evidence must not stop the run
            self.capture_failed += 1
            return
        sample.kind = kind
        self.saved_by_kind[kind] += 1
        self._saved.append(sample)

    # -- rounds end: labels, then the measurements that need them ----------------------------------------------------------------

    def _close(self, round_id):
        data = self._rounds.pop(round_id, None)
        if data is not None:
            self._evaluate_round(data, self._stats, self._entry_label, final=True)

    def _evaluate_round(self, data, stats, entry_label, final):
        stats.rounds += 1
        confirmed = data["status"] == rl.RESULT_CONFIRMED
        stats.rounds_confirmed += 1 if confirmed else 0
        self._evaluate_samples(data["samples"], data, stats, entry_label, None, final)

    def _label(self, sample, data):
        """(label or None, why)."""
        if data is None:
            return None, "outside any round"
        if sample.stale:
            return None, "stale panel row (an earlier round's)"
        if data["status"] != rl.RESULT_CONFIRMED:
            return None, "round not confirmed"
        row = data["rows"].get(sample.side)
        if not row:
            return None, "no complete confirmed panel row"
        if not sample.confident or not sample.card:
            return None, "reading refused or absent"
        if sample.index >= len(row) or row[sample.index] != sample.card:
            return None, "not the final confirmed card at this position"
        if row.count(sample.card) != 1:
            return None, "card repeated in the final row"
        if sample.card not in [data["cards"].get(slot) for slot in SEATS[sample.side]]:
            return None, "not among the table's confirmed cards for this seat"
        return sample.card, "agreed"

    def _evaluate_samples(self, samples, data, stats, entry_label, fixed_reason, final):
        for sample in samples:
            sample.label, sample.basis = self._label(sample, data) if fixed_reason is None else (None, fixed_reason)
            if sample.entry_id is not None:
                entry_label[sample.entry_id] = sample.label
            if sample.label is not None:
                stats.labelled += 1
            else:
                stats.unlabelled_reasons[sample.basis] += 1
        for sample in samples:
            if sample.label is None:
                stats.dists["unlabelled_consecutive_polls"][colour_of(sample.card)].add(sample.prev_diff)
                continue
            colour = colour_of(sample.label)
            stats.dists["initial_picture"][colour].add(sample.initial_diff)
            before = sample.prev
            if before is not None and before.label is not None and sample.prev_diff is not None:
                name = "consecutive_polls_same_card" if before.label == sample.label else "consecutive_polls_different_card"
                stats.dists[name][colour].add(sample.prev_diff)
            same = other = None
            for ident, value in zip(sample.ids or (), sample.diffs or ()):
                label = entry_label.get(ident)
                if value == NO_DIFF or label is None:
                    continue
                if label == sample.label:
                    same = value if same is None else min(same, value)
                else:
                    other = value if other is None else min(other, value)
            stats.dists["nearest_cached_picture_of_the_same_card"][colour].add(same)
            stats.dists["nearest_cached_picture_of_a_different_card"][colour].add(other)
            reused = entry_label.get(sample.nearest_entry) if sample.hit else None
            if reused is not None and reused == sample.label:
                stats.reuse_correct += 1
            elif reused is not None:
                stats.reuse_wrong += 1
                if len(stats.wrong_examples) < EXAMPLE_LIMIT:
                    stats.wrong_examples.append({"sample": sample.id, "round": sample.round, "side": sample.side, "position": sample.index,
                                                 "label": sample.label, "would_reuse_the_read_of": reused, "difference": sample.nearest})
            if not sample.hit and same is not None:
                stats.reread_with_same_card_cached += 1
        if final:
            for sample in samples:
                sample.ids = sample.diffs = sample.prev = None       # nothing keeps the old pictures' bookkeeping alive

    # -- the summary --------------------------------------------------------------------------------------------------------------

    def summary(self):
        """Everything so far, rounds still open evaluated provisionally on a copy. Safe to call at any time, any number of times."""
        stats, labels = copy.deepcopy(self._stats), dict(self._entry_label)
        for data in self._rounds.values():
            self._evaluate_round(data, stats, labels, final=False)
        pending = len(self._rounds)

        def dists(name):
            return {colour: dist.summary() for colour, dist in sorted(stats.dists.get(name, {}).items()) if dist.n}
        accepted = sum(n for (kind, _c), n in self.reads.items() if kind == "accepted")
        return {
            "polls": self.polls, "thumbnail_samples": self.samples, "diagnostic_errors": self.errors, "first_error": self.first_error,
            "mirrored_cache": {"tolerance": self.tolerance, "size": self.pool_size,
                               "rule": "a picture within the tolerance of a cached one is a hit; any other is a miss and, unless queued, becomes a "
                                       "cached picture (the oldest leaves when full)"},
            "label_rule": LABEL_RULE,
            "capture": {"capturing": self.saver is not None and self.max_captures > 0, "limit": self.max_captures,
                        "saved": sum(self.saved_by_kind.values()), "saved_by_kind": dict(self.saved_by_kind),
                        "skipped_by_limit": dict(self.skipped_by_limit), "capture_failed": self.capture_failed,
                        "not_thumbnail_sized_skipped": self.oversize, "max_picture_side": MAX_THUMBNAIL_SIDE},
            "readings": {"accepted": accepted, "refused": sum(n for (kind, _c), n in self.reads.items() if kind == "refused"),
                         "queued": sum(n for (kind, _c), n in self.reads.items() if kind == "queued"),
                         "by_kind_and_colour_of_the_proposed_card": {"%s/%s" % key: n for key, n in sorted(self.reads.items())}},
            "rounds": {"evaluated": stats.rounds - pending, "confirmed": stats.rounds_confirmed, "still_open_evaluated_provisionally": pending},
            "labelled_samples": stats.labelled,
            "unlabelled_samples": sum(stats.unlabelled_reasons.values()),
            "unlabelled_reasons": dict(stats.unlabelled_reasons),
            "labelled": {
                "consecutive_polls_same_card": dists("consecutive_polls_same_card"),
                "consecutive_polls_different_card": dists("consecutive_polls_different_card"),
                "initial_picture": dists("initial_picture"),
                "nearest_cached_picture_of_the_same_card": dists("nearest_cached_picture_of_the_same_card"),
                "nearest_cached_picture_of_a_different_card": dists("nearest_cached_picture_of_a_different_card"),
                "cache_would_reuse_the_right_read": stats.reuse_correct,
                "cache_would_reuse_a_different_cards_read": stats.reuse_wrong,
                "wrong_reuse_examples": list(stats.wrong_examples),
                "read_again_although_the_same_card_was_cached": stats.reread_with_same_card_cached},
            "unlabelled": {"consecutive_polls_by_colour_of_the_proposed_card": dists("unlabelled_consecutive_polls"),
                           "note": "unlabelled observations make no claim about which card the picture shows"},
            "saved": [{"name": sample.saved, "kind": sample.kind, "round": sample.round, "side": sample.side, "position": sample.index,
                       "t": round(sample.t, 3), "read_card": sample.card, "accepted": sample.confident, "label": sample.label,
                       "label_basis": sample.basis or "round not yet evaluated"} for sample in self._saved],
        }
