"""Per-poll timing, system load, crop stability and table-loss evidence for the validation harness.

A 600-second practice session (PHT-validation-20261019-193150) ran with a median in-round tick of 578 ms, a largest poll gap of 2.24 s and
nine crops re-read on every tick, and four rounds did not complete - two of them with the dealer's cards read on one poll only before the
table was lost within a second. The run could not say WHY from what it kept: the timing lines are windows of 20 ticks, not per poll;
nothing recorded how loaded the machine was; nothing recorded how much a card's pixels move from one poll to the next (the number that decides
whether re-reading every crop every tick is necessary); and no frame was kept when the dealer's cards appeared and the table then vanished.

This module keeps those four things, for the harness only:

  * per poll: the tracker's own stage timings (capture, locate, read, panel, ...), the gap since the previous update, and how many slots were
    read afresh;
  * per poll: a light CPU sample - how busy the whole machine was and how much of that was this process - since the previous poll;
  * per poll: for every card slot, the mean grey-level change of its crop against the previous poll's crop (the metric the tracker's own
    dealer-crop "still" test uses), and in the summary how often that change is 0, small or large;
  * per run, bounded: when a dealer card is read, the round is not complete, and within about two seconds the tracker loses the table, the
    two frames (the poll that read the dealer and the poll that lost the table), the dealer crops and a short timeline are saved, once per
    round and at most `max_exposure` in the run.

DIAGNOSTICS ONLY. It reads the update the tracker already emitted and the frame the tracker already captured; it changes nothing, writes nothing
itself (the harness's saver does, into a new folder of the run's evidence), captures nothing, sleeps never, and every part is guarded so a failure
is counted and the poll's line still comes back. A value the data path does not carry is None, never a guess.
"""

import collections
import ctypes
import os
import time

import cv2
import numpy as np

from read_diagnostics import CARD_SLOTS, DEALER_SLOTS, _box, _slice

STAGES = (("total", "total"), ("capture", "capture"), ("locate", "roi"), ("crop", "crop"), ("read", "recognition"), ("memory", "memory"),
          ("gate", "gate"), ("panel", "panel"), ("diagnostics", "diagnostics"))
CHANGES = ("screen_diff", "table_diff")                 # grey-level changes between polls the tracker measures itself (not milliseconds)
EXPOSURE_WINDOW_S = 2.0                                  # the table lost this soon after a dealer card was read is the case worth a frame
MAX_EXPOSURE_CAPTURES = 6                                # incidents saved per run (two frames and two crops each)
TIMELINE_POLLS = 12
CROP_DIFF_LIMIT = 5000                                   # values kept per slot for the summary's percentiles
DIFF_STEPS = (0.5, 1.0, 2.0, 4.0, 8.0)                   # mean grey-level change: the tracker's dealer "still" tolerance is 2.0
LAYOUT_LOST = ("cached", "lost")                         # the tracker's words for "the table was not found in this frame"


def _percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))] if ordered else None


def _round(value, places=1):
    return None if value is None else round(float(value), places)


# -- system load ----------------------------------------------------------------------------------------------------------------------

class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def windows_system_times():
    """(idle, kernel, user) CPU time of the whole machine in 100 ns units - kernel includes idle - or None where it cannot be asked."""
    if os.name != "nt":
        return None
    try:
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return tuple((part.high << 32) | part.low for part in (idle, kernel, user))
    except Exception:  # noqa: BLE001 - load is a refinement, never a reason to stop
        return None


class CpuSampler:
    """How busy the machine was, and how much of it this process, since the previous sample.

    Percentages of the machine's whole capacity (all logical processors). The first sample, and any with no time elapsed, has no answer.
    """

    def __init__(self, system_times=windows_system_times, clock=time.perf_counter, process_time=time.process_time, cpus=None):
        self._system, self._clock, self._process = system_times, clock, process_time
        self.cpus = int(cpus or os.cpu_count() or 1)
        self._last = None

    def sample(self):
        """{"system": %, "process": %} or None. Never raises."""
        try:
            now = (self._clock(), self._process(), self._system())
        except Exception:  # noqa: BLE001
            return None
        last, self._last = self._last, now
        if last is None or now[0] - last[0] <= 0:
            return None
        process = max(0.0, min(100.0, 100.0 * (now[1] - last[1]) / ((now[0] - last[0]) * self.cpus)))
        system = None
        if now[2] is not None and last[2] is not None:
            idle, total = now[2][0] - last[2][0], (now[2][1] - last[2][1]) + (now[2][2] - last[2][2])
            if total > 0:
                system = max(0.0, min(100.0, 100.0 * (1.0 - idle / float(total))))
        return {"system": _round(system), "process": _round(process)}


