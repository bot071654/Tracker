"""Live recognition diagnostics.

These do not test that cards are read correctly - the live screen is the only
judge of that. They test that the diagnostics tell the truth about what was
read: that asking for detail changes no answer, that raw frame, memory and
display are kept apart, that impossible duplicates, flipping slots, round
boundaries and carried-over cards are reported rather than resolved, and that
what gets saved is the game area and the recogniser's own inputs.
"""

import glob
import json
import os
import queue

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module  # noqa: E402
from app import debug_lines  # noqa: E402
from capture.screen_capture import crop  # noqa: E402
from recognition import live_diagnostics as ld  # noqa: E402
from recognition import table_layout  # noqa: E402
from recognition.card_recognizer import read_slot, recognize_card  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))
needs_frame = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot")

TABLE = {
    "dealer_1": "8D", "dealer_2": "QD",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D", "turn": "3H", "river": "6S",
    "player_1": "8S", "player_2": "9C",
}


@pytest.fixture(scope="module")
def frame():
    return cv2.imread(SCREENSHOTS[0])


# -- the recogniser's working --------------------------------------------------------

@needs_frame
def test_asking_for_detail_changes_no_answer(frame):
    layout = table_layout.locate(frame)
    for slot, region in layout["regions"].items():
        image = crop(frame, region)
        plain = recognize_card(image)
        explained = recognize_card(image, detail=True)
        detail = explained.pop("detail")
        assert explained == plain, slot
        assert detail["rank_scores"][0][0] == plain["rank"]
        assert detail["suit_scores"][0][0] == plain["suit"]
        assert len(detail["suit_scores"]) == 2, "both suits of the ink colour are scored"
        assert detail["rank_glyph"].shape == (76, 56)
        assert detail["suit_glyph"].shape == (56, 56)


@needs_frame
def test_plain_reads_carry_no_images(frame):
    region = table_layout.locate(frame)["regions"]["flop_1"]
    assert "detail" not in read_slot(crop(frame, region))


# -- checks -------------------------------------------------------------------------------

def test_one_card_in_two_slots_is_a_duplicate():
    found = ld.duplicates(dict(TABLE, dealer_1="3D"))
    assert found == {"3D": ["dealer_1", "flop_3"]}


def test_a_clean_table_has_no_duplicates():
    assert ld.duplicates(TABLE) == {}


def test_a_card_twice_in_one_panel_row_is_a_duplicate():
    sides = {"player": {"cards": ["9C", "9C", "KC", None, "8S"]}}
    assert ld.panel_row_duplicates(sides) == {"player": ["9C"]}


def test_a_card_on_the_table_and_in_the_panel_is_not_a_duplicate():
    """The panel's best five are the table's own cards shown again."""
    sides = {"player": {"cards": ["3D", "3H", "QH", "9C", "8S"]},
             "dealer": {"cards": ["QD", "QH", "3D", "3H", "8D"]}}
    assert ld.panel_row_duplicates(sides) == {}
    assert ld.duplicates(TABLE) == {}


def test_a_slot_flipping_between_readings_is_unstable():
    watch = ld.UnstableWatch()
    for card in ("9C", "3C", "9C"):
        unstable = watch.observe({"flop_2": card})
    assert unstable == {"flop_2": ["9C", "3C", "9C"]}


def test_a_steady_slot_or_a_covered_one_is_not_unstable():
    watch = ld.UnstableWatch()
    for card in ("9C", None, "9C", "9C"):
        unstable = watch.observe({"flop_2": card})
    assert unstable == {}


def test_rounds_start_with_cards_and_end_with_them():
    watch = ld.RoundWatch()
    assert watch.observe(0, {}) == ([], {})
    events, _ = watch.observe(0, {"player_1": "KC", "player_2": "6H"})
    assert events == [("ROUND START", 0, {})]
    events, _ = watch.observe(1, {})
    assert events[0][0] == "ROUND END" and events[0][2]["player_1"] == "KC"


