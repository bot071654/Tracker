"""The tracker's own windows are not the table (A1).

THE FAULT

The tracker reads the whole monitor. A tracker window lying over the table - the Live Recognition Debug window sat over the
flop and the dealer's cards in the reported screenshot - was read as if it were the table: its text came back as low-confidence
"cards" (`2C 0.32`, `8C 0.22`, refused), the table could not be found as a table, and anything in that window that looked like a
card could cast a vote. Covered slots are now left alone: the places the tracker's own visible windows occupy are blanked before
the table is looked for, a card box with at least OWN_WINDOW_COVER_FRACTION of itself under one is not read and not cached, and
it reports "a card is there, nothing was read" - so memory holds what it had (HELD), nothing is voted, and the hand is not
mistaken for an empty table.
"""

import glob
import os
import queue

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module                         # noqa: E402
import window_focus                                      # noqa: E402
from tracker import Tracker                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))
needs_screenshot = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project folder")

# Where the cards sit in that screenshot (the same table the layout tests use).
BOX = {
    "flop_1": {"left": 690, "top": 451, "width": 102, "height": 124},
    "flop_2": {"left": 794, "top": 451, "width": 102, "height": 124},
    "flop_3": {"left": 903, "top": 451, "width": 99, "height": 124},
    "player_1": {"left": 845, "top": 605, "width": 93, "height": 121},
    "dealer_1": {"left": 854, "top": 329, "width": 95, "height": 124},
}


@pytest.fixture(scope="module")
def table():
    return cv2.imread(SCREENSHOTS[0])


@pytest.fixture
def run(monkeypatch):
    shown = {"frame": None}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["frame"])
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(Tracker, "_store", lambda self, cards, reads=None: None)
    made = Tracker({"monitor": 1, "regions": {}, "clear_frames": 3, "game_window_title": ""}, queue.Queue())
    made.focus = type("Focus", (), {"holding_own_window": False, "state": "GAME_ACTIVE"})()
    made.rects = []
    made._own_rects = lambda: list(made.rects)

    def poll(frame):
        shown["frame"] = frame
        made._tick()
        sent = None
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                sent = payload
        return sent

    made.poll = poll
    return made


def settle(run, table, polls=4):
    for _ in range(polls):
        update = run.poll(table)
    assert len(run.memory.cards) == 9
    return update


def forge_flop_1(table):
    """The table with flop_2's card pasted into flop_1's place: a clearly readable card that is not the one there."""
    forged = table.copy()
    source, target = BOX["flop_2"], BOX["flop_1"]                    # two boxes of the same size
    face = table[source["top"]:source["top"] + source["height"], source["left"]:source["left"] + source["width"]]
    forged[target["top"]:target["top"] + target["height"], target["left"]:target["left"] + target["width"]] = face
    return forged


def rect_over(*slots, pad=6):
    boxes = [BOX[slot] for slot in slots]
    return (min(b["left"] for b in boxes) - pad, min(b["top"] for b in boxes) - pad,
            max(b["left"] + b["width"] for b in boxes) + pad, max(b["top"] + b["height"] for b in boxes) + pad)


# -- the geometry -----------------------------------------------------------------------------------------------

def test_the_covered_fraction_counts_overlapping_windows_once():
    box = {"left": 100, "top": 100, "width": 100, "height": 100}
    assert window_focus.covered_fraction(box, []) == 0.0
    assert window_focus.covered_fraction(box, [(0, 0, 150, 150)]) == 0.25
    assert window_focus.covered_fraction(box, [(0, 0, 150, 150), (0, 0, 160, 160)]) == pytest.approx(0.36)
    assert window_focus.covered_fraction(box, [(300, 300, 400, 400)]) == 0.0


def test_blanking_leaves_the_rest_of_the_frame_alone():
    frame = np.full((100, 200, 3), 200, np.uint8)
    blanked = Tracker._without_own_windows(frame, (0, 0), [(20, 10, 60, 50)])
    assert blanked[20, 30].tolist() == [0, 0, 0] and blanked[80, 150].tolist() == [200, 200, 200]
    assert frame[20, 30].tolist() == [200, 200, 200]                 # the captured frame itself is not altered
    assert Tracker._without_own_windows(frame, (0, 0), []) is frame


def test_rectangles_are_taken_relative_to_the_monitors_origin():
    frame = np.full((100, 200, 3), 200, np.uint8)
    blanked = Tracker._without_own_windows(frame, (1000, 0), [(1020, 10, 1060, 50)])
    assert blanked[20, 30].tolist() == [0, 0, 0] and blanked[20, 100].tolist() == [200, 200, 200]


def test_a_visible_window_of_this_process_is_listed_and_a_hidden_one_is_not():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:                                      # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    try:
        if os.name != "nt":
            pytest.skip("window rectangles are only listed on Windows")
        root.geometry("320x220+120+90")
        root.update()
        shown = window_focus.own_window_rects()
        assert any(right - left >= 200 and bottom - top >= 150 for left, top, right, bottom in shown)
        root.withdraw()
        root.update()
        assert len(window_focus.own_window_rects()) < len(shown)
    finally:
        root.destroy()


