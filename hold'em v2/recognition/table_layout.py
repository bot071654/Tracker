"""Finding the cards on the table in whatever frame the game is showing.

The nine card positions used to be calibrated once, as absolute screen pixels.
That holds only while the browser stays exactly where it was. Move the window,
change the zoom, or let the broadcast cut to a different camera, and every box
is pointing at felt.

So the boxes are worked out from each frame instead. A card face is a bright,
card-shaped block, and the table draws its cards larger than anything else on
screen that looks like one, so they can be found without knowing where they
ought to be. Their arrangement then says which is which:

        dealer          a pair above the community row
    flop flop flop      the row holding three to five cards
      turn river
        player          a pair below the community row

Two things this module deliberately does not do.

It does not decide whether a card is readable - card_detector and
card_recognizer do that, unchanged. This only says where to look.

And it does not insist. A hand sweeping over the table merges three cards into
one shape that fits nothing, and the honest answer is then "I cannot see the
table this frame" rather than a guess. The caller keeps the last layout that
did work, which is what carries the reading through the sweep.
"""

import logging

import cv2
import numpy as np

from config.settings import CARD_SLOTS

logger = logging.getLogger(__name__)

COMMUNITY_SLOTS = ["flop_1", "flop_2", "flop_3", "turn", "river"]
DEALER_SLOTS = ["dealer_1", "dealer_2"]
PLAYER_SLOTS = ["player_1", "player_2"]

# What a card face looks like. The same test card_detector.face_ratio applies,
# so this module and the recogniser agree about what counts as a card.
FACE_VALUE_MIN = 150
FACE_SATURATION_MAX = 90

# A playing card is taller than it is wide. Wide enough for the angle these
# cards are filmed at, tight enough to reject arms, chips and the card shoe.
ASPECT_RANGE = (0.55, 1.05)

# A card is a solid block. Anything hollow is a shape drawn around something
# else - the gold arc of table text, a button ring.
MIN_FILL = 0.70

# Below this a card is too small to read anyway, so finding it buys nothing.
MIN_CARD_WIDTH = 25
MIN_CARD_HEIGHT = 35

# Detection runs on a shrunken frame. The cards are large, and the saving is
# most of the cost: 27.6ms at full size against 6.6ms at half, with the same
# nine cards found either way.
DETECT_SCALE = 0.5

# Cards of one size agree within this fraction of each other.
SIZE_TOLERANCE = 0.25

# Two cards are in the same row when their centres are within this fraction of
# a card height. The community row is dealt in a line, so it is generous.
ROW_TOLERANCE = 0.5

# ...but they also have to be next to each other. Cards dealt in a row are
# shoulder to shoulder - measured on the sample frame, consecutive community
# cards leave gaps of 3 to 8 pixels on a card 110 wide. Anything much further
# apart is not the same row of cards.
#
# This is what keeps the table apart from the rest of the desktop. A white
# panel in another window is card-shaped, card-sized and card-coloured, and
# without this the detector assigned the turn and the third flop card to a
# window 290 pixels to the right: on a real screen the tracker's own window,
# and any pale application beside the game, are full of such rectangles.
#
# Room is left for one card of the row to be missing entirely - a hand resting
# on the board hides it, and the gap across the hole is then about 1.1 card
# widths. Measured: at 1.0 a covered middle card splits the row and costs two
# slots, at 1.5 it costs only the card that is actually hidden, while the other
# window stays 2.6 widths away and is still rejected.
ROW_GAP_LIMIT = 1.5        # of a card width

# A small margin is left around each found card.
#
# What is found is the white face, so a box drawn tight to it puts the pip hard
# against the edge - where card_detector trims blobs that touch the boundary,
# and a trimmed pip is what stops a club being told from a spade. On the sample
# frame the eight of spades reads as a club at 0.03 of separation with no
# margin, and as a spade at 0.118 with this one.
#
# More is not better: at 0.06 the same card falls back to 0.037, and at 0.12 the
# eight of diamonds turns into a five, because the box starts taking in the card
# beside it. This was measured, not guessed.
PAD_RATIO = 0.03


