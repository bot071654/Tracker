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


# -- two dealer cards fused into one shape -----------------------------------------------------------------------------------
#
# Found in a 10-minute practice session (Round 45, frame saved by the validation harness): two dealer cards turned face up
# side by side, plainly separate to the eye, were never given dealer slots. The finder's bright-pixel mask had them as two
# components about 6 px apart (full resolution), but `_card_mask` closes the printed index and pips back into the face with a
# 5 x 5 close, and a thin bridge a few pixels high joined the two cards into one shape 208 x 110 - aspect 1.89, outside
# ASPECT_RANGE - which was rejected like any arm or sleeve. No pair above the board, no dealer slots, no dealer cards read,
# and the round never completed although the result panel showed the dealer's hand.
#
# What tells that shape from the arm, the sleeve or a hand: it is two cards wide (1.7 - 2.6 card widths; the measured one was
# 2.2) and a card tall, it sits over the board, and across its width the UNMERGED mask shows one narrow valley - six columns
# with 0 - 3 bright pixels, against 40 - 53 inside either card - that splits it into two pieces each of which is a card:
# card-sized, card-shaped, solid, alike, level with each other and almost touching.
#
# This is only a second chance for the DEALER's pair and only when the ordinary pairing found nothing there. It looks only in the
# strip above the board; it never runs if an ordinary card is already in that strip or if anything about the shape fails
# (one valley, two good halves, matching sizes, the board's own scale); and it only says WHERE two cards are - the same boxes
# go through the same recogniser, the same margins and the same confirmation as any other card. A fused shape that is not
# clearly two cards stays rejected, as before. Nothing here touches the board's cards, the player's, or what is carried
# between layouts.
#
# Two FACE-DOWN cards are not a face-up pair. The face test above (V > 150, S < 90) is loose enough to pass the blue-lattice back of
# a face-down card (V about 183, S about 52, hue about 106), and the dealer's two backs, touching, fuse and split exactly like a
# face-up pair: in a practice session the fallback fired 42 times, 36 of them on the flop, on backs. What separates them is paper: of
# the pixels the face test accepts, the share that are paper white (V > 200, S < 35) was 0.000 - 0.018 in 29 saved backs and 0.42 -
# 0.89 for white faces, the lowest being a face under a large hand. So both halves must be at least FUSED_HALF_PAPER_MIN paper
# white, measured on the recovered box at full resolution, or no pair is returned; a half that cannot be measured counts as not paper
# white. Offline, 98 of 98 composites of real backs were refused and 124 of 124 face-up and hand-covered composites (and the real
# Round 45 frame) were still recovered. Nothing else about the fallback changed: same shapes, same geometry, same boxes.
DEALER_ZONE_HEIGHTS = 2.2           # card heights above the board's top edge that count as where the dealer's cards would be
DEALER_ZONE_SIDE = 0.25             # of the board's span, either side
FUSED_WIDTH_CARDS = (1.7, 2.6)      # a fused pair is this many card widths wide (measured: 2.2)
FUSED_VALLEY_LEVEL = 0.10           # the emptiest column between the cards has at most this share of the height bright (measured: 0)
FUSED_BODY_LEVEL = 0.40             # a column of a card has at least this share of the height bright (measured: 0.5 - 0.96)
FUSED_BODY_MIN_CARDS = 0.4          # a body is at least this many card widths of such columns
FUSED_VALLEY_MAX_CARDS = 0.4        # the valley is at most this many card widths across (measured: 0.13)
FUSED_HALF_WIDTH_CARDS = (0.6, 1.25)
FUSED_PAIR_SIMILARITY = 0.88        # the two pieces are this alike in width and height (measured: 0.98; the board's own cards: 0.92)
PAPER_VALUE_MIN = 200               # paper white: brighter than the face test's floor (FACE_VALUE_MIN) ...
PAPER_SATURATION_MAX = 35           # ... and barely coloured (FACE_SATURATION_MAX is 90)
FUSED_HALF_PAPER_MIN = 0.20         # each half must be at least this paper white (backs: <= 0.018; faces: >= 0.42, mostly 0.6 - 0.9)
# What a candidate pair's report says (see recover_fused_dealer_pair's `report`): the overall reason, and each half's status.
REFUSAL_ACCEPTED = "accepted"
REFUSAL_BELOW_THRESHOLD = "below threshold"            # a half measured, but less paper white than FUSED_HALF_PAPER_MIN
REFUSAL_INVALID_MEASUREMENT = "invalid measurement"    # a half could not be measured (empty or unreadable patch, no face-test pixels)


