"""Per-tick timing instrumentation: where a poll's time goes, and how long an update waits before the window shows it.

Measurement only. These tests check what is recorded and that recording changes nothing about when or how the tracker reads.
"""

import logging
import queue

import pytest

from config.settings import DEFAULT_CONFIG
from tick_timing import TimingSummary
from tracker import Tracker


def make_tracker(**extra):
    config = dict(DEFAULT_CONFIG)
    config.update(extra)
    config["game_window_title"] = ""                # the focus gate off: every poll runs
    return Tracker(config, queue.Queue())


# -- the summary -------------------------------------------------------------------------------------------------

def test_a_summary_line_is_written_every_n_ticks(caplog):
    caplog.set_level(logging.INFO)
    summary = TimingSummary("T", every=4)
    for value in (10.0, 20.0, 30.0, 40.0):
        summary.add({"tick": value}, label="dynamic")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("T ")]
    assert len(lines) == 1
    assert "n=4" in lines[0] and "tick 30/40/40" in lines[0] and "dynamic=4" in lines[0]


def test_a_slow_tick_is_logged_at_once_with_every_stage(caplog):
    caplog.set_level(logging.INFO)
    summary = TimingSummary("T", every=100, slow_ms=500.0)
    summary.add({"tick": 100.0, "read": 5.0})
    summary.add({"tick": 900.0, "capture": 100.0, "read": 700.0}, label="cached")
    slow = [r.getMessage() for r in caplog.records if r.getMessage().startswith("T_SLOW")]
    assert len(slow) == 1
    assert "tick=900" in slow[0] and "read=700" in slow[0] and "cached" in slow[0]


def test_missing_stages_are_left_out_not_reported_as_zero(caplog):
    caplog.set_level(logging.INFO)
    summary = TimingSummary("T", every=2)
    summary.add({"tick": 10.0, "panel": None})
    summary.add({"tick": 20.0, "panel": None})
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("T ")][0]
    assert "panel" not in line


# -- the tracker ------------------------------------------------------------------------------------------------

def test_record_tick_reports_the_stages_the_tick_already_measured(caplog):
    caplog.set_level(logging.INFO)
    tracker = make_tracker(tick_timing_every=2, tick_slow_ms=10000)
    tracker._timing.update(capture=100.0, roi=90.0, recognition=300.0, panel=50.0, fresh={"player_1", "flop_1"})
    tracker._layout_source = "dynamic"
    tracker._record_tick(10.0, 10.5)                # first tick: no period yet
    tracker._record_tick(10.7, 11.2)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("TICK_TIMING")][0]
    for field in ("tick 500", "capture 100", "locate 90", "read 300", "panel 50", "fresh 2", "dynamic=2"):
        assert field in line, (field, line)
    assert "other 0" in line                                 # the stages here add up to more than the tick
    assert "period 700" in line and "wait 200" in line     # start to start 0.7 s, idle 0.2 s between 10.5 and 10.7


def test_a_pause_does_not_show_up_as_a_slow_poll_gap(caplog):
    caplog.set_level(logging.INFO)
    tracker = make_tracker(tick_timing_every=1)
    tracker._record_tick(10.0, 10.2)
    tracker._last_tick = None                        # what the paused branch of _run does
    tracker._record_tick(900.0, 900.2)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("TICK_TIMING")]
    assert all("period" not in line for line in lines)


def test_recording_never_raises_into_the_loop(monkeypatch):
    tracker = make_tracker()
    monkeypatch.setattr(tracker._tick_summary, "add", lambda *a, **k: 1 / 0)
    tracker._record_tick(1.0, 2.0)                   # must not raise


def test_the_loop_records_each_poll_and_not_the_paused_ones(caplog):
    caplog.set_level(logging.INFO)
    tracker = make_tracker(tick_timing_every=1, tick_slow_ms=10000, poll_interval_seconds=0)
    tracker._check_resolution = lambda: None
    tracker._tick = lambda: None
    left = {"n": 3}

    def wait(_timeout=None):
        left["n"] -= 1
        if left["n"] <= 0:
            tracker._stop.set()
        return False
    tracker._stop.wait = wait
    tracker._run()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("TICK_TIMING")]
    assert len(lines) == 3 and "period" not in lines[0] and "period" in lines[1]


