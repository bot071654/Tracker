"""(tests/test_dealer_diagnostics.py is the tracker's own dealer-card reporting; this file is the harness's dealer-BOX evidence.)

Evidence for "are the dealer's cards missing from the table, or visible and missed by slot assignment?" (dealer_diagnostics.py).

Round 155 of a practice session never completed: both panel rows 5/5, the table found afresh every poll, and no dealer box for
the whole round. These tests pin down what the diagnostic says in each situation that could produce that, using the real
casino frame with the dealer's cards left alone, painted out, half painted out, joined into one shape, or accompanied by an
extra white block - and that what it says is what the tracker's own layout finder did (it is checked against
table_layout.locate every time). Then the per-poll recording, the 3-second trigger and the frame saving, through the real
recorder, with the harness's own saver and its refusal to overwrite evidence.
"""

import importlib.util
import json
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import dealer_diagnostics as dd                                   # noqa: E402
import round_lifecycle as rl                                      # noqa: E402
import round_validation as rv                                     # noqa: E402
from recognition import table_layout as tl                        # noqa: E402

from test_round_lifecycle import FLOP, FULL, PLAYER, RIVER, TURN, panel_snapshot, run   # noqa: E402,F401 - fixtures too

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOT = os.path.join(ROOT, "Screenshot 2026-09-02 152130.png")
needs_frame = pytest.mark.skipif(not os.path.exists(SCREENSHOT), reason="no casino screenshot in the project folder")
BUILD_UP = {**PLAYER, **FLOP, **TURN, **RIVER}


@pytest.fixture(scope="module")
def frame():
    return cv2.imread(SCREENSHOT)


@pytest.fixture(scope="module")
def dealer_boxes(frame):
    regions = tl.locate(frame)["regions"]
    return [(r["left"], r["top"], r["width"], r["height"]) for r in (regions["dealer_1"], regions["dealer_2"])]


def felt_of(frame):
    return np.median(frame[620:640, 1380:1400].reshape(-1, 3), axis=0).astype(frame.dtype)


def painted(frame, boxes, colour):
    out = frame.copy()
    for x, y, w, h in boxes:
        out[max(0, y - 6):y + h + 6, max(0, x - 6):x + w + 6] = colour
    return out


def with_dealer_cards_painted_out(frame, boxes, which=(0, 1)):
    return painted(frame, [boxes[i] for i in which], felt_of(frame))


def with_dealer_cards_joined(frame, boxes):
    """The two dealer cards bridged by one white block, as a sleeve or a hand across them would: one wide shape, not two cards."""
    out = frame.copy()
    (x1, y1, w1, h1), (x2, y2, w2, h2) = boxes
    out[y1 + 10:y1 + h1 - 10, x1 + w1 - 4:x2 + 4] = 235
    return out


def with_an_extra_white_block(frame, boxes):
    """A third card-shaped block in the dealer's row, beside the pair."""
    out = frame.copy()
    x, y, w, h = boxes[1]
    out[y:y + h, x + w + 14:x + 2 * w + 14] = 235
    return out


CASES = {
    "both dealer cards present": lambda f, b: f,
    "both painted out": lambda f, b: with_dealer_cards_painted_out(f, b),
    "one painted out": lambda f, b: with_dealer_cards_painted_out(f, b, (1,)),
    "joined into one shape": with_dealer_cards_joined,
    "an extra white block in the row": with_an_extra_white_block,
}


# -- what the diagnostic says in each case ------------------------------------------------------------------------------------

@needs_frame
def test_with_the_dealers_cards_on_the_table_the_finder_pairs_them_and_the_zone_shows_two_cards(frame, dealer_boxes):
    d = dd.slot_assignment_diagnostics(frame)
    assert d["dealer_slots_assigned"] and d["player_slots_assigned"] and d["above_row_lengths"] == [2]
    assert d["community"]["n"] == 5 and d["dealer_zone"]["accepted"] == 2
    above = [row for row in d["rows"] if row["role"] == "above"][0]
    assert above["pair_over_board"] is True


@needs_frame
def test_with_the_dealers_cards_absent_there_is_nothing_in_the_zone_and_no_row_above_the_board(frame, dealer_boxes):
    """Evidence for 'the cards were not on the table': no card-shaped block, no row, and the strip is no whiter."""
    present = dd.slot_assignment_diagnostics(frame)
    absent = dd.slot_assignment_diagnostics(with_dealer_cards_painted_out(frame, dealer_boxes))
    assert not absent["dealer_slots_assigned"] and absent["above_row_lengths"] == [] and absent["dealer_zone"]["accepted"] == 0
    assert absent["dealer_zone"]["white_fraction"] < present["dealer_zone"]["white_fraction"]
    assert absent["player_slots_assigned"] and absent["community"]["n"] == 5          # the rest of the table is still found


