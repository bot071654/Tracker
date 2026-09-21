"""Reading the Dealer/Player result panels the casino draws beside the table.

The panels in the bottom-left corner of the game show each seat's best five
cards as small thumbnails, drawn by the page rather than filmed, so they are
steady pixels on a dark background. They are the primary source for each
seat's *final* hand: what its best five are, what that hand is called, and who
won. The cards in the middle of the table stay the source for *positions* -
which two cards are the player's, which are the dealer's, which is the turn -
because a best five says nothing about where each card came from.

So nothing here fills a table slot. A panel can confirm the table, contradict
it, or supply the hand classification while a table card is still being
confirmed; it never invents a hole card.

What the panel is, measured on the sample frame rather than assumed:

    thumbnails      33x47 pixels, 6-8 pixels apart, in rows of up to five,
                    each with its index printed over 0.19-0.26 of its area
    background      dark: the brightest tenth of the pixels around a row
                    reaches 26, where around the table's own cards it is
                    154-218 and around a white application panel about 240
    two rows        dealer above player, left edges within a pixel of each
                    other, the player row 2.47 thumbnail heights lower
    position        left of the table's cards and below the top of the board

The player's row is live: it holds the two hole cards once they are dealt and
the best five from the flop on. The dealer's row appears at the showdown.
"""

import logging
import time
from itertools import combinations

import cv2
import numpy as np

from config.settings import DEFAULT_CONFIG
from poker.hand_evaluator import compare_hands, dealer_qualifies, evaluate_hand
from recognition.card_recognizer import read_slot, undecided_half
from recognition.table_layout import find_card_boxes, group_rows

logger = logging.getLogger(__name__)

SIDES = ("player", "dealer")
HOLE = {"player": ("player_1", "player_2"), "dealer": ("dealer_1", "dealer_2")}
BOARD = ("flop_1", "flop_2", "flop_3", "turn", "river")

# A panel row holds the best five cards, and fills up as the hand develops.
MAX_PANEL_CARDS = 5

# A thumbnail is much smaller than a card on the table - about a third of the
# height. When the table has been found, anything close to its size is not a
# thumbnail.
PANEL_SIZE_LIMIT = 0.6

# Thumbnails of one row are drawn from one sprite sheet and agree in size.
SIZE_TOLERANCE = 0.25

# The smallest thumbnail worth looking for. The table search stops at 25x35,
# which is right for table cards but cuts the panel off at a browser zoom of
# 0.75, where its thumbnails shrink to about 25x35 themselves: only two of the
# ten were found. At 18x26 all ten are found and read correctly at 0.75; at
# 0.70 the dealer's row is still lost, which is where this stops helping.
PANEL_MIN_SIZE = (18, 26)

# A thumbnail has its rank and suit printed on it. Measured on the sample frame
# the ink covers 0.19-0.26 of each thumbnail's inner area and 0.24-0.32 of a
# table card's; a blank white box covers none. Without this, a row of blank
# boxes on a dark background below the real panel was taken for the player's.
MIN_INK = 0.08

# The panel's background, measured around both rows of the sample frame with a
# margin of a quarter of a thumbnail: median brightness 23, brightest tenth 26.
# Around the table's own cards the same measure gives a median of 55-64 and a
# brightest tenth of 154-218, and a white window is brighter still. Either
# limit alone would separate them; both together leave a wide margin.
SURROUND_PAD = 0.25
SURROUND_MEDIAN_MAX = 45
SURROUND_BRIGHT_MAX = 90

# How the two rows sit against each other: measured 2.47 thumbnail heights
# apart with left edges within a pixel.
ROW_OFFSET = 2.47                 # in thumbnail heights
ROW_OFFSET_RANGE = (1.8, 3.2)     # in thumbnail heights
ROW_ALIGN_LIMIT = 0.5             # in thumbnail widths

# Multi-frame behaviour, in polls of the tracker.
CONFIRM_FRAMES = 2      # identical complete readings that confirm a row
VANISH_FRAMES = 3       # polls a row may be missing before it is forgotten

# The player's row starts every deal with the two hole cards. It is the only
# length a confirmed row may drop back to: within a round the row only grows.
DEAL_LENGTH = 2

