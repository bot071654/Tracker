"""The result panel as the primary source for each seat's final hand.

The panel says what each seat's best five are, what the hand is called and who
won. The table says which card is where. These tests hold both halves of that:
the panel is read, confirmed over polls, kept to its own round and compared
with the table - and nothing read from it is ever written into a table slot.

Only one real frame with both panels showing exists on disk, the sample
screenshot. Everything that happens over time - a panel appearing at the
showdown, going away, a thumbnail missed for a poll, the next deal - is built
from that frame by painting parts of it out, the way the table tests do. The
ground truth is the hand the screenshot test already asserts:

    table          dealer 8D QD | board 2D QH 3D 3H 6S | player 8S 9C
    player panel   3D 3H QH 9C 8S    (Pair)
    dealer panel   QD QH 3D 3H 8D    (Two Pair)
"""

import glob
import os
import queue

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module  # noqa: E402
from poker.hand_evaluator import RANKS, SUITS  # noqa: E402
from recognition import result_panel as rp  # noqa: E402
from recognition import table_layout  # noqa: E402
from recognition.card_recognizer import read_slot  # noqa: E402
from tests.fake_cards import fonts_available, render_card  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))

needs_frame = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot")
needs_fonts = pytest.mark.skipif(not fonts_available(), reason="no drawing fonts")

PLAYER_FIVE = ["3D", "3H", "QH", "9C", "8S"]
DEALER_FIVE = ["QD", "QH", "3D", "3H", "8D"]
TABLE = {
    "dealer_1": "8D", "dealer_2": "QD",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D", "turn": "3H", "river": "6S",
    "player_1": "8S", "player_2": "9C",
}
TABLE_REGIONS = {
    "dealer_1": {"left": 854, "top": 329, "width": 95, "height": 124},
    "dealer_2": {"left": 961, "top": 329, "width": 95, "height": 124},
    "flop_1": {"left": 690, "top": 451, "width": 102, "height": 124},
    "flop_2": {"left": 794, "top": 451, "width": 102, "height": 124},
    "flop_3": {"left": 903, "top": 451, "width": 99, "height": 124},
    "turn": {"left": 1005, "top": 451, "width": 102, "height": 124},
    "river": {"left": 1114, "top": 451, "width": 103, "height": 124},
    "player_1": {"left": 845, "top": 605, "width": 93, "height": 121},
    "player_2": {"left": 958, "top": 605, "width": 92, "height": 121},
}
FELT = (60, 95, 45)
PANEL_BACKGROUND = (23, 23, 23)