@needs_frame
def test_with_one_dealer_card_visible_the_diagnostic_shows_a_row_of_one_that_cannot_be_a_pair(frame, dealer_boxes):
    """Evidence for 'visible and missed': a card IS in the zone, in a row above the board, but a row of one is not a hand."""
    d = dd.slot_assignment_diagnostics(with_dealer_cards_painted_out(frame, dealer_boxes, (1,)))
    assert not d["dealer_slots_assigned"]
    assert d["above_row_lengths"] == [1] and d["dealer_zone"]["accepted"] == 1
    above = [row for row in d["rows"] if row["role"] == "above"][0]
    assert above["pair_over_board"] is False


@needs_frame
def test_with_the_cards_joined_the_diagnostic_lists_the_rejected_shape_and_why(frame, dealer_boxes):
    """Evidence for 'visible and merged': the zone holds a bright block the finder rejected, with the reason."""
    d = dd.slot_assignment_diagnostics(with_dealer_cards_joined(frame, dealer_boxes))
    assert not d["dealer_slots_assigned"] and d["dealer_zone"]["accepted"] == 0
    reasons = [blob["verdict"] for blob in d["dealer_zone"]["blobs"]]
    assert any(reason.startswith("rejected: aspect") for reason in reasons), reasons
    biggest = max(d["dealer_zone"]["blobs"], key=lambda b: b["box"][2])
    assert biggest["box"][2] > 1.6 * dealer_boxes[0][2]                              # as wide as the two cards together


@needs_frame
def test_with_a_third_block_in_the_row_the_diagnostic_shows_a_row_of_three(frame, dealer_boxes):
    d = dd.slot_assignment_diagnostics(with_an_extra_white_block(frame, dealer_boxes))
    assert not d["dealer_slots_assigned"] and d["above_row_lengths"] == [3] and d["dealer_zone"]["accepted"] == 3


@needs_frame
@pytest.mark.parametrize("case", sorted(CASES))
def test_what_the_diagnostic_reports_is_what_the_trackers_own_finder_did(frame, dealer_boxes, case):
    picture = CASES[case](frame, dealer_boxes)
    d = dd.slot_assignment_diagnostics(picture)
    layout = tl.locate(picture)
    assert layout is not None
    assert sorted(d["slots"]) == sorted(layout["regions"])
    assert d["dealer_slots_assigned"] == all(slot in layout["regions"] for slot in dd.DEALER_SLOTS)
    assert d["table_boxes"] == layout["found"] or d["table_boxes"] >= layout["found"]


@needs_frame
def test_the_diagnostic_does_not_touch_the_frame_it_is_given(frame):
    before = frame.copy()
    dd.slot_assignment_diagnostics(frame)
    assert np.array_equal(before, frame)


def test_no_table_and_no_community_row_are_reported_not_raised():
    blank = np.zeros((300, 400, 3), np.uint8)
    d = dd.slot_assignment_diagnostics(blank)
    assert d["card_height"] is None and d["rows"] == [] and not d["dealer_slots_assigned"] and d["dealer_zone"] is None


@needs_frame
def test_the_compact_form_keeps_what_tells_the_cases_apart(frame, dealer_boxes):
    compact = dd.compact(dd.slot_assignment_diagnostics(with_dealer_cards_painted_out(frame, dealer_boxes, (1,))))
    assert compact == {"fresh_dealer_slots": False, "recovered": False, "above_rows": [1], "community": 5, "table_boxes": 8, "zone_cards": 1,
                       "zone_rejected": compact["zone_rejected"], "zone_white": compact["zone_white"]}
    assert dd.compact(None) is None


# -- the watch: per-poll record and the trigger -----------------------------------------------------------------------------------

class Saver:
    def __init__(self):
        self.saved = []

    def __call__(self, frame, name, info):
        self.saved.append((name, info))
        return name + ".png"


def feed(watch, record, steps, round_id=1, state="RIVER", frame=None):
    """steps: (seconds, dealer row visible, player row visible, dealer boxes?) -> the compact lines."""
    lines = []
    for t, dealer_row, player_row, boxes in steps:
        regions = ["dealer_1", "dealer_2"] if boxes else []
        lines.append(watch.observe(t, round_id, state, {"dealer": dealer_row, "player": player_row},
                                   {"layout_source": "dynamic", "regions": regions + ["player_1"], "region_sources": {}}, frame, record))
    return lines


