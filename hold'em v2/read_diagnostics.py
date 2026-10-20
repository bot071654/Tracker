"""Evidence for one question: when a card's raw reading disagrees with the card memory, what was in the pixels?

A 10-minute practice session (PHT-validation-20261019-152111) had five raw dealer readings that differed from the finished hand.
All five were black suits read as diamonds, the rank was right every time, none came from a fusion-recovered box, and the dealer's
hand (once a red fingernail) was in or beside the crop where a crop had been saved. The pixels behind the five readings were not
kept: a confident reading saves no crop, and the validation harness only saved a frame when a dealer box was missing. So the cause of
each flip could be argued about but not shown.

This module keeps what that session could not:

  * on every poll, one compact line per DEALER slot - the raw reading, its confidence and margins, the colour the recogniser's own
    diagnostics reported, the card memory's value and whether the two agree, the box and whether it was carried, whether the slot
    was read afresh this poll; and
  * whenever ANY slot's confident raw reading disagrees with the card memory (the tracker's own "MEMORY MISMATCH" condition), the
    crop at the slot's box, a larger crop around it, and a JSON of everything above plus a replay of the saved crop through the
    tracker's own read path and a measurement of the colour of its ink - once per distinct (round, slot, raw, memory), at most
    `max_captures` in a run.

DIAGNOSTICS ONLY. Nothing here is imported by the tracker or the app, and nothing here changes what the tracker reads or decides:

  * it reads the update the tracker already emitted and the frame the tracker already captured for that poll; it captures nothing
    itself and has no clock of its own besides the wall-clock stamp on a saved capture;
  * it mutates neither the update nor the frame (crops are slices, written by the harness's saver, which this module does not touch);
  * the replay calls the recogniser's pure read function on the saved crop, in the harness's thread, on a handful of polls per run;
    it never feeds an answer back anywhere;
  * every step is guarded: a failure is counted and recorded, and the poll's line still comes back - a diagnostic never stops a run;
  * a value the data path does not carry is the string "unavailable", never a guess; null means "there is nothing there".
"""

import datetime

import numpy as np

CARD_SLOTS = ("dealer_1", "dealer_2", "flop_1", "flop_2", "flop_3", "turn", "river", "player_1", "player_2")
DEALER_SLOTS = ("dealer_1", "dealer_2")
UNAVAILABLE = "unavailable"

MAX_CAPTURES = 40            # saved captures per run: 3 files each, tens of kilobytes - a few megabytes in all
PER_KEY_CAPTURES = 1         # per distinct (round, slot, raw, memory): the same flip repeating for seconds is one piece of evidence
MAX_SEED_CAPTURES = 40       # saved seed/switch captures per run, a cap of its own: the disagreement captures never use it up
SEED_CONFIDENCE_BELOW = 0.80 # off the dealer's slots a seed or switch is captured only when its confidence is below this (the dealer gate's floor)
MAX_MEMORY_EVENTS = 300      # seed/switch events kept in the summary (about 600 bytes each); further ones are counted, not kept
CONTEXT_PAD = 0.35           # the wider crop reaches this share of the box's size past each edge, to show a hand, a sleeve, the logo

RED_OVER_OTHER = 30          # the recogniser's own "red" test (card_detector.is_red): red exceeds both green and blue by this much


def _plain(value):
    """`value` with numpy scalars and tuples made JSON-safe, so a diagnostic line can always be written."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return round(float(value), 4)
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in (sorted(value) if isinstance(value, (set, frozenset)) else value)]
    return value


def _get(mapping, key):
    """mapping[key], or "unavailable" when the data path did not carry it at all (None stays None: nothing there)."""
    if not isinstance(mapping, dict) or key not in mapping:
        return UNAVAILABLE
    return _plain(mapping[key])


def _is_number(value):
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool)


def _box(region):
    """[left, top, width, height] of a region dict, or None."""
    if not region:
        return None
    try:
        return [int(region["left"]), int(region["top"]), int(region["width"]), int(region["height"])]
    except (KeyError, TypeError, ValueError):
        return None


def is_mismatch(row):
    """The tracker's own MEMORY MISMATCH condition (recognition/live_diagnostics.py): a confident raw card, a memory card, and they differ."""
    return bool(row) and bool(row.get("raw_card")) and bool(row.get("memory_card")) and row["raw_card"] != row["memory_card"]