# Polls for a fresh panel of ten thumbnails to be read and confirmed: they are
# read a few at a time, then the full rows must repeat.
SETTLE = -(-10 // rp.READS_PER_POLL) + rp.CONFIRM_FRAMES


@pytest.fixture(scope="module")
def frame():
    image = cv2.imread(SCREENSHOTS[0])
    assert image is not None
    return image


@pytest.fixture(scope="module")
def rows(frame):
    found = rp.locate(frame)
    assert set(found) == {"player", "dealer"}
    return found


def paint(image, boxes, colour=PANEL_BACKGROUND, pad=1):
    out = image.copy()
    for x, y, w, h in boxes:
        out[y - pad:y + h + pad, x - pad:x + w + pad] = colour
    return out


def without_panel(image, rows):
    """The frame with the whole panel gone."""
    boxes = [box for row in rows.values() for box in row]
    x0 = min(b[0] for b in boxes) - 30
    x1 = max(b[0] + b[2] for b in boxes) + 30
    y0 = min(b[1] for b in boxes) - 30
    y1 = max(b[1] + b[3] for b in boxes) + 30
    out = image.copy()
    out[y0:y1, x0:x1] = PANEL_BACKGROUND
    return out


def sweep_table(image, slots):
    out = image.copy()
    for slot in slots:
        r = TABLE_REGIONS[slot]
        out[r["top"] - 14:r["top"] + r["height"] + 14,
            r["left"] - 14:r["left"] + r["width"] + 14] = FELT
    return out


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def feed(panels, clock, frames, round_id=1, cards=None):
    snapshot = None
    for image in frames:
        clock.now += 0.2
        snapshot = panels.observe(image, round_id=round_id, cards=cards)
    return snapshot


def new_tracker(reader=read_slot):
    clock = Clock()
    return rp.PanelTracker(reader=reader, clock=clock), clock


def thumbnail(card):
    """A drawn card shrunk to the panel's thumbnail size."""
    return cv2.resize(render_card(card, layout="stacked"), (34, 47),
                      interpolation=cv2.INTER_AREA)


# -- 1, 2: reading both rows ---------------------------------------------------------

@needs_frame
def test_player_panel_reads_five_of_five(frame):
    panels, clock = new_tracker()
    player = feed(panels, clock, [frame] * SETTLE)["player"]
    assert player["state"] == rp.VISIBLE_COMPLETE
    assert player["confirmed"] == PLAYER_FIVE
    assert all(read["status"] == rp.CONFIRMED for read in player["reads"])


@needs_frame
def test_dealer_panel_reads_five_of_five(frame):
    panels, clock = new_tracker()
    dealer = feed(panels, clock, [frame] * SETTLE)["dealer"]
    assert dealer["state"] == rp.VISIBLE_COMPLETE
    assert dealer["confirmed"] == DEALER_FIVE


@needs_frame
def test_every_thumbnail_reports_its_evidence(frame):
    panels, clock = new_tracker()
    snapshot = feed(panels, clock, [frame] * SETTLE)
    for side in rp.SIDES:
        for read in snapshot[side]["reads"]:
            for key in ("card", "confidence", "rank_confidence", "suit_confidence",
                        "suit_margin", "status", "region"):
                assert key in read, key


@needs_frame
def test_one_poll_is_not_enough_to_confirm(frame):
    panels, clock = new_tracker()
    first = feed(panels, clock, [frame])
    statuses = [read["status"] for side in rp.SIDES for read in first[side]["reads"]]
    assert first["player"]["confirmed"] is None
    assert first["player"]["state"] == rp.VISIBLE_PARTIAL
    assert set(statuses) <= {rp.CONFIRMING, rp.QUEUED}
    assert statuses.count(rp.CONFIRMING) == rp.READS_PER_POLL


@needs_frame
def test_a_filling_panel_costs_only_a_few_readings_per_poll(frame):
    """The panel fills up at the showdown, exactly when the dealer's table cards
    need polling. Measured before this limit: one poll reading everything took
    0.9s. The panel can wait a poll or two; the table cannot."""
    calls = []

    def counting(image):
        calls.append(1)
        return read_slot(image)

    panels, clock = new_tracker(counting)
    per_poll = []
    for _ in range(SETTLE):
        before = len(calls)
        snapshot = feed(panels, clock, [frame])
        per_poll.append(len(calls) - before)
    assert max(per_poll) <= rp.READS_PER_POLL, per_poll
    assert sum(per_poll) == 10, "every thumbnail read exactly once: %s" % per_poll
    assert snapshot["player"]["complete"] and snapshot["dealer"]["complete"]


# -- 3-9: small stacked cards, all 52 ------------------------------------------------

@needs_fonts
def test_no_thumbnail_is_ever_read_as_another_card():
    """All 52 at thumbnail size. Refusing is allowed; being wrong is not."""
    wrong = []
    for card in [rank + suit for rank in RANKS for suit in SUITS]:
        read = read_slot(thumbnail(card))
        if read["confident"] and read["card"] != card:
            wrong.append("%s -> %s" % (card, read["card"]))
    assert not wrong, wrong


@needs_fonts
def test_every_red_and_spade_thumbnail_is_read():
    """Measured: the 39 hearts, diamonds and spades all read at this size. The
    13 drawn clubs do not - their pip beats the spade by under 0.01 at 34x47
    and is refused, which the tests below pin down. The real panel's 9C does
    read (see test_result_panel)."""
    missed = []
    for card in [rank + suit for rank in RANKS for suit in "HDS"]:
        read = read_slot(thumbnail(card))
        if not read["confident"] or read["card"] != card:
            missed.append(card)
    assert not missed, missed


@needs_fonts
@pytest.mark.parametrize("card", ["10D", "10S", "8H", "3H"])
def test_named_cards_read_as_themselves_in_a_thumbnail(card):
    read = read_slot(thumbnail(card))
    assert read["confident"] and read["card"] == card, read


@needs_fonts
@pytest.mark.parametrize("card", ["10C", "QC"])
def test_club_thumbnails_are_refused_never_misread(card):
    read = read_slot(thumbnail(card))
    assert not read["confident"] or read["card"] == card, read


@needs_fonts
def test_a_club_too_close_to_a_spade_is_ambiguous_not_forced():
    read = read_slot(thumbnail("9C"))
    assert not read["confident"]
    assert rp.thumbnail_status(read) == rp.AMBIGUOUS


@needs_fonts
def test_8h_and_3h_thumbnails_are_not_confused():
    assert read_slot(thumbnail("8H"))["card"] == "8H"
    assert read_slot(thumbnail("3H"))["card"] == "3H"


# -- 10-13, 20: over time --------------------------------------------------------------

@needs_frame
def test_a_partly_visible_row_is_partial_and_never_complete(frame, rows):
    partial = paint(frame, rows["player"][3:])
    panels, clock = new_tracker()
    player = feed(panels, clock, [partial] * SETTLE)["player"]
    assert player["state"] == rp.VISIBLE_PARTIAL
    assert player["detected"] == 3
    assert not player["complete"]
    assert rp.outcome({"player": player})["player_hand"] is None


@needs_frame
def test_the_dealer_row_appears_at_the_showdown(frame, rows):
    before = paint(frame, rows["dealer"])
    panels, clock = new_tracker()
    snapshot = feed(panels, clock, [before] * SETTLE)
    assert snapshot["dealer"]["state"] == rp.NOT_VISIBLE
    assert snapshot["player"]["complete"]

    polls = 0
    while snapshot["dealer"]["confirmed"] is None and polls < SETTLE:
        snapshot = feed(panels, clock, [frame])
        polls += 1
    assert snapshot["dealer"]["confirmed"] == DEALER_FIVE
    assert polls >= rp.CONFIRM_FRAMES, "confirmed from a single poll"
    assert rp.outcome(snapshot)["winner"] == "Dealer"


@needs_frame
def test_a_panel_that_goes_away_is_held_briefly_then_forgotten(frame, rows):
    gone = without_panel(frame, rows)
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE)
    for _ in range(rp.VANISH_FRAMES - 1):
        player = feed(panels, clock, [gone])["player"]
        assert player["held"] and player["confirmed"] == PLAYER_FIVE
    player = feed(panels, clock, [gone])["player"]
    assert player["state"] == rp.NOT_VISIBLE
    assert player["confirmed"] is None


