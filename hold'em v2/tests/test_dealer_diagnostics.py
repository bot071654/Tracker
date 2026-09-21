"""Dealer-card diagnostics: status, timing, crops and the verifier's report.

These test that the dealer's cards are *reported* truthfully - the five
statuses, when each card was boxed, read, remembered and shown, which box it
was read through, and where each poll's time went - and that nothing about
that reporting changes what the tracker reads, remembers or shows.
"""

import glob
import json
import logging
import os
import queue
import subprocess
import sys
import time

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module  # noqa: E402
from app import dealer_lines, debug_lines  # noqa: E402
from recognition import dealer_watch as dw  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))
needs_frame = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot")
FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
FELT = (60, 95, 45)
DEALER_REGIONS = {
    "dealer_1": {"left": 854, "top": 329, "width": 95, "height": 124},
    "dealer_2": {"left": 961, "top": 329, "width": 95, "height": 124},
}


@pytest.fixture(scope="module")
def frame():
    return cv2.imread(SCREENSHOTS[0])


def read(card=None, confidence=0.0, confident=False, present=True, margin=0.3):
    return {"present": present, "card": card, "confidence": confidence,
            "confident": confident, "suit_margin": margin,
            "rank_confidence": confidence, "suit_confidence": 0.95}


# -- statuses -------------------------------------------------------------------------

def test_the_five_statuses():
    assert dw.dealer_status(read(present=False), None) == dw.NOT_DETECTED
    assert dw.dealer_status(read(card="9H", confidence=0.30), None) == dw.UNKNOWN
    assert dw.dealer_status(read(card="7C", confidence=0.80, margin=0.01), None) == dw.AMBIGUOUS
    assert dw.dealer_status(read(card="9H", confidence=0.72, confident=True), None) == dw.DETECTED
    assert dw.dealer_status(read(card="9H", confidence=0.95, confident=True), "9H") == dw.CONFIRMED


def test_a_shown_card_is_confirmed_even_while_a_hand_covers_it():
    assert dw.dealer_status(read(present=False), "KD") == dw.CONFIRMED


# -- timing -----------------------------------------------------------------------------

def test_boxed_read_memory_shown_are_timed_per_card():
    watch = dw.DealerWatch()
    polls = [
        (0.0, {"dealer_1": read(card=None)}, {}, {}),                           # a face, unread
        (0.2, {"dealer_1": read("KD", 0.72, True)}, {"dealer_1": "KD"}, {}),     # read, gate refuses
        (0.4, {"dealer_1": read("KD", 0.88, True)}, {"dealer_1": "KD"}, {"dealer_1": "KD"}),
    ]
    records = []
    for now, reads, memory, shown in polls:
        records += watch.observe(1, reads, memory, shown, now=now)
    assert records == [{"slot": "dealer_1", "card": "KD", "boxed_to_read_ms": 200,
                        "read_to_memory_ms": 0, "memory_to_shown_ms": 200,
                        "boxed_to_shown_ms": 400, "first_read_card": "KD"}]


def test_a_card_is_reported_once_and_forgotten_at_the_next_round():
    watch = dw.DealerWatch()
    shown = {"dealer_1": "KD"}
    reads = {"dealer_1": read("KD", 0.9, True)}
    assert len(watch.observe(1, reads, shown, shown, now=0.0)) == 1
    assert watch.observe(1, reads, shown, shown, now=0.2) == []
    assert len(watch.observe(2, reads, shown, shown, now=5.0)) == 1


# -- crops -------------------------------------------------------------------------------

def evidence(status="DETECTED", card="9H", confidence=0.72, left=842):
    return dw.slot_evidence("dealer_1", {"left": left, "top": 303, "width": 112, "height": 123},
                            "calibrated", read(card, confidence, status == "DETECTED"),
                            card, None, status)


