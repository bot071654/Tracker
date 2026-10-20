"""Dealer boxes remembered from an earlier layout (A2).

THE FAULT

Both dealer slots said "no box" (EMPTY, 0.0 ms) while the dealer's cards were on the table. A layout is made only of the cards
the detector can see, and the dealer's cards are only seen while face up, so a layout formed earlier in a hand has no dealer
boxes; and once the table could not be found again (something over it) the last layout, dealer-less, was all there was. The
installed build has no calibrated boxes to fall back on, so a dealer slot with no box was simply never read.

Dealer boxes seen in one layout are now carried into later layouts - but only while the table is PROVEN not to have moved: at
least DEALER_CARRY_MIN_ANCHORS board / player boxes present in both layouts, every one within a few pixels. Movement, a resize,
too few boxes to tell, a lost layout or a pause all forget them. A carried box decides nothing: it is read like any other, and a
face-down card or bare felt there reads as nothing.
"""

import glob
import os
import queue

import pytest

import tracker as tracker_module
from tracker import Tracker

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def box(left, top, width=100, height=124):
    return {"left": left, "top": top, "width": width, "height": height}


BOARD = {"flop_1": box(687, 441), "flop_2": box(800, 437), "flop_3": box(906, 439),
         "turn": box(1014, 439), "river": box(1122, 437), "player_1": box(854, 577), "player_2": box(966, 582)}
DEALERS = {"dealer_1": box(866, 303, 98, 122), "dealer_2": box(962, 303, 98, 122)}


def layout(with_dealers=True, shift=0, drop=(), dealers=None):
    regions = {slot: box(b["left"] + shift, b["top"] + shift, b["width"], b["height"]) for slot, b in BOARD.items()
               if slot not in drop}
    if with_dealers:
        regions.update(dealers or {slot: dict(b) for slot, b in DEALERS.items()})
    return {"regions": regions, "card_height": 124, "card_size": (100, 124), "found": len(regions)}


@pytest.fixture
def tracker():
    made = Tracker({"monitor": 1, "regions": {}, "game_window_title": ""}, queue.Queue())
    made._own_rects = lambda: []
    return made


def feed(tracker, found):
    """Run one freshly found layout through the same step _locate uses, and keep it as the current layout."""
    result = tracker._with_dealer_boxes(found)
    tracker._layout = result
    return result


# -- carrying ------------------------------------------------------------------------------------------------------

def test_dealer_boxes_are_carried_into_a_later_layout_that_has_none(tracker):
    feed(tracker, layout(with_dealers=True))
    later = feed(tracker, layout(with_dealers=False))
    assert later["regions"]["dealer_1"] == DEALERS["dealer_1"] and later["regions"]["dealer_2"] == DEALERS["dealer_2"]
    assert later["carried"] == ["dealer_1", "dealer_2"]


def test_a_layout_with_no_dealer_boxes_before_any_were_seen_is_unchanged(tracker):
    found = layout(with_dealers=False)
    assert feed(tracker, found) is found and "carried" not in found


def test_a_fresh_dealer_box_replaces_the_remembered_one_and_the_missing_one_is_carried(tracker):
    feed(tracker, layout(with_dealers=True))
    moved = {"dealer_1": box(870, 305, 96, 120)}
    later = feed(tracker, layout(with_dealers=True, dealers=moved))
    assert later["regions"]["dealer_1"] == moved["dealer_1"]                      # what was seen now wins
    assert later["regions"]["dealer_2"] == DEALERS["dealer_2"] and later["carried"] == ["dealer_2"]


def test_small_jitter_in_the_board_boxes_still_proves_the_table_has_not_moved(tracker):
    feed(tracker, layout(with_dealers=True))
    jittered = layout(with_dealers=False)
    jittered["regions"]["flop_1"] = box(689, 443, 103, 122)
    assert "carried" in feed(tracker, jittered)


# -- forgetting ----------------------------------------------------------------------------------------------------

def test_a_table_that_moved_does_not_get_the_old_dealer_boxes(tracker):
    feed(tracker, layout(with_dealers=True))
    later = feed(tracker, layout(with_dealers=False, shift=30))
    assert "dealer_1" not in later["regions"] and "carried" not in later


def test_one_moved_box_among_many_is_enough_to_forget(tracker):
    feed(tracker, layout(with_dealers=True))
    odd = layout(with_dealers=False)
    odd["regions"]["river"] = box(1122 + 40, 437)
    assert "dealer_1" not in feed(tracker, odd)["regions"]
    assert tracker._dealer_memory is None


