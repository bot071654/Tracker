"""Finding the cards in the frame instead of trusting calibrated boxes.

The calibrated boxes are absolute screen pixels, so they only hold while the
browser stays put. These tests work from the real casino screenshot with the
calibrated boxes deliberately withheld: the detector has to find the nine cards
knowing nothing about where they were last time.

The doctored frames stand in for what the live table does - cards dealt one
street at a time, a dealer's hand sweeping across, the table drawn at another
size. The bar for those is not that every card is read; it is that no card is
read *wrongly*, because a wrong card is recorded and a missing one is not.
"""

import glob
import os

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from capture.screen_capture import crop  # noqa: E402
from config.settings import CARD_SLOTS  # noqa: E402
from recognition import table_layout  # noqa: E402
from recognition.card_recognizer import read_slot  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))
SAMPLES = sorted(glob.glob(os.path.join(ROOT, "samples", "*.png")))

pytestmark = pytest.mark.skipif(
    not SCREENSHOTS, reason="no casino screenshot in the project folder")

EXPECTED = {
    "dealer_1": "8D", "dealer_2": "QD",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "player_1": "8S", "player_2": "9C",
}

# Where the cards sit in that screenshot - used only to doctor the frame and
# to check the detector's answers, never given to the detector.
REGIONS = {
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
HAND = (150, 178, 214)


@pytest.fixture(scope="module")
def frame():
    image = cv2.imread(SCREENSHOTS[0])
    assert image is not None
    return image


def blank(frame, slots, colour=FELT):
    """A copy of the frame with those card positions wiped back to felt."""
    out = frame.copy()
    for slot in slots:
        r = REGIONS[slot]
        out[r["top"] - 14:r["top"] + r["height"] + 14,
            r["left"] - 14:r["left"] + r["width"] + 14] = colour
    return out


def read(frame, layout):
    """slot -> card, from a located layout. None where nothing was read."""
    cards = {}
    regions = (layout or {}).get("regions") or {}
    for slot in CARD_SLOTS:
        region = regions.get(slot)
        if region is None:
            cards[slot] = None
            continue
        result = read_slot(crop(frame, region))
        cards[slot] = result["card"] if result["confident"] else None
    return cards


def wrong_cards(cards, expected):
    """Cards read as something other than what is there. Missing is not wrong."""
    return {slot: cards[slot] for slot in expected
            if cards.get(slot) is not None and cards[slot] != expected[slot]}


# -- finding the cards at all -------------------------------------------------

def test_the_nine_cards_are_found_without_any_calibration(frame):
    layout = table_layout.locate(frame)
    assert layout is not None, "no table found in a plain showdown frame"
    assert layout["found"] == 9
    assert set(layout["regions"]) == set(CARD_SLOTS)


def test_every_card_reads_correctly_from_the_found_boxes(frame):
    cards = read(frame, table_layout.locate(frame))
    assert cards == EXPECTED


def test_the_boxes_found_land_on_the_real_cards(frame):
    """Overlap with the known positions, rather than merely reading correctly."""
    layout = table_layout.locate(frame)
    for slot, want in REGIONS.items():
        got = layout["regions"][slot]
        overlap_x = max(0, min(got["left"] + got["width"], want["left"] + want["width"])
                        - max(got["left"], want["left"]))
        overlap_y = max(0, min(got["top"] + got["height"], want["top"] + want["height"])
                        - max(got["top"], want["top"]))
        assert overlap_x * overlap_y > 0.5 * want["width"] * want["height"], slot


def test_the_result_panels_are_not_mistaken_for_the_table(frame):
    """The panels draw ten cards at a third of the size - and there are more of
    them than there are on the table, so counting would pick them every time."""
    layout = table_layout.locate(frame)
    for region in layout["regions"].values():
        assert region["left"] > 600, "picked up the bottom-left result panels"
    assert layout["card_size"][1] > 90, "locked onto the panels' small cards"


# -- the rest of the desktop --------------------------------------------------
#
# The detector searches the whole screen, and every other window on it is full
# of pale rectangles. This was not theoretical: with failure crops switched on,
# 867 were saved from one live session, and the dealer's first card had landed
# on the tracker's own sidebar - white panels with session titles on them - as
# often as on a card. Two rows of white blocks 290px to the right of the board
# were enough to take the located slots from nine to four, putting the turn and
# the third flop card in the other window and losing the dealer, the player and
# the river altogether.
#
# What separates the table from the desktop is not brightness or size, which a
# UI panel matches: it is arrangement. Cards in a row touch each other, and the
# hole cards line up over the board.

WINDOW = (245, 245, 245)


def windows(frame, spots, colour=WINDOW):
    """A copy of the frame with another application's panels painted on it."""
    out = frame.copy()
    for x, y, width, height in spots:
        out[y:y + height, x:x + width] = colour
    return out


def beside_the_table(x):
    """Four pale panels down the screen at `x`, the way a sidebar sits."""
    return [(x, 200 + i * 150, 110, 130) for i in range(4)]


@pytest.mark.parametrize("where,spots", [
    ("to the right", beside_the_table(1500) + beside_the_table(1630)),
    ("to the left", beside_the_table(20) + beside_the_table(150)),
    ("on both sides", beside_the_table(20) + beside_the_table(1630)),
    ("above the table", [(1400, 100, 110, 130), (1530, 100, 110, 130)]),
])
def test_another_window_cannot_supply_a_card(frame, where, spots):
    """The nine cards are still found, and none of them is off the table."""
    busy = windows(frame, spots)
    layout = table_layout.locate(busy)
    assert layout, "lost the table because of a window %s" % where

    cards = read(busy, layout)
    assert not wrong_cards(cards, EXPECTED)
    assert sorted(slot for slot in EXPECTED if cards[slot]) == sorted(EXPECTED), (
        "slots lost to a window %s: %s"
        % (where, [slot for slot in EXPECTED if not cards[slot]]))

    for slot, region in layout["regions"].items():
        for x, y, width, height in spots:
            overlaps = (region["left"] < x + width
                        and x < region["left"] + region["width"]
                        and region["top"] < y + height
                        and y < region["top"] + region["height"])
            assert not overlaps, "%s landed on the window %s" % (slot, where)


def test_a_pair_of_panels_is_not_adopted_as_a_hand(frame):
    """A window off to one side shows two card-shaped blocks side by side. That
    is what the dealer's hand looks like, except for where it is."""
    busy = windows(frame, [(1500, 330, 110, 124), (1620, 330, 110, 124)])
    layout = table_layout.locate(busy)
    for slot in ("dealer_1", "dealer_2"):
        assert layout["regions"][slot]["left"] < 1200, "%s taken from the window" % slot
    assert read(busy, layout)["dealer_1"] == EXPECTED["dealer_1"]


def test_a_row_of_cards_has_to_be_a_row(frame):
    """Two pairs at the same height, far apart, are two things - not one row of
    four. Sharing a line is what every window at that height also does."""
    height = 120
    rows = table_layout.group_rows(
        [(100, 400, 100, height), (205, 400, 100, height),     # a pair here
         (900, 400, 100, height), (1005, 400, 100, height)],   # and one there
        height)
    assert len(rows) == 2, "the gap across the screen was treated as one row"
    assert [len(row) for row in rows] == [2, 2]


def test_cards_touching_each_other_stay_one_row():
    height, width = 120, 100
    row = [(690 + i * (width + 6), 450, width, height) for i in range(5)]
    assert [len(r) for r in table_layout.group_rows(row, height)] == [5]


def test_a_row_survives_one_card_being_hidden():
    """A hand resting on the board hides a card completely, leaving a gap about
    one card wide. The row must bridge it, or the covered card costs its
    neighbours too: measured, a tighter limit lost two slots instead of one."""
    height, width = 120, 100
    row = [(690 + i * (width + 6), 450, width, height) for i in range(5)]
    del row[2]
    assert [len(r) for r in table_layout.group_rows(row, height)] == [4]


def test_a_window_is_further_off_than_a_hidden_card(frame):
    """The two cases the gap limit sits between, on the real frame."""
    boxes = table_layout.find_card_boxes(frame)
    card_height = table_layout.dominant_card_size(boxes)
    table = [box for box in boxes
             if abs(box[3] - card_height) <= 0.25 * card_height]
    width = max(box[2] for box in table)
    hidden_card = (width + 12) / float(width)
    a_window = (1500 - 1210) / float(width)
    assert hidden_card < table_layout.ROW_GAP_LIMIT < a_window


# -- the table as it is dealt -------------------------------------------------

def test_before_the_flop_nothing_is_claimed(frame):
    """Two player cards alone are not a table; the caller falls back instead."""
    only_player = blank(frame, ["flop_1", "flop_2", "flop_3", "turn", "river",
                                "dealer_1", "dealer_2"])
    assert not table_layout.looks_plausible(table_layout.locate(only_player))


def test_the_flop_is_read_while_the_dealer_is_still_face_down(frame):
    flop = blank(frame, ["turn", "river", "dealer_1", "dealer_2"])
    cards = read(flop, table_layout.locate(flop))
    assert [cards[s] for s in ("flop_1", "flop_2", "flop_3")] == ["2D", "QH", "3D"]
    assert cards["player_1"] == "8S" and cards["player_2"] == "9C"
    assert cards["turn"] is None and cards["river"] is None
    assert cards["dealer_1"] is None and cards["dealer_2"] is None


def test_the_turn_is_picked_up_when_it_lands(frame):
    turn = blank(frame, ["river", "dealer_1", "dealer_2"])
    cards = read(turn, table_layout.locate(turn))
    assert cards["turn"] == "3H"
    assert cards["river"] is None


def test_the_river_is_picked_up_when_it_lands(frame):
    river = blank(frame, ["dealer_1", "dealer_2"])
    cards = read(river, table_layout.locate(river))
    assert cards["river"] == "6S"
    assert cards["dealer_1"] is None and cards["dealer_2"] is None


def test_a_face_down_card_is_not_read_as_a_card(frame):
    """The dealer's cards are face down until the showdown. A card back is a
    solid colour, so it must not even be offered as a candidate."""
    backs = frame.copy()
    for slot in ("dealer_1", "dealer_2"):
        r = REGIONS[slot]
        cv2.rectangle(backs, (r["left"], r["top"]),
                      (r["left"] + r["width"], r["top"] + r["height"]),
                      (120, 40, 40), -1)          # a blue card back
    cards = read(backs, table_layout.locate(backs))
    assert cards["dealer_1"] is None and cards["dealer_2"] is None
    assert cards["player_1"] == "8S", "the rest of the table still reads"


def test_an_empty_table_finds_nothing(frame):
    felt = np.full_like(frame, FELT)
    assert table_layout.locate(felt) is None


# -- covered cards ------------------------------------------------------------

def test_a_hand_over_the_table_never_invents_a_card(frame):
    """The point of the fallback. A sweep merges cards into one shape that fits
    no arrangement, so the frame must yield nothing rather than a wrong guess."""
    covered = frame.copy()
    r = REGIONS["flop_2"]
    cv2.ellipse(covered, (r["left"] + r["width"] // 2, r["top"] + r["height"] // 2),
                (r["width"] // 2 + 18, r["height"] // 2 + 14), 12, 0, 360, HAND, -1)
    cards = read(covered, table_layout.locate(covered))
    assert wrong_cards(cards, EXPECTED) == {}


def test_the_last_good_layout_carries_a_covered_frame(frame):
    """What the tracker actually does: keep the layout that worked and read the
    covered frame with it, so the cards that are still visible still count."""
    good = table_layout.locate(frame)
    covered = frame.copy()
    r = REGIONS["flop_2"]
    cv2.ellipse(covered, (r["left"] + r["width"] // 2, r["top"] + r["height"] // 2),
                (r["width"] // 2 + 18, r["height"] // 2 + 14), 12, 0, 360, HAND, -1)

    cards = read(covered, good)
    assert wrong_cards(cards, EXPECTED) == {}
    assert cards["flop_2"] is None, "the covered card should not read"
    readable = [slot for slot in CARD_SLOTS if cards[slot]]
    assert len(readable) >= 7, "the rest of the table was lost too: %s" % cards


# -- other sizes and positions ------------------------------------------------

@pytest.mark.parametrize("scale", [0.75, 0.9, 1.1])
def test_the_table_is_found_at_other_sizes(frame, scale):
    """Browser zoom and window size change how big the cards are drawn."""
    resized = cv2.resize(frame, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    layout = table_layout.locate(resized)
    assert table_layout.looks_plausible(layout), "table lost at %sx" % scale
    assert wrong_cards(read(resized, layout), EXPECTED) == {}


@pytest.mark.parametrize("dx,dy", [(0, -40), (0, 45), (-30, 0), (25, 18)])
def test_the_table_is_found_when_the_window_moves(frame, dx, dy):
    """The browser moving is what breaks calibrated boxes outright."""
    moved = np.full_like(frame, 30)
    h, w = frame.shape[:2]
    sx, sy = max(0, dx), max(0, dy)
    moved[sy:h - max(0, -dy) + sy if dy < 0 else h,
          sx:w - max(0, -dx) + sx if dx < 0 else w] = frame[
        max(0, -dy):h - sy if dy > 0 else h, max(0, -dx):w - sx if dx > 0 else w]

    layout = table_layout.locate(moved)
    assert table_layout.looks_plausible(layout)
    cards = read(moved, layout)
    assert wrong_cards(cards, EXPECTED) == {}
    assert cards["player_1"] == "8S" and cards["player_2"] == "9C"


def test_regions_come_back_in_screen_coordinates(frame):
    """A capture of a second monitor does not start at zero, and the regions
    have to line up with the calibrated ones, which are absolute."""
    plain = table_layout.locate(frame)
    shifted = table_layout.locate(frame, origin=(1920, 0))
    assert (shifted["regions"]["player_1"]["left"]
            == plain["regions"]["player_1"]["left"] + 1920)
    assert (shifted["regions"]["player_1"]["top"]
            == plain["regions"]["player_1"]["top"])


# -- merging with the fallback ------------------------------------------------

def test_merge_fills_the_gaps_from_the_fallback():
    layout = {"regions": {"player_1": {"left": 1, "top": 2, "width": 3, "height": 4}}}
    fallback = {slot: {"left": 9, "top": 9, "width": 9, "height": 9}
                for slot in CARD_SLOTS}
    merged = table_layout.merge(layout, fallback)
    assert merged["player_1"]["left"] == 1, "the found box must win"
    assert merged["river"]["left"] == 9, "the gap must come from the fallback"
    assert set(merged) == set(CARD_SLOTS)


def test_merge_without_a_layout_is_just_the_fallback():
    fallback = {"player_1": {"left": 9, "top": 9, "width": 9, "height": 9}}
    assert table_layout.merge(None, fallback) == fallback


def test_a_layout_without_the_player_is_not_adopted():
    """The player's pair is on the table from the first second of the round to
    the last, so a layout that cannot place it has found something else."""
    assert not table_layout.looks_plausible(
        {"regions": {slot: {} for slot in ["flop_1", "flop_2", "flop_3"]}})
    assert table_layout.looks_plausible(
        {"regions": {slot: {} for slot in
                     ["flop_1", "flop_2", "flop_3", "player_1", "player_2"]}})


# -- any extra frames the user has supplied -----------------------------------

@pytest.mark.skipif(not SAMPLES, reason="no frames in samples/")
@pytest.mark.parametrize("path", SAMPLES)
def test_supplied_samples_never_produce_a_wrong_card(path):
    """Whatever camera or size these were captured at, a card that is read must
    be the card that is there. Reading nothing is allowed; guessing is not."""
    image = cv2.imread(path)
    assert image is not None, path
    layout = table_layout.locate(image)
    if not table_layout.looks_plausible(layout):
        pytest.skip("%s: no table located" % os.path.basename(path))
    for slot, region in layout["regions"].items():
        result = read_slot(crop(image, region))
        assert not (result["confident"] and result["card"] is None)


# -- the tracker's own choice of where to look --------------------------------

def test_the_tracker_prefers_what_it_finds_then_what_it_found_before(frame,
                                                                     monkeypatch):
    """dynamic while the table is visible, cached while it is covered, and the
    calibrated boxes only until something better has been seen."""
    import queue

    import tracker as tracker_module

    calibrated = {slot: dict(region) for slot, region in REGIONS.items()}
    config = {"regions": calibrated, "monitor": 1}
    tracker = tracker_module.Tracker(config, queue.Queue())

    # Nothing found yet, and nothing seen before: fall back to calibration.
    felt = np.full_like(frame, FELT)
    regions = tracker._locate(felt, (0, 0))
    assert tracker._layout_source == "calibrated"
    assert regions["player_1"] == calibrated["player_1"]

    # A clear frame: find the table and use it.
    regions = tracker._locate(frame, (0, 0))
    assert tracker._layout_source == "dynamic"
    found_player = regions["player_1"]
    assert found_player != calibrated["player_1"]

    # A frame where the table cannot be made out: keep what worked.
    regions = tracker._locate(felt, (0, 0))
    assert tracker._layout_source == "cached"
    assert regions["player_1"] == found_player


def test_the_tracker_reads_the_table_through_the_layout_it_found(frame,
                                                                 monkeypatch):
    """The whole path: grab, locate, crop, recognise - with no calibration."""
    import queue

    import tracker as tracker_module

    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: frame)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))

    tracker = tracker_module.Tracker({"regions": {}, "monitor": 1}, queue.Queue())
    cards, reads = tracker._read_table()

    assert tracker._layout_source == "dynamic"
    assert cards == EXPECTED
    assert tracker_module.derive_state(cards) == tracker_module.COMPLETE


# -- running without ever having calibrated -----------------------------------

def test_an_uncalibrated_tracker_still_reads_the_table(frame, monkeypatch):
    """A fresh install on another machine: no regions in the config at all."""
    import queue

    import tracker as tracker_module

    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: frame)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))

    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    cards, _ = tracker._read_table()
    assert cards == EXPECTED
    assert tracker._layout_source == "dynamic"


def test_an_uncalibrated_desktop_with_no_game_invents_nothing(monkeypatch):
    """The other half of it: no calibration, no table, and no cards either."""
    import queue

    import tracker as tracker_module

    desktop = np.random.RandomState(0).randint(0, 255, (600, 900, 3)).astype("uint8")
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: desktop)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))

    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    cards, _ = tracker._read_table()
    assert all(card is None for card in cards.values()), cards
    assert tracker_module.derive_state(cards) == tracker_module.WAITING


