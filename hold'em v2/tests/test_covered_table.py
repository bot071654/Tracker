"""A tracker window lying over the table must not look like a table that has gone.

While one of the tracker's own windows (Teach / Correct Scenario, a rule editor) is in front and the gate is holding its state
through it, bright non-card content over the remembered card boxes is most likely that window. The tracker then reads nothing
and feeds the card memory nothing for that poll, so neither the window's pixels nor a repeat of old readings becomes fresh
data and the hand is not cleared by the overlay. Once the hold ends, or the window is not the cause, the boxes are judged as
before: non-card content stops them being read and the memory clears normally. A table that comes back is found again.
"""

import glob
import os
import queue

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module  # noqa: E402
from tracker import Tracker  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))
pytestmark = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project folder")

FELT = (60, 95, 45)


class Focus:
    """Stands in for the gate: only what the tracker asks of it."""
    holding_own_window = False


@pytest.fixture(scope="module")
def table():
    return cv2.imread(SCREENSHOTS[0])


@pytest.fixture
def overlay(table):
    """What the monitor shows with a tracker window over the table: bright, with text, no cards."""
    page = np.full_like(table, 255)
    cv2.putText(page, "Teach / Correct Scenario", (400, 300), cv2.FONT_HERSHEY_SIMPLEX, 2, (30, 30, 30), 4)
    return page


@pytest.fixture
def run(monkeypatch):
    """A tracker whose screen is whatever the test says, and a way to take one poll and see what it sent."""
    shown = {"frame": None}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["frame"])
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(Tracker, "_store", lambda self, cards, reads=None: None)
    made = Tracker({"monitor": 1, "regions": {}, "clear_frames": 3, "game_window_title": ""}, queue.Queue())
    made.focus = Focus()

    def poll(frame):
        shown["frame"] = frame
        made._tick()
        sent = []
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                sent.append(payload)
        return sent

    made.poll = poll
    return made


def settle(tracker, table, polls=3):
    for _ in range(polls):
        tracker.poll(table)
    assert len(tracker.memory.cards) == 9 and tracker._layout_source == "dynamic"


def test_a_tracker_window_over_the_table_for_a_long_time_does_not_clear_the_hand(run, table, overlay):
    settle(run, table)
    before = dict(run.memory.cards)
    generation = run.memory.generation
    run.focus.holding_own_window = True
    sent_after_the_limit = 0
    for poll in range(30):
        sent = run.poll(overlay)
        if poll >= run._layout_max_misses():
            sent_after_the_limit += len(sent)
        assert run.memory.cards == before, "poll %d changed the hand" % poll
        assert run.memory.generation == generation
    assert run._layout_source == "covered"
    assert sent_after_the_limit == 0                    # nothing is reported from covered pixels
    assert run._layout is not None and run._layout_stale is False


def test_covered_pixels_are_not_read_or_cached(run, table, overlay):
    settle(run, table)
    run.focus.holding_own_window = True
    for _ in range(run._layout_max_misses() + 1):
        run.poll(overlay)
    reads_before = {slot: dict(read) for slot, read in run._last_read.items()}
    crops_before = {slot: crop.copy() for slot, crop in run._last_crop.items() if crop is not None}
    run.poll(overlay)
    assert run._layout_source == "covered"
    assert {slot: dict(read) for slot, read in run._last_read.items()} == reads_before
    assert all(np.array_equal(run._last_crop[slot], crop) for slot, crop in crops_before.items())


def test_normal_validation_resumes_when_the_hold_ends(run, table, overlay):
    settle(run, table)
    run.focus.holding_own_window = True
    for _ in range(25):
        run.poll(overlay)
    assert run._layout_source == "covered" and len(run.memory.cards) == 9
    run.focus.holding_own_window = False                # the hold is over (or the window was never the cause)
    run.poll(overlay)
    assert run._layout_source == "lost" and run._layout_stale is True
    for _ in range(run.memory.clear_frames + 1):
        run.poll(overlay)
    assert run.memory.cards == {}, "the stale hand was never cleared"


def test_a_table_that_returns_is_found_again_after_the_hold_and_after_a_loss(run, table, overlay):
    settle(run, table)
    run.focus.holding_own_window = True
    for _ in range(25):
        run.poll(overlay)
    run.focus.holding_own_window = False
    for _ in range(run.memory.clear_frames + 2):
        run.poll(overlay)
    assert run.memory.cards == {}
    run.poll(table)
    assert run._layout_source == "dynamic" and run._layout_stale is False and len(run.memory.cards) == 9


def test_the_table_returning_during_the_hold_is_used_at_once(run, table, overlay):
    settle(run, table)
    run.focus.holding_own_window = True
    for _ in range(15):
        run.poll(overlay)
    assert run._layout_source == "covered"
    sent = run.poll(table)                              # the window is moved off the table
    assert run._layout_source == "dynamic" and run._covered is False
    assert sent and len(run.memory.cards) == 9