def test_every_poll_records_whether_the_trackers_has_dealer_boxes():
    watch, record = dd.DealerWatch(saver=Saver()), {}
    lines = feed(watch, record, [(0.0, False, True, False), (0.5, False, True, True)])
    assert [line["box"] for line in lines] == [[0, 0], [1, 1]]
    summary = record["dealer_boxes"]
    assert summary["polls"] == 2 and summary["polls_with_any_box"] == 1 and summary["polls_with_both_boxes"] == 1
    assert summary["first_box_s"] == 0.5


def test_a_frame_is_saved_once_the_dealer_panel_has_been_up_three_seconds_with_no_dealer_box():
    saver, record = Saver(), {}
    watch = dd.DealerWatch(saver=saver, seconds=3.0)
    frame = np.zeros((20, 20, 3), np.uint8)
    feed(watch, record, [(t / 2.0, True, True, False) for t in range(0, 6)], frame=frame, state="FLOP")             # 0.0 .. 2.5 s
    assert saver.saved == []                                                                          # not yet
    feed(watch, record, [(3.0, True, True, False)], frame=frame, state="FLOP")
    assert [name for name, _ in saver.saved] == ["round1_result-panel_t3.0"]
    info = saver.saved[0][1]
    assert info["trigger"] == "result panel" and info["absent_for_s"] == pytest.approx(3.0) and info["round"] == 1
    feed(watch, record, [(3.5, True, True, False), (9.0, True, True, False)], frame=frame, state="FLOP")
    assert len(saver.saved) == 1                                                                     # once per round and trigger
    assert record["dealer_boxes"]["frames"][0]["name"] == "round1_result-panel_t3.0.png"
    assert record["dealer_boxes"]["longest_absence_with_dealer_row_visible_s"] == pytest.approx(9.0)


def test_a_dealer_box_appearing_before_three_seconds_cancels_the_wait():
    saver, record = Saver(), {}
    watch = dd.DealerWatch(saver=saver, seconds=3.0)
    frame = np.zeros((20, 20, 3), np.uint8)
    feed(watch, record, [(0.0, True, True, False), (2.9, True, True, False), (3.0, True, True, True), (3.5, True, True, False),
                         (6.0, True, True, False)], frame=frame)
    assert saver.saved == []                                                                          # the clock restarted at 3.5


def test_nothing_is_saved_when_the_result_panel_is_not_up_or_the_boxes_are_there():
    saver, record = Saver(), {}
    watch = dd.DealerWatch(saver=saver, seconds=3.0, showdown_seconds=60.0)
    frame = np.zeros((20, 20, 3), np.uint8)
    feed(watch, record, [(t, False, False, False) for t in range(0, 20)], frame=frame, state="FLOP")   # no panel at all
    feed(watch, record, [(t, True, True, True) for t in range(20, 40)], frame=frame)                   # panel, with boxes
    assert saver.saved == []


def test_the_showdown_trigger_catches_a_round_whose_dealer_panel_never_appears():
    saver, record = Saver(), {}
    watch = dd.DealerWatch(saver=saver, seconds=3.0, showdown_seconds=6.0)
    frame = np.zeros((20, 20, 3), np.uint8)
    feed(watch, record, [(t, False, True, False) for t in range(0, 8)], frame=frame, state="RIVER")   # the player's row only
    assert [info["trigger"] for _, info in saver.saved] == ["showdown phase"]
    assert saver.saved[0][1]["absent_for_s"] == pytest.approx(6.0)


def test_each_round_gets_its_own_frame_and_the_run_has_a_cap():
    saver = Saver()
    watch = dd.DealerWatch(saver=saver, seconds=1.0, max_frames=2)
    frame = np.zeros((20, 20, 3), np.uint8)
    records = {n: {} for n in (1, 2, 3)}
    for n in (1, 2, 3):
        feed(watch, records[n], [(10 * n + 0.0, True, True, False), (10 * n + 1.5, True, True, False)], round_id=n, frame=frame)
    assert [info["round"] for _, info in saver.saved] == [1, 2]                                       # the third is over the cap
    assert records[3]["dealer_boxes"]["frames"] == []


def test_without_a_saver_or_a_frame_nothing_is_saved_and_nothing_fails():
    record = {}
    watch = dd.DealerWatch(saver=None)
    feed(watch, record, [(0.0, True, True, False), (5.0, True, True, False)], frame=np.zeros((20, 20, 3), np.uint8))
    watch = dd.DealerWatch(saver=Saver())
    feed(watch, {}, [(0.0, True, True, False), (5.0, True, True, False)], frame=None)