def test_empty_generations_between_rounds_are_not_rounds():
    """The memory starts a new generation every few polls of an empty table."""
    watch = ld.RoundWatch()
    watch.observe(0, {"player_1": "KC"})
    events = []
    for generation in range(1, 6):
        events += watch.observe(generation, {})[0]
    assert [kind for kind, _, _ in events] == ["ROUND END"]


def test_the_same_card_in_the_same_slot_after_a_new_round_is_reported():
    watch = ld.RoundWatch()
    watch.observe(0, {"flop_1": "KH", "player_1": "KC"})
    events, carry = watch.observe(1, {"flop_1": "KH", "player_1": "2S"})
    assert ("ROUND START", 1, {}) in events
    assert carry == {"flop_1": "KH"}


def test_carry_over_is_only_looked_for_at_the_start_of_a_round():
    watch = ld.RoundWatch()
    watch.observe(0, {"flop_1": "KH"})
    for _ in range(ld.CARRY_POLLS):
        watch.observe(1, {"flop_1": "5S"})
    assert watch.observe(1, {"flop_1": "KH"})[1] == {}


def test_frame_and_memory_are_kept_apart():
    reads = {slot: {"present": True, "card": card, "confident": True, "confidence": 0.9}
             for slot, card in TABLE.items()}
    reads["flop_2"] = dict(reads["flop_2"], card="3C")
    memory = dict(TABLE)
    rows = {row["slot"]: row for row in ld.slot_rows(
        {}, {}, {}, reads, memory, {}, memory, {}, {})}
    assert rows["flop_2"]["raw_card"] == "3C"
    assert rows["flop_2"]["memory_card"] == "QH"
    assert "MEMORY MISMATCH" in rows["flop_2"]["flags"]
    assert not rows["flop_1"]["flags"]


def test_a_card_held_in_memory_but_not_shown_is_flagged():
    reads = {slot: {"present": False} for slot in TABLE}
    rows = {row["slot"]: row for row in ld.slot_rows(
        {}, {}, {}, reads, {"dealer_1": "5D"}, {}, {"dealer_1": None}, {}, {})}
    assert "NOT SHOWN" in rows["dealer_1"]["flags"]


def test_every_problem_is_reported_not_resolved():
    diagnostics = ld.LiveDiagnostics()
    reads = {slot: {"present": True, "card": card, "confident": True, "confidence": 0.9}
             for slot, card in TABLE.items()}
    reads["dealer_1"] = dict(reads["dealer_1"], card="3D")          # also flop_3
    memory = dict(TABLE)
    report = diagnostics.poll(None, (0, 0), {}, {}, {}, reads, memory, {}, dict(memory),
                              {}, 1, "COMPLETE", "dynamic")
    problems = " | ".join(report["problems"])
    assert "DUPLICATE CARD DETECTION (frame): 3D in dealer_1 and flop_3" in problems
    assert "MEMORY MISMATCH dealer_1: frame 3D, memory 8D" in problems
    assert memory == TABLE, "diagnostics changed memory"
    flagged = {row["slot"] for row in report["slots"] if "DUPLICATE" in row["flags"]}
    assert flagged == {"dealer_1", "flop_3"}, "both slots of a duplicate are marked"


# -- what is saved ---------------------------------------------------------------------------

def run_tracker(monkeypatch, tmp_path, images, save=True):
    events = queue.Queue()
    tracker = tracker_module.Tracker({"monitor": 1, "live_diagnostics": save}, events)
    tracker._store = lambda cards, reads=None: None
    tracker.diagnostics = ld.LiveDiagnostics(root=str(tmp_path / "debug"))
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    for image in images:
        monkeypatch.setattr(tracker_module, "grab_full_screen",
                            lambda monitor=1, image=image: image)
        tracker._tick()
    if tracker.diagnostics.writer:
        tracker.diagnostics.writer.flush()
    updates = []
    while not events.empty():
        kind, payload = events.get()
        if kind == "update":
            updates.append(payload)
    return tracker, updates