# Searching the whole screen costs about 26ms on a 1920x1080 frame; searching
# the neighbourhood of the panel found last time costs under 1ms. The whole
# screen is searched only when there is no neighbourhood yet, or it has stopped
# containing a panel - and then no more than once in this many seconds.
FULL_SEARCH_INTERVAL = 1.0
ROI_MISSES = 2

# How many thumbnails not seen before may be read in one poll. Reading one
# costs about 30ms, and the moment a panel fills up - the dealer's row at the
# showdown - is the moment the dealer's own cards are on the table and need
# polling quickly. Measured: a poll reading all ten thumbnails as well as the
# nine table cards took 0.9s. The panel stays up for seconds; the table's cards
# do not. So the rest of a new row waits a poll or two, and the table does not.
READS_PER_POLL = 2

READABLE = DEFAULT_CONFIG["confidence_threshold"]

# Per-thumbnail statuses.
CONFIRMED = "CONFIRMED"
CONFIRMING = "CONFIRMING"
AMBIGUOUS = "AMBIGUOUS"
UNKNOWN = "UNKNOWN"
QUEUED = "QUEUED"           # found, waiting its turn to be read - not a failure
HELD = "HELD"               # unreadable this poll, the confirmed card stands

# Per-row states.
NOT_VISIBLE = "NOT_VISIBLE"
VISIBLE_PARTIAL = "VISIBLE_PARTIAL"
VISIBLE_COMPLETE = "VISIBLE_COMPLETE"


# -- finding the panels ----------------------------------------------------------

def _size_clusters(boxes):
    """Boxes grouped by height, largest first, each group within tolerance."""
    clusters = []
    for box in sorted(boxes, key=lambda b: -b[3]):
        for cluster in clusters:
            anchor = cluster[0][3]
            if (1 - SIZE_TOLERANCE) * anchor <= box[3] <= (1 + SIZE_TOLERANCE) * anchor:
                cluster.append(box)
                break
        else:
            clusters.append([box])
    return clusters


def _inked(frame, box):
    """True when a box has something printed on it, as a card's index is."""
    x, y, w, h = box
    inner = frame[y + int(h * 0.08):y + int(h * 0.92), x + int(w * 0.08):x + int(w * 0.92)]
    if inner.size == 0:
        return False
    hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
    ink = (hsv[:, :, 2] < 150) | (hsv[:, :, 1] > 90)
    return float(ink.mean()) >= MIN_INK


def _dark_surround(frame, row):
    """True when the pixels around a row are the panel's dark background."""
    width = int(np.median([box[2] for box in row]))
    height = int(np.median([box[3] for box in row]))
    pad_x, pad_y = max(2, int(width * SURROUND_PAD)), max(2, int(height * SURROUND_PAD))
    x0 = max(0, min(box[0] for box in row) - pad_x)
    y0 = max(0, min(box[1] for box in row) - pad_y)
    x1 = min(frame.shape[1], max(box[0] + box[2] for box in row) + pad_x)
    y1 = min(frame.shape[0], max(box[1] + box[3] for box in row) + pad_y)
    if x1 <= x0 or y1 <= y0:
        return False
    value = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)[:, :, 2]
    mask = np.ones(value.shape, bool)
    for x, y, w, h in row:
        mask[max(0, y - y0):y - y0 + h, max(0, x - x0):x - x0 + w] = False
    ring = value[mask]
    if ring.size < 20:
        return False
    return (float(np.median(ring)) <= SURROUND_MEDIAN_MAX
            and float(np.percentile(ring, 90)) <= SURROUND_BRIGHT_MAX)


def _near_table(boxes, layout, origin):
    """Keep only boxes where a panel can be, when the table has been found.

    Left of the middle of the board, below its top edge, and smaller than its
    cards. Without a layout - the wide camera shot, or between rounds - this is
    skipped and the panel has to prove itself by its own look.
    """
    regions = (layout or {}).get("regions") or {}
    board = [regions[slot] for slot in BOARD if slot in regions]
    if not board:
        return boxes
    centre = (min(r["left"] for r in board)
              + max(r["left"] + r["width"] for r in board)) / 2.0 - origin[0]
    top = min(r["top"] for r in board) - origin[1]
    limit = PANEL_SIZE_LIMIT * float(layout.get("card_height") or 0)
    return [box for box in boxes
            if box[0] + box[2] < centre and box[1] > top
            and (not limit or box[3] < limit)]


