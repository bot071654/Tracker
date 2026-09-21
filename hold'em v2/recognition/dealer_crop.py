"""Cutting the dealer's card out of its box before it is read.

Measured in a live run of twelve rounds (13 September, 21:24-21:35), the
dealer's boxes are wider than the card inside them - the calibrated ones
especially, 107-112 pixels around a card 86-96 pixels wide - and the general
face finder merges the card with light pixels beside it. The index strip it cuts
then starts in the felt, and when the strip is binarised that felt becomes a
solid bar glued to the left of the rank: "|K" was read as a ten, "|2" as a
seven, "|9" at 0.78 instead of 0.95. A sliver of the gold table logo beside the
pip took one five of hearts' suit score from 0.98 to 0.36.

Cut tightly to the card's own face first and those readings come back. Over 184
live dealer crops whose true card is known, confident correct readings rose
from 26 to 35 (18 to 28 at 0.85 or better), confident wrong readings fell from 6
to 4 - every one of them below the dealer gate's 0.80 floor - and replaying the
dealer gate over the saved readings of eleven rounds recovered six dealer cards,
lost none and accepted nothing false.

Only the dealer's two cards are cut this way. Board, player and result-panel
cards are read exactly as before.
"""

import cv2
import numpy as np

# A card face is whiter and less coloured than the presence test asks for. The
# presence test lets through the gold of the table logo and the edge of a hand,
# and the general face finder then closes the gap between them and the card.
FACE_VALUE_MIN = 175
FACE_SATURATION_MAX = 60

# What a dealer card's face looks like inside its box, measured on the 184
# crops: width over height 0.80-0.92 for the middle half; the white fills about
# 0.59 of its bounding box, since the index, pips and barcode are not white. A
# region outside these bounds - a sleeve, two cards touching, a hand across the
# card - is not cut, and the box is read exactly as it always was.
ASPECT_RANGE = (0.55, 1.0)
# 0.55 refused a dealer 10 of spades lying flat and plainly visible (live, 17
# September): its big black "10" and two pips leave white filling 0.537 of the
# face. Uncut, thin gold lines of the table logo joined the card's outline, felt
# came into the index strip, and it read as nothing or as a club at 0.52; cut, it
# reads 10S at 0.955. Over the 136 refused dealer crops of that session whose
# true card is known, 0.52 recovers that card and reads nothing wrong; 0.50 adds
# a confident wrong 10C at 0.787. A lower floor only cuts crops that were not
# cut before - a face that already cleared 0.55 is cut exactly as it was.
MIN_FILL = 0.52
MIN_WIDTH_SHARE = 0.45
MIN_HEIGHT_SHARE = 0.60

# The margin kept around the face: the same fraction table_layout keeps around
# a card it finds, measured there as the margin a pip needs.
PAD_RATIO = 0.03

# How different two dealer crops may be and still count as the same picture, in
# mean grey levels. Between saved crops of one card lying still the difference
# was 0.8; nudged a little, 4.6-18.8; turned over, covered by a hand or a
# sleeve, 51-86. Only what is plainly the same picture is not read again.
STILL_DIFF = 2.0


def face_box(image):
    """(x, y, width, height) of the dealer card's face in `image`, or None."""
    if image is None or image.size == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] > FACE_VALUE_MIN)
             & (hsv[:, :, 1] < FACE_SATURATION_MAX)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    if count < 2:
        return None
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, width, height, area = (int(value) for value in stats[index, :5])
    if height == 0:
        return None
    card_shaped = (ASPECT_RANGE[0] <= width / float(height) <= ASPECT_RANGE[1]
                   and area >= MIN_FILL * width * height
                   and width >= MIN_WIDTH_SHARE * image.shape[1]
                   and height >= MIN_HEIGHT_SHARE * image.shape[0])
    return (x, y, width, height) if card_shaped else None


def cut(image):
    """(image to read, face box or None). The box itself when no face is found."""
    box = face_box(image)
    if box is None:
        return image, None
    x, y, width, height = box
    pad_x, pad_y = int(width * PAD_RATIO), int(height * PAD_RATIO)
    return (image[max(0, y - pad_y):y + height + pad_y,
                  max(0, x - pad_x):x + width + pad_x], box)


def still(image, previous):
    """True when two dealer crops are the same picture give or take video noise."""
    if image is None or previous is None or image.shape != previous.shape:
        return False
    now = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.int16)
    before = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY).astype(np.int16)
    return float(np.abs(now - before).mean()) <= STILL_DIFF