def test_a_failing_diagnostic_is_recorded_and_the_run_goes_on():
    watch = dd.DealerWatch(saver=Saver())
    line = watch.observe(0.0, 1, "RIVER", {"dealer": True, "player": True}, {"regions": []}, "not a frame", {})
    assert "error" in line["diag"]


# -- through the real recorder ------------------------------------------------------------------------------------------------------

class Rig:
    def __init__(self, run, saver):
        self.run, self.clock, self.saver = run, 100.0, saver
        self.watch = dd.DealerWatch(saver=saver, seconds=3.0)
        self.recorder = rv.RoundRecorder(dealer_watch=self.watch)

    def poll(self, table, mode, polls=1, step=1.0, boxes=False):
        self.run.panel_mode = mode
        for _ in range(polls):
            self.clock += step
            payload = self.run.poll(table)
            payload["emitted_mono"] = self.clock
            extras = {"frame": np.zeros((30, 40, 3), np.uint8), "layout_source": "dynamic",
                      "regions": (["dealer_1", "dealer_2"] if boxes else []) + ["player_1", "flop_1"], "region_sources": {}}
            self.recorder.observe(payload, extras)


def test_through_the_recorder_every_poll_line_carries_the_dealer_record_and_a_stuck_round_saves_a_frame(run):
    saver = Saver()
    rig = Rig(run, saver)
    rig.poll(BUILD_UP, "missing", polls=3)
    rig.poll(FULL, "partial", polls=6)                        # the showdown: the dealer's panel row is up and nothing is boxed
    assert all("dealer_table" in line and "panel" in line["dealer"] for line in rig.recorder.polls)   # and the panel row is intact
    record = [r for r in rig.recorder.finish()][0]
    assert record["dealer_boxes"]["polls"] == 9 and record["dealer_boxes"]["polls_with_any_box"] == 0
    assert [info["trigger"] for _, info in saver.saved] == ["result panel"]
    summary = rv.summarize(rig.recorder.finish())
    assert summary["dealer_boxes"]["rounds_with_no_dealer_box_in_any_poll"] == 1
    assert summary["dealer_boxes"]["rounds_with_dealer_row_visible_and_no_box_for_3s"] == 1
    assert summary["dealer_boxes"]["frames_saved"] == ["round%s_result-panel_t%.1f.png" % (record["round_id"],
                                                                                          saver.saved[0][1]["t"])]


def test_through_the_recorder_a_round_with_dealer_boxes_saves_no_frame(run):
    saver = Saver()
    rig = Rig(run, saver)
    rig.poll(BUILD_UP, "missing", polls=3)
    rig.poll(FULL, "agree", polls=6, boxes=True)
    assert saver.saved == []
    record = [r for r in rig.recorder.finish() if r["completed"]][0]
    assert record["dealer_boxes"]["polls_with_both_boxes"] == 6


def test_the_recorder_without_extras_or_a_watch_behaves_as_it_did(run):
    recorder = rv.RoundRecorder()
    run.panel_mode = "missing"
    payload = run.poll(BUILD_UP)
    payload["emitted_mono"] = 1.0
    line = recorder.observe(payload)
    assert "dealer_table" not in line and "dealer_boxes" not in recorder.finish()[0]
    watching = rv.RoundRecorder(dealer_watch=dd.DealerWatch())
    assert "dealer_table" not in watching.observe(payload)                           # a watch needs the harness's extras


# -- the harness: saving frames, never overwriting evidence --------------------------------------------------------------------------