# -- stage timing ------------------------------------------------------------------------------------------------------------------------

def timing_fields(payload, extras):
    """The tracker's own stage timings for this poll in milliseconds (and its two grey-level change measures), or None for a missing one."""
    timing = (payload or {}).get("timing")
    timing = timing if isinstance(timing, dict) else {}
    fields = {name: _round(timing.get(key)) if isinstance(timing.get(key), (int, float)) else None for name, key in STAGES}
    for key in CHANGES:
        fields[key] = _round(timing.get(key), 2) if isinstance(timing.get(key), (int, float)) else None
    fresh = (extras or {}).get("fresh")
    fields["fresh"] = len(fresh) if fresh is not None else None
    return fields


# -- how much a card's pixels move between polls ----------------------------------------------------------------------------------------

class CropChangeWatch:
    """Per slot, the mean grey-level change of this poll's crop against the previous poll's crop (None when there is nothing to compare)."""

    def __init__(self):
        self._previous = {}                              # slot -> grey copy of the crop last poll
        self.values = collections.defaultdict(list)      # slot -> changes, capped
        self.exact = collections.Counter()               # slot -> polls whose crop was pixel-identical to the previous one
        self.compared = collections.Counter()            # slot -> comparisons made
        self.shape_changed = collections.Counter()       # slot -> polls whose box moved or resized, so no comparison was made

    def observe(self, frame, origin, rows):
        """{slot: change or None} for the slots the tracker's diagnostics rows give a box for."""
        changes = {}
        seen = set()
        for row in rows or []:
            slot = row.get("slot") if isinstance(row, dict) else None
            box = _box(row.get("region")) if slot in CARD_SLOTS else None
            if box is None or frame is None:
                continue
            crop, _ = _slice(frame, box, origin or (0, 0))
            if crop is None or crop.size == 0:
                continue
            grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            seen.add(slot)
            previous = self._previous.get(slot)
            self._previous[slot] = grey.copy()
            if previous is None:
                changes[slot] = None
                continue
            if previous.shape != grey.shape:
                self.shape_changed[slot] += 1
                changes[slot] = None
                continue
            difference = cv2.absdiff(grey, previous)
            value = float(difference.mean())
            changes[slot] = round(value, 2)
            self.compared[slot] += 1
            self.exact[slot] += int(not difference.any())
            if len(self.values[slot]) < CROP_DIFF_LIMIT:
                self.values[slot].append(value)
        for slot in list(self._previous):
            if slot not in seen:
                del self._previous[slot]                 # a slot with no box this poll has nothing to be compared against next poll
        return changes

    def summary(self):
        out = {}
        for slot in CARD_SLOTS:
            values = self.values.get(slot)
            if not values:
                continue
            out[slot] = {"comparisons": self.compared[slot], "median": _round(_percentile(values, 0.5), 2), "p90": _round(_percentile(values, 0.9), 2),
                         "share_identical": round(self.exact[slot] / float(self.compared[slot]), 3),
                         **{"share_le_%s" % step: round(sum(1 for v in values if v <= step) / float(len(values)), 3) for step in DIFF_STEPS}}
            if self.shape_changed[slot]:
                out[slot]["box_changed_polls"] = self.shape_changed[slot]
        return out


# -- the dealer is read, the round does not complete, and the table is lost ---------------------------------------------------------------