def candidate_rows(frame, layout=None, origin=(0, 0)):
    """Rows of thumbnails that look like a result panel, top first."""
    if frame is None or frame.size == 0:
        return []
    boxes = find_card_boxes(frame, scale=1.0, min_size=PANEL_MIN_SIZE)
    boxes = [box for box in _near_table(boxes, layout, origin) if _inked(frame, box)]
    rows = []
    for cluster in _size_clusters(boxes):
        if len(cluster) < 2:
            continue
        height = float(np.median([box[3] for box in cluster]))
        for row in group_rows(cluster, height):
            if 2 <= len(row) <= MAX_PANEL_CARDS and _dark_surround(frame, row):
                rows.append(row)
    return sorted(rows, key=lambda row: row[0][1])


def _assign(rows, known_player=None):
    """Which row is whose: {"player": row, "dealer": row}, either may be absent.

    Two rows that sit the way the panel draws them - aligned on the left, the
    player's a little under two and a half thumbnails lower - are the dealer's
    over the player's. Otherwise the lowest row is the player's: it is the one
    shown all round.

    Unless it sits where the dealer's row goes. `known_player` is where the
    player's row was last seen, as (left, top, thumbnail height). The player's
    row is not always found - a banner, a finger, thumbnails still being drawn -
    and live, the dealer's row found on its own at the showdown was taken for
    the player's: "Player best 5" showed the dealer's pair of threes.
    """
    if not rows:
        return {}
    best = None
    for upper, lower in combinations(rows, 2):
        width = float(np.median([box[2] for box in lower]))
        height = float(np.median([box[3] for box in lower]))
        offset = (lower[0][1] - upper[0][1]) / height
        aligned = abs(lower[0][0] - upper[0][0]) <= ROW_ALIGN_LIMIT * width
        if aligned and ROW_OFFSET_RANGE[0] <= offset <= ROW_OFFSET_RANGE[1]:
            miss = abs(offset - ROW_OFFSET)
            if best is None or miss < best[0]:
                best = (miss, upper, lower)
    if best:
        return {"dealer": best[1], "player": best[2]}
    lone = rows[-1]
    if known_player is not None and _is_dealer_row(lone, known_player):
        return {"dealer": lone}
    return {"player": lone}


def _is_dealer_row(row, known_player):
    """True when `row` sits where the dealer's row is drawn over a player row
    last seen at `known_player` = (left, top, thumbnail height)."""
    left, top, height = known_player
    if not height:
        return False
    width = float(np.median([box[2] for box in row]))
    offset = (top - row[0][1]) / float(height)
    return (abs(row[0][0] - left) <= ROW_ALIGN_LIMIT * width
            and ROW_OFFSET_RANGE[0] <= offset <= ROW_OFFSET_RANGE[1])


def locate(frame, layout=None, origin=(0, 0), known_player=None):
    """Find the result panels. Returns {"player": [box...], "dealer": [...]}.

    Either side may be missing: the player's panel fills in during the round
    and the dealer's only appears at the showdown. An empty dict means no
    panel was visible, which is not a failure.

    The panel is recognised by what it looks like - a row of printed thumbnails
    on a dark background - so it is found whether or not the table's own cards
    are on screen, and a row of white boxes in another window is not taken for
    it. With the table's layout, anywhere a panel cannot be is also ruled out.
    `known_player` (left, top, thumbnail height, in `frame` pixels) is where the
    player's row was last seen; see _assign.
    """
    return _assign(candidate_rows(frame, layout, origin), known_player)


def _read(frame, boxes, reader=read_slot):
    """Read one panel row. Returns (cards, reads) with None for the unreadable."""
    from capture.screen_capture import crop

    cards, reads = [], []
    for box in boxes:
        region = {"left": box[0], "top": box[1], "width": box[2], "height": box[3]}
        read = reader(crop(frame, region))
        read["region"] = region
        reads.append(read)
        cards.append(read["card"] if read["confident"] else None)
    return cards, reads