def test_a_dealer_crop_is_saved_when_its_reading_changes_not_every_poll(tmp_path):
    saver = dw.DealerSaver(folder=str(tmp_path))
    image = np.full((123, 112, 3), 230, np.uint8)
    assert saver.consider(evidence(), image)
    assert not saver.consider(evidence(), image), "an unchanged reading was saved again"
    assert not saver.consider(evidence(confidence=0.73), image), "a tiny wobble is not a change"
    assert saver.consider(evidence(confidence=0.80), image)
    assert saver.consider(evidence(card="9D"), image)
    assert saver.consider(evidence(left=870), image), "a moved box is a change"
    saver.flush()
    assert len(list(tmp_path.glob("dealer_1_*.png"))) == 4
    record = json.loads(sorted(tmp_path.glob("dealer_1_*.json"))[0].read_text())
    assert record["box_source"] == "calibrated" and record["box"]["width"] == 112


def test_nothing_is_saved_for_an_empty_box(tmp_path):
    saver = dw.DealerSaver(folder=str(tmp_path))
    empty = dw.slot_evidence("dealer_1", None, None, read(present=False), None, None,
                             dw.NOT_DETECTED)
    assert not saver.consider(empty, np.zeros((10, 10, 3), np.uint8))


# -- in the tracker ------------------------------------------------------------------------

def run(monkeypatch, tmp_path, images, config=None):
    events = queue.Queue()
    tracker = tracker_module.Tracker(dict({"monitor": 1}, **(config or {})), events)
    tracker._store = lambda cards, reads=None: None
    tracker.dealer_saver = dw.DealerSaver(folder=str(tmp_path / "live"))
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    for image in images:
        monkeypatch.setattr(tracker_module, "grab_full_screen",
                            lambda monitor=1, image=image: image)
        tracker._tick()
    tracker.dealer_saver.flush()
    updates = []
    while not events.empty():
        kind, payload = events.get()
        if kind == "update":
            updates.append(payload)
    return tracker, updates


@needs_frame
def test_every_update_says_where_the_poll_time_went(monkeypatch, tmp_path, frame):
    _, updates = run(monkeypatch, tmp_path, [frame] * 2, {"dealer_debug": True})
    timing = updates[-1]["timing"]
    for stage in ("capture", "roi", "crop", "recognition", "memory", "gate", "panel",
                  "diagnostics", "total"):
        assert stage in timing, stage
    assert updates[-1]["emitted_at"] <= time.time()


@needs_frame
def test_a_clean_dealer_is_confirmed_and_its_latency_logged(monkeypatch, tmp_path, frame, caplog):
    with caplog.at_level(logging.INFO, logger="tracker"):
        _, updates = run(monkeypatch, tmp_path, [frame] * 2, {"dealer_debug": True})
    dealer = updates[-1]["dealer"]
    assert dealer["slots"]["dealer_1"]["status"] == dw.CONFIRMED
    assert dealer["slots"]["dealer_1"]["box_source"] == "this frame"
    latency = [r.message for r in caplog.records if r.message.startswith("DEALER_LATENCY")]
    assert any("dealer_1=8D" in m for m in latency) and any("dealer_2=QD" in m for m in latency)
    assert list((tmp_path / "live").glob("dealer_1_*.png"))


@needs_frame
def test_at_the_river_every_poll_is_timed(monkeypatch, tmp_path, frame, caplog):
    covered = frame.copy()
    for region in DEALER_REGIONS.values():          # dealer's cards not yet turned
        covered[region["top"] - 14:region["top"] + region["height"] + 14,
                region["left"] - 14:region["left"] + region["width"] + 14] = FELT
    with caplog.at_level(logging.INFO, logger="tracker"):
        _, updates = run(monkeypatch, tmp_path, [covered] * 3, {"dealer_debug": True})
    assert updates[-1]["state"] == tracker_module.RIVER
    timing = [r.message for r in caplog.records if r.message.startswith("DEALER_TIMING")]
    assert len(timing) == 3
    assert "dealer_1 NOT_DETECTED" in timing[-1]


@needs_frame
def test_dealer_diagnostics_are_off_unless_asked(monkeypatch, tmp_path, frame):
    _, updates = run(monkeypatch, tmp_path, [frame] * 2)
    assert updates[-1]["dealer"] is None
    assert not (tmp_path / "live").exists() or not list((tmp_path / "live").iterdir())


@needs_frame
def test_dealer_diagnostics_change_nothing_that_is_read_or_shown(monkeypatch, tmp_path, frame):
    _, off = run(monkeypatch, tmp_path / "a", [frame] * 3)
    _, on = run(monkeypatch, tmp_path / "b", [frame] * 3, {"dealer_debug": True})
    assert [u["cards"] for u in off] == [u["cards"] for u in on]
    assert [u["state"] for u in off] == [u["state"] for u in on]