@needs_frame
def test_a_new_round_does_not_inherit_the_old_panel(frame):
    """Round id changes while the old panel is still on screen."""
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE, round_id=1)
    snapshot = feed(panels, clock, [frame] * 3, round_id=2)
    for side in rp.SIDES:
        assert snapshot[side]["stale"], side
        assert not snapshot[side]["complete"], side
        assert rp.verify(side, snapshot[side], {})[0] == "unavailable"
    assert rp.outcome(snapshot) == rp.outcome({})


@needs_frame
def test_a_stale_row_is_taken_back_only_when_it_fits_the_table(frame):
    """The panel reaches a new deal before the card memory does, so the round
    id changes after the new panel is already up. A row that fits the table's
    cards exactly belongs to this round; one that does not stays stale."""
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE, round_id=1)
    elsewhere = dict(TABLE, player_1="AS", player_2="KS")
    snapshot = feed(panels, clock, [frame] * 3, round_id=2, cards=elsewhere)
    assert snapshot["player"]["stale"]
    snapshot = feed(panels, clock, [frame], round_id=2, cards=TABLE)
    assert not snapshot["player"]["stale"]
    assert snapshot["player"]["complete"]


@needs_frame
def test_the_next_deal_retires_the_dealer_row(frame, rows):
    """The player's row going back to two cards is a new deal, and the dealer's
    row still on screen from the last hand must not be used with it."""
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE)
    deal = paint(frame, rows["player"][2:])          # dealer row left up
    snapshot = feed(panels, clock, [deal] * 2)
    assert "5 cards to 2" in (panels.last_retire or "")
    assert snapshot["player"]["confirmed"] == PLAYER_FIVE[:2]
    assert not snapshot["player"]["stale"]
    snapshot = feed(panels, clock, [deal] * 2)
    assert snapshot["dealer"]["stale"], "the old dealer row was taken as the new hand's"
    assert rp.outcome(snapshot)["dealer_hand"] is None


