"""Live recognition diagnostics: where each card was cut from, what it read as,
and how that squares with memory, the result panel and the rest of the table.

When the live tracker shows a wrong or stale card, the dash or the wrong value
on screen does not say which of these went wrong:

    the card's box is in the wrong place, or cut badly
    the rank was misread, or the suit was
    the right card was put in the wrong slot
    card memory is holding something the screen no longer shows
    a card from the previous round was carried into this one
    the result panel disagrees with the table

This module separates them. Every poll it compares the raw reading of this
frame with card memory and with what is shown, looks for the same card in two
places, watches for a slot flipping between readings, and marks where rounds
start and end. When saving is on, it keeps the picture of each moment that
changed: the game area with every box drawn and labelled, each card's crop,
and the rank and suit glyphs the templates were actually matched against.

It reports and nothing else. No reading, slot, memory entry or stored hand is
changed by anything here, and a failure here never stops a poll.
"""

import json
import logging
import os
import queue
import threading
import time
from collections import deque

import cv2
import numpy as np

from config.settings import CARD_SLOTS, ROOT, SLOT_LABELS

logger = logging.getLogger(__name__)

DEBUG_ROOT = os.path.join(ROOT, "debug")

# Confident raw readings kept per slot to judge whether it is flipping.
UNSTABLE_WINDOW = 5

# Polls at the start of a round during which a card in the same slot as the
# round before is reported as a possible carry-over.
CARRY_POLLS = 10

# Frames saved in one session before saving stops, so a long session cannot
# fill the disk.
MAX_SAVES = 1500

# Margin kept around the game area when it is saved.
VIEWPORT_PAD = 40

COLOURS = {                        # BGR
    "CONFIRMED": (60, 200, 60),
    "CONFIRMING": (0, 215, 255),
    "HELD": (255, 200, 0),
    "AMBIGUOUS": (0, 140, 255),
    "UNKNOWN": (60, 60, 230),
    "EMPTY": (150, 150, 150),
    "PROBLEM": (255, 0, 255),
}


# -- checks ----------------------------------------------------------------------

def duplicates(cards):
    """{card: [slots]} for every card found in more than one slot."""
    places = {}
    for slot in CARD_SLOTS:
        card = (cards or {}).get(slot)
        if card:
            places.setdefault(card, []).append(slot)
    return {card: slots for card, slots in places.items() if len(slots) > 1}


def panel_row_duplicates(panel_sides):
    """{side: [cards]} for a card appearing twice within one panel row.

    A card on the board legitimately appears in both rows, and a card in a row
    is legitimately also on the table: those are the same physical card shown
    twice by the casino, not a duplicate. Twice in one row is impossible.
    """
    found = {}
    for side, data in (panel_sides or {}).items():
        cards = [card for card in (data or {}).get("cards") or [] if card]
        repeated = sorted({card for card in cards if cards.count(card) > 1})
        if repeated:
            found[side] = repeated
    return found


class UnstableWatch:
    """Notices a slot whose confident raw reading keeps changing."""

    def __init__(self, window=UNSTABLE_WINDOW):
        self._recent = {slot: deque(maxlen=window) for slot in CARD_SLOTS}

    def reset(self):
        for recent in self._recent.values():
            recent.clear()

    def observe(self, raw_cards):
        """Returns {slot: recent readings} for slots showing two or more cards."""
        for slot in CARD_SLOTS:
            card = raw_cards.get(slot)
            if card:
                self._recent[slot].append(card)
        return {slot: list(recent) for slot, recent in self._recent.items()
                if len(set(recent)) > 1}


class RoundWatch:
    """ROUND START and ROUND END, from the card memory's generation.

    The memory starts a new generation every few polls while the table is
    empty, so a generation only counts as a round once it holds a card.
    """

    def __init__(self):
        self.round_id = None
        self.generation = None
        self.cards = {}
        self.previous = {}
        self.previous_id = None
        self.polls = 0

    def observe(self, generation, memory_cards):
        """Returns (events, carry_over).

        events      [("ROUND END", id, cards)] and/or [("ROUND START", id, {})]
        carry_over  {slot: card} - a card in the same slot as the round before,
                    in this round's first CARRY_POLLS polls
        """
        events = []
        memory_cards = {slot: card for slot, card in (memory_cards or {}).items() if card}
        if generation != self.generation:
            self.generation = generation
            if self.round_id is not None and self.cards:
                events.append(("ROUND END", self.round_id, dict(self.cards)))
                self.previous, self.previous_id = dict(self.cards), self.round_id
            self.round_id, self.cards, self.polls = None, {}, 0
        if self.round_id is None and memory_cards:
            self.round_id = generation
            events.append(("ROUND START", generation, {}))
        if self.round_id is not None:
            self.cards = memory_cards or self.cards
            self.polls += 1
        carry = {}
        if self.round_id is not None and self.polls <= CARRY_POLLS and self.previous:
            carry = {slot: card for slot, card in memory_cards.items()
                     if self.previous.get(slot) == card}
        return events, carry