def test_too_few_matching_boxes_means_uncertain_and_forgets(tracker):
    feed(tracker, layout(with_dealers=True))
    few = layout(with_dealers=False, drop=("flop_1", "flop_2", "flop_3", "turn"))      # 3 boxes left
    assert "dealer_1" not in feed(tracker, few)["regions"]
    assert tracker._dealer_memory is None


def test_once_forgotten_a_later_matching_layout_does_not_bring_them_back(tracker):
    feed(tracker, layout(with_dealers=True))
    feed(tracker, layout(with_dealers=False, shift=30))
    assert "dealer_1" not in feed(tracker, layout(with_dealers=False))["regions"]


def test_a_pause_forgets_them_even_from_the_layout_being_kept(tracker):
    tracker._layout = feed(tracker, layout(with_dealers=True))
    feed(tracker, layout(with_dealers=False))
    assert "dealer_1" in tracker._layout["regions"]
    tracker._expire_layout()
    assert "dealer_1" not in tracker._layout["regions"] and tracker._dealer_memory is None


def test_a_lost_layout_forgets_them(tracker):
    feed(tracker, layout(with_dealers=True))
    kept = feed(tracker, layout(with_dealers=False))
    assert "dealer_1" in kept["regions"]
    tracker._forget_dealer_boxes("the table has gone")
    assert "dealer_1" not in tracker._layout["regions"]


# -- through the real locate ---------------------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))
needs_screenshot = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project folder")
FELT = (60, 95, 45)


def bare(frame, *slots):
    import test_table_layout as layouts
    return layouts.blank(frame, slots)


@pytest.fixture
def run(monkeypatch):
    shown = {"frame": None}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["frame"])
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(Tracker, "_store", lambda self, cards, reads=None: None)
    made = Tracker({"monitor": 1, "regions": {}, "clear_frames": 3, "game_window_title": ""}, queue.Queue())
    made._own_rects = lambda: []
    made.focus = type("Focus", (), {"holding_own_window": False, "state": "GAME_ACTIVE"})()

    def poll(frame):
        shown["frame"] = frame
        made._tick()
        last = None
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                last = payload
        return last

    made.poll = poll
    return made


@needs_screenshot
def test_the_reported_case_dealer_slots_have_boxes_when_the_layout_was_formed_without_them(run):
    table = cv2.imread(SCREENSHOTS[0])
    for _ in range(3):
        run.poll(table)                                                     # dealers face up: boxes seen
    assert "dealer_1" in run._last_regions and run._region_sources["dealer_1"] == "this frame"
    for _ in range(3):
        run.poll(bare(table, "dealer_1", "dealer_2"))                       # a layout formed with no dealer cards
    assert "dealer_1" in run._last_regions and "dealer_2" in run._last_regions
    assert run._region_sources["dealer_1"] == "carried from an earlier layout"


@needs_screenshot
def test_a_carried_box_over_bare_felt_reads_as_nothing_and_never_as_a_card(run):
    table = cv2.imread(SCREENSHOTS[0])
    for _ in range(3):
        run.poll(table)
    update = None
    for _ in range(4):
        update = run.poll(bare(table, "dealer_1", "dealer_2"))
    # What was read this poll: nothing. (update["cards"] is the card memory's held answer for the slot, not a reading.)
    assert update["seen"]["dealer_1"] is None and update["seen"]["dealer_2"] is None
    assert not update["reads"]["dealer_1"]["present"] and not update["reads"]["dealer_1"]["confident"]
    assert not update["reads"]["dealer_2"]["present"] and not update["reads"]["dealer_2"]["confident"]


@needs_screenshot
def test_a_table_moved_on_screen_is_not_given_the_old_dealer_boxes(run):
    table = cv2.imread(SCREENSHOTS[0])
    for _ in range(3):
        run.poll(table)
    moved = np.full_like(table, FELT)
    moved[40:, 60:] = bare(table, "dealer_1", "dealer_2")[:-40, :-60]       # the whole table 60 px right and 40 px down
    for _ in range(3):
        run.poll(moved)
    assert "dealer_1" not in run._last_regions and "dealer_2" not in run._last_regions


@needs_screenshot
def test_dealer_cards_turned_face_up_are_read_through_the_carried_boxes_when_the_layout_cannot_be_found(run):
    """Cached layout (the table could not be found again) still has dealer boxes, so the cards are read when they appear."""
    table = cv2.imread(SCREENSHOTS[0])
    for _ in range(3):
        run.poll(table)
    for _ in range(3):
        run.poll(bare(table, "dealer_1", "dealer_2"))                       # dealer-less layout; boxes carried
    update = run.poll(table)                                                # the dealer's cards turn face up
    assert update["cards"]["dealer_1"] == "8D" and update["cards"]["dealer_2"] == "QD"