@needs_frame
def test_a_thumbnail_missed_for_a_couple_of_polls_changes_nothing(frame, rows):
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE)
    blink = paint(frame, [rows["player"][4]])
    snapshot = feed(panels, clock, [blink] * 2)
    assert snapshot["player"]["confirmed"] == PLAYER_FIVE
    assert snapshot["dealer"]["confirmed"] == DEALER_FIVE
    assert panels.last_retire is None
    snapshot = feed(panels, clock, [frame])
    assert snapshot["player"]["complete"] and snapshot["dealer"]["complete"]


@needs_frame
def test_a_row_the_screen_contradicts_stops_being_confirmed(frame, rows):
    """Live, 14:28: the player's flop-stage best five stayed confirmed through
    the turn and the river because one new thumbnail could not be read, and was
    then reported as a conflict with the table. A visibly different card in the
    row ends the confirmation, whatever else is unreadable."""
    a, b = rows["player"][0], rows["player"][1]
    w, h = min(a[2], b[2]), min(a[3], b[3])
    changed = frame.copy()
    changed[a[1]:a[1] + h, a[0]:a[0] + w] = frame[b[1]:b[1] + h, b[0]:b[0] + w]
    changed[b[1]:b[1] + h, b[0]:b[0] + w] = frame[a[1]:a[1] + h, a[0]:a[0] + w]
    x, y, ww, hh = rows["player"][4]
    changed[y + 2:y + hh // 2, x + 2:x + ww - 2] = (250, 250, 250)    # unreadable
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE)
    for _ in range(4):
        player = feed(panels, clock, [changed])["player"]
    assert player["confirmed"] != PLAYER_FIVE
    assert rp.verify("player", player, TABLE)[0] != "conflict"


@needs_frame
def test_an_unreadable_thumbnail_for_one_poll_keeps_the_row(frame, rows):
    x, y, w, h = rows["dealer"][2]
    smudged = frame.copy()
    smudged[y + 2:y + h // 2, x + 2:x + w - 2] = (250, 250, 250)   # index wiped
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE)
    dealer = feed(panels, clock, [smudged])["dealer"]
    assert dealer["complete"]
    assert dealer["confirmed"] == DEALER_FIVE


# -- 14-16: against the table ------------------------------------------------------------

def test_the_panel_agrees_with_the_table():
    for side, five in (("player", PLAYER_FIVE), ("dealer", DEALER_FIVE)):
        verdict, message = rp.verify(side, {"confirmed": five}, TABLE)
        assert verdict == "consistent", message


def test_a_panel_card_the_table_cannot_hold_is_a_conflict():
    misread = dict(TABLE, dealer_2="KS")            # the table read QD as KS
    verdict, message = rp.verify("dealer", {"confirmed": DEALER_FIVE}, misread)
    assert verdict == "conflict"
    assert "QD" in message


def test_the_other_seats_hole_card_in_a_panel_is_a_conflict():
    swapped = dict(TABLE, player_1="QD", dealer_2="8S")
    verdict, message = rp.verify("dealer", {"confirmed": DEALER_FIVE}, swapped)
    assert verdict == "conflict"
    assert "player" in message


def test_a_conflict_changes_nothing_on_the_table():
    misread = dict(TABLE, dealer_2="KS")
    before = dict(misread)
    rp.verify("dealer", {"confirmed": DEALER_FIVE}, misread)
    assert misread == before


def test_an_equal_hand_with_a_different_kicker_is_consistent():
    """Two queens left to choose a kicker from: either one is the same hand."""
    table = {"player_1": "AS", "player_2": "AH", "flop_1": "KD", "flop_2": "KC",
             "flop_3": "QS", "turn": "QH", "river": "2C",
             "dealer_1": None, "dealer_2": None}
    for kicker in ("QS", "QH"):
        verdict, _ = rp.verify("player", {"confirmed": ["AS", "AH", "KD", "KC", kicker]},
                               table)
        assert verdict == "consistent", kicker


def test_a_weaker_panel_hand_is_a_mismatch_not_agreement():
    table = {"player_1": "AS", "player_2": "AH", "flop_1": "KD", "flop_2": "KC",
             "flop_3": "QS", "turn": "QH", "river": "2C",
             "dealer_1": None, "dealer_2": None}
    verdict, _ = rp.verify("player", {"confirmed": ["AS", "AH", "KD", "KC", "2C"]}, table)
    assert verdict == "mismatch"