def read_panels(frame, origin=(0, 0), layout=None):
    """What the result panels say in one frame, as far as they can be read.

    Returns a dict per side:

        {"player": {"cards": ["3D", "3H", None, "9C", "8S"],
                    "reads": [...], "complete": False}}

    A thumbnail that cannot be read confidently comes back as None rather than
    as a guess - five cards are only worth having if they are the right five.
    One frame, no memory: PanelTracker is what the running tracker uses.
    """
    panels = {}
    for side, boxes in locate(frame, layout, origin).items():
        cards, reads = _read(frame, boxes)
        for read in reads:
            read["region"]["left"] += origin[0]
            read["region"]["top"] += origin[1]
        panels[side] = {
            "cards": cards,
            "reads": reads,
            "complete": len(cards) == MAX_PANEL_CARDS and all(cards),
        }
    return panels


# -- checking a panel against the table --------------------------------------------

def compare(side, panel_cards, calculated):
    """Check a panel row against the five cards this app worked out.

    Returns (verdict, message) where verdict is "verified", "conflict" or
    "unknown". Order is not compared: the casino lists the best five in its own
    order, and a hand is a set.
    """
    known = [card for card in panel_cards if card]
    if len(known) < MAX_PANEL_CARDS:
        return "unknown", (
            "%s panel: only %d of %d cards could be read" %
            (side, len(known), MAX_PANEL_CARDS))
    if set(known) == set(calculated):
        return "verified", "%s panel agrees: %s" % (side, " ".join(sorted(known)))
    return "conflict", (
        "%s panel shows %s but this app calculated %s"
        % (side, " ".join(sorted(known)), " ".join(sorted(calculated))))


def _score(cards):
    try:
        return evaluate_hand(list(cards))["score"]
    except Exception:  # noqa: BLE001 - a malformed reading simply cannot be scored
        return None


def verify(side, panel_side, cards):
    """Does one side's confirmed panel row fit the cards on the table?

    Returns (verdict, message):

        consistent   every card the panel shows is one the table has for this
                     seat, and the panel's hand is exactly as strong as the
                     best the table's cards make - the same hand, even where
                     the casino picked a different but equal kicker
        pending      nothing contradicts, but they cannot be compared yet: the
                     table has unread places, or the panel row is still short
        mismatch     every panel card is on the table, but the hands differ in
                     strength. Usually one source has not caught up with the
                     latest card; if it lasts, one of them is wrong
        conflict     the panel shows a card the table cannot hold for this
                     seat - the other seat's hole card, or more unplaced cards
                     than the table has unread places
        unavailable  no confirmed panel row for this round

    Nothing is changed. The table's cards are only read.
    """
    if (not panel_side or panel_side.get("stale")
            or not panel_side.get("confirmed")):
        return "unavailable", "%s panel: nothing confirmed for this round" % side

    other = "dealer" if side == "player" else "player"
    shown = list(panel_side["confirmed"])
    own = [cards.get(slot) for slot in HOLE[side] + BOARD]
    known = [card for card in own if card]
    theirs = {cards.get(slot) for slot in HOLE[other] if cards.get(slot)}

    clash = sorted(card for card in shown if card in theirs and card not in known)
    if clash:
        return "conflict", "%s panel shows %s, which the table has as the %s's card" % (
            side, " ".join(clash), other)

    unplaced = [card for card in shown if card not in known]
    free = len(own) - len(known)
    if len(unplaced) > free:
        return "conflict", (
            "%s panel shows %s, but the table has only %d unread place(s) for this seat"
            % (side, " ".join(sorted(unplaced)), free))
    if unplaced:
        return "pending", "%s panel shows %s, not yet read on the table" % (
            side, " ".join(sorted(unplaced)))

    if len(shown) < MAX_PANEL_CARDS:
        hole = [cards.get(slot) for slot in HOLE[side]]
        if all(hole) and set(shown) == set(hole):
            return "consistent", "%s panel agrees with the hole cards: %s" % (
                side, " ".join(sorted(shown)))
        return "pending", "%s panel row has %d card(s) so far" % (side, len(shown))

    if len(known) < MAX_PANEL_CARDS:
        return "pending", "%s: the table has %d card(s) for this seat" % (side, len(known))
    panel_score, table_score = _score(shown), _score(known)
    if panel_score is not None and panel_score == table_score:
        return "consistent", "%s panel agrees: %s" % (side, " ".join(sorted(shown)))
    return "mismatch", "%s panel shows %s, the table's best is %s" % (
        side, " ".join(sorted(shown)),
        " ".join(sorted(evaluate_hand(known)["best_five"])) if table_score else "unscorable")