class ExposureWatch:
    """Save the evidence when a dealer card is read on a round that is not complete and the table is lost within EXPOSURE_WINDOW_S.

    `saver(name, images, info)` stores {"seen_frame", "lost_frame", "dealer_1", "dealer_2" ...} and the info dict beside them and returns the name
    it used; it is the harness's job, so this class touches no disk. One incident per round, at most `max_incidents` saved in the run.
    """

    def __init__(self, saver=None, max_incidents=MAX_EXPOSURE_CAPTURES, window=EXPOSURE_WINDOW_S):
        self.saver, self.max_incidents, self.window = saver, int(max_incidents), float(window)
        self.incidents = []                              # every one, with or without a capture
        self.saved = 0
        self.skipped_by_limit = 0
        self.watched = 0                                 # dealer-read polls on rounds that were not yet complete
        self._seen = None                                # the latest such poll: its time, round, frame, reads and dealer crops
        self._done = set()                               # rounds that have had their incident
        self._timeline = collections.deque(maxlen=TIMELINE_POLLS)

    def observe(self, t, round_id, payload, extras, record):
        """One poll; `record` is this poll's compact line for the timeline. Returns the incident entry when one is raised, else None."""
        self._timeline.append(record)
        lifecycle = (payload.get("lifecycle") or {}).get("state")
        complete = lifecycle == "COMPLETE" or payload.get("state") == "COMPLETE"
        seen = {slot: (payload.get("seen") or {}).get(slot) for slot in DEALER_SLOTS}
        dealer_read = any(seen.values())
        lost = (extras or {}).get("layout_source") in LAYOUT_LOST
        if self._seen is not None and (complete or round_id != self._seen["round"] or t - self._seen["t"] > self.window):
            self._seen = None                            # completed, or another round, or too long ago for this to be the cause
        if self._seen is not None and lost and round_id not in self._done:
            return self._raise(t, round_id, payload, extras, lifecycle)
        if dealer_read and not complete and round_id not in self._done and not lost:
            self.watched += 1
            self._seen = {"t": t, "round": round_id, "state": payload.get("state"), "frame": (extras or {}).get("read_frame"),
                          "origin": (extras or {}).get("origin") or (0, 0), "reads": self._dealer_reads(payload, seen), "crops": self._dealer_crops(payload, extras)}
        return None

    @staticmethod
    def _dealer_reads(payload, seen):
        reads = payload.get("reads") or {}
        return {slot: {"card": seen[slot], "confidence": _round((reads.get(slot) or {}).get("confidence"), 4)} for slot in DEALER_SLOTS if seen[slot]}

    @staticmethod
    def _dealer_crops(payload, extras):
        frame = (extras or {}).get("read_frame")
        crops = {}
        rows = {row.get("slot"): row for row in ((payload.get("diagnostics") or {}).get("slots") or []) if isinstance(row, dict)}
        for slot in DEALER_SLOTS:
            box = _box((rows.get(slot) or {}).get("region"))
            if frame is not None and box is not None:
                crop, _ = _slice(frame, box, (extras or {}).get("origin") or (0, 0))
                if crop is not None:
                    crops[slot] = crop.copy()
        return crops

    def _raise(self, t, round_id, payload, extras, lifecycle):
        seen, self._seen = self._seen, None
        self._done.add(round_id)
        lost_frame = (extras or {}).get("read_frame")
        timeline = [dict(entry) for entry in self._timeline if entry.get("t") is not None and entry["t"] >= seen["t"] - 3.0]
        entry = {"round": round_id, "seen_t": round(seen["t"], 3), "lost_t": round(t, 3), "seconds_between": round(t - seen["t"], 3),
                 "state_at_seen": seen["state"], "state_at_lost": payload.get("state"), "lifecycle_at_lost": lifecycle,
                 "layout_at_lost": (extras or {}).get("layout_source"), "dealer_reads_at_seen": seen["reads"], "capture": None}
        if self.saver is None or self.max_incidents <= 0:
            entry["skipped"] = "not capturing"
        elif self.saved >= self.max_incidents:
            entry["skipped"] = "limit"
            self.skipped_by_limit += 1
        else:
            images = {name: image for name, image in (("seen_frame", seen["frame"]), ("lost_frame", lost_frame)) if image is not None}
            images.update(seen["crops"])
            info = dict(entry, kind="dealer_read_then_table_lost", timeline=timeline,
                        note="seen_frame = the frame the tracker read when it read the dealer's card(s); lost_frame = the frame it read when it could "
                             "not find the table; the dealer crops are cut from seen_frame at the boxes the tracker used")
            try:
                name = self.saver("round%s_dealer_seen_t%.1f_table_lost_t%.1f" % (round_id, seen["t"], t), images, info)
                entry["capture"] = name
                self.saved += 1
            except Exception:  # noqa: BLE001 - failing to save evidence must not stop the run
                entry["skipped"] = "capture failed"
        self.incidents.append(entry)
        return entry

    def summary(self):
        return {"dealer_read_polls_watched": self.watched, "incidents": [dict(i) for i in self.incidents], "captures_saved": self.saved,
                "capture_limit": self.max_incidents, "skipped_by_limit": self.skipped_by_limit, "capturing": self.saver is not None and self.max_incidents > 0}


# -- the harness's single hook ------------------------------------------------------------------------------------------------------------

