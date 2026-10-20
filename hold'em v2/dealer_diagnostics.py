"""Evidence for one question: when the tracker finds no dealer cards on the table, are they absent, or visible and missed?

Round 155 of a practice session never completed. Both result-panel rows were confirmed 5/5, the table was found freshly every
poll, and yet the dealer's two table slots never got a box, so the dealer's cards were never read. Everything the tracker
reports says only THAT; nothing says WHY. There are two answers that need opposite remedies:

  * the dealer's cards were not on the table (for instance the round ended without a showdown), or
  * they were face up and the layout finder did not turn them into a pair of boxes above the board.

This module looks at the same frame the tracker looked at and says what the layout finder had to work with. It mirrors the
tracker's own steps by calling them (table_layout.find_card_boxes, dominant_card_size, group_rows, assign_slots) rather than
reimplementing them, so what it reports IS what the tracker's finder did; the one thing it adds is a list of the bright blobs
the finder considered and threw away, with the reason, and a look at the part of the screen where the dealer's cards would be.

DIAGNOSTICS ONLY. Nothing here is used by the tracker; it changes no detection, no recognition, no threshold and no rule. It
reads a frame it is given and returns dictionaries; the harness decides what to keep.
"""

import cv2
import numpy as np

from recognition import table_layout as tl

DEALER_SLOTS = ("dealer_1", "dealer_2")
DEALER_ZONE_HEIGHTS = 2.2           # card heights above the board's top edge that count as "where the dealer's cards would be"
BLOB_LIMIT = 24                     # blobs kept per frame, biggest first: a busy desktop must not make the evidence huge


def _blobs(frame, scale=tl.DETECT_SCALE):
    """Every bright, card-sized block the finder looked at, with the verdict it got. Same filters as find_card_boxes."""
    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale and scale != 1.0 else frame
    scale = scale if scale and scale != 1.0 else 1.0
    contours, _ = cv2.findContours(tl._card_mask(small), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < tl.MIN_CARD_WIDTH * scale or h < tl.MIN_CARD_HEIGHT * scale:
            continue
        aspect = w / float(h)
        fill = cv2.contourArea(contour) / float(w * h)
        if not tl.ASPECT_RANGE[0] <= aspect <= tl.ASPECT_RANGE[1]:
            verdict = "rejected: aspect %.2f outside %.2f-%.2f" % (aspect, tl.ASPECT_RANGE[0], tl.ASPECT_RANGE[1])
        elif fill < tl.MIN_FILL:
            verdict = "rejected: fill %.2f under %.2f" % (fill, tl.MIN_FILL)
        else:
            verdict = "card"
        found.append({"box": [int(round(v / scale)) for v in (x, y, w, h)], "aspect": round(aspect, 2), "fill": round(fill, 2),
                      "verdict": verdict})
    found.sort(key=lambda b: -b["box"][2] * b["box"][3])
    return found[:BLOB_LIMIT]


PAPER_VALUE_MIN = tl.PAPER_VALUE_MIN                 # "paper white" (200): the guard's own numbers, so the record and the decision cannot drift apart
PAPER_SATURATION_MAX = tl.PAPER_SATURATION_MAX       # (35)


def paper_white_share(patch):
    """Of the pixels the face test accepts (V > 150 and S < 90), the share that are paper white (V > 200 and S < 35).

    Why it is measured: a face-down card's blue-lattice back (V about 183, S about 52, hue about 106) passes the face test but is
    almost never paper white - 0.000 to 0.018 in 29 saved backs - while white card faces are 0.6 to 0.9 and the worst hand-covered
    face seen was 0.39. table_layout.recover_fused_dealer_pair applies the same measurement as a guard (both halves at least
    FUSED_HALF_PAPER_MIN); this records it, for the pairs that guard lets through.

    Returns {"face_pixels", "paper_pixels", "share"}; "share" is None when there is nothing to measure (no patch, an empty or
    invalid patch, or no pixel passing the face test) - never a guess and never a division by zero.
    """
    empty = {"face_pixels": 0, "paper_pixels": 0, "share": None}
    try:
        if patch is None or getattr(patch, "ndim", 0) != 3 or patch.shape[2] != 3 or patch.size == 0:
            return empty
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        face = (hsv[:, :, 2] > tl.FACE_VALUE_MIN) & (hsv[:, :, 1] < tl.FACE_SATURATION_MAX)
        face_pixels = int(np.count_nonzero(face))
        if face_pixels == 0:
            return empty
        paper_pixels = int(np.count_nonzero(face & (hsv[:, :, 2] > PAPER_VALUE_MIN) & (hsv[:, :, 1] < PAPER_SATURATION_MAX)))
        return {"face_pixels": face_pixels, "paper_pixels": paper_pixels, "share": round(paper_pixels / float(face_pixels), 4)}
    except Exception:  # noqa: BLE001 - a diagnostic must never stop a poll
        return empty


def recovered_halves(frame, pair):
    """The paper-white measurement for each half of a fusion-recovered dealer pair: [{"slot", "box", "face_pixels", ...}, ...].

    `pair` is what table_layout.recover_fused_dealer_pair returned: two (x, y, w, h) boxes in frame coordinates, left card first. The
    box is clipped to the frame; a half that falls outside it, or a pair that is not two boxes, is reported with a null share.
    """
    halves = []
    for slot, box in zip(DEALER_SLOTS, list(pair or [])[:2]):
        entry = {"slot": slot, "box": None, "source": "fusion fallback"}
        try:
            x, y, w, h = [int(v) for v in box]
            entry["box"] = [x, y, w, h]
            entry.update(paper_white_share(frame[max(0, y):max(0, y + h), max(0, x):max(0, x + w)]))
        except Exception:  # noqa: BLE001 - one bad box must not lose the other half
            entry.update({"face_pixels": 0, "paper_pixels": 0, "share": None})
        halves.append(entry)
    return halves


def candidate_record(report):
    """A fusion candidate's report (table_layout.recover_fused_dealer_pair's `report`) in the form kept as evidence, or None.

    None when there was no candidate (the shape was not a geometrically valid pair, so the guard was never asked). Otherwise {"accepted",
    "reason", "threshold", "halves": [{"slot", "box", "paper_white", "status"}]} - the guard's own words and numbers, copied, not
    recomputed: a malformed report gives None rather than a guess.
    """
    try:
        if not report or not report.get("candidate"):
            return None
        return {"accepted": bool(report["accepted"]), "reason": str(report["reason"]), "threshold": float(report["threshold"]),
                "halves": [{"slot": half["slot"], "box": list(half["box"]), "paper_white": None if half["share"] is None else round(float(half["share"]), 4),
                            "status": str(half["status"])} for half in report["halves"]]}
    except Exception:  # noqa: BLE001 - a diagnostic must never stop a poll
        return None


def slot_assignment_diagnostics(frame):
    """What the layout finder had to work with in `frame`, and what it made of it.

    Keys: frame, boxes_found, card_height, table_boxes, rows (every row of cards with its role), community, above_row_lengths,
    dealer_slots_assigned, player_slots_assigned, slots, blobs (everything bright and card-sized, with the verdict) and
    dealer_zone (what is where the dealer's cards would be: blobs inside it, how many were accepted or rejected, and how
    white and how saturated the screen is there).
    """
    height, width = frame.shape[:2]
    result = {"frame": [int(width), int(height)]}
    boxes = tl.find_card_boxes(frame)
    card_height = tl.dominant_card_size(boxes)
    result.update(boxes_found=len(boxes), card_height=None if card_height is None else round(float(card_height), 1))
    blobs = _blobs(frame)
    result["blobs"] = blobs
    if card_height is None:
        result.update(table_boxes=0, rows=[], community=None, above_row_lengths=[], dealer_slots_assigned=False,
                      player_slots_assigned=False, slots=[], dealer_zone=None)
        return result

    table = [box for box in boxes if (1 - tl.SIZE_TOLERANCE) * card_height <= box[3] <= (1 + tl.SIZE_TOLERANCE) * card_height]
    rows = tl.group_rows(table, card_height)
    community = None
    for row in rows:
        if 3 <= len(row) <= 5 and (community is None or len(row) > len(community)):
            community = row
    slots = tl.assign_slots(rows)
    recovered = False
    halves = []
    candidate = None
    if slots and community is not None and not all(slot in slots for slot in DEALER_SLOTS):
        # The same second chance table_layout.locate gives the dealer's pair when the mask fused the two cards into one shape.
        # The optional report is filled by the production function itself, so the threshold and the reasons are its own.
        report = {}
        pair = tl.recover_fused_dealer_pair(frame, community, card_height, table, report=report)
        candidate = candidate_record(report)
        if pair:
            slots = dict(slots)
            slots.update(zip(DEALER_SLOTS, pair))
            recovered = True
            halves = recovered_halves(frame, pair)         # measured AFTER the fallback decided; it informs nothing
    index = rows.index(community) if community is not None else None
    described = []
    for number, row in enumerate(rows):
        role = "other"
        if community is not None:
            role = "community" if row is community else ("above" if number < index else "below")
        entry = {"n": len(row), "y": int(round(sum(b[1] + b[3] / 2.0 for b in row) / len(row))),
                 "x0": int(min(b[0] for b in row)), "x1": int(max(b[0] + b[2] for b in row)), "role": role}
        if community is not None and role in ("above", "below"):
            entry["pair_over_board"] = bool(len(row) == 2 and tl._over(row, community))
        described.append(entry)
    result.update(table_boxes=len(table), rows=described,
                  community=None if community is None else {"n": len(community), "x0": int(community[0][0]),
                                                           "x1": int(community[-1][0] + community[-1][2]),
                                                           "top": int(min(b[1] for b in community))},
                  above_row_lengths=[r["n"] for r in described if r["role"] == "above"],
                  dealer_slots_assigned=all(slot in slots for slot in DEALER_SLOTS),
                  player_slots_assigned=all(slot in slots for slot in tl.PLAYER_SLOTS),
                  slots=sorted(slots), dealer_pair_recovered=recovered, recovered_halves=halves, dealer_zone=None,
                  fusion_candidate=candidate, dealer_pair_refused=bool(candidate and not candidate["accepted"]))
    if community is not None:
        result["dealer_zone"] = _dealer_zone(frame, community, card_height, blobs)
    return result


def _dealer_zone(frame, community, card_height, blobs):
    """The strip above the board, across its width: what is there, in the finder's terms and in plain colour."""
    top = min(b[1] for b in community)
    span = community[-1][0] + community[-1][2] - community[0][0]
    x0 = max(0, int(community[0][0] - 0.25 * span))
    x1 = min(frame_width(frame), int(community[-1][0] + community[-1][2] + 0.25 * span))
    y0, y1 = max(0, int(top - DEALER_ZONE_HEIGHTS * card_height)), max(0, int(top))
    zone = {"box": [x0, y0, x1, y1], "accepted": 0, "rejected": 0, "blobs": []}
    for blob in blobs:
        bx, by, bw, bh = blob["box"]
        if x0 <= bx + bw / 2.0 <= x1 and y0 <= by + bh / 2.0 <= y1:
            zone["blobs"].append(blob)
            zone["accepted" if blob["verdict"] == "card" else "rejected"] += 1
    patch = frame[y0:y1, x0:x1]
    if patch.size:
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        face = (hsv[:, :, 2] > tl.FACE_VALUE_MIN) & (hsv[:, :, 1] < tl.FACE_SATURATION_MAX)
        zone.update(white_fraction=round(float(face.mean()), 3), median_value=int(np.median(hsv[:, :, 2])),
                    median_saturation=int(np.median(hsv[:, :, 1])))
    return zone