# -- one slot, position -> crop -> recognition ----------------------------------------

def slot_rows(regions, region_sources, images, reads, memory_cards, memory_support,
              shown, statuses, unstable):
    """One report per slot. Pure data: no images."""
    rows = []
    for slot in CARD_SLOTS:
        region = (regions or {}).get(slot)
        image = (images or {}).get(slot)
        read = (reads or {}).get(slot) or {}
        detail = read.get("detail") or {}
        support = (memory_support or {}).get(slot) or (0.0, 0, 0.0)
        raw = read.get("card") if read.get("confident") else None
        memory = (memory_cards or {}).get(slot)
        flags = []
        if raw and memory and raw != memory:
            flags.append("MEMORY MISMATCH")
        if memory and not (shown or {}).get(slot):
            flags.append("NOT SHOWN")          # held in memory, refused for display
        if slot in (unstable or {}):
            flags.append("UNSTABLE")
        rows.append({
            "slot": slot,
            "label": SLOT_LABELS[slot],
            "region": dict(region) if region else None,
            "region_source": (region_sources or {}).get(slot),
            "crop": list(image.shape[:2]) if image is not None else None,
            "present": bool(read.get("present")),
            "raw_card": raw,
            "read_as": read.get("card"),
            "confidence": read.get("confidence", 0.0),
            "rank_confidence": read.get("rank_confidence"),
            "suit_confidence": read.get("suit_confidence"),
            "suit_margin": read.get("suit_margin"),
            "ink": detail.get("ink"),
            "rank_scores": detail.get("rank_scores"),
            "suit_scores": detail.get("suit_scores"),
            "cuts_tried": detail.get("cuts_tried"),
            "cut_chosen": detail.get("cut_chosen"),
            "glyphs_available": "rank_glyph" in detail,
            "memory_card": memory,
            "memory_readings": support[1],
            "memory_best": support[2],
            "shown_card": (shown or {}).get(slot),
            "status": (statuses or {}).get(slot),
            "flags": flags,
        })
    return rows


# -- pictures -----------------------------------------------------------------------------