class PollWatch:
    """What RoundRecorder calls once per update: gap, stage timings, CPU sample and crop changes for the line, and the exposure watch."""

    def __init__(self, exposure_saver=None, max_exposure=MAX_EXPOSURE_CAPTURES, cpu=None, window=EXPOSURE_WINDOW_S):
        self.cpu = cpu if cpu is not None else CpuSampler()
        self.crops = CropChangeWatch()
        self.exposure = ExposureWatch(saver=exposure_saver, max_incidents=max_exposure, window=window)
        self.polls = 0
        self.errors = 0
        self.first_error = None
        self._last_t = None
        self._rows = {"in_round": collections.defaultdict(list), "idle": collections.defaultdict(list)}
        self._gaps = []
        self._cpu = {"system": [], "process": [], "ticks_busy": [], "ticks_quiet": []}

    def _note(self, exc):
        self.errors += 1
        if self.first_error is None:
            self.first_error = "%s: %s" % (type(exc).__name__, exc)

    def observe(self, t, round_id, payload, extras, line):
        """The `perf` entry for this poll's line. Never raises; a part that fails is None and counted."""
        self.polls += 1
        payload, extras = payload or {}, extras or {}
        perf = {"gap_s": None if self._last_t is None else round(t - self._last_t, 3)}
        self._last_t = t
        in_round = (payload.get("lifecycle") or {}).get("state") not in (None, "WAITING")
        for part, make in (("timing", lambda: timing_fields(payload, extras)), ("cpu", self.cpu.sample),
                           ("crop_diff", lambda: self.crops.observe(extras.get("read_frame"), extras.get("origin"),
                                                                    (payload.get("diagnostics") or {}).get("slots")))):
            try:
                perf[part] = make()
            except Exception as exc:  # noqa: BLE001 - one failed measurement must not lose the others
                self._note(exc)
                perf[part] = None
        try:
            self._accumulate(perf, in_round)
        except Exception as exc:  # noqa: BLE001
            self._note(exc)
        try:
            record = {"t": round(t, 3), "state": payload.get("state"), "layout": extras.get("layout_source"), "gap_s": perf["gap_s"],
                      "tick_ms": (perf.get("timing") or {}).get("total"), "read_ms": (perf.get("timing") or {}).get("read"),
                      "dealer_seen": {s: c for s, c in ((payload.get("seen") or {}).items()) if s in DEALER_SLOTS and c},
                      "cpu_system": (perf.get("cpu") or {}).get("system"), "table_boxes": ((line.get("dealer_table") or {}).get("diag") or {}).get("table_boxes")}
            incident = self.exposure.observe(t, round_id, payload, extras, record)
            if incident is not None:
                perf["exposure_incident"] = {"capture": incident["capture"], "seconds_between": incident["seconds_between"]}
        except Exception as exc:  # noqa: BLE001
            self._note(exc)
        return perf

    def _accumulate(self, perf, in_round):
        timing = perf.get("timing") or {}
        bucket = self._rows["in_round" if in_round else "idle"]
        for name, value in timing.items():
            if value is not None and name != "fresh" and name not in CHANGES:
                bucket[name].append(value)
        if perf["gap_s"] is not None:
            self._gaps.append(perf["gap_s"])
        cpu = perf.get("cpu") or {}
        if cpu.get("system") is not None:
            self._cpu["system"].append(cpu["system"])
            if timing.get("total") is not None and in_round:
                self._cpu["ticks_busy" if cpu["system"] >= 85.0 else "ticks_quiet"].append(timing["total"])
        if cpu.get("process") is not None:
            self._cpu["process"].append(cpu["process"])

    def summary(self):
        def stats(values):
            return {"n": len(values), "median": _round(_percentile(values, 0.5)), "p90": _round(_percentile(values, 0.9)), "max": _round(max(values))} if values else None
        gaps = self._gaps
        return {"polls": self.polls, "diagnostic_errors": self.errors, "first_error": self.first_error,
                "timing_ms": {kind: {name: stats(values) for name, values in rows.items()} for kind, rows in self._rows.items()},
                "gaps_s": {"median": _round(_percentile(gaps, 0.5), 3), "max": _round(max(gaps), 3) if gaps else None,
                           "over_1s": sum(1 for g in gaps if g > 1.0), "over_2s": sum(1 for g in gaps if g > 2.0)},
                "cpu_percent": {"samples": len(self._cpu["system"]), "system": stats(self._cpu["system"]), "process": stats(self._cpu["process"]),
                                "in_round_tick_ms_when_system_busy_85_plus": stats(self._cpu["ticks_busy"]),
                                "in_round_tick_ms_when_system_below_85": stats(self._cpu["ticks_quiet"])},
                "crop_change_grey_levels": self.crops.summary(), "table_loss_after_dealer_read": self.exposure.summary()}