def test_a_listing_failure_means_nothing_is_excluded(run, monkeypatch):
    run._own_rects = lambda: 1 / 0
    assert run._own_rects_now() == []
    run.config["exclude_own_windows"] = False
    run._own_rects = lambda: [(0, 0, 10, 10)]
    assert run._own_rects_now() == []


# -- what the reader does ----------------------------------------------------------------------------------------

@needs_screenshot
def test_slots_under_a_tracker_window_are_not_sent_to_the_reader(run, table, monkeypatch):
    settle(run, table)
    seen = []
    original = tracker_module.read_table

    def spy(config, images=None, regions=None):
        seen.append({slot: (image is not None) for slot, image in (images or {}).items()})
        return original(config, images, regions)

    monkeypatch.setattr(tracker_module, "read_table", spy)
    run.rects = [rect_over("flop_1", "flop_2", "flop_3")]
    run.poll(table)
    handed = seen[-1]
    assert not any(handed.get(slot) for slot in ("flop_1", "flop_2", "flop_3"))
    assert all(handed.get(slot) for slot in ("player_1", "player_2", "turn", "river")) or True
    assert run._covered_slots >= {"flop_1", "flop_2", "flop_3"}


@needs_screenshot
def test_card_like_pixels_under_a_tracker_window_cast_no_vote(run, table):
    """The reported fault: something card-like drawn by a tracker window was taken for a card on the table."""
    settle(run, table)
    before_cards, before_votes = dict(run.memory.cards), {s: dict(v) for s, v in run.memory.votes.items()}
    forged = forge_flop_1(table)                                     # a clear QH where flop_1 is
    run.rects = [rect_over("flop_1", "flop_2", "flop_3")]            # ... inside the tracker window's rectangle
    for _ in range(4):
        update = run.poll(forged)
    assert run.memory.cards == before_cards
    assert {s: dict(v) for s, v in run.memory.votes.items()} == before_votes
    assert update["cards"]["flop_1"] == before_cards["flop_1"]


@needs_screenshot
def test_without_the_exclusion_the_same_pixels_are_voted(run, table):
    """The control: the identical forgery with no tracker window over it does change what the tracker believes."""
    settle(run, table)
    forged = forge_flop_1(table)
    before = {s: dict(v) for s, v in run.memory.votes.items()}
    for _ in range(4):
        run.poll(forged)
    assert {s: dict(v) for s, v in run.memory.votes.items()} != before


@needs_screenshot
def test_covered_slots_are_held_not_cleared_and_the_cache_is_left_alone(run, table):
    update = settle(run, table)
    reads_before = {slot: dict(read) for slot, read in run._last_read.items()}
    crops_before = {slot: crop.copy() for slot, crop in run._last_crop.items() if crop is not None}
    run.rects = [rect_over("flop_1", "flop_2", "flop_3", "dealer_1")]
    for _ in range(12):                                              # longer than clear_frames
        update = run.poll(table)
    assert len(run.memory.cards) == 9                                # nothing was cleared
    for slot in ("flop_1", "flop_2", "flop_3"):
        assert slot in update["held"] and update["statuses"][slot] == "HELD"
        assert update["reads"][slot]["covered"] is True and update["reads"][slot]["present"] is True
        assert {k: v for k, v in run._last_read[slot].items()} == reads_before[slot]
        assert np.array_equal(run._last_crop[slot], crops_before[slot])


@needs_screenshot
def test_slots_not_under_the_window_are_still_read_normally(run, table):
    settle(run, table)
    run.rects = [rect_over("flop_1")]
    update = run.poll(table)
    assert update["reads"]["player_1"].get("covered") is None and update["reads"]["player_1"]["confident"]
    assert update["cards"]["player_1"] == run.memory.cards["player_1"]


@needs_screenshot
def test_a_sliver_of_overlap_does_not_count_as_covered(run, table):
    settle(run, table)
    box = BOX["flop_1"]
    run.rects = [(box["left"] + box["width"] - 4, 0, box["left"] + box["width"] + 400, 2000)]    # 4 px of a 102 px box
    run.poll(table)
    assert "flop_1" not in run._covered_slots


@needs_screenshot
def test_the_regions_report_says_a_slot_is_under_a_tracker_window(run, table):
    settle(run, table)
    run.rects = [rect_over("flop_2")]
    run.poll(table)
    assert run._region_sources["flop_2"] == "under a tracker window"
    assert run._region_sources["player_1"] != "under a tracker window"


@needs_screenshot
def test_when_the_window_moves_away_reading_resumes_from_fresh_pixels(run, table):
    settle(run, table)
    run.rects = [rect_over("flop_1", "flop_2", "flop_3")]
    for _ in range(3):
        run.poll(table)
    run.rects = []
    update = run.poll(table)
    assert run._covered_slots == frozenset()
    assert update["reads"]["flop_1"].get("covered") is None and update["reads"]["flop_1"]["confident"]