def _paper_white_share(patch):
    """Of the pixels the face test accepts, the share that are paper white; None when nothing can be measured (empty, invalid, no face pixels)."""
    try:
        if patch is None or patch.ndim != 3 or patch.shape[2] != 3 or patch.size == 0:
            return None
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        face = (hsv[:, :, 2] > FACE_VALUE_MIN) & (hsv[:, :, 1] < FACE_SATURATION_MAX)
        face_pixels = int(np.count_nonzero(face))
        if face_pixels == 0:
            return None
        paper = face & (hsv[:, :, 2] > PAPER_VALUE_MIN) & (hsv[:, :, 1] < PAPER_SATURATION_MAX)
        return float(np.count_nonzero(paper)) / face_pixels
    except Exception:  # noqa: BLE001 - an unmeasurable half is refused below, never a crash
        return None


def community_row(rows):
    """The row `assign_slots` takes for the board: the longest of 3 to 5 cards (the same choice, kept in one place for callers)."""
    community = None
    for row in rows:
        if 3 <= len(row) <= 5 and (community is None or len(row) > len(community)):
            community = row
    return community


def _note_candidate(report, pair, shares, statuses, threshold, refusal):
    """Fill `report` with what the paper-white guard saw. Records only; the guard's decision was made by the caller.

    Observational: it never raises and never touches the answer.
    """
    try:
        report.update({
            "candidate": True, "threshold": threshold, "accepted": refusal is None, "reason": refusal or REFUSAL_ACCEPTED,
            "halves": [{"slot": slot, "box": [int(v) for v in box], "share": share, "status": status}
                       for slot, box, share, status in zip(DEALER_SLOTS, pair, shares, statuses)]})
    except Exception:  # noqa: BLE001 - a diagnostics sink that fails must not change what the fallback returns
        pass


