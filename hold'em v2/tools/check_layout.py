"""Report what the dynamic layout detector makes of a frame.

Point it at any full-screen capture of the table and it prints where it found
the cards, what they read as, and which layout it used - which is the quickest
way to see whether a new camera angle or window size is handled.

Run:  python tools/check_layout.py                  (every sample it can find)
      python tools/check_layout.py path/to/shot.png
      python tools/check_layout.py --live           (grab the screen now)
      python tools/check_layout.py --slots          (box/confidence per slot)
"""

import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capture.screen_capture import crop, grab_full_screen  # noqa: E402
from config.settings import CARD_SLOTS, ROOT, load_config  # noqa: E402
from recognition.card_recognizer import read_slot  # noqa: E402
from tracker import card_status  # noqa: E402
from recognition.table_layout import locate, looks_plausible, merge  # noqa: E402

ROWS = [("Player cards", ["player_1", "player_2"]),
        ("Flop", ["flop_1", "flop_2", "flop_3"]),
        ("Turn", ["turn"]),
        ("River", ["river"]),
        ("Dealer cards", ["dealer_1", "dealer_2"])]


def read_frame(frame, fallback=None):
    """Locate and read one frame. Returns (cards, layout_source).

    One frame at a time, so there is no previous layout to fall back on the
    way the running tracker has - here it is the table or the calibrated
    boxes, and nothing when there are neither.
    """
    detail = {}
    layout = locate(frame)
    if looks_plausible(layout):
        source = "dynamic"
    else:
        layout = None
        source = "calibrated" if fallback else "none"

    regions = merge(layout, fallback)
    cards, confidences = {}, []
    for slot in CARD_SLOTS:
        region = regions.get(slot)
        if region is None:
            cards[slot] = None
            continue
        read = read_slot(crop(frame, region))
        cards[slot] = read["card"] if read["confident"] else None
        detail[slot] = read
        if read["present"]:
            confidences.append(read["confidence"])
    average = sum(confidences) / len(confidences) if confidences else 0.0
    return cards, source, average, layout, detail


def slot_table(regions_found, detail):
    """One line per slot: what was found, what it read as, and why.

    A dash on the table above says only that a slot came out empty. It does
    not say whether a box was found there, whether a card was seen in it, or
    what the reader made of it - and those want different fixes. This is the
    breakdown, which is the thing worth having when a card goes missing once
    in twenty rounds and cannot be caught in the act.
    """
    print("")
    print("%-9s %-6s %-8s %-7s %-6s %-6s %-6s  %s"
          % ("SLOT", "BOX", "CARD", "OVERALL", "RANK", "SUIT", "MARGIN", "STATUS"))
    for slot in CARD_SLOTS:
        read = detail.get(slot)
        box = "YES" if slot in regions_found else "no"
        if read is None:
            print("%-9s %-6s %-8s %-7s %-6s %-6s %-6s  %s"
                  % (slot, box, "--", "-", "-", "-", "-", "NO BOX"))
            continue
        status, why = card_status(read)
        print("%-9s %-6s %-8s %-7.3f %-6.3f %-6.3f %-6.3f  %-10s %s"
              % (slot, box, read.get("card") or "--",
                 read.get("confidence", 0.0), read.get("rank_confidence", 0.0),
                 read.get("suit_confidence", 0.0), read.get("suit_margin", 0.0),
                 status, why))


def report(name, frame, fallback=None, per_slot=False):
    cards, source, confidence, layout, detail = read_frame(frame, fallback)
    print("\nScreenshot: %s  (%dx%d)" % (name, frame.shape[1], frame.shape[0]))
    print("Game detected: %s" % ("YES" if layout else "NO"))
    for label, slots in ROWS:
        shown = " ".join(cards.get(slot) or "--" for slot in slots)
        print("%-14s %s" % (label + ":", shown))
    print("Detection confidence: %.2f" % confidence)
    print("Layout source: %s" % source)
    if layout:
        print("Card size found: %dx%d px" % tuple(layout["card_size"]))
    if per_slot:
        slot_table((layout or {}).get("regions") or {}, detail)
    return cards


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--live" in sys.argv:
        config = load_config()
        report("live screen", grab_full_screen(int(config.get("monitor", 1))),
               config.get("regions"), per_slot="--slots" in sys.argv)
        return 0

    paths = args or (sorted(glob.glob(os.path.join(ROOT, "samples", "*.png")))
                     + sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png"))))
    if not paths:
        print("No frames found. Put full-screen captures in samples/ or pass a path.")
        return 1
    for path in paths:
        frame = cv2.imread(path)
        if frame is None:
            print("Could not read %s" % path)
            continue
        report(os.path.basename(path), frame, per_slot="--slots" in sys.argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
