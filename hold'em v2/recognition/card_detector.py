"""Card presence detection and rank/suit glyph extraction.

A card region either shows a white card face or shows the table / a card back.
Once a face is found, its top strip is split into a rank glyph and a suit glyph
for card_recognizer to match against the template library.

Two printed layouts are handled:

    side by side   "8 (diamond)"   the live-table cards, rank left, suit right
    stacked        "8"             smaller cards such as the result boxes,
                   "(diamond)"     rank above suit

The layout is decided from the ink itself, so no configuration is needed.
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Normalised glyph sizes used for both templates and live patches.
RANK_SIZE = (56, 76)   # (width, height)
SUIT_SIZE = (56, 56)

# Fraction of the card face that holds the rank and suit, measured from the top.
# The centre of these cards carries a barcode, which must stay out of the crop.
INDEX_HEIGHT_RATIO = 0.42

# Taller strip used when the rank sits above the suit instead of beside it.
STACKED_HEIGHT_RATIO = 0.72

# Taller still, for a card drawn so small that its index is the whole card.
# The result panels do that: rank in the top half, suit in the bottom half,
# with the suit running to about nine tenths of the way down. Cut at 0.72 the
# suit loses its foot and nothing matches; cut here, the same thumbnails read
# correctly. It is offered as an extra reading rather than instead of the
# other, so the cards on the table are unaffected by it.
SMALL_STACKED_HEIGHT_RATIO = 0.95

# How much of the card face to trim away on each side before reading the index,
# so the printed card outline is not mistaken for a glyph.
FACE_INSET_RATIO = 0.07

# Ink blobs smaller than this fraction of the index strip are treated as noise.
MIN_BLOB_AREA_RATIO = 0.004

# Rows at the top of a card face narrower than this share of its widest row are
# a line of the table art touching the card, not the card (see card_face).
TOP_SPIKE_ROW_SHARE = 0.2


def face_ratio(image):
    """Fraction of pixels that look like a white card face (0.0 - 1.0)."""
    if image is None or image.size == 0:
        return 0.0
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    whiteish = (value > 150) & (saturation < 90)
    return float(np.count_nonzero(whiteish)) / whiteish.size


def card_present(image, threshold=0.35):
    """(present, ratio) for a card region.

    An empty seat shows the table felt and a face-down card shows a coloured
    back; both have a low white ratio, so one threshold separates them from a
    face-up card.
    """
    ratio = face_ratio(image)
    return ratio >= threshold, ratio


def card_face(image, paper_outside=False):
    """Crop the white card face out of a region.

    Calibration boxes usually include a little surrounding table, so the face
    is located rather than assumed. Returns None when no face is found.

    With `paper_outside` (used when reading the index), everything outside the
    card's own outline is painted white, and a thin spike at the top of the
    outline is trimmed off. The crop is the upright bounding box of the face,
    so a tilted card leaves felt in its corners, and a line of table art
    touching the card stretches the box up over more felt; read as ink, that
    felt joined the rank. A live 8 and 6 of hearts were refused for exactly that.
    """
    if image is None or image.size == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] > 150) & (hsv[:, :, 1] < 90)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    outline = max(contours, key=cv2.contourArea)
    x, y, width, height = cv2.boundingRect(outline)
    if not paper_outside:
        if width < 12 or height < 16:
            return None
        return image[y:y + height, x:x + width]

    # The card's own outline, filled: its printed ink lies inside it.
    filled = np.zeros(white.shape, dtype=np.uint8)
    cv2.drawContours(filled, [outline], -1, 255, cv2.FILLED)
    # A thin light line of the table art touching the top of the card - the
    # gold trim above the board - becomes a spike on the outline and pulls the
    # box upwards. Rows at the top that are only a sliver of the card's width
    # are that spike, not card, so they are dropped. Only the top: trimming the
    # sides or bottom as well moved ordinary cards and lost readings.
    rows = (filled[y:y + height, x:x + width] > 0).sum(axis=1)
    top = 0
    while top < height and rows[top] < rows.max() * TOP_SPIKE_ROW_SHARE:
        top += 1
    points = outline.reshape(-1, 2)
    if top >= 3:
        y, height = y + top, height - top
        points = points[points[:, 1] >= y]
    if width < 12 or height < 16 or len(points) < 3:
        return None
    # Painted outside the convex hull, not the outline itself: a glyph touching
    # the edge of the box dents the outline, and following the dent erased part
    # of the glyph (a 3 of diamonds lost its top). The hull keeps that ink.
    paper = np.zeros(white.shape, dtype=np.uint8)
    cv2.drawContours(paper, [cv2.convexHull(points.reshape(-1, 1, 2))], -1, 255, cv2.FILLED)
    face = image[y:y + height, x:x + width].copy()
    face[paper[y:y + height, x:x + width] == 0] = (255, 255, 255)
    return face