def test_the_timing_lines_carry_no_card_or_account_data(caplog):
    caplog.set_level(logging.INFO)
    tracker = make_tracker(tick_timing_every=1)
    tracker._timing.update(capture=1.0, roi=1.0, recognition=1.0, panel=1.0, fresh={"player_1"})
    tracker._record_tick(1.0, 1.1)
    text = " ".join(r.getMessage() for r in caplog.records if "TICK_TIMING" in r.getMessage())
    assert "player_1" not in text and "token" not in text.lower() and "@" not in text


def test_the_update_carries_the_monotonic_stamp_the_window_measures_with(monkeypatch, tmp_path):
    """The window turns emitted_mono into the queue delay; both ends use the same clock."""
    import glob
    import os

    import cv2

    import tracker as tracker_module

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shots = sorted(glob.glob(os.path.join(root, "Screenshot*.png")))
    if not shots:
        pytest.skip("no casino screenshot in the project folder")
    frame = cv2.imread(shots[0])
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: frame)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    # dealer_debug stays on, but the crops it keeps go to this test's folder, never to the real debug/live.
    real_saver = tracker_module.dealer_watch.DealerSaver
    monkeypatch.setattr(tracker_module.dealer_watch, "DealerSaver", lambda: real_saver(folder=str(tmp_path)))
    tracker = make_tracker(regions={})
    tracker._tick()
    updates = []
    while not tracker.events.empty():
        kind, payload = tracker.events.get_nowait()
        if kind == "update":
            updates.append(payload)
    assert updates and isinstance(updates[-1]["emitted_mono"], float)
    assert tracker._timing["total"] > 0


# -- the window -----------------------------------------------------------------------------------------------

tk = pytest.importorskip("tkinter")


def test_the_window_measures_queue_delay(monkeypatch, caplog):
    import time

    import test_banner_lifecycle as lifecycle

    try:
        window = tk.Tk()
    except tk.TclError as exc:                      # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    try:
        import app as app_module
        from voice import engines
        monkeypatch.setattr(engines, "Pyttsx3Engine", lambda *_a, **_k: engines.NullEngine())
        monkeypatch.setattr(app_module.App, "_startup_checks", lambda self: None)
        monkeypatch.setattr(app_module.App, "_refresh_statistics", lambda self: None)
        monkeypatch.setattr(app_module.db, "check_connection", lambda *_a, **_k: (True, "stubbed"))
        monkeypatch.setattr(app_module.Tracker, "start", lambda self: None)
        monkeypatch.setattr(app_module.Tracker, "stop", lambda self: None)
        monkeypatch.setattr(app_module.Tracker, "is_running", lambda self: getattr(self, "_fake", False))
        made = app_module.App(window)
        caplog.set_level(logging.INFO)
        made._ui_summary = TimingSummary("UI_TIMING", every=2, slow_ms=500.0, slow_key="queue")
        made.start()
        made.tracker._fake = True
        sent = lifecycle.payload("PLAY")
        sent["emitted_mono"] = time.monotonic() - 0.75     # waited 750 ms in the queue
        made.events.put(("update", sent))
        made._poll_events()
        slow = [r.getMessage() for r in caplog.records if r.getMessage().startswith("UI_TIMING_SLOW")]
        assert slow, caplog.text
        waited = float(slow[0].split("queue=")[1].split()[0])
        assert 740 <= waited < 1500
        made.tracker._fake = False
        made.on_close()
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


def test_other_is_the_time_the_named_stages_do_not_explain(caplog):
    caplog.set_level(logging.INFO)
    tracker = make_tracker(tick_timing_every=1)
    tracker._timing.update(capture=100.0, roi=50.0, recognition=10.0, panel=40.0, fresh=set())
    tracker._record_tick(0.0, 0.5)                   # 500 ms in all, 200 ms of it in the named stages
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("TICK_TIMING")][0]
    assert "other 300" in line