def recover_fused_dealer_pair(frame, community, card_height, table, scale=DETECT_SCALE, report=None):
    """The two boxes of a dealer pair that the mask fused into one shape above the board, or None. See the note above.

    `community` is the board's row, `card_height` the table's card height and `table` the ordinary card boxes found. Boxes are
    (x, y, w, h) in `frame` coordinates, left card first, the same form `find_card_boxes` returns.

    `report` is optional and observational only (the tracker never passes one): given a dict, it is filled whenever the shape was
    a geometrically valid pair, whether the paper-white guard then accepted or refused it - {"candidate", "threshold", "accepted",
    "reason", "halves": [{"slot", "box", "share", "status"}]} - so a refusal can be recorded. It changes nothing about the return value.
    """
    if frame is None or not community:
        return None
    top = min(box[1] for box in community)
    left, right = community[0][0], community[-1][0] + community[-1][2]
    span = right - left
    card_width = float(np.median([box[2] for box in community]))
    x0, x1 = max(0, int(left - DEALER_ZONE_SIDE * span)), min(frame.shape[1], int(right + DEALER_ZONE_SIDE * span))
    y0, y1 = max(0, int(top - DEALER_ZONE_HEIGHTS * card_height)), int(top)
    if x1 <= x0 or y1 - y0 < 0.5 * card_height or card_width <= 0:
        return None
    for box in table:                      # an ordinary card up there: the ordinary pairing decides, not this
        if x0 <= box[0] + box[2] / 2.0 <= x1 and y0 <= box[1] + box[3] / 2.0 <= y1:
            return None

    small = cv2.resize(frame[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    raw = (hsv[:, :, 2] > FACE_VALUE_MIN) & (hsv[:, :, 1] < FACE_SATURATION_MAX)
    closed = _card_mask(small) > 0
    contours, _ = cv2.findContours(closed.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    wide = []
    for contour in contours:
        bx, by, bw, bh = cv2.boundingRect(contour)
        full_w, full_h = bw / scale, bh / scale
        if bw / float(bh) <= ASPECT_RANGE[1]:
            continue                       # card-shaped or narrower: not a fused pair (and an ordinary card is handled above)
        if not (1 - SIZE_TOLERANCE) * card_height <= full_h <= (1 + SIZE_TOLERANCE) * card_height:
            continue                       # an arm, a sleeve or a banner is not as tall as a card
        if not FUSED_WIDTH_CARDS[0] <= full_w / card_width <= FUSED_WIDTH_CARDS[1]:
            continue
        wide.append((bx, by, bw, bh))
    if len(wide) != 1:
        return None                        # nothing that looks like a fused pair, or more than one: no convincing evidence
    bx, by, bw, bh = wide[0]

    # The unmerged mask across the shape. Each card is a body of bright columns (40 - 53 of the 55 rows in the measured case);
    # between the two there is a valley that is all but empty (0 - 3). Small bright fragments at the shape's ends - a bit of
    # sleeve, the edge of a card behind - are not bodies and are ignored; but there must be exactly TWO bodies, each at
    # least a good part of a card wide, with an almost empty valley between them that is not too wide to have been closed over.
    columns = raw[by:by + bh, bx:bx + bw].sum(axis=0)
    body = columns >= FUSED_BODY_LEVEL * bh
    bodies, begin = [], None
    for index in range(len(body) + 1):
        if index < len(body) and body[index] and begin is None:
            begin = index
        elif (index == len(body) or not body[index]) and begin is not None:
            if (index - begin) / scale >= FUSED_BODY_MIN_CARDS * card_width:
                bodies.append((begin, index))
            begin = None
    if len(bodies) != 2:
        return None                        # one body is one card or an arm; three is not a pair
    gap = columns[bodies[0][1]:bodies[1][0]]
    if len(gap) == 0 or gap.min() > FUSED_VALLEY_LEVEL * bh:
        return None                        # nothing empty enough between them to be a gap between two cards
    if len(gap) / scale > FUSED_VALLEY_MAX_CARDS * card_width:
        return None                        # too wide a gap to have been closed over: not the same pair
    split = bodies[0][1] + int(np.argmin(gap))

    halves = []
    for first, last in ((max(0, bodies[0][0] - 3), split), (split + 1, min(bw, bodies[1][1] + 3))):
        piece = closed[by:by + bh, bx + first:bx + last]
        solid = piece.sum(axis=0) >= 0.25 * bh
        if not solid.any():
            return None
        xs = np.where(solid)[0]
        piece = piece[:, xs[0]:xs[-1] + 1]
        rows_on = np.where(piece.any(axis=1))[0]
        width, height = piece.shape[1], rows_on[-1] - rows_on[0] + 1
        fill = float(piece[rows_on[0]:rows_on[-1] + 1].sum()) / float(width * height)
        full_w, full_h = width / scale, height / scale
        if not FUSED_HALF_WIDTH_CARDS[0] <= full_w / card_width <= FUSED_HALF_WIDTH_CARDS[1]:
            return None
        if not (1 - SIZE_TOLERANCE) * card_height <= full_h <= (1 + SIZE_TOLERANCE) * card_height:
            return None
        if not ASPECT_RANGE[0] <= width / float(height) <= ASPECT_RANGE[1] or fill < MIN_FILL:
            return None
        halves.append((bx + first + xs[0], by + rows_on[0], width, height))
    (ax, ay, aw, ah), (cx, cy, cw, ch) = halves
    if min(aw, cw) < FUSED_PAIR_SIMILARITY * max(aw, cw) or min(ah, ch) < FUSED_PAIR_SIMILARITY * max(ah, ch):
        return None                        # two cards dealt together are one size: a card and a bridge are not
    if abs((ay + ah / 2.0) - (cy + ch / 2.0)) > 0.25 * max(ah, ch):
        return None                        # not level with each other
    centre = x0 + (ax + (cx + cw)) / 2.0 / scale
    if not left <= centre <= right:
        return None                        # not over the board
    pair = [tuple(int(round(value / scale)) + offset for value, offset in zip(half, (x0, y0, 0, 0))) for half in halves]
    # Two face-down backs fuse and split like a face-up pair: only paper counts (see above). Both halves are measured, so that a
    # refusal can say what each half was; the answer is the same as stopping at the first bad one.
    shares = [_paper_white_share(frame[max(0, y):max(0, y + height), max(0, x):max(0, x + width)]) for x, y, width, height in pair]
    statuses = [REFUSAL_INVALID_MEASUREMENT if share is None else REFUSAL_BELOW_THRESHOLD if share < FUSED_HALF_PAPER_MIN else REFUSAL_ACCEPTED
                for share in shares]
    refusal = (REFUSAL_INVALID_MEASUREMENT if REFUSAL_INVALID_MEASUREMENT in statuses else
               REFUSAL_BELOW_THRESHOLD if REFUSAL_BELOW_THRESHOLD in statuses else None)
    if report is not None:
        _note_candidate(report, pair, shares, statuses, FUSED_HALF_PAPER_MIN, refusal)
    return None if refusal else pair


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
    rows = group_rows(table, card_height)
    slots = assign_slots(rows)
    if not slots:
        return None
    recovered = ()
    if not all(slot in slots for slot in DEALER_SLOTS):
        pair = recover_fused_dealer_pair(frame, community_row(rows), card_height, table)
        if pair:
            slots = dict(slots)
            slots.update(zip(DEALER_SLOTS, pair))
            recovered = tuple(DEALER_SLOTS)

    # The size this table's cards actually are, taken from the middle of what
    # was found so one clipped card cannot drag it.
    smallest = (int(np.median([box[2] for box in table])),
                int(np.median([box[3] for box in table])))

    layout = {
        "regions": {slot: _region(box, frame.shape, smallest, origin=origin)
                    for slot, box in slots.items()},
        "card_height": card_height,
        "card_size": smallest,
        "found": len(slots),
    }
    if recovered:
        layout["recovered"] = list(recovered)      # the dealer's pair, split out of a fused shape (see above)
    return layout


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