def harness():
    spec = importlib.util.spec_from_file_location("validate_rounds_under_test", os.path.join(ROOT, "tools", "validate_rounds.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_frame_saver_writes_the_png_and_the_diagnostics_and_never_overwrites(tmp_path):
    saver = harness().FrameSaver(str(tmp_path))
    picture = np.full((12, 16, 3), 90, np.uint8)
    first = saver(picture, "round7_result-panel_t3.0", {"round": 7})
    second = saver(picture * 0 + 200, "round7_result-panel_t3.0", {"round": 7, "again": True})
    assert (first, second) == ("round7_result-panel_t3.0.png", "round7_result-panel_t3.0_2.png")
    frames = tmp_path / "frames"
    assert sorted(os.listdir(frames)) == ["round7_result-panel_t3.0.json", "round7_result-panel_t3.0.png",
                                          "round7_result-panel_t3.0_2.json", "round7_result-panel_t3.0_2.png"]
    assert cv2.imread(str(frames / first))[0, 0].tolist() == [90, 90, 90]                       # the first one is untouched
    assert json.load(open(frames / "round7_result-panel_t3.0_2.json"))["again"] is True


def test_the_harness_refuses_an_evidence_folder_that_already_holds_anything(tmp_path):
    module = harness()
    folder = tmp_path / "earlier-evidence"
    folder.mkdir()
    (folder / "summary.json").write_text("{}")
    with pytest.raises(FileExistsError):
        module.fresh_evidence_dir(str(folder))
    assert os.listdir(folder) == ["summary.json"]
    assert module.fresh_evidence_dir(str(tmp_path / "new")) == str(tmp_path / "new")
    assert module.fresh_evidence_dir(str(tmp_path / "new")) == str(tmp_path / "new")           # empty is fine


def test_the_frame_tap_hands_back_exactly_what_the_capture_returned():
    module = harness()
    shown = {"frame": np.ones((4, 4, 3), np.uint8)}
    tap = module.FrameTap(lambda monitor=1: shown["frame"])
    assert tap(1) is shown["frame"] and tap.latest is shown["frame"]


# -- the harness end to end, with no casino ------------------------------------------------------------------------------------------------

def run_harness(tmp_path, picture_path, phases, extra=()):
    import subprocess
    import sys
    out = tmp_path / "evidence"
    result = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "validate_rounds.py"), "--replay", picture_path,
                             "--out", str(out), "--replay-phases", phases, "--dealer-seconds", "2", *extra],
                            capture_output=True, text=True, cwd=ROOT, timeout=240)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    return out


@needs_frame
def test_replay_with_the_dealers_cards_present_records_boxes_every_poll_and_saves_no_frame(tmp_path):
    out = run_harness(tmp_path, SCREENSHOT, "7,3")
    polls = [json.loads(line) for line in open(out / "polls.jsonl")]
    on_table = [p for p in polls if p["lifecycle"] in ("IN_PROGRESS", "COMPLETE")]
    assert on_table and all("dealer_table" in p and "panel" in p["dealer"] for p in on_table)
    assert any(p["dealer_table"]["box"] == [1, 1] for p in on_table)
    summary = json.load(open(out / "summary.json"))
    assert summary["dealer_boxes"]["frames_saved"] == [] and not (out / "frames").exists()
    assert summary["dealer_boxes"]["rounds_with_no_dealer_box_in_any_poll"] == 0


@needs_frame
def test_replay_with_the_dealers_cards_missing_saves_the_raw_frame_with_the_diagnostics_beside_it(tmp_path, frame, dealer_boxes):
    hidden = tmp_path / "dealer-cards-hidden.png"
    cv2.imwrite(str(hidden), with_dealer_cards_painted_out(frame, dealer_boxes))
    out = run_harness(tmp_path, str(hidden), "9,3")
    saved = sorted(os.listdir(out / "frames"))
    assert any(name.endswith(".png") for name in saved) and any(name.endswith(".json") for name in saved)
    picture = cv2.imread(str(out / "frames" / [n for n in saved if n.endswith(".png")][0]))
    assert picture.shape == frame.shape                                                  # the raw frame, whole
    sidecar = json.load(open(out / "frames" / [n for n in saved if n.endswith(".json")][0]))
    assert sidecar["trigger"] in ("result panel", "showdown phase") and sidecar["absent_for_s"] >= 2.0
    assert sidecar["diagnostics"]["dealer_slots_assigned"] is False and sidecar["diagnostics"]["above_row_lengths"] == []
    assert sidecar["diagnostics"]["dealer_zone"]["accepted"] == 0
    summary = json.load(open(out / "summary.json"))
    assert summary["dealer_boxes"]["frames_saved"] and summary["dealer_boxes"]["rounds_with_no_dealer_box_in_any_poll"] >= 1
    report = open(out / "report.md", encoding="utf-8").read()
    assert "## Dealer boxes" in report


@needs_frame
def test_replay_with_a_frame_budget_of_zero_saves_nothing(tmp_path, frame, dealer_boxes):
    hidden = tmp_path / "dealer-cards-hidden.png"
    cv2.imwrite(str(hidden), with_dealer_cards_painted_out(frame, dealer_boxes))
    out = run_harness(tmp_path, str(hidden), "8,3", extra=("--max-frames", "0"))
    assert not (out / "frames").exists()
    assert json.load(open(out / "summary.json"))["dealer_boxes"]["frames_saved"] == []