def slot_line(slot, row, read, fresh):
    """One slot as the harness records it. `row` is the tracker's diagnostics row, `read` its raw read, `fresh` the slots read afresh.

    Any of them may be missing; whatever is missing is "unavailable". `ink` is only there when the tracker was asked for the working
    (its live_diagnostics switch); the replay in a capture reports it regardless.
    """
    row = row if isinstance(row, dict) else {}
    read = read if isinstance(read, dict) else {}
    have_row = bool(row)
    raw = _get(row, "raw_card") if have_row else (read.get("card") if read.get("confident") else None) if read else UNAVAILABLE
    memory = _get(row, "memory_card")
    ink = row.get("ink") if have_row and row.get("ink") else UNAVAILABLE
    line = {
        "raw": _plain(raw),
        "read_as": _plain(read.get("card")) if read else _get(row, "read_as"),
        "confidence": _get(read, "confidence") if read else _get(row, "confidence"),
        "rank_margin": _get(read, "rank_margin"),
        "suit_margin": _get(read, "suit_margin") if read else _get(row, "suit_margin"),
        "ink": ink,
        "memory": memory,
        "memory_readings": _get(row, "memory_readings"),
        "memory_best": _get(row, "memory_best"),
        "agrees": (raw == memory) if raw not in (None, UNAVAILABLE) and memory not in (None, UNAVAILABLE) else None,
        "shown": _get(row, "shown_card"),
        "status": _get(row, "status"),
        "flags": _plain(row.get("flags")) if have_row and "flags" in row else UNAVAILABLE,
        "box": _box(row.get("region")) if have_row else None,
        "box_source": _get(row, "region_source"),
        "read_this_poll": (slot in fresh) if fresh is not None else UNAVAILABLE,
    }
    if slot in DEALER_SLOTS:
        line["dealer_face"] = _plain(read.get("dealer_face")) if "dealer_face" in read else UNAVAILABLE
    return line


def _slice(frame, box, origin, pad=0.0):
    """(view of `frame` at `box`, [x0, y0, x1, y1] in the frame), or (None, None). The same arithmetic as capture.screen_capture.crop."""
    left, top, width, height = box
    pad_x, pad_y = int(width * pad), int(height * pad)
    x0, y0 = max(0, left - int(origin[0]) - pad_x), max(0, top - int(origin[1]) - pad_y)
    x1 = min(frame.shape[1], left - int(origin[0]) + width + pad_x)
    y1 = min(frame.shape[0], top - int(origin[1]) + height + pad_y)
    if x1 <= x0 or y1 <= y0:
        return None, None
    return frame[y0:y1, x0:x1], [x0, y0, x1, y1]


def replay_read(crop, slot, thresholds):
    """What the tracker's read path says about `crop` now: the dealer's cut first, then read_slot with the working kept.

    Pure: it calls the same functions the tracker calls on the same pixels and returns a dictionary. With the saved crop being what
    the tracker read, `card` should equal the live reading; when it does not, the live reading came from other pixels (a cached read).
    """
    from recognition import card_recognizer, dealer_crop
    image, face = crop, None
    if slot in DEALER_SLOTS:
        image, face = dealer_crop.cut(crop)
    read = card_recognizer.read_slot(image, thresholds[0], thresholds[1], detail=True)
    detail = read.get("detail") or {}
    return _plain({
        "card": read.get("card"), "confident": read.get("confident"), "confidence": read.get("confidence"),
        "rank_margin": read.get("rank_margin"), "suit_margin": read.get("suit_margin"), "present": read.get("present"),
        "ratio": read.get("ratio"), "ink": detail.get("ink"), "suit_scores": detail.get("suit_scores"),
        "rank_scores": detail.get("rank_scores"), "dealer_face": list(face) if face is not None else None,
        "cut_shape": list(image.shape[:2]),
    })