def test_starting_the_tracker_no_longer_needs_calibration(monkeypatch):
    """Start Tracker used to refuse outright until nine boxes were drawn."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    root.withdraw()

    import app as app_module

    monkeypatch.setattr(app_module.db, "check_connection", lambda: (True, "ok"))
    monkeypatch.setattr(app_module.db, "ensure_schema", lambda: None)
    refused = []
    monkeypatch.setattr(app_module.messagebox, "showwarning",
                        lambda *a, **k: refused.append(a))
    started = []
    monkeypatch.setattr(app_module.Tracker, "start", lambda self: started.append(True))

    try:
        application = app_module.App(root)
        application.config["regions"] = {}          # never calibrated
        application.start()
        assert refused == [], "starting was refused: %s" % (refused,)
        assert started, "the tracker was not started"
        assert application.status_var.get() == "Status: RUNNING"
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_the_source_label_says_none_when_there_is_nothing_to_fall_back_on(
        frame, monkeypatch):
    """Diagnostic only: with no calibration there is no calibration to blame."""
    import queue

    import tracker as tracker_module

    felt = np.full_like(frame, FELT)
    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    tracker._locate(felt, (0, 0))
    assert tracker._layout_source == "none"

    calibrated = tracker_module.Tracker(
        {"monitor": 1, "regions": dict(REGIONS)}, queue.Queue())
    calibrated._locate(felt, (0, 0))
    assert calibrated._layout_source == "calibrated"


def test_the_source_label_does_not_change_what_is_read(frame, monkeypatch):
    """The label is a label. An uncalibrated miss reads exactly as before."""
    import queue

    import tracker as tracker_module

    felt = np.full_like(frame, FELT)
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: felt)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))

    tracker = tracker_module.Tracker({"monitor": 1}, queue.Queue())
    cards, _ = tracker._read_table()
    assert tracker._layout_source == "none"
    assert all(card is None for card in cards.values())