def viewport(regions, panel_regions, shape, origin=(0, 0), pad=VIEWPORT_PAD):
    """(x0, y0, x1, y1) in frame pixels around every box, or None."""
    boxes = [r for r in list((regions or {}).values()) + list(panel_regions or []) if r]
    if not boxes:
        return None
    x0 = min(r["left"] for r in boxes) - origin[0] - pad
    y0 = min(r["top"] for r in boxes) - origin[1] - pad
    x1 = max(r["left"] + r["width"] for r in boxes) - origin[0] + pad
    y1 = max(r["top"] + r["height"] for r in boxes) - origin[1] + pad
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(shape[1], int(x1)), min(shape[0], int(y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


SHORT = {"dealer_1": "D1", "dealer_2": "D2", "flop_1": "F1", "flop_2": "F2",
         "flop_3": "F3", "turn": "T", "river": "R", "player_1": "P1", "player_2": "P2"}
FONT = cv2.FONT_HERSHEY_SIMPLEX
TEXT = (220, 220, 220)


def _tag(image, text, centre_x, centre_y, colour):
    """A short tag in the middle of a box, clear of the corners' printed index."""
    (width, height), _ = cv2.getTextSize(text, FONT, 0.5, 1)
    x, y = int(centre_x - width / 2) - 3, int(centre_y - height / 2) - 3
    cv2.rectangle(image, (x, y), (x + width + 6, y + height + 6), (0, 0, 0), -1)
    cv2.putText(image, text, (x + 3, y + height + 2), FONT, 0.5, colour, 1, cv2.LINE_AA)


def _colour(row):
    return COLOURS["PROBLEM"] if row["flags"] else COLOURS.get(row["status"], COLOURS["EMPTY"])


def _legend(rows, panel_sides, report):
    """Every reading as text, for the panel beside the picture."""
    lines = []
    for row in rows:
        suits = " ".join("%s%.2f" % (label, score)
                         for label, score in row.get("suit_scores") or [])
        if row["read_as"]:
            first = "%-2s %-8s %-4s rank %.2f  suit %.2f%s  all %.2f%s" % (
                SHORT[row["slot"]], row["slot"], row["read_as"],
                row["rank_confidence"] or 0, row["suit_confidence"] or 0,
                " (%s)" % suits if suits else "", row["confidence"] or 0,
                "" if row["raw_card"] else "  REFUSED")
        else:
            first = "%-2s %-8s --   %s" % (SHORT[row["slot"]], row["slot"],
                                          "card present, not read" if row["present"]
                                          else "no card")
        region = row["region"]
        box = ("%d,%d %dx%d %s" % (region["left"], region["top"], region["width"],
                                   region["height"], row["region_source"] or "")
               if region else "no box")
        second = "   memory %-3s shown %-3s %-10s box %s%s" % (
            row["memory_card"] or "--", row["shown_card"] or "--", row["status"] or "--",
            box, ("   " + " ".join(row["flags"])) if row["flags"] else "")
        lines += [(first, _colour(row)), (second, _colour(row)), ("", TEXT)]
    for side in ("player", "dealer"):
        data = (panel_sides or {}).get(side) or {}
        cards = " ".join((read.get("card") if read.get("confident") else "?")
                         for read in data.get("reads") or []) or "--"
        lines.append(("panel %-6s %-17s %s%s" % (side, cards, data.get("state") or "NOT_VISIBLE",
                                                "  (previous round)" if data.get("stale") else ""),
                      TEXT))
    for side, (verdict, _) in sorted(((report or {}).get("verification") or {}).items()):
        lines.append(("center vs panel %-6s %s" % (side, verdict.upper()),
                      COLOURS["PROBLEM"] if verdict == "conflict" else TEXT))
    problems = (report or {}).get("problems") or []
    lines.append(("problems: %s" % (len(problems) if problems else "none"),
                  COLOURS["PROBLEM"] if problems else TEXT))
    lines += [("  " + text[:100], COLOURS["PROBLEM"]) for text in problems]
    return lines


def annotate(view, rect, origin, rows, panel_sides, report=None):
    """The saved game area with every box drawn, and every reading beside it.

    Nothing is written over a card except a short tag in its middle, so the
    index in its corners - what the recogniser reads - stays in view. The
    readings go in a legend to the right, where they cannot cover each other
    however the camera has framed the table.
    """
    marked = view.copy()
    x0, y0 = rect[0] + origin[0], rect[1] + origin[1]
    for row in rows:
        region = row["region"]
        if not region:
            continue
        left, top = region["left"] - x0, region["top"] - y0
        right, bottom = left + region["width"], top + region["height"]
        cv2.rectangle(marked, (left, top), (right, bottom), _colour(row), 2)
        _tag(marked, SHORT[row["slot"]], (left + right) / 2.0, (top + bottom) / 2.0,
             _colour(row))
    for data in (panel_sides or {}).values():
        for read in (data or {}).get("reads") or []:
            region = read.get("region")
            if region:
                left, top = region["left"] - x0, region["top"] - y0
                cv2.rectangle(marked, (left, top),
                              (left + region["width"], top + region["height"]),
                              COLOURS.get(read.get("status"), COLOURS["EMPTY"]), 1)

    lines = _legend(rows, panel_sides, report)
    step = 17
    width = max(cv2.getTextSize(text, FONT, 0.45, 1)[0][0] for text, _ in lines if text) + 20
    height = max(marked.shape[0], step * len(lines) + 16)
    canvas = np.full((height, marked.shape[1] + width, 3), 24, np.uint8)
    canvas[:marked.shape[0], :marked.shape[1]] = marked
    for index, (text, colour) in enumerate(lines):
        cv2.putText(canvas, text, (marked.shape[1] + 10, 18 + index * step), FONT, 0.45,
                    colour, 1, cv2.LINE_AA)
    return canvas


def _plain(value):
    """A JSON-ready copy: images dropped, numbers and tuples made plain."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()
                if not hasattr(v, "shape")}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value if not hasattr(v, "shape")]
    if hasattr(value, "item"):
        return value.item()
    return value


class Writer:
    """Writes saved moments on a background thread, so saving never slows a poll."""

    def __init__(self, root=DEBUG_ROOT, max_saves=MAX_SAVES):
        self.root = root
        self.max_saves = max_saves
        self.saved = 0
        self.dropped = 0
        self._queue = queue.Queue(maxsize=16)
        self._thread = threading.Thread(target=self._run, name="diagnostics", daemon=True)
        self._thread.start()

    def submit(self, stamp, view, annotated, crops, report):
        if self.saved >= self.max_saves:
            return False
        try:
            self._queue.put_nowait((stamp, view, annotated, crops, report))
            self.saved += 1
            if self.saved == self.max_saves:
                logger.warning("Live diagnostics: %d moments saved, saving stops for "
                               "this session", self.max_saves)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def flush(self, timeout=10.0):
        """Wait until everything submitted has been written."""
        deadline = time.time() + timeout
        while self._queue.unfinished_tasks and time.time() < deadline:
            time.sleep(0.02)

    def _run(self):
        while True:
            stamp, view, annotated, crops, report = self._queue.get()
            try:
                live = os.path.join(self.root, "live")
                folder = os.path.join(self.root, "card_crops", stamp)
                os.makedirs(live, exist_ok=True)
                os.makedirs(folder, exist_ok=True)
                cv2.imwrite(os.path.join(live, "frame_%s.png" % stamp), view)
                cv2.imwrite(os.path.join(live, "frame_%s_annotated.png" % stamp), annotated)
                with open(os.path.join(live, "frame_%s.json" % stamp), "w",
                          encoding="utf-8") as handle:
                    json.dump(_plain(report), handle, indent=2)
                for name, image in crops.items():
                    if image is not None and getattr(image, "size", 0):
                        cv2.imwrite(os.path.join(folder, "%s.png" % name), image)
            except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
                logger.warning("Live diagnostics could not save %s: %s", stamp, exc)
            finally:
                self._queue.task_done()


# -- the whole poll ---------------------------------------------------------------------------

class LiveDiagnostics:
    """Everything above, run once per poll by the tracker."""

    def __init__(self, root=DEBUG_ROOT, max_saves=MAX_SAVES):
        self.root = root
        self.max_saves = max_saves
        self.unstable = UnstableWatch()
        self.rounds = RoundWatch()
        self.writer = None
        self._signature = None
        self._problems = set()

    def poll(self, frame, origin, regions, region_sources, images, reads,
             memory_cards, memory_support, shown, statuses, generation, state,
             layout_source, panels=None, save=False, panel_crops=None):
        raw = {slot: ((reads or {}).get(slot) or {}).get("card")
               if ((reads or {}).get(slot) or {}).get("confident") else None
               for slot in CARD_SLOTS}
        events, carry = self.rounds.observe(generation, memory_cards)
        if any(kind == "ROUND START" for kind, _, _ in events):
            self.unstable.reset()
        unstable = self.unstable.observe(raw)
        panel_sides = (panels or {}).get("sides") or {}
        verdicts = (panels or {}).get("verdicts") or {}

        found = {
            "frame": duplicates(raw),
            "memory": duplicates(memory_cards),
            "shown": duplicates(shown),
            "panel_row": panel_row_duplicates(panel_sides),
        }
        rows = slot_rows(regions, region_sources, images, reads, memory_cards,
                         memory_support, shown, statuses, unstable)
        # Each slot holding a card that is also in another slot is marked on its
        # own row, so its box is drawn as a problem rather than as a good read.
        by_slot = {row["slot"]: row for row in rows}
        for where in ("frame", "memory", "shown"):
            for slots in found[where].values():
                for slot in slots:
                    if "DUPLICATE" not in by_slot[slot]["flags"]:
                        by_slot[slot]["flags"].append("DUPLICATE")

        problems = []
        for row in rows:
            if "MEMORY MISMATCH" in row["flags"]:
                problems.append("MEMORY MISMATCH %s: frame %s, memory %s"
                                % (row["slot"], row["raw_card"], row["memory_card"]))
        for slot, seen in sorted(unstable.items()):
            problems.append("UNSTABLE RECOGNITION %s: %s" % (slot, " ".join(seen)))
        for where in ("frame", "memory", "shown"):
            for card, slots in sorted(found[where].items()):
                problems.append("DUPLICATE CARD DETECTION (%s): %s in %s"
                                % (where, card, " and ".join(slots)))
        for side, cards in sorted(found["panel_row"].items()):
            problems.append("DUPLICATE CARD DETECTION (panel %s row): %s"
                            % (side, " ".join(cards)))
        if carry:
            problems.append("POSSIBLE CARRY-OVER from round %s: %s" % (
                self.rounds.previous_id,
                " ".join("%s=%s" % (slot, card) for slot, card in sorted(carry.items()))))
        for side, (verdict, message) in sorted(verdicts.items()):
            if verdict == "conflict":
                problems.append("CENTER vs PANEL CONFLICT (%s): %s" % (side, message))

        for kind, round_id, cards in events:
            if kind == "ROUND END":
                logger.info("ROUND END id=%s | %s", round_id,
                            " ".join("%s=%s" % (s, cards.get(s) or "--") for s in CARD_SLOTS))
            else:
                logger.info("ROUND START id=%s", round_id)
        current = set(problems)
        for text in problems:
            if text not in self._problems:
                logger.warning("LIVE DIAGNOSTIC %s", text)
        self._problems = current

        report = {
            "round_id": self.rounds.round_id,
            "generation": generation,
            "state": state,
            "layout_source": layout_source,
            "slots": rows,
            "duplicates": found,
            "unstable": unstable,
            "carry_over": carry,
            "round_events": [[kind, round_id] for kind, round_id, _ in events],
            "panel": {side: {"state": data.get("state"), "confirmed": data.get("confirmed"),
                             "cards": data.get("cards"), "stale": data.get("stale"),
                             "reads": data.get("reads")}
                      for side, data in panel_sides.items()},
            "verification": {side: list(pair) for side, pair in verdicts.items()},
            "problems": problems,
            "saved": self.writer.saved if self.writer else 0,
        }
        if save and frame is not None:
            self._save(frame, origin, regions, images, reads, rows, panel_sides,
                       report, panel_crops)
            report["saved"] = self.writer.saved if self.writer else 0
        return report

    def _signature_of(self, rows, panel_sides, generation):
        return (generation,
                tuple((row["slot"], row["read_as"], row["raw_card"], row["memory_card"],
                       row["shown_card"], row["status"]) for row in rows),
                tuple((side, tuple(data.get("confirmed") or ()), tuple(data.get("cards") or ()))
                      for side, data in sorted(panel_sides.items())))

    def _save(self, frame, origin, regions, images, reads, rows, panel_sides, report,
              panel_crops):
        anything = (any(row["present"] for row in rows)
                    or any((data or {}).get("detected") for data in panel_sides.values()))
        if not anything:
            return
        signature = self._signature_of(rows, panel_sides, report["generation"])
        if signature == self._signature:
            return
        panel_regions = [read.get("region") for data in panel_sides.values()
                         for read in (data or {}).get("reads") or []]
        rect = viewport(regions, panel_regions, frame.shape, origin)
        if rect is None:
            return
        self._signature = signature
        if self.writer is None:
            self.writer = Writer(self.root, self.max_saves)

        view = frame[rect[1]:rect[3], rect[0]:rect[2]].copy()
        report["viewport"] = {"left": rect[0] + origin[0], "top": rect[1] + origin[1],
                              "width": rect[2] - rect[0], "height": rect[3] - rect[1]}
        crops = {}
        for slot in CARD_SLOTS:
            image = (images or {}).get(slot)
            if image is None:
                continue
            crops[slot] = image.copy()
            detail = ((reads or {}).get(slot) or {}).get("detail") or {}
            if "rank_glyph" in detail:
                crops["%s_rank" % slot] = detail["rank_glyph"]
                crops["%s_suit" % slot] = detail["suit_glyph"]
        for (side, index), image in (panel_crops or {}).items():
            crops["panel_%s_%d" % (side, index + 1)] = image.copy()
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
        report["stamp"] = stamp
        self.writer.submit(stamp, view,
                           annotate(view, rect, origin, rows, panel_sides, report),
                           crops, dict(report))