def colour_probe(crop):
    """How red the ink looks in the crop's first index strip, measured the way the recogniser tests it but over the whole strip.

    The recogniser asks whether the MEAN colour of the pixels inside the suit glyph's own mask has red above green and blue by 30. That
    mask is not exposed, so this measures the strip's whole ink mask: it says whether anything red (a nail, skin, gold) is inside the
    index strip's dark pixels, not what the recogniser's own sample was. `crop_red_like_share` is the share of the crop's pixels that
    pass the same red test, whatever they are.
    """
    import cv2
    from recognition import card_detector as cd
    face = cd.card_face(crop, paper_outside=True)
    face = crop if face is None else face
    height, width = face.shape[:2]
    inset_x, inset_y = max(1, int(width * cd.FACE_INSET_RATIO)), max(1, int(height * cd.FACE_INSET_RATIO))
    strip = face[inset_y:max(inset_y + 2, int(height * cd.INDEX_HEIGHT_RATIO)), inset_x:width - inset_x]
    whole = crop.astype(np.int32)
    red_like = (whole[:, :, 2] > whole[:, :, 1] + RED_OVER_OTHER) & (whole[:, :, 2] > whole[:, :, 0] + RED_OVER_OTHER)
    result = {"crop_red_like_share": round(float(red_like.mean()), 4)}
    if strip.size == 0:
        result["index_strip"] = UNAVAILABLE
        return result
    strip = cv2.resize(strip, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    mask = cd._binarize(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)) > 0
    pixels = strip[mask].astype(np.int32)
    result["index_strip_ink_pixels"] = int(len(pixels))
    if len(pixels):
        blue, green, red = (float(pixels[:, i].mean()) for i in range(3))
        result.update({
            "ink_mean_bgr": [round(blue, 1), round(green, 1), round(red, 1)],
            "ink_red_like_share": round(float(((pixels[:, 2] > pixels[:, 1] + RED_OVER_OTHER)
                                               & (pixels[:, 2] > pixels[:, 0] + RED_OVER_OTHER)).mean()), 4),
            "would_test_red_on_whole_strip_mask": bool(red > green + RED_OVER_OTHER and red > blue + RED_OVER_OTHER),
        })
    return result


