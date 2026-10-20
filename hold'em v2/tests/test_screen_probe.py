"""Screen-change measurement (B2): diagnostic only.

Whether "the game freezes" or only the tracker's view of it does cannot be told from the log today: nothing records whether the
captured picture is changing. Each poll now compares a shrunken copy of the frame with the last one - leaving out the tracker's own
windows, so its own redrawing does not look like the game moving - and the timing summary carries `screen_diff` (whole picture),
`table_diff` (around the known card boxes) and `still` (polls in a row with no change). A long stand-still is logged once with what
the focus gate and the layout were doing. None of it feeds back into what the tracker reads or decides.
"""

import glob
import logging
import os
import queue

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module                         # noqa: E402
from tracker import Tracker                              # noqa: E402

H, W = 270, 480                                          # small frames keep the many polls here quick


@pytest.fixture
def run(monkeypatch):
    shown = {"frame": None}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["frame"])
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    made = Tracker({"monitor": 1, "regions": {}, "game_window_title": ""}, queue.Queue())
    made._own_rects = lambda: []
    made.focus = type("Focus", (), {"holding_own_window": False, "state": "GAME_ACTIVE"})()

    def look(frame):
        shown["frame"] = frame
        made._timing = {}
        made._read_table()
        return dict(made._timing)

    made.look = look
    return made


def plain(value=90):
    return np.full((H, W, 3), value, np.uint8)


def test_identical_pictures_count_as_still_and_a_change_ends_it(run):
    assert "screen_diff" not in run.look(plain())                      # nothing to compare with yet
    for expected in (1, 2, 3):
        timing = run.look(plain())
        assert timing["screen_diff"] == pytest.approx(0.0) and timing["still"] == expected
    moving = plain()
    moving[50:150, 100:300] = 250
    timing = run.look(moving)
    assert timing["screen_diff"] > 1.0 and timing["still"] == 0


def test_a_change_only_inside_a_tracker_window_does_not_count_as_the_game_moving(run):
    run.look(plain())
    run._own_rects = lambda: [(100, 50, 300, 150)]
    run.look(plain())
    changed = plain()
    changed[60:140, 110:290] = 255                                      # the tracker's own redrawing
    timing = run.look(changed)
    assert timing["screen_diff"] == pytest.approx(0.0) and timing["still"] >= 1


def test_the_table_measure_looks_only_around_the_known_boxes(run):
    run._layout = {"regions": {"flop_1": {"left": 200, "top": 100, "width": 60, "height": 80}}}
    run.look(plain())
    away = plain()
    away[200:260, 20:100] = 255                                         # change well away from the cards
    timing = run.look(away)
    assert timing["screen_diff"] > 0.0 and timing["table_diff"] == pytest.approx(0.0)
    near = away.copy()
    near[110:170, 210:250] = 255
    timing = run.look(near)
    assert timing["table_diff"] > 1.0


def test_a_long_stand_still_is_reported_once_with_the_context(run, monkeypatch, caplog):
    monkeypatch.setattr(tracker_module, "SCREEN_STILL_REPORT_POLLS", 3)
    caplog.set_level(logging.INFO)
    run.look(plain())
    for _ in range(8):
        run.look(plain())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("SCREEN_STILL")]
    assert len(lines) == 1
    assert "3 polls" in lines[0] and "focus=GAME_ACTIVE" in lines[0] and "layout=" in lines[0]
    for _ in range(100):
        run.look(plain())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("SCREEN_STILL")]
    assert len(lines) == 2                                              # reported again only 100 polls later
    moving = plain()
    moving[:, :100] = 200
    run.look(moving)
    for _ in range(4):
        run.look(plain(5))
        run.look(plain(5))
    assert len([r for r in caplog.records if r.getMessage().startswith("SCREEN_STILL")]) >= 3   # a new stand-still is reported again


def test_the_measurements_reach_the_tick_timing_line(run, caplog):
    caplog.set_level(logging.INFO)
    run._tick_summary.every = 2
    run._timing.update(capture=10.0, roi=10.0, recognition=1.0, panel=1.0, fresh=set(), screen_diff=2.5, table_diff=0.5, still=4)
    run._record_tick(1.0, 1.2)
    run._record_tick(1.3, 1.5)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("TICK_TIMING")][0]
    assert "screen_diff 2/2/2" in line and "table_diff 0/0/0" in line and "still 4/4/4" in line


def test_a_failure_to_measure_changes_nothing(run, monkeypatch):
    monkeypatch.setattr(tracker_module, "SCREEN_PROBE_SIZE", (0, 0))     # makes only the measurement's own resize fail
    timing = run.look(plain())                                         # must not raise
    assert "screen_diff" not in timing and "capture" in timing


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))


@pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project folder")
def test_measuring_changes_no_reading_and_no_decision(monkeypatch):
    """Two identical runs over the same pictures, one with the measurement and one with it removed, read the same table."""
    table = cv2.imread(SCREENSHOTS[0])

    def run_once(measure):
        shown = {"frame": None}
        monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["frame"])
        monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
        monkeypatch.setattr(Tracker, "_store", lambda self, cards, reads=None: None)
        made = Tracker({"monitor": 1, "regions": {}, "game_window_title": "", "clear_frames": 3}, queue.Queue())
        made._own_rects = lambda: []
        made.focus = type("Focus", (), {"holding_own_window": False, "state": "GAME_ACTIVE"})()
        if not measure:
            made._probe_screen = lambda *a, **k: None
        outcomes = []
        for frame in (table, table, np.full_like(table, 60), table):
            shown["frame"] = frame
            made._tick()
            update = None
            while not made.events.empty():
                kind, payload = made.events.get_nowait()
                if kind == "update":
                    update = payload
            outcomes.append((update["cards"], update["scenario"]["decision"], update["state"], update["round_id"]))
        return outcomes

    assert run_once(True) == run_once(False)