def frame_width(frame):
    return int(frame.shape[1])


def compact(diagnostics):
    """The one-line form kept for every poll: enough to tell the cases apart, small enough to keep them all."""
    if not diagnostics:
        return None
    zone = diagnostics.get("dealer_zone") or {}
    line = {"fresh_dealer_slots": bool(diagnostics.get("dealer_slots_assigned")), "recovered": bool(diagnostics.get("dealer_pair_recovered")), "above_rows": diagnostics.get("above_row_lengths"),
            "community": (diagnostics.get("community") or {}).get("n"), "table_boxes": diagnostics.get("table_boxes"),
            "zone_cards": zone.get("accepted"), "zone_rejected": zone.get("rejected"), "zone_white": zone.get("white_fraction")}
    if diagnostics.get("recovered_halves"):
        # Only on a fusion-recovered poll, so every other poll keeps the shape it always had.
        line["halves"] = [{"slot": half.get("slot"), "box": half.get("box"), "source": half.get("source"),
                           "paper_white": half.get("share"), "face_px": half.get("face_pixels")}
                          for half in diagnostics["recovered_halves"]]
    candidate = diagnostics.get("fusion_candidate")
    if candidate and not candidate.get("accepted"):
        # Only on a poll where the paper-white guard refused a geometrically valid pair: why, against what line, and what each half measured.
        # The boxes are kept once per distinct refusal in the round's summary (DealerWatch), not on every poll.
        line["refused"] = {"reason": candidate["reason"], "threshold": candidate["threshold"],
                           "paper_white": [half["paper_white"] for half in candidate["halves"]],
                           "status": [half["status"] for half in candidate["halves"]]}
    return line