def test_a_panel_ahead_of_the_table_is_pending():
    early = dict(TABLE, dealer_1=None)
    verdict, message = rp.verify("dealer", {"confirmed": DEALER_FIVE}, early)
    assert verdict == "pending"
    assert "8D" in message


def test_the_two_hole_cards_before_the_flop_are_consistent():
    table = dict.fromkeys(TABLE)
    table.update(player_1="9C", player_2="3S")
    assert rp.verify("player", {"confirmed": ["9C", "3S"]}, table)[0] == "consistent"


def test_nothing_confirmed_is_unavailable():
    assert rp.verify("player", None, TABLE)[0] == "unavailable"
    assert rp.verify("player", {"confirmed": None}, TABLE)[0] == "unavailable"


def test_the_winner_needs_both_panels():
    only_player = {"player": {"complete": True, "confirmed": PLAYER_FIVE}}
    assert rp.outcome(only_player)["winner"] is None
    both = dict(only_player, dealer={"complete": True, "confirmed": DEALER_FIVE})
    result = rp.outcome(both)
    assert (result["player_hand"], result["dealer_hand"], result["winner"],
            result["qualified"]) == ("Pair", "Two Pair", "Dealer", True)


# -- 17-19: where the panel is -------------------------------------------------------------

@needs_frame
def test_the_panel_is_found_with_the_table_swept_away(frame):
    swept = sweep_table(frame, TABLE_REGIONS)
    panels = rp.read_panels(swept)
    assert panels["player"]["cards"] == PLAYER_FIVE
    assert panels["dealer"]["cards"] == DEALER_FIVE


@needs_frame
def test_white_boxes_in_another_window_are_not_a_panel(frame):
    busy = frame.copy()
    busy[600:1000, 1500:1900] = (238, 238, 238)
    for i in range(5):
        busy[700:747, 1520 + i * 40:1553 + i * 40] = (252, 252, 252)
    found = rp.locate(busy)
    for row in found.values():
        assert all(box[0] < 1500 for box in row), "a white window was read as a panel"


@needs_frame
def test_blank_boxes_on_a_dark_background_are_not_a_panel(frame, rows):
    decoy = paint(frame, rows["dealer"])
    decoy[900:1000, 250:520] = PANEL_BACKGROUND
    for i in range(5):
        decoy[930:977, 270 + i * 40:303 + i * 40] = (252, 252, 252)
    found = rp.locate(decoy)
    assert found["player"][0][1] == rows["player"][0][1], "blank boxes taken for the player"


@needs_frame
def test_a_panel_where_no_panel_can_be_is_ignored_once_the_table_is_known(frame, rows):
    """A copy of the panel moved to the right of the board: with the table found,
    it is outside the part of the game a panel sits in."""
    boxes = [box for row in rows.values() for box in row]
    x0, x1 = min(b[0] for b in boxes) - 25, max(b[0] + b[2] for b in boxes) + 25
    y0, y1 = min(b[1] for b in boxes) - 25, max(b[1] + b[3] for b in boxes) + 25
    moved = without_panel(frame, rows)
    moved[y0:y1, 1400:1400 + (x1 - x0)] = frame[y0:y1, x0:x1]
    assert rp.locate(moved), "the copied panel is not a valid panel at all"
    layout = table_layout.locate(moved)
    assert layout, "the table was not found"
    assert rp.locate(moved, layout=layout) == {}


@needs_frame
@pytest.mark.parametrize("scale", [0.75, 0.9, 1.1, 1.25])
def test_the_panel_reads_at_other_browser_sizes(frame, scale):
    resized = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    panels = rp.read_panels(resized)
    assert panels["player"]["cards"] == PLAYER_FIVE
    assert panels["dealer"]["cards"] == DEALER_FIVE


@needs_frame
def test_the_panel_follows_the_window_when_it_moves(frame):
    moved = cv2.copyMakeBorder(frame, 80, 0, 120, 0, cv2.BORDER_CONSTANT, value=0)
    panels = rp.read_panels(moved, origin=(1920, 0))
    assert panels["player"]["cards"] == PLAYER_FIVE
    plain = rp.read_panels(frame)["player"]["reads"][0]["region"]
    shifted = panels["player"]["reads"][0]["region"]
    assert shifted["left"] == plain["left"] + 120 + 1920
    assert shifted["top"] == plain["top"] + 80