def outcome(snapshot):
    """Each seat's final hand and the winner, from the panels alone.

    Only confirmed, complete, current rows count. The winner needs both.
    """
    result = {"player_hand": None, "dealer_hand": None, "winner": None,
              "qualified": None, "player_best_five": None, "dealer_best_five": None}
    hands = {}
    for side in SIDES:
        data = (snapshot or {}).get(side) or {}
        if not data.get("complete"):
            continue
        try:
            hands[side] = evaluate_hand(list(data["confirmed"]))
        except Exception:  # noqa: BLE001
            continue
        result["%s_hand" % side] = hands[side]["name"]
        result["%s_best_five" % side] = list(data["confirmed"])
    if len(hands) == 2:
        result["winner"] = compare_hands(hands["player"], hands["dealer"])
        result["qualified"] = dealer_qualifies(hands["dealer"])
    return result


# -- following the panels over time ---------------------------------------------

def thumbnail_status(read, confirmed_card=None):
    """CONFIRMED, CONFIRMING, HELD, QUEUED, AMBIGUOUS or UNKNOWN for one thumbnail.

    The same distinctions the table's cards get. A thumbnail that failed to read
    this poll is never reported as CONFIRMED just because its row is: it is HELD
    (the confirmed card stands, nothing is wrong enough to keep) or AMBIGUOUS
    (read well enough to argue about, which is evidence worth keeping).
    """
    card = read.get("card") if read.get("confident") else None
    ambiguous = bool(not card and read.get("card")
                     and undecided_half(read)
                     and read.get("confidence", 0.0) >= READABLE)
    if confirmed_card is not None:
        if card == confirmed_card or read.get("queued"):
            return CONFIRMED
        if card:
            return CONFIRMING           # the row is changing to another card
        return AMBIGUOUS if ambiguous else HELD
    if read.get("queued"):
        return QUEUED
    if card:
        return CONFIRMING
    return AMBIGUOUS if ambiguous else UNKNOWN


class _Row:
    """One side's row across polls."""

    def __init__(self, restart_length=None):
        # The one length a confirmed row may shrink back to: the player's two
        # hole cards at the next deal. Any other shorter reading is a thumbnail
        # missed for a poll, not a new hand, and is not allowed to replace it.
        self.restart_length = restart_length
        self.reset()
        self.retired = None      # the confirmed cards of the round before

    def reset(self):
        self.confirmed = None
        self.candidate = None
        self.candidate_count = 0
        self.absent = 0
        self.detected = 0
        self.reads = []
        self.cards = []

    @property
    def stale(self):
        return (self.confirmed is not None and self.retired is not None
                and set(self.confirmed) == self.retired)

    def update(self, reads):
        self.reads = reads
        self.detected = len(reads)
        self.cards = [read["card"] if read.get("confident") else None for read in reads]
        if not reads:
            self.absent += 1
            if self.absent >= VANISH_FRAMES:
                self.confirmed = None
                self.candidate = None
                self.candidate_count = 0
            return
        self.absent = 0

        cards = tuple(self.cards)
        if self.confirmed is not None and len(cards) == len(self.confirmed) and all(
                card in (None, kept) for card, kept in zip(cards, self.confirmed)):
            # The same row, perhaps with a thumbnail unreadable this poll.
            self.candidate, self.candidate_count = None, 0
            return
        if (self.confirmed is not None and len(cards) == len(self.confirmed)
                and any(card is not None and card != kept
                        for card, kept in zip(cards, self.confirmed))):
            # The row on screen shows a different card where a confirmed one
            # was, so the confirmed row is no longer what is on screen, however
            # much of the rest is unreadable. Keeping it is how, in a live run,
            # the player's flop-stage best five outlived the turn and the river
            # for ten polls - one new thumbnail could not be read - and was then
            # reported as a conflict with the table. It stops being confirmed.
            self.confirmed = None
        if (self.confirmed is not None and len(cards) < len(self.confirmed)
                and len(cards) != self.restart_length):
            return                  # a thumbnail missed this poll
        if None in cards:
            return                  # a partial reading cannot confirm anything
        if cards == self.candidate:
            self.candidate_count += 1
        else:
            self.candidate, self.candidate_count = cards, 1
        if self.candidate_count >= CONFIRM_FRAMES:
            self.confirmed = self.candidate
            self.candidate, self.candidate_count = None, 0
            if self.retired is not None and set(self.confirmed) != self.retired:
                self.retired = None     # the panel has moved on to this round

    def snapshot(self, side, round_id, observed_at):
        confirmed = list(self.confirmed) if self.confirmed else None
        reads = []
        for index, read in enumerate(self.reads):
            kept = self.confirmed[index] if (
                self.confirmed and index < len(self.confirmed)) else None
            reads.append({
                "card": read.get("card"),
                "confidence": read.get("confidence", 0.0),
                "rank_confidence": read.get("rank_confidence"),
                "suit_confidence": read.get("suit_confidence"),
                "suit_margin": read.get("suit_margin"),
                "confident": bool(read.get("confident")),
                "queued": bool(read.get("queued")),
                "status": thumbnail_status(read, kept),
                "region": read.get("region"),
            })
        held = self.detected == 0 and confirmed is not None
        if self.detected == 0 and not held:
            state = NOT_VISIBLE
        elif confirmed and len(confirmed) == MAX_PANEL_CARDS and not self.stale:
            state = VISIBLE_COMPLETE
        else:
            state = VISIBLE_PARTIAL
        return {
            "side": side,
            "state": state,
            "detected": self.detected,
            "confirmed": confirmed,
            "complete": state == VISIBLE_COMPLETE,
            "stale": self.stale,
            "held": held,
            "cards": list(self.cards),
            "reads": reads,
            "round_id": round_id,
            "observed_at": observed_at,
        }