def test_a_dealer_diagnostics_failure_never_stops_a_poll(monkeypatch, tmp_path):
    events = queue.Queue()
    tracker = tracker_module.Tracker({"monitor": 1, "dealer_debug": True}, events)
    tracker._store = lambda cards, reads=None: None

    def broken(*args, **kwargs):
        raise RuntimeError("watch exploded")

    tracker.dealer_watch.observe = broken
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "grab_full_screen",
                        lambda monitor=1: np.full((600, 900, 3), FELT, np.uint8))
    tracker._tick()
    updates = [p for k, p in iter(lambda: events.get() if not events.empty() else (None, None),
                                  (None, None)) if k == "update"]
    assert updates and updates[-1]["dealer"] is None


@needs_frame
def test_the_debug_window_shows_the_dealer_block(monkeypatch, tmp_path, frame):
    _, updates = run(monkeypatch, tmp_path, [frame] * 2, {"dealer_debug": True})
    text = "\n".join(debug_lines(updates[-1]))
    for expected in ("DEALER LIVE DEBUG", "dealer_1 CONFIRMED 8D", "this poll ms", "capture"):
        assert expected in text, expected
    assert "off" in dealer_lines({"dealer": None})[0]


# -- the verifier ------------------------------------------------------------------------------

def verifier(log, mark_text, *args):
    code = (
        "import sys; sys.path.insert(0, %r); import tools.verify_run as v; "
        "v.LOG = %r; v.WATERMARK = %r; v.FAILURES = %r; "
        "sys.argv = ['verify_run.py'] + %r; raise SystemExit(v.main())"
        % (ROOT, str(log), str(log) + ".mark", str(log) + ".none", list(args)))
    if mark_text is not None:
        with open(str(log) + ".mark", "w") as handle:
            handle.write(mark_text)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=ROOT, timeout=120)
    assert done.returncode == 0, done.stderr
    return done.stdout


def line(moment, message, name="tracker"):
    return "%s,000 INFO    %-22s %s\n" % (moment, name, message)


def test_a_time_mark_reads_across_a_rotated_log(tmp_path):
    log = tmp_path / "tracker.log"
    (tmp_path / "tracker.log.1").write_text(
        line("2026-09-13 19:00:00", "State WAITING -> RIVER | dealer_1=-- dealer_2=--")
        + line("2026-09-13 19:05:00", "State WAITING -> RIVER | dealer_1=-- dealer_2=--"))
    log.write_text(line("2026-09-13 19:06:00",
                        "State RIVER -> WAITING | dealer_1=-- dealer_2=--"))
    out = verifier(log, "2026-09-13 19:03:00")
    assert "Reading 2 lines" in out
    assert "rounds that reached the river  1" in out
    assert "round ended, dealer never   1" in out


def test_an_old_line_mark_past_a_rotation_is_not_silently_empty(tmp_path):
    log = tmp_path / "tracker.log"
    log.write_text(line("2026-09-13 19:06:00", "State WAITING -> RIVER | dealer_1=--"))
    out = verifier(log, "12827")
    assert "rotated" in out and "Reading 1 lines" in out


@needs_frame
def test_the_verifier_reads_the_trackers_own_dealer_lines(monkeypatch, tmp_path, frame):
    log = tmp_path / "tracker.log"
    handler = logging.FileHandler(str(log), encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.INFO)
    try:
        covered = frame.copy()
        for region in DEALER_REGIONS.values():
            covered[region["top"] - 14:region["top"] + region["height"] + 14,
                    region["left"] - 14:region["left"] + region["width"] + 14] = FELT
        run(monkeypatch, tmp_path, [covered] * 3 + [frame] * 2, {"dealer_debug": True})
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
        handler.close()
    out = verifier(log, None, "--all")
    assert "rounds that reached the river  1" in out
    assert "dealer shown                1" in out
    assert "capture" in out and "n=3" in out, out
    assert "dealer_1  polls by status: NOT_DETECTED 100%" in out
    assert "boxed->shown" in out