def test_an_empty_table_during_the_hold_is_still_seen_as_empty(run, table):
    """Teach open between rounds: the felt is empty, not covered, so the next deal must still be read."""
    settle(run, table)
    felt = np.full_like(table, FELT)
    run.focus.holding_own_window = True
    for _ in range(25):
        run.poll(felt)
    assert run._layout_source == "cached" and run._covered is False


def test_without_the_hold_a_changed_screen_is_never_called_covered(run, table, overlay):
    settle(run, table)
    for _ in range(25):
        run.poll(overlay)
    assert run._layout_source == "lost" and run._covered is False


# -- covered from the first missed poll while the hold is active -----------------------------------------------

def deal_only(table):
    """Only the player's two cards on the felt: the first thing a new deal shows."""
    import test_table_layout as layout_tests
    return layout_tests.blank(table, [slot for slot in layout_tests.REGIONS if not slot.startswith("player")])


def test_a_covered_overlay_is_detected_on_the_first_missed_poll(run, table, overlay):
    settle(run, table)
    run.focus.holding_own_window = True
    assert run._layout_max_misses() > 1                 # the wait that used to apply
    sent = run.poll(overlay)
    assert run._layout_misses == 1
    assert run._layout_source == "covered" and run._covered is True
    assert sent == []


def test_nothing_is_read_voted_cached_or_sent_from_the_first_covered_poll(run, table, overlay, monkeypatch):
    settle(run, table)
    before = dict(run.memory.cards), run.memory.generation, dict(run.memory.votes)
    reads_before = {slot: dict(read) for slot, read in run._last_read.items()}
    crops_before = {slot: crop.copy() for slot, crop in run._last_crop.items() if crop is not None}
    calls = []
    original_changed, original_update = Tracker._read_changed, run.memory.update
    monkeypatch.setattr(Tracker, "_read_changed", lambda self, images: calls.append("read") or original_changed(self, images))
    monkeypatch.setattr(run.memory, "update", lambda *a, **k: calls.append("memory") or original_update(*a, **k))

    run.focus.holding_own_window = True
    for poll in range(12):                              # inside the old five-poll wait and well past it
        assert run.poll(overlay) == []
        assert run._layout_source == "covered", poll
    assert calls == []                                   # the data path was never entered
    assert (dict(run.memory.cards), run.memory.generation, dict(run.memory.votes)) == before
    assert {slot: dict(read) for slot, read in run._last_read.items()} == reads_before
    assert all(np.array_equal(run._last_crop[slot], crop) for slot, crop in crops_before.items())


def test_empty_felt_in_the_hold_still_lets_the_next_deal_be_read(run, table):
    settle(run, table)
    felt = np.full_like(table, FELT)
    run.focus.holding_own_window = True
    for _ in range(25):
        assert run.poll(felt) is not None
        assert run._covered is False
    sent = run.poll(deal_only(table))
    assert run._covered is False and run._layout_source == "cached"
    assert sent and sent[-1]["seen"]["player_1"] and sent[-1]["seen"]["player_2"]


def test_the_first_missed_polls_are_read_normally_without_the_hold(run, table, overlay):
    """A hand sweeping across costs a few polls; with no tracker window in front they are read through as before."""
    settle(run, table)
    for poll in range(run._layout_max_misses()):
        run.poll(overlay)
        assert run._layout_source == "cached" and run._covered is False, poll
    run.poll(overlay)
    assert run._layout_source == "lost"


def test_a_hold_that_ends_early_hands_back_to_the_normal_wait_and_then_normal_expiry(run, table, overlay):
    settle(run, table)
    run.focus.holding_own_window = True
    for _ in range(2):
        run.poll(overlay)
    assert run._layout_source == "covered"
    run.focus.holding_own_window = False                # the hold is over after two polls
    seen = []
    for _ in range(run._layout_max_misses() + 1):
        run.poll(overlay)
        seen.append(run._layout_source)
    assert seen[0] == "cached" and seen[-1] == "lost"
    assert "covered" not in seen
    for _ in range(run.memory.clear_frames + 1):
        run.poll(overlay)
    assert run.memory.cards == {}


def test_a_stale_layout_is_not_forgotten_by_a_hold_that_starts_after_it(run, table, overlay):
    settle(run, table)
    for _ in range(run._layout_max_misses() + 1):
        run.poll(overlay)
    assert run._layout_stale is True and run._layout_source == "lost"
    run.focus.holding_own_window = True
    run.poll(overlay)
    assert run._layout_source == "covered" and run._layout_stale is True
    run.focus.holding_own_window = False
    run.poll(overlay)
    assert run._layout_source == "lost" and run._layout_stale is True