class PanelTracker:
    """The result panels followed from poll to poll, one round at a time.

    Confirms a row only after it has read the same way on consecutive polls,
    keeps it through a poll where a thumbnail is unreadable or missed, and
    forgets it when it has been gone for a few polls.

    It never passes a panel from one round into the next. A new round is
    recognised two ways, so neither depends on the other: the round id the
    tracker passes in changing, or the player's confirmed row dropping back to
    the two hole cards of a new deal. Either way, what was confirmed is retired,
    and the same cards being confirmed again afterwards - the old panel still on
    screen - are marked stale and not used. A stale row is taken back only when
    it fits the table's current cards exactly, which is what happens when the
    panel reaches the new deal a poll or two before the card memory does.

    It is also careful with time. At most READS_PER_POLL new thumbnails are
    read in one poll and the rest are QUEUED for the next, so a panel filling
    up at the showdown cannot hold up the poll that has to catch the dealer's
    cards on the table.
    """

    def __init__(self, reader=read_slot, clock=time.monotonic,
                 full_search_interval=FULL_SEARCH_INTERVAL):
        self._reader = reader
        self._clock = clock
        self._interval = full_search_interval
        self._rows = {"player": _Row(restart_length=DEAL_LENGTH), "dealer": _Row()}
        self._round = None
        self._roi = None
        self._misses = 0
        self._last_full = None
        self._cache = {}            # (shape, hash of pixels) -> (pixels, read)
        self.crops = {}             # (side, index) -> pixels of the last poll
        self.searches = {"roi": 0, "full": 0}
        self.read_count = 0         # thumbnails actually recognised, for measuring
        self.last_retire = None

    # finding, cheaply where possible
    def _roi_for(self, found, shape):
        boxes = [box for row in found.values() for box in row]
        width = float(np.median([box[2] for box in boxes]))
        height = float(np.median([box[3] for box in boxes]))
        anchor = found.get("player") or boxes
        left = min(box[0] for box in boxes)
        return (max(0, int(left - 1.0 * width)),
                max(0, int(min(box[1] for box in boxes) - 3.5 * height)),
                min(shape[1], int(left + 7.0 * width)),
                min(shape[0], int(anchor[0][1] + 2.0 * height)))

    def _remember_player(self, found):
        row = found.get("player")
        if row:
            self._player_at = (row[0][0], row[0][1],
                               float(np.median([box[3] for box in row])))

    def _find(self, frame, layout, origin):
        known = getattr(self, "_player_at", None)
        if self._roi is not None:
            x0, y0, x1, y1 = self._roi
            self.searches["roi"] += 1
            local = (known[0] - x0, known[1] - y0, known[2]) if known else None
            found = locate(frame[y0:y1, x0:x1], known_player=local)
            if found:
                found = {side: [(b[0] + x0, b[1] + y0, b[2], b[3]) for b in row]
                         for side, row in found.items()}
                self._roi = self._roi_for(found, frame.shape)
                self._misses = 0
                self._remember_player(found)
                return found
            self._misses += 1
            if self._misses < ROI_MISSES:
                return {}
        now = self._clock()
        if self._last_full is not None and now - self._last_full < self._interval:
            return {}
        self._last_full = now
        self.searches["full"] += 1
        found = locate(frame, layout, origin, known_player=known)
        if found:
            self._roi = self._roi_for(found, frame.shape)
            self._misses = 0
            self._remember_player(found)
        return found

    # Thumbnails already read, remembered by what they look like. A row shows at
    # most seven distinct cards over a round, so this holds several rounds.
    CACHE_SIZE = 64

    def _read_box(self, side, index, frame, box, origin, budget):
        x, y, w, h = box
        pixels = frame[y:y + h, x:x + w]
        self.crops[(side, index)] = pixels
        # Keyed by the picture, so identical pixels are never read twice, which
        # covers a row that has not changed. It does not reliably cover a card
        # the casino has moved along the row: measured, two swapped thumbnails
        # were read again, because each position's box is cut a pixel
        # differently and the pictures are no longer identical.
        key = (pixels.shape, hash(pixels.tobytes()))
        cached = self._cache.get(key)
        if cached is not None and np.array_equal(cached[0], pixels):
            read = dict(cached[1])
        elif budget[0] <= 0:
            read = {"card": None, "confidence": 0.0, "confident": False, "queued": True}
        else:
            budget[0] -= 1
            self.read_count += 1
            read = self._reader(pixels)
            if len(self._cache) >= self.CACHE_SIZE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (pixels.copy(), dict(read))
        read["region"] = {"left": x + origin[0], "top": y + origin[1],
                          "width": w, "height": h}
        return read

    # rounds
    def retire(self, reason):
        """Close the current round's panel evidence.

        A row already marked as the previous round's is left alone: there is
        nothing new to close. That matters between rounds, when the card memory
        starts a new generation every few polls of an empty table - measured,
        three in nineteen polls - and resetting the panel each time would make
        it flicker and write the same lines to the log over and over.
        """
        changed = False
        for row in self._rows.values():
            if row.stale:
                continue
            if row.confirmed:
                row.retired = set(row.confirmed)
                changed = True
            row.reset()
        if changed:
            self.last_retire = reason

    def observe(self, frame, origin=(0, 0), layout=None, round_id=None, cards=None):
        """Take one poll's frame; returns the snapshot for both sides.

        `cards` is the table as the tracker has it this poll. It is only read,
        to decide whether a row that looks like the previous round's in fact
        belongs to this one.
        """
        self.last_retire = None
        if round_id is not None and round_id != self._round:
            if self._round is not None:
                self.retire("round changed (%s -> %s)" % (self._round, round_id))
            self._round = round_id

        player = self._rows["player"]
        before = list(player.confirmed) if player.confirmed else None

        found = self._find(frame, layout, origin) if frame is not None else {}
        seen = set()
        budget = [READS_PER_POLL]
        for side in SIDES:
            reads = [self._read_box(side, index, frame, box, origin, budget)
                     for index, box in enumerate(found.get(side) or [])]
            seen.update((side, index) for index in range(len(reads)))
            self._rows[side].update(reads)
        for key in [key for key in self.crops if key not in seen]:
            del self.crops[key]

        # The player's row only grows within a round. A confirmed row shorter
        # than the one before is the next deal, so whatever the dealer's row
        # still shows belongs to the hand before it.
        if before and player.confirmed and len(player.confirmed) < len(before):
            dealer = self._rows["dealer"]
            if dealer.confirmed:
                dealer.retired = set(dealer.confirmed)
            dealer.reset()
            player.retired = set(before)
            self.last_retire = ("the player's panel row went from %d cards to %d"
                                % (len(before), len(player.confirmed)))

        if cards:
            for side in SIDES:
                row = self._rows[side]
                if row.stale and verify(
                        side, {"confirmed": row.confirmed}, cards)[0] == "consistent":
                    row.retired = None
        return self.snapshot()

    def snapshot(self):
        now = self._clock()
        return {side: self._rows[side].snapshot(side, self._round, now)
                for side in SIDES}