class ReadWatch:
    """Per-poll dealer lines, and evidence saved whenever a raw reading disagrees with the card memory.

    `saver(name, images, info)` stores {"crop": array, "context": array} and the info dict beside them and returns the name it used; it
    is the harness's job, so this class touches no disk. `thresholds` are the tracker's confidence and presence thresholds, for the replay.
    """

    def __init__(self, saver=None, max_captures=MAX_CAPTURES, per_key=PER_KEY_CAPTURES, thresholds=(0.62, 0.35),
                 seed_saver=None, max_seed_captures=MAX_SEED_CAPTURES):
        self.saver, self.max_captures, self.per_key, self.thresholds = saver, int(max_captures), int(per_key), thresholds
        # Seed/switch captures: their own saver (the harness gives the same kind of saver) and their own cap.
        self.seed_saver, self.max_seed_captures = seed_saver, int(max_seed_captures)
        self.seed_captured = 0
        self.seed_skipped_by_limit = 0
        self.memory_entries = []          # one per seed or switch event, in order: {"round", "slot", "kind", "value", ...}
        self.memory_events_dropped = 0
        self._by_round_slot = {}          # (round, slot) -> its entries, to keep each one's final memory value up to date
        self._seed_keys = set()           # (round, slot, value) already captured
        self._round = object()            # the round the last-value tracking belongs to (a sentinel until the first poll)
        self._last_value = {}             # slot -> the last non-empty memory value seen this round
        self.polls = 0
        self.mismatch_polls = 0
        self.captured = 0
        self.skipped_by_limit = 0
        self.errors = 0
        self.first_error = None
        self.entries = []                 # one per distinct (round, slot, raw, memory), in the order first seen
        self._by_key = {}

    def observe(self, t, round_id, payload, extras):
        """One poll. Returns the line for polls.jsonl - never raises."""
        index = self.polls
        self.polls += 1
        try:
            return self._observe(index, t, round_id, payload or {}, extras or {})
        except Exception as exc:  # noqa: BLE001 - a diagnostic must never stop a run
            self._note_error(exc)
            return {"poll": index, "error": "%s: %s" % (type(exc).__name__, exc)}

    def _note_error(self, exc):
        self.errors += 1
        if self.first_error is None:
            self.first_error = "%s: %s" % (type(exc).__name__, exc)

    def _observe(self, index, t, round_id, payload, extras):
        diagnostics = payload.get("diagnostics")
        rows = {row.get("slot"): row for row in (diagnostics or {}).get("slots") or [] if isinstance(row, dict)}
        reads = payload.get("reads") or {}
        fresh = extras.get("fresh")
        fresh = set(fresh) if fresh is not None else None
        line = {"poll": index, "slots": {slot: slot_line(slot, rows.get(slot), reads.get(slot), fresh) for slot in DEALER_SLOTS}}
        if not rows:
            line["unavailable"] = "no per-slot diagnostics in this update"
        mismatched = []
        for slot in CARD_SLOTS:
            row = rows.get(slot)
            if not is_mismatch(row):
                continue
            mismatched.append(slot)
            self.mismatch_polls += 1
            key = (round_id, slot, row["raw_card"], row["memory_card"])
            entry = self._by_key.get(key)
            if entry is None:
                entry = self._by_key[key] = {"round": round_id, "slot": slot, "raw": row["raw_card"], "memory": row["memory_card"],
                                             "first_t": round(t, 3), "first_poll": index, "occurrences": 0, "captures": []}
                self.entries.append(entry)
            entry["occurrences"] += 1
            if self.saver is None or len(entry["captures"]) >= self.per_key:
                continue
            if self.captured >= self.max_captures:
                self.skipped_by_limit += 1
                continue
            name = self._capture(index, t, round_id, slot, row, reads.get(slot), fresh, payload, extras)
            if name:
                entry["captures"].append(name)
                self.captured += 1
                line["slots"].setdefault(slot, slot_line(slot, row, reads.get(slot), fresh))["capture"] = name
        if mismatched:
            line["mismatch"] = mismatched
        try:
            events = self._memory_events(index, t, round_id, rows, reads, fresh, payload, extras, line)
        except Exception as exc:  # noqa: BLE001 - seed tracking must never cost the poll its line or its disagreement captures
            self._note_error(exc)
            events = []
        if events:
            line["memory_events"] = events
        return line

    def _memory_events(self, index, t, round_id, rows, reads, fresh, payload, extras, line):
        """Notice a slot's memory being seeded, or switching value, and save the pixels of the poll it happened on.

        Seeded: the slot had no memory value this round and now has one. Switched: it had one and now has another. Only the card
        memory's own value is looked at (the tracker's diagnostics row), a poll that repeats the value is nothing, and a new round
        starts the tracking afresh. Every event is recorded; a capture is taken only for the dealer's slots, or elsewhere when the
        confidence at that moment is below SEED_CONFIDENCE_BELOW (or unknown), once per (round, slot, value), at most
        `max_seed_captures` in a run.
        """
        if round_id != self._round:
            self._round, self._last_value = round_id, {}
        events = []
        for slot in CARD_SLOTS:
            row = rows.get(slot)
            value = row.get("memory_card") if isinstance(row, dict) else None
            if not isinstance(value, str) or not value:
                continue                                           # empty or unavailable: nothing to compare, and nothing is guessed
            for older in self._by_round_slot.get((round_id, slot), ()):
                older["final_memory"] = value                      # the latest value seen this round, kept up to date
            previous = self._last_value.get(slot)
            if value == previous:
                continue
            self._last_value[slot] = value
            kind = "seed" if previous is None else "switch"
            if len(self.memory_entries) >= MAX_MEMORY_EVENTS:
                self.memory_events_dropped += 1
                continue
            read = reads.get(slot)
            confidence = row.get("memory_best")
            if not _is_number(confidence):
                confidence = (read or {}).get("confidence") if isinstance(read, dict) else None
            confidence = float(confidence) if _is_number(confidence) else None
            entry = {"round": round_id, "slot": slot, "kind": kind, "value": value, "previous": previous, "t": round(float(t), 3),
                     "poll": index, "confidence": None if confidence is None else round(confidence, 4),
                     "initial": slot_line(slot, row, read, fresh), "final_memory": value, "capture": None, "skipped": None}
            self.memory_entries.append(entry)
            self._by_round_slot.setdefault((round_id, slot), []).append(entry)
            if slot not in DEALER_SLOTS and confidence is not None and confidence >= SEED_CONFIDENCE_BELOW:
                entry["skipped"] = "not eligible: confidence %.2f is not below %.2f" % (confidence, SEED_CONFIDENCE_BELOW)
            elif (round_id, slot, value) in self._seed_keys:
                entry["skipped"] = "duplicate: this value was already captured for this round and slot"
            elif self.seed_saver is None or self.max_seed_captures <= 0:
                entry["skipped"] = "not capturing"
            elif self.seed_captured >= self.max_seed_captures:
                entry["skipped"] = "limit"
                self.seed_skipped_by_limit += 1
            else:
                name = self._capture(index, t, round_id, slot, row, read, fresh, payload, extras, saver=self.seed_saver, kind=kind,
                                     name="round%s_%s_%s%s_t%.1f" % (round_id, slot, kind, value, t),
                                     extra={"memory_event": {"kind": kind, "value": value, "previous": previous, "confidence": entry["confidence"],
                                                             "confidence_unavailable": confidence is None,
                                                             "final_memory": "not known at capture time: see summary.json, "
                                                                             "read_evidence.memory_events.entries"}})
                if name:
                    entry["capture"] = name
                    self._seed_keys.add((round_id, slot, value))
                    self.seed_captured += 1
                    line["slots"].setdefault(slot, slot_line(slot, row, read, fresh))["seed_capture"] = name
                else:
                    entry["skipped"] = "capture failed"
            events.append({"slot": slot, "kind": kind, "value": value, "capture": entry["capture"], "skipped": entry["skipped"]})
        return events

    def _capture(self, index, t, round_id, slot, row, read, fresh, payload, extras, saver=None, kind="disagreement", name=None, extra=None):
        """Save the crop, the wider crop and the JSON for one mismatch (or seed/switch). Returns the saved name, or None (and counts the error)."""
        try:
            lifecycle = payload.get("lifecycle") or {}
            info = {"wall_clock": datetime.datetime.now().isoformat(timespec="milliseconds"), "t": round(float(t), 3), "poll": index,
                    "kind": kind, "round": round_id, "slot": slot, "state": payload.get("state"), "lifecycle": lifecycle.get("state"),
                    "result_status": lifecycle.get("result_status"), "slot_read": slot_line(slot, row, read, fresh),
                    "origin": _plain(extras.get("origin")),
                    "note": "crop = the frame's pixels at the box the tracker read this poll (not necessarily the pixels behind a cached "
                            "reading: see slot_read.read_this_poll); replay = the tracker's own read path run on that crop now; "
                            "colour_probe = harness measurement, not the recogniser's own sample"}
            images = {}
            # The frame the tracker read this tick when the harness noted it; else the latest grab, which can be a later one.
            frame, box = extras.get("read_frame"), _box(row.get("region"))
            info["frame_source"] = "the frame the tracker read this poll" if frame is not None else UNAVAILABLE
            if frame is None:
                frame = extras.get("frame")
                if frame is not None:
                    info["frame_source"] = "the harness's latest grab (may be later than the poll's own read)"
            if frame is None or box is None:
                info["crop"] = "%s: %s" % (UNAVAILABLE, "no frame in this update" if frame is None else "no box for this slot")
            else:
                crop, crop_box = _slice(frame, box, extras.get("origin") or (0, 0))
                context, context_box = _slice(frame, box, extras.get("origin") or (0, 0), CONTEXT_PAD)
                info["frame_shape"] = list(frame.shape[:2])
                if crop is None:
                    info["crop"] = "%s: box outside the frame" % UNAVAILABLE
                else:
                    images["crop"], info["crop_box_in_frame"] = crop, crop_box
                    images["context"], info["context_box_in_frame"] = context, context_box
                    for label, function in (("replay", lambda: replay_read(crop, slot, self.thresholds)),
                                            ("colour_probe", lambda: colour_probe(crop))):
                        try:
                            info[label] = function()
                        except Exception as exc:  # noqa: BLE001 - one failed measurement must not lose the crop
                            self._note_error(exc)
                            info[label] = {"error": "%s: %s" % (type(exc).__name__, exc)}
                    live = info["slot_read"]["raw"]
                    replayed = info.get("replay") or {}
                    if "card" in replayed:
                        info["replay_reproduces_live_reading"] = bool(replayed.get("confident") and replayed["card"] == live)
            if extra:
                info.update(extra)
            name = name or "round%s_%s_raw%s_mem%s_t%.1f" % (round_id, slot, row.get("raw_card"), row.get("memory_card"), t)
            return (saver or self.saver)(name, images, info)
        except Exception as exc:  # noqa: BLE001 - failing to save evidence must not stop the run
            self._note_error(exc)
            return None

    def summary(self):
        return {"polls_watched": self.polls, "mismatch_polls": self.mismatch_polls, "distinct_mismatches": len(self.entries),
                "captures_saved": self.captured, "capture_limit": self.max_captures, "skipped_by_limit": self.skipped_by_limit,
                "capturing": self.saver is not None, "diagnostic_errors": self.errors, "first_error": self.first_error,
                "entries": [dict(entry) for entry in self.entries],
                "memory_events": {"events": len(self.memory_entries), "events_dropped": self.memory_events_dropped,
                                  "captures_saved": self.seed_captured, "capture_limit": self.max_seed_captures,
                                  "skipped_by_limit": self.seed_skipped_by_limit,
                                  "capturing": self.seed_saver is not None and self.max_seed_captures > 0,
                                  "seeds": sum(1 for e in self.memory_entries if e["kind"] == "seed"),
                                  "switches": sum(1 for e in self.memory_entries if e["kind"] == "switch"),
                                  "seeds_whose_final_value_differs": sum(1 for e in self.memory_entries
                                                                         if e["kind"] == "seed" and e["final_memory"] != e["value"]),
                                  "entries": [dict(entry, final_differs=entry["final_memory"] != entry["value"])
                                              for entry in self.memory_entries]}}