def _binarize(gray):
    """Ink (glyphs) as white on black."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def _blobs(binary):
    """Bounding boxes of the ink blobs, ignoring specks and the card edge."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    height, width = binary.shape[:2]
    min_area = max(12, int(height * width * MIN_BLOB_AREA_RATIO))

    boxes = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if area < min_area:
            continue
        # A blob spanning almost the whole strip is the card edge, not a glyph.
        if w > width * 0.92 and h > height * 0.92:
            continue
        # Cards are rarely square to the screen, so a sliver of the printed
        # border survives the inset as a long thin line. No rank or suit is
        # anywhere near that elongated, so shape alone identifies it.
        if w > h * 5 and h < height * 0.15:
            continue
        # The top of the barcode printed in the middle of the card. The index
        # strip is cut above it, but when the card fills its box and the video
        # is soft the stripes blur into one bar that pokes up into the bottom
        # of the strip - measured live at 68x17 in a 248x140 strip, too thick
        # for the rule above. Joined to the rank and suit clusters it corrupted
        # both glyphs, and a clean 5 and 8 of diamonds were refused poll after
        # poll. A rank or suit cut by the strip edge is still tall; this is flat.
        if y + h >= height - 6 and w > h * 2.5 and h < height * 0.2:
            continue
        # The same shape against the top edge is felt above a tilted card: a
        # light line of the table artwork touching the card stretches the face
        # box upwards, and the band of felt it takes in reads as ink. Measured
        # live at 126x21 and 120x24 over a turn 8 and river 6 of hearts. A rank
        # or suit touching the top of the strip is tall, never flat like this.
        if y <= 2 and w > h * 2.5 and h < height * 0.2:
            continue
        # A tall sliver against either edge is border, but only against an
        # edge: the "1" of a "10" is tall and narrow too, and sits inside.
        if (h > w * 5 and w < width * 0.10
                and (x <= 2 or x + w >= width - 3)):
            continue
        # The suit sits at the right of the index and is roughly as wide as it
        # is tall. Anything tall and narrow against the right edge is the card
        # border or the card behind it - never a glyph. (The left edge is left
        # alone: the "1" of a "10" legitimately looks like that.)
        if x + w >= width - 2 and h > w * 3:
            continue
        # The border of a card tilted on the table crosses the strip at a
        # slant, so its box is several times wider than the line itself and
        # the rule above never sees it. Live, a jack of clubs had a 13x103 line
        # at -82 degrees in a 21x103 box against the left edge; grouped with
        # the J it held the rank to 0.57 on every poll of the hand. A "1" is
        # upright - its box is as narrow as the stroke - so it is not touched.
        if (x <= 2 or x + w >= width - 3) and h > height * 0.5 and w < width * 0.12:
            points = cv2.findNonZero((labels[y:y + h, x:x + w] == index).astype("uint8"))
            (_, _), (side_a, side_b), _ = cv2.minAreaRect(points)
            thin, long_side = min(side_a, side_b), max(side_a, side_b)
            if thin > 0 and long_side > thin * 5 and w >= thin * 1.4:
                continue
        boxes.append((x, y, w, h))
    return sorted(boxes, key=lambda box: box[0])


def _group(boxes, gap, axis=0):
    """Merge boxes into clusters separated by more than `gap` along `axis`."""
    if not boxes:
        return []
    size_index = 2 if axis == 0 else 3
    ordered = sorted(boxes, key=lambda box: box[axis])
    groups = [[ordered[0]]]
    for box in ordered[1:]:
        last = groups[-1][-1]
        if box[axis] - (last[axis] + last[size_index]) > gap:
            groups.append([box])
        else:
            groups[-1].append(box)
    return groups


def _bounds(group):
    """Bounding box around a group of boxes."""
    left = min(box[0] for box in group)
    top = min(box[1] for box in group)
    right = max(box[0] + box[2] for box in group)
    bottom = max(box[1] + box[3] for box in group)
    return left, top, right, bottom