def _card_mask(frame):
    """Card faces as white on black."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] > FACE_VALUE_MIN)
            & (hsv[:, :, 1] < FACE_SATURATION_MAX)).astype(np.uint8) * 255
    # Close the printed index and pips back into the face, then drop specks.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def find_card_boxes(frame, scale=DETECT_SCALE, min_size=None):
    """Every card-shaped bright block in `frame`, as (x, y, w, h) boxes.

    Boxes come back in `frame` coordinates whatever scale the search ran at.
    `min_size` replaces the smallest (width, height) worth finding, for a caller
    looking for something smaller than a table card; the table search leaves it
    alone.
    """
    min_width, min_height = min_size or (MIN_CARD_WIDTH, MIN_CARD_HEIGHT)
    if frame is None or frame.size == 0:
        return []
    if scale and scale != 1.0:
        small = cv2.resize(frame, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
    else:
        small, scale = frame, 1.0

    contours, _ = cv2.findContours(_card_mask(small), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width < min_width * scale or height < min_height * scale:
            continue
        if not ASPECT_RANGE[0] <= width / float(height) <= ASPECT_RANGE[1]:
            continue
        if cv2.contourArea(contour) / float(width * height) < MIN_FILL:
            continue
        boxes.append(tuple(int(round(value / scale))
                           for value in (x, y, width, height)))
    return boxes


def dominant_card_size(boxes):
    """The height the table's own cards are, or None.

    Not the size shared by the most boxes: the Dealer/Player result panels
    draw ten cards at about a third of the size, and the table often shows
    fewer than that - two before the flop. Counting picks the panels every
    time. The table's cards are simply the biggest card-shaped things on
    screen, so the largest size that at least two boxes agree on wins. Two,
    because a lone big block is an arm rather than a row of cards.
    """
    if not boxes:
        return None
    heights = sorted((box[3] for box in boxes), reverse=True)
    for height in heights:
        group = [other for other in heights
                 if (1 - SIZE_TOLERANCE) * height <= other
                 <= (1 + SIZE_TOLERANCE) * height]
        if len(group) >= 2:
            return sum(group) / float(len(group))
    return None


def group_rows(boxes, card_height):
    """Boxes gathered into rows, top row first, each ordered left to right.

    A row is cards that share a line *and* sit next to each other. Sharing a
    line is not enough on its own: anything else on the desktop at the same
    height would join the row, and a pale window beside the game is full of
    card-shaped rectangles.
    """
    tolerance = card_height * ROW_TOLERANCE
    bands = []
    for box in sorted(boxes, key=lambda b: b[1] + b[3] / 2.0):
        centre = box[1] + box[3] / 2.0
        if bands and abs(centre - bands[-1][1]) <= tolerance:
            bands[-1][0].append(box)
        else:
            bands.append(([box], centre))

    rows = []
    for band, _ in bands:
        rows.extend(_split_at_gaps(sorted(band, key=lambda b: b[0])))
    return sorted(rows, key=lambda row: row[0][1] + row[0][3] / 2.0)


def _split_at_gaps(row):
    """Break a line of boxes wherever they stop being next to each other."""
    if len(row) < 2:
        return [row]
    limit = max(box[2] for box in row) * ROW_GAP_LIMIT
    pieces, current = [], [row[0]]
    for box in row[1:]:
        previous = current[-1]
        if box[0] - (previous[0] + previous[2]) > limit:
            pieces.append(current)
            current = [box]
        else:
            current.append(box)
    pieces.append(current)
    return pieces


def assign_slots(rows):
    """Which row is whose. Returns slot -> box, empty when the table is unclear.

    The community row is the giveaway: nothing else on the table shows three
    to five cards side by side. Once it is found the other two rows are fixed
    by where they sit, so the dealer and the player cannot be swapped.

    The hole cards are dealt above and below the board and lined up with it -
    measured on the sample frame, all three rows are centred within six pixels
    of each other. So a pair only counts as the dealer's or the player's when
    it sits over the board's own span, which is what stops a pair of white
    rectangles somewhere else on the desktop from being adopted as a hand.
    """
    community = None
    for row in rows:
        if 3 <= len(row) <= 5 and (community is None or len(row) > len(community)):
            community = row
    if community is None:
        return {}

    index = rows.index(community)

    def hands(candidates):
        return [row for row in candidates
                if len(row) == 2 and _over(row, community)]

    above = hands(rows[:index])
    below = hands(rows[index + 1:])
    anchor = below[0] if below else (above[-1] if above else None)

    board = _board_slots(community, anchor)
    if board is None:
        return {}
    slots = dict(board)
    if above:
        slots.update(zip(DEALER_SLOTS, above[-1]))
    if below:
        slots.update(zip(PLAYER_SLOTS, below[0]))
    return slots


# Consecutive board cards sit this many card widths apart, centre to centre
# (gaps of 3 to 8 pixels on a card 110 wide), used when no two visible cards are
# neighbours to measure it from.
BOARD_PITCH = 1.05


def _board_slots(community, anchor):
    """The board slot each visible community card is in, as slot -> box.

    Counting cards from the left is only right when the first one is visible.
    With flop 1 under the dealer's hand the rest all moved two places left - the
    third flop card read as the first, the turn as the second, the river as the
    third - and the scenario was worked out on the wrong flop.

    So the slot comes from where the card is. The hands are dealt centred on the
    board, over and under its middle card, the third of the flop: measured on the
    sample frame, within six pixels. Each card is so many card-pitches from there.
    Without a hand to measure from, counting from the left is all there is (such
    a layout is never adopted - see looks_plausible). Positions that do not fit
    five slots return None rather than a guess.
    """
    if anchor is None:
        return dict(zip(COMMUNITY_SLOTS, community))
    width = float(np.median([box[2] for box in community]))
    centres = [box[0] + box[2] / 2.0 for box in community]
    neighbours = [b - a for a, b in zip(centres, centres[1:]) if b - a <= width * 1.4]
    pitch = min(neighbours) if neighbours else width * BOARD_PITCH
    middle = _centre(anchor)
    board = {}
    for box, centre in zip(community, centres):
        position = 2 + int(round((centre - middle) / pitch))
        if not 0 <= position < len(COMMUNITY_SLOTS):
            return None
        slot = COMMUNITY_SLOTS[position]
        if slot in board:
            return None
        board[slot] = box
    return board


def _centre(row):
    return sum(box[0] + box[2] / 2.0 for box in row) / len(row)


def _over(row, community):
    """True when `row` is lined up over the board rather than off to one side."""
    return community[0][0] <= _centre(row) <= community[-1][0] + community[-1][2]


def _region(box, frame_shape, smallest=None, pad=PAD_RATIO, origin=(0, 0)):
    """One found card as the region dict the rest of the app passes around.

    What is found is the *visible* white area, which is smaller than the card
    whenever something overlaps it - and a box that stops short of the card's
    bottom edge shrinks the index strip taken from the top of it, which was
    enough on one dealer card to turn a diamond into a heart. So no box is
    allowed to be smaller than the cards on this table plainly are. It is
    grown from the top-left corner, because that is where the index is printed
    and the one corner worth keeping still.
    """
    x, y, width, height = box
    if smallest:
        width = max(width, smallest[0])
        height = max(height, smallest[1])
    pad_x, pad_y = int(width * pad), int(height * pad)
    left = max(0, x - pad_x)
    top = max(0, y - pad_y)
    right = min(frame_shape[1], x + width + pad_x)
    bottom = min(frame_shape[0], y + height + pad_y)
    return {"left": left + origin[0], "top": top + origin[1],
            "width": right - left, "height": bottom - top}


def locate(frame, origin=(0, 0), scale=DETECT_SCALE):
    """Find the card slots visible in one frame.

    Returns None when the table cannot be read off this frame at all, and
    otherwise:

        {"regions": {slot: {left, top, width, height}},
         "card_height": px, "found": n}

    `origin` is the absolute screen coordinate of the frame's top-left pixel,
    so the regions come back in the same absolute coordinates the calibrated
    ones use and the rest of the app needs no telling which it got.
    """
    boxes = find_card_boxes(frame, scale)
    card_height = dominant_card_size(boxes)
    if card_height is None:
        return None

    table = [box for box in boxes
             if (1 - SIZE_TOLERANCE) * card_height <= box[3]
             <= (1 + SIZE_TOLERANCE) * card_height]
    slots = assign_slots(group_rows(table, card_height))
    if not slots:
        return None

    # The size this table's cards actually are, taken from the middle of what
    # was found so one clipped card cannot drag it.
    smallest = (int(np.median([box[2] for box in table])),
                int(np.median([box[3] for box in table])))

    return {
        "regions": {slot: _region(box, frame.shape, smallest, origin=origin)
                    for slot, box in slots.items()},
        "card_height": card_height,
        "card_size": smallest,
        "found": len(slots),
    }


def looks_plausible(layout):
    """True when a layout is worth adopting as the one to read from.

    A frame caught mid-deal can show a believable row of three that is really
    two cards and a hand, so a layout is only adopted when it also places the
    player - the one pair that is on the table from the first second of the
    round to the last.
    """
    if not layout:
        return False
    regions = layout["regions"]
    return (all(slot in regions for slot in PLAYER_SLOTS)
            and sum(1 for slot in COMMUNITY_SLOTS if slot in regions) >= 3)


def merge(layout, fallback):
    """A layout's own regions, filled out from `fallback` where it found none.

    A card that is covered this frame is missing from the layout rather than
    wrong in it, so the last known box for that slot is the best place to look
    - and if the card really is gone, the region simply reads as empty.
    """
    regions = dict(fallback or {})
    regions.update((layout or {}).get("regions") or {})
    return {slot: regions[slot] for slot in CARD_SLOTS if slot in regions}