@needs_frame
def test_steady_polls_search_only_near_the_panel(frame):
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * 12)
    assert panels.searches["full"] == 1
    assert panels.searches["roi"] == 11


# -- in the tracker ----------------------------------------------------------------------

def run(monkeypatch, images, config=None):
    events = queue.Queue()
    tracker = tracker_module.Tracker(config or {"monitor": 1}, events)
    tracker._store = lambda cards, reads=None: None
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    for image in images:
        monkeypatch.setattr(tracker_module, "grab_full_screen",
                            lambda monitor=1, image=image: image)
        tracker._tick()
    updates = []
    while not events.empty():
        kind, payload = events.get()
        if kind == "update":
            updates.append(payload)
    return tracker, updates, events


@needs_frame
def test_each_update_carries_the_panel_and_its_verdict(monkeypatch, frame):
    tracker, updates, _ = run(monkeypatch, [frame] * SETTLE)
    panels = updates[-1]["panels"]
    assert panels["outcome"]["winner"] == "Dealer"
    assert panels["verdicts"]["player"][0] == "consistent"
    assert panels["verdicts"]["dealer"][0] == "consistent"
    assert panels["sides"]["player"]["round_id"] == tracker.memory.generation


@needs_frame
def test_the_panel_never_fills_a_table_slot(monkeypatch, frame):
    """The dealer's first card is not on the table; the panel shows it. The
    panel may say the dealer has two pair - it may not say which card is where."""
    swept = sweep_table(frame, ["dealer_1"])
    _, updates, _ = run(monkeypatch, [swept] * SETTLE)
    last = updates[-1]
    assert last["cards"].get("dealer_1") is None
    assert last["panels"]["outcome"]["dealer_hand"] == "Two Pair"
    assert "8D" in last["panels"]["sides"]["dealer"]["confirmed"]
    assert last["panels"]["verdicts"]["dealer"][0] == "pending"


@needs_frame
def test_steady_tracker_polls_do_not_search_the_whole_screen(monkeypatch, frame):
    tracker, _, _ = run(monkeypatch, [frame] * 8)
    assert tracker.panels.searches["full"] == 1


def test_a_panel_failure_never_stops_a_poll(monkeypatch):
    felt = np.full((600, 900, 3), FELT, np.uint8)
    events = queue.Queue()
    tracker = tracker_module.Tracker({"monitor": 1}, events)
    tracker._store = lambda cards, reads=None: None

    def broken(*args, **kwargs):
        raise RuntimeError("panel exploded")

    tracker.panels.observe = broken
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: felt)
    tracker._tick()
    kinds = []
    while not events.empty():
        kinds.append(events.get())
    updates = [payload for kind, payload in kinds if kind == "update"]
    assert updates and updates[-1]["panels"] is None


def test_a_conflict_is_reported_as_a_warning():
    events = queue.Queue()
    tracker = tracker_module.Tracker({"monitor": 1}, events)
    data = {"state": rp.VISIBLE_COMPLETE, "confirmed": DEALER_FIVE,
            "stale": False, "round_id": 3}
    tracker._note_panel("dealer", data, "conflict", "dealer panel shows QD ...", TABLE)
    warnings = []
    while not events.empty():
        kind, payload = events.get()
        if kind == "warning":
            warnings.append(payload)
    assert warnings and "disagrees" in warnings[0]


def test_a_lasting_mismatch_becomes_a_conflict():
    events = queue.Queue()
    tracker = tracker_module.Tracker({"monitor": 1}, events)
    table = {"player_1": "AS", "player_2": "AH", "flop_1": "KD", "flop_2": "KC",
             "flop_3": "QS", "turn": "QH", "river": "2C",
             "dealer_1": None, "dealer_2": None}
    weaker = {"state": rp.VISIBLE_COMPLETE, "confirmed": ["AS", "AH", "KD", "KC", "2C"],
              "complete": True, "stale": False, "held": False, "detected": 5,
              "cards": [], "reads": [], "round_id": 0, "observed_at": 0}
    empty = dict(weaker, state=rp.NOT_VISIBLE, confirmed=None, complete=False)
    tracker.panels.observe = lambda *a, **k: {"player": weaker, "dealer": empty}
    verdicts = []
    for _ in range(tracker_module.PANEL_MISMATCH_POLLS):
        tracker._frame = (np.zeros((10, 10, 3), np.uint8), (0, 0))
        verdicts.append(tracker._observe_panels(table)["verdicts"]["player"][0])
    assert verdicts[0] == "mismatch"
    assert verdicts[-1] == "conflict"