def fit_glyph(glyph, size):
    """Scale a glyph into `size` keeping its aspect ratio, centred on black.

    Stretching would distort wide glyphs (notably "10") and make them match
    poorly against templates drawn differently, so the glyph is letterboxed.
    """
    if glyph is None or glyph.size == 0:
        return None
    target_w, target_h = size
    height, width = glyph.shape[:2]
    scale = min(target_w / float(width), target_h / float(height))
    new_w = max(1, min(target_w, int(round(width * scale))))
    new_h = max(1, min(target_h, int(round(height * scale))))
    resized = cv2.resize(glyph, (new_w, new_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((target_h, target_w), dtype=np.uint8)
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas[y:y + new_h, x:x + new_w] = resized
    return canvas


def _cut(binary, box):
    left, top, right, bottom = box
    return binary[top:bottom, left:right]


# A pip is a compact, roughly square glyph. These bounds are wide enough for
# all four suits (a diamond is the narrowest, a heart the widest) and tight
# enough to reject the things that get mistaken for a suit: a clipped sliver,
# a digit, or the upside-down index printed at the bottom of the card.
SUIT_ASPECT_RANGE = (0.5, 1.6)

# Above this width-to-height ratio, a "suit" box is suspected of holding the
# tail of the rank as well, and a separated reading is offered alongside it.
TOUCHING_PIP_ASPECT = 1.02


def looks_like_a_pip(box, strip_shape):
    """True when a box could plausibly hold a suit symbol."""
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if width < 4 or height < 4:
        return False
    if not SUIT_ASPECT_RANGE[0] <= width / float(height) <= SUIT_ASPECT_RANGE[1]:
        return False
    # A suit never spans most of the index strip; the rank sits beside it.
    return width <= strip_shape[1] * 0.6


# Gaps at which the index is cut into rank and suit, as a fraction of the strip.
# One threshold cannot serve every card: on a "10" the space between the digits
# can be as wide as the space before the suit, so a single setting either splits
# the ten in half or swallows the suit into it. Each threshold that produces a
# different grouping becomes a candidate reading, and matching decides.
SPLIT_GAPS = (0.025, 0.05, 0.09, 0.15)


def _separate_touching_pip(binary, rank_box, suit_box):
    """Split a blob that holds the end of the rank and the suit stuck together.

    On a tight index - a wide "10" printed close to its pip - the two can touch,
    so they arrive as one connected blob that no gap threshold can divide. They
    join at a thin neck, so the blob is cut at its narrowest column instead.

    Returns a list of (rank_box, suit_box) readings - empty when the box
    already looks like a lone pip.
    """
    left, top, right, bottom = suit_box
    width, height = right - left, bottom - top
    # Any box wider than it is tall may be hiding a glyph next to the pip. The
    # cut only offers an extra reading, so it is worth trying whenever there is
    # room for one - a genuine lone pip simply loses on the match.
    if width <= height * TOUCHING_PIP_ASPECT or width < 8:
        return []

    columns = (binary[top:bottom, left:right] > 0).sum(axis=0)
    low, high = int(width * 0.2), int(width * 0.8)
    if high <= low:
        return []

    # Where to cut is a guess, so make several: the thinnest column, which is
    # where two glyphs usually join, and the cuts that would leave a pip-shaped
    # piece on the right. Matching sorts out which was right.
    positions = [left + low + int(np.argmin(columns[low:high]))]
    for share in (0.8, 0.95, 1.1):
        positions.append(right - int(height * share))

    splits = []
    for cut in positions:
        if cut <= rank_box[2] or cut >= right - 2 or cut <= left:
            continue
        grown = (rank_box[0], min(rank_box[1], top), cut, max(rank_box[3], bottom))
        split = (grown, (cut, top, right, bottom))
        if split not in splits:
            splits.append(split)
    return splits


def _splits(binary, axis):
    """Every distinct way to cut the index into (rank_box, suit_box) along `axis`.

    The suit is the last cluster and the rank is everything before it, so a
    two-digit "10" reads correctly whenever its digits and the suit end up in
    separate clusters.
    """
    height, width = binary.shape[:2]
    boxes = _blobs(binary)
    if not boxes:
        return []

    span = width if axis == 0 else height
    found = []

    def remember(*splits):
        for split in splits:
            if split and split not in found:
                found.append(split)

    for fraction in SPLIT_GAPS:
        clusters = _significant(
            _group(boxes, gap=max(2, int(span * fraction)), axis=axis), binary
        )
        if len(clusters) < 2:
            continue
        rank = _bounds([box for cluster in clusters[:-1] for box in cluster])
        suit = _bounds(clusters[-1])
        remember((rank, suit))
        if axis == 0:
            # The suit may be stuck to the rank; offer separated readings too.
            remember(*_separate_touching_pip(binary, rank, suit))

    if not found and axis == 0:
        # No gap width separated a suit from a rank. That happens when the ink
        # never breaks into two clusters: everything is one blob, or - on a
        # "10" - the digits sit close enough to group together while the "0" is
        # fused to the pip. Cutting the whole index is then the only way to
        # read the card at all.
        whole = _bounds(boxes)
        remember(*_separate_touching_pip(
            binary, (whole[0], whole[1], whole[0], whole[3]), whole))
    return found


def _significant(groups, binary, share=0.18):
    """Drop clusters holding far less ink than the biggest one.

    Stray marks near a card - a fingertip, a chip, the corner of the card
    behind it - would otherwise be mistaken for the suit. The two digits of a
    "10" both carry plenty of ink, so they survive.
    """
    if len(groups) < 2:
        return groups
    ink = [int(np.count_nonzero(_cut(binary, _bounds(group)))) for group in groups]
    threshold = max(ink) * share
    kept = [group for group, amount in zip(groups, ink) if amount >= threshold]
    return kept if len(kept) >= 2 else groups


def is_red(strip_bgr, binary, box):
    """True when the ink inside `box` is red (hearts/diamonds) not black."""
    left, top, right, bottom = box
    patch = strip_bgr[top:bottom, left:right]
    mask = binary[top:bottom, left:right] > 0
    if patch.size == 0 or not mask.any():
        return False
    pixels = patch[mask].astype(np.int32)
    blue, green, red = pixels[:, 0].mean(), pixels[:, 1].mean(), pixels[:, 2].mean()
    return red > green + 30 and red > blue + 30


def extract_glyph_candidates(image, require_pip_shape=True):
    """Every plausible way to read a card's index, best guess first.

    A card printed "rank suit" side by side and a card printed with the rank
    above the suit are split differently, and a two-digit "10" can look like a
    side-by-side pair. Rather than guess, both readings are offered and the
    recogniser keeps whichever actually matches a card.

    Each candidate is {"rank": binary image, "suit": binary image, "red": bool}.

    `require_pip_shape` drops readings whose suit position does not hold
    something pip-shaped, which is what stops a bad split being read as a card
    or taught as a template. Teaching already knows which card it is looking
    at, so it passes False and settles the choice against that answer instead.
    """
    face = card_face(image, paper_outside=True)
    if face is None:
        face = image
    if face is None or face.size == 0:
        return []

    # Inset a little so the card's own outline does not read as ink.
    height, width = face.shape[:2]
    inset_x = max(1, int(width * FACE_INSET_RATIO))
    inset_y = max(1, int(height * FACE_INSET_RATIO))

    candidates = []
    for ratio, axis in ((INDEX_HEIGHT_RATIO, 0),
                        (STACKED_HEIGHT_RATIO, 1),
                        (SMALL_STACKED_HEIGHT_RATIO, 1)):
        strip = face[inset_y:max(inset_y + 2, int(height * ratio)),
                     inset_x:width - inset_x]
        if strip.size == 0:
            continue


        # Work at 4x so thin strokes survive thresholding and resizing.
        strip = cv2.resize(strip, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        binary = _binarize(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY))

        for rank_box, suit_box in _splits(binary, axis):
            if require_pip_shape and not looks_like_a_pip(suit_box, binary.shape):
                # Whatever this split found in the suit position is not a pip,
                # so the split is wrong. Teaching from it would poison the
                # templates and reading from it would invent a card.
                continue

            rank_glyph = fit_glyph(_cut(binary, rank_box), RANK_SIZE)
            suit_glyph = fit_glyph(_cut(binary, suit_box), SUIT_SIZE)
            if rank_glyph is None or suit_glyph is None:
                continue

            candidates.append({
                "rank": rank_glyph,
                "suit": suit_glyph,
                "red": is_red(strip, binary, suit_box),
            })

    if not candidates:
        logger.debug("Could not split the card index into rank and suit")
    return candidates


def extract_glyphs(image):
    """The best-guess rank and suit glyphs for a card image, or None."""
    candidates = extract_glyph_candidates(image)
    return candidates[0] if candidates else None
