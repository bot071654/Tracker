"""Find card templates that were learned wrongly.

Teaching saves whatever glyph the recogniser extracted. If a card was labelled
by mistake, or its index was split badly, the bad sample sits in the library
and quietly drags every later reading towards the wrong answer - a clipped
club, for instance, matches almost anything and turns kings of spades into
kings of clubs.

Two checks:

  shape     a suit template that is not shaped like a pip at all
  parts     a template holding more than it should - a rank that swept in the
            suit, the barcode or the upside-down index at the foot of the card,
            or a suit with a stray fragment stuck to it, which shrinks the pip
            when the glyph is scaled and makes it match its own suit poorly
  rivals    a suit template that matches the other suit of its colour clearly
            better than its own

Run:  python tools/audit_templates.py                 (report only)
      python tools/audit_templates.py --remove        (delete what it flags)
      python tools/audit_templates.py --sheet out.png (contact sheet to eyeball)
"""

import argparse
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import TEMPLATE_DIR  # noqa: E402
from recognition.card_recognizer import _match_score  # noqa: E402

SAME_COLOUR = {"H": "D", "D": "H", "S": "C", "C": "S"}

# How much better a template must match the other suit of its colour before it
# is called wrong. Spades and clubs are close shapes and honest samples sit
# only a little apart, so a small lead means nothing.
RIVAL_MARGIN = 0.15


def learned(kind):
    """Templates learned from the casino.

    Skips the drawn fallbacks: those with no suffix at all, and the extra font
    samples marked "_font", which are shapes from Windows fonts rather than
    from the table and are not expected to match the casino's own artwork.
    """
    return [path for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, kind, "*.png")))
            if "_" in os.path.basename(path)
            and not os.path.basename(path).split("_", 1)[1].startswith("font")]


def label_of(path):
    return os.path.basename(path).split("_")[0]


def pip_shape(image):
    """(solidity, widest) for a suit glyph, or None when it has no ink.

    `widest` is where the widest row sits, 0 at the top: a heart is widest near
    the top, a diamond across its middle.
    """
    binary = (image > 127).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    hull = cv2.contourArea(cv2.convexHull(contour))
    rows = np.nonzero(binary.sum(axis=1))[0]
    if hull <= 0 or rows.size == 0:
        return None
    height = rows[-1] - rows[0] + 1
    widest = (int(np.argmax(binary.sum(axis=1))) - rows[0]) / float(max(1, height))
    return cv2.contourArea(contour) / hull, widest


# Envelopes measured from known-good samples of this casino's artwork, widened
# so an honest sample is never flagged.
PIP_LIMITS = {
    "H": {"solidity": (0.86, 0.99), "widest": (0.05, 0.35)},
    "D": {"solidity": (0.90, 1.00), "widest": (0.35, 0.60)},
    "S": {"solidity": (0.74, 0.92), "widest": (0.38, 0.65)},
    "C": {"solidity": (0.70, 0.92), "widest": (0.38, 0.70)},
}


def check_shape(path):
    """Reason this suit template is not shaped like its label, or None."""
    label = label_of(path)
    limits = PIP_LIMITS.get(label)
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return "unreadable file"
    shape = pip_shape(image)
    if shape is None or limits is None:
        return "no ink" if shape is None else None
    solidity, widest = shape
    low, high = limits["solidity"]
    if not low <= solidity <= high:
        return "solidity %.2f outside %s for %s" % (solidity, (low, high), label)
    low, high = limits["widest"]
    if not low <= widest <= high:
        return "widest point %.2f outside %s for %s" % (widest, (low, high), label)
    return None


def check_rival_suit(path):
    """Reason this suit template matches the other suit of its colour better."""
    label = label_of(path)
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return "unreadable file"

    def best(target):
        score = 0.0
        pattern = os.path.join(TEMPLATE_DIR, "suits", "%s_*.png" % target)
        for other in glob.glob(pattern):
            if os.path.basename(other) == os.path.basename(path):
                continue
            image_other = cv2.imread(other, cv2.IMREAD_GRAYSCALE)
            if image_other is not None:
                score = max(score, _match_score(image, image_other))
        return score

    # Spades and clubs are genuinely close, so a small difference means little.
    # Only a clear preference for the other suit indicates a bad sample.
    rival = SAME_COLOUR[label]
    own, other = best(label), best(rival)
    if other > own + RIVAL_MARGIN:
        return "matches %s better than %s (%.2f vs %.2f)" % (rival, label, other, own)
    return None


def marks_in(path):
    """Number of separate marks in a template, or None when unreadable."""
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    binary = (image > 127).astype(np.uint8)
    ink = binary.sum()
    if ink == 0:
        return 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    return sum(1 for index in range(1, count) if stats[index][4] >= 0.05 * ink)


def check_parts(path, limit):
    """Reason this template holds more marks than it should, or None.

    A suit is a single shape. A rank is one glyph, or two for a "10". Anything
    beyond that is the split having swept in something that is not part of it.
    """
    marks = marks_in(path)
    if marks is None:
        return "unreadable file"
    if marks == 0:
        return "no ink"
    if marks > limit:
        return "%d separate marks - more than a %s" % (
            marks, "suit" if limit == 1 else "rank")
    return None


def audit():
    flagged = []
    for path in learned("suits"):
        reason = (check_shape(path) or check_parts(path, limit=1)
                  or check_rival_suit(path))
        if reason:
            flagged.append((path, reason))
    for path in learned("ranks"):
        reason = check_parts(path, limit=2)
        if reason:
            flagged.append((path, reason))
    return flagged


def contact_sheet(paths, destination):
    """Write the flagged templates side by side so they can be eyeballed."""
    tiles = []
    for path in paths:
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        canvas = np.zeros((80, 60), np.uint8)
        canvas[:image.shape[0], :image.shape[1]] = image[:80, :60]
        tiles.append(np.hstack([canvas, np.full((80, 6), 128, np.uint8)]))
    if not tiles:
        return False
    cv2.imwrite(destination, cv2.resize(np.hstack(tiles), None, fx=3, fy=3,
                                        interpolation=cv2.INTER_NEAREST))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="delete flagged templates")
    parser.add_argument("--sheet", help="write a contact sheet of flagged templates here")
    args = parser.parse_args()

    flagged = audit()
    total = len(learned("suits")) + len(learned("ranks"))
    if not flagged:
        print("All %d learned templates look consistent." % total)
        return 0

    print("Flagged %d of %d learned templates:\n" % (len(flagged), total))
    for path, reason in flagged:
        print("  %-28s %s" % (os.path.relpath(path, TEMPLATE_DIR), reason))

    if args.sheet and contact_sheet([p for p, _ in flagged], args.sheet):
        print("\nContact sheet: %s" % args.sheet)

    if args.remove:
        for path, _ in flagged:
            os.remove(path)
        print("\nRemoved %d template(s). Teach those cards again when they appear."
              % len(flagged))
    else:
        print("\nRe-run with --remove to delete them, then teach those cards again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