def test_panel_failures_leave_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker_module, "LOG_ROOT", str(tmp_path))
    tracker = tracker_module.Tracker({"monitor": 1, "debug_save_failures": True},
                                     queue.Queue())
    tracker.panels.crops[("player", 0)] = np.full((47, 33, 3), 200, np.uint8)
    tracker.panels.crops[("player", 1)] = np.full((47, 33, 3), 200, np.uint8)
    unreadable = {"status": rp.UNKNOWN, "card": None, "confidence": 0.21,
                  "rank_confidence": 0.3, "suit_confidence": 0.2, "suit_margin": 0.0,
                  "region": {"left": 305, "top": 829, "width": 33, "height": 47}}
    waiting = dict(unreadable, status=rp.QUEUED, confidence=0.0)
    snapshot = {"player": {"reads": [unreadable, waiting], "round_id": 4},
                "dealer": {"reads": [], "round_id": 4}}
    tracker._note_panel_failures(snapshot)
    tracker._note_panel_failures(snapshot)             # unchanged: kept once
    saved = list((tmp_path / "logs" / "failures").glob("*.png"))
    assert len(saved) == 1, "a queued thumbnail is not a failure: %s" % saved
    assert "panel_player_1" in saved[0].name and "unknown" in saved[0].name


@needs_frame
def test_a_failed_read_on_a_confirmed_row_is_not_reported_as_confirmed(frame, rows):
    """The row stands through a bad poll, but the thumbnail that failed says so."""
    x, y, w, h = rows["dealer"][2]
    smudged = frame.copy()
    smudged[y + 2:y + h // 2, x + 2:x + w - 2] = (250, 250, 250)
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE)
    dealer = feed(panels, clock, [smudged])["dealer"]
    assert dealer["complete"]
    assert dealer["reads"][2]["status"] in (rp.HELD, rp.AMBIGUOUS), dealer["reads"][2]
    assert all(read["status"] == rp.CONFIRMED
               for index, read in enumerate(dealer["reads"]) if index != 2)


def test_held_and_queued_thumbnails_leave_no_failure_evidence():
    assert rp.thumbnail_status({"card": None, "confident": False}, "QD") == rp.HELD
    assert rp.thumbnail_status({"queued": True, "card": None}, "QD") == rp.CONFIRMED
    assert rp.thumbnail_status({"queued": True, "card": None}) == rp.QUEUED
    close = {"card": "9S", "confident": False, "confidence": 0.87, "suit_margin": 0.01}
    assert rp.thumbnail_status(close, "9C") == rp.AMBIGUOUS
    assert rp.thumbnail_status(close) == rp.AMBIGUOUS


@needs_frame
def test_repeated_round_changes_over_an_unchanged_panel_retire_it_once(frame):
    panels, clock = new_tracker()
    feed(panels, clock, [frame] * SETTLE, round_id=1)
    retires = 0
    for round_id in range(2, 8):
        for _ in range(3):
            clock.now += 0.2
            snapshot = panels.observe(frame, round_id=round_id)
            retires += panels.last_retire is not None
    assert retires == 1
    assert snapshot["player"]["stale"] and snapshot["dealer"]["stale"]


@needs_frame
def test_a_long_empty_table_between_rounds_retires_the_panel_once(monkeypatch, frame):
    """The card memory starts a new generation every few polls of an empty
    table. The panel must close its round once, not every time."""
    swept = sweep_table(frame, TABLE_REGIONS)
    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    tracker._store = lambda cards, reads=None: None
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    retires, generations = 0, set()
    for image in [frame] * SETTLE + [swept] * 15:
        monkeypatch.setattr(tracker_module, "grab_full_screen",
                            lambda monitor=1, image=image: image)
        tracker._tick()
        generations.add(tracker.memory.generation)
        retires += tracker.panels.last_retire is not None
    assert len(generations) >= 3, "the memory never moved on - nothing was exercised"
    assert retires == 1
    snapshot = tracker.panels.snapshot()
    assert snapshot["player"]["stale"] and snapshot["dealer"]["stale"]