@needs_frame
def test_a_saved_moment_has_the_frame_the_boxes_and_the_recognisers_inputs(
        monkeypatch, tmp_path, frame):
    run_tracker(monkeypatch, tmp_path, [frame] * 3)
    live = tmp_path / "debug" / "live"
    frames = sorted(p for p in live.glob("frame_*.png") if "annotated" not in p.name)
    assert frames, "nothing saved"
    assert list(live.glob("frame_*_annotated.png"))
    report = json.loads(sorted(live.glob("frame_*.json"))[-1].read_text())
    folder = tmp_path / "debug" / "card_crops" / report["stamp"]
    for slot in TABLE:
        assert (folder / ("%s.png" % slot)).exists(), slot
        assert (folder / ("%s_rank.png" % slot)).exists(), slot
        assert (folder / ("%s_suit.png" % slot)).exists(), slot
    rows = {row["slot"]: row for row in report["slots"]}
    assert rows["flop_1"]["region_source"] == "this frame"
    assert rows["flop_1"]["read_as"] == "2D"
    assert rows["flop_1"]["suit_scores"][0][0] == "D"


@needs_frame
def test_what_is_saved_is_the_game_area_not_the_whole_desktop(monkeypatch, tmp_path, frame):
    run_tracker(monkeypatch, tmp_path, [frame] * 2)
    saved = cv2.imread(str(sorted(p for p in (tmp_path / "debug" / "live").glob("frame_*.png")
                                  if "annotated" not in p.name)[0]))
    assert saved.shape[0] < frame.shape[0] and saved.shape[1] < frame.shape[1]


@needs_frame
def test_an_unchanged_table_is_saved_once_not_every_poll(monkeypatch, tmp_path, frame):
    tracker, _ = run_tracker(monkeypatch, tmp_path, [frame] * 12)
    saves = len([p for p in (tmp_path / "debug" / "live").glob("frame_*.png")
                 if "annotated" not in p.name])
    assert 1 <= saves <= 8, saves           # the panel filling in is a change too


@needs_frame
def test_nothing_is_written_when_saving_is_off(monkeypatch, tmp_path, frame):
    _, updates = run_tracker(monkeypatch, tmp_path, [frame] * 3, save=False)
    assert not (tmp_path / "debug").exists()
    assert updates[-1]["diagnostics"]["slots"], "the report is still produced"


@needs_frame
def test_the_clean_sample_frame_reports_no_problems(monkeypatch, tmp_path, frame):
    _, updates = run_tracker(monkeypatch, tmp_path, [frame] * 8, save=False)
    diag = updates[-1]["diagnostics"]
    assert diag["problems"] == []
    rows = {row["slot"]: row for row in diag["slots"]}
    assert {slot: rows[slot]["raw_card"] for slot in TABLE} == TABLE


def test_a_diagnostics_failure_never_stops_a_poll(monkeypatch, tmp_path):
    felt = np.full((600, 900, 3), (60, 95, 45), np.uint8)
    events = queue.Queue()
    tracker = tracker_module.Tracker({"monitor": 1}, events)
    tracker._store = lambda cards, reads=None: None

    def broken(*args, **kwargs):
        raise RuntimeError("diagnostics exploded")

    tracker.diagnostics.poll = broken
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: felt)
    tracker._tick()
    updates = []
    while not events.empty():
        kind, payload = events.get()
        if kind == "update":
            updates.append(payload)
    assert updates and updates[-1]["diagnostics"] is None


@needs_frame
def test_the_debug_window_text_shows_frame_memory_panel_and_problems(
        monkeypatch, tmp_path, frame):
    _, updates = run_tracker(monkeypatch, tmp_path, [frame] * 8, save=False)
    text = "\n".join(debug_lines(updates[-1]))
    for expected in ("LIVE RECOGNITION DEBUG", "RAW FRAME", "MEMORY", "flop_1",
                     "RESULT PANEL", "CENTER vs PANEL", "PROBLEMS", "this frame"):
        assert expected in text, expected