class DealerWatch:
    """Per-poll record of dealer-box presence, and a raw frame saved when the panel is up but the dealer's boxes are not.

    Two triggers, each at most once per round and at most `max_frames` frames in a run:

      result panel     the dealer's row of the result panel is visible and the tracker has had no dealer box for `seconds`
                       (3 s). This is the case Round 155 was.
      showdown phase   the table is at the river or complete, a result-panel row is visible, and there has been no dealer box
                       for `showdown_seconds`: catches a round whose dealer panel row never appears either.

    `saver(frame, name, info)` stores the frame (and the info beside it) and returns where; it is the harness's job, so this
    class touches no disk. A box counts as present when it is among the regions the tracker is reading from this poll, carried
    or found fresh - the question is whether the dealer's cards were READ, and `fresh_dealer_slots` in the diagnostics says
    whether this frame's own layout found them.
    """

    MAX_REFUSALS_PER_ROUND = 3            # distinct paper-white refusals kept in full per round (the rest are counted, not kept)
    MAX_REFUSALS_PER_RUN = 40             # and in the whole run
    REFUSAL_BOX_GRID = 10                 # px: a refusal whose boxes only jitter within this grid is the same refusal, counted again

    def __init__(self, saver=None, seconds=3.0, showdown_seconds=6.0, max_frames=12):
        self.saver, self.seconds, self.showdown_seconds, self.max_frames = saver, float(seconds), float(showdown_seconds), int(max_frames)
        self.frames = []                  # [{"name", "trigger", "round", "t"}] in the order saved
        self._since = {}                  # trigger -> time the condition began, for the current round
        self._round = None
        self._saved = set()               # (round, trigger)
        self._refusals = {}               # (round, distinct-refusal key) -> its record in the round's summary, to count repeats
        self._refusals_kept = 0           # full records kept in the run so far

    def _note_refusal(self, t, round_id, state, candidate, summary):
        """Count a paper-white refusal for the round and keep its full record once: not again for every poll that repeats it."""
        summary["refused_polls"] += 1
        reasons = summary["refusal_reasons"]
        reasons[candidate["reason"]] = reasons.get(candidate["reason"], 0) + 1
        for half in candidate["halves"]:
            share = half["paper_white"]
            if share is None:
                summary["refused_halves_unmeasured"] += 1
                continue
            low, high = summary["refused_paper_white_min"], summary["refused_paper_white_max"]
            summary["refused_paper_white_min"] = share if low is None else min(low, share)
            summary["refused_paper_white_max"] = share if high is None else max(high, share)
        grid = self.REFUSAL_BOX_GRID
        key = (round_id, candidate["reason"], tuple(h["status"] for h in candidate["halves"]),
               tuple(tuple(int(v) // grid for v in h["box"]) for h in candidate["halves"]))
        entry = self._refusals.get(key)
        if entry is not None:
            entry["polls"] += 1
            entry["last_t"] = round(t, 3)
            return
        if len(summary["refusals"]) >= self.MAX_REFUSALS_PER_ROUND or self._refusals_kept >= self.MAX_REFUSALS_PER_RUN:
            summary["refusals_dropped"] += 1
            return
        entry = {"t": round(t, 3), "last_t": round(t, 3), "state": state, "polls": 1, "reason": candidate["reason"],
                 "threshold": candidate["threshold"], "halves": candidate["halves"]}
        self._refusals[key] = entry
        summary["refusals"].append(entry)
        self._refusals_kept += 1

    def observe(self, t, round_id, state, rows_visible, tracker_state, frame, record):
        """One poll. `record` is the round's record (or None between rounds); returns the compact line for polls.jsonl."""
        tracker_state = tracker_state or {}
        regions = set(tracker_state.get("regions") or ())
        boxes = [slot in regions for slot in DEALER_SLOTS]
        sources = tracker_state.get("region_sources") or {}
        try:
            diagnostics = slot_assignment_diagnostics(frame) if frame is not None else None
        except Exception as exc:  # noqa: BLE001 - a diagnostic must never stop a run
            diagnostics = {"error": str(exc)}
        line = {"box": [int(b) for b in boxes], "source": [sources.get(slot) for slot in DEALER_SLOTS],
                "layout": tracker_state.get("layout_source"), "seen": tracker_state.get("seen"),
                "present": tracker_state.get("present"), "diag": compact(diagnostics) if diagnostics and "error" not in (diagnostics or {}) else diagnostics}
        if record is None:
            self._since, self._round = {}, None
            return line
        if round_id != self._round:
            self._since, self._round = {}, round_id
        summary = record.setdefault("dealer_boxes", self.blank_summary())
        summary["polls"] += 1
        any_box, both = any(boxes), all(boxes)
        summary["polls_with_any_box"] += int(any_box)
        summary["polls_with_both_boxes"] += int(both)
        if any_box and summary["first_box_s"] is None:
            summary["first_box_s"] = round(t, 3)
        fresh = bool(diagnostics and diagnostics.get("dealer_slots_assigned"))
        summary["polls_with_fresh_dealer_slots"] += int(fresh)
        halves = diagnostics.get("recovered_halves") if diagnostics and "error" not in diagnostics else None
        if halves:
            summary["recovered_polls"] += 1
            for half in halves:
                share = half.get("share")
                if share is None:
                    summary["recovered_halves_unmeasured"] += 1
                    continue
                low, high = summary["recovered_paper_white_min"], summary["recovered_paper_white_max"]
                summary["recovered_paper_white_min"] = share if low is None else min(low, share)
                summary["recovered_paper_white_max"] = share if high is None else max(high, share)
        candidate = diagnostics.get("fusion_candidate") if diagnostics and "error" not in diagnostics else None
        if candidate and not candidate.get("accepted"):
            self._note_refusal(t, round_id, state, candidate, summary)
        if diagnostics and "error" not in diagnostics:
            for length in diagnostics.get("above_row_lengths") or ():
                if length not in summary["above_row_lengths_seen"]:
                    summary["above_row_lengths_seen"].append(length)
            zone = diagnostics.get("dealer_zone") or {}
            summary["zone_cards_max"] = max(summary["zone_cards_max"], zone.get("accepted") or 0)
            summary["zone_rejected_max"] = max(summary["zone_rejected_max"], zone.get("rejected") or 0)
            if zone.get("white_fraction") is not None:
                summary["zone_white_max"] = max(summary["zone_white_max"], zone["white_fraction"])
            for blob in zone.get("blobs") or ():
                if blob["verdict"] != "card":
                    reason = blob["verdict"].split(" ")[1].rstrip(":") if " " in blob["verdict"] else blob["verdict"]
                    summary["zone_rejections"][reason] = summary["zone_rejections"].get(reason, 0) + 1

        dealer_row = bool(rows_visible.get("dealer"))
        any_row = bool(rows_visible.get("player") or rows_visible.get("dealer"))
        conditions = {"result panel": (dealer_row and not any_box, self.seconds),
                      "showdown phase": (state in ("RIVER", "COMPLETE") and any_row and not any_box, self.showdown_seconds)}
        for trigger, (holds, needed) in conditions.items():
            if not holds:
                self._since.pop(trigger, None)
                continue
            began = self._since.setdefault(trigger, t)
            if trigger == "result panel":
                summary["longest_absence_with_dealer_row_visible_s"] = max(summary["longest_absence_with_dealer_row_visible_s"],
                                                                         round(t - began, 3))
            if t - began >= needed and (round_id, trigger) not in self._saved:
                self._save(t, round_id, trigger, began, frame, diagnostics, tracker_state, summary)
        return line

    def _save(self, t, round_id, trigger, began, frame, diagnostics, tracker_state, summary):
        self._saved.add((round_id, trigger))
        if self.saver is None or frame is None or len(self.frames) >= self.max_frames:
            return
        name = "round%s_%s_t%.1f" % (round_id, trigger.replace(" ", "-"), t)
        info = {"round": round_id, "trigger": trigger, "t": round(t, 3), "absent_since_s": round(began, 3),
                "absent_for_s": round(t - began, 3), "tracker": tracker_state, "diagnostics": diagnostics}
        saved = self.saver(frame, name, info)
        entry = {"name": saved or name, "trigger": trigger, "round": round_id, "t": round(t, 3)}
        self.frames.append(entry)
        summary["frames"].append(entry)

    @staticmethod
    def blank_summary():
        return {"polls": 0, "polls_with_any_box": 0, "polls_with_both_boxes": 0, "first_box_s": None,
                "polls_with_fresh_dealer_slots": 0, "longest_absence_with_dealer_row_visible_s": 0.0,
                "above_row_lengths_seen": [], "zone_cards_max": 0, "zone_rejected_max": 0, "zone_white_max": 0.0,
                "zone_rejections": {}, "frames": [], "recovered_polls": 0, "recovered_paper_white_min": None,
                "recovered_paper_white_max": None, "recovered_halves_unmeasured": 0,
                "refused_polls": 0, "refused_paper_white_min": None, "refused_paper_white_max": None, "refused_halves_unmeasured": 0,
                "refusal_reasons": {}, "refusals": [], "refusals_dropped": 0}
