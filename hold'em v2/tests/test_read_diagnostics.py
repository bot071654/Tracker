"""The harness's per-slot reading evidence (read_diagnostics.py), wired into round_validation.py and tools/validate_rounds.py.

Question it exists for: five raw dealer readings in a practice session differed from the finished hand - black suits read as
diamonds - and the pixels behind them were not kept. These tests pin down what is recorded on every poll, what is saved when a raw
reading disagrees with the card memory, and - as much the point - that none of it can change what the tracker reads or decides:
it records only what the update and the tracker's own frame already hold, writes only into a new evidence folder, never over a file
that is there, and a failure anywhere in it is counted and never stops a poll.
"""

import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import read_diagnostics as rd                                    # noqa: E402
import round_validation as rv                                    # noqa: E402
from recognition import card_recognizer                          # noqa: E402
from recognition import table_layout as tl                       # noqa: E402
import external_fixtures as ext                           # noqa: E402 - fixtures kept outside the repository skip when absent

from test_round_lifecycle import FULL, PLAYER, FLOP, TURN, RIVER, panel_snapshot, run   # noqa: E402,F401 - fixtures too

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "dealer_fusion", "round45_result-panel_t391.6.png")
BUILD_UP = {**PLAYER, **FLOP, **TURN, **RIVER}
UNAVAILABLE = rd.UNAVAILABLE


def load_harness():
    spec = importlib.util.spec_from_file_location("validate_rounds_under_test", os.path.join(ROOT, "tools", "validate_rounds.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def frame():
    return ext.load_frame(FIXTURE)


@pytest.fixture(scope="module")
def boxes(frame):
    regions = tl.locate(frame)["regions"]
    return {slot: {key: regions[slot][key] for key in ("left", "top", "width", "height")} for slot in ("dealer_1", "dealer_2")}


class Saver:
    """Stands in for the harness's saver: keeps what it is given."""

    def __init__(self):
        self.saved = []

    def __call__(self, name, images, info):
        self.saved.append((name, images, info))
        return name


def row(slot, raw=None, memory=None, region=None, **more):
    base = {"slot": slot, "label": slot, "region": region, "region_source": "this frame", "crop": [122, 98], "present": True,
            "raw_card": raw, "read_as": raw, "confidence": 0.91, "rank_confidence": 0.95, "suit_confidence": 0.9, "suit_margin": 0.085,
            "ink": None, "rank_scores": None, "suit_scores": None, "cuts_tried": None, "cut_chosen": None, "glyphs_available": False,
            "memory_card": memory, "memory_readings": 3, "memory_best": 0.93, "shown_card": memory, "status": "CONFIRMED",
            "flags": ["MEMORY MISMATCH"] if raw and memory and raw != memory else []}
    base.update(more)
    return base


def read(card, confident=True, **more):
    base = {"present": True, "ratio": 0.64, "card": card, "confidence": 0.91, "confident": confident, "rank_margin": 0.36,
            "suit_margin": 0.085, "dealer_face": [0, 4, 98, 107]}
    base.update(more)
    return base


def payload_of(rows, round_id=7, reads=None, state="RIVER"):
    return {"state": state, "round_id": round_id, "emitted_mono": 1.0,
            "lifecycle": {"round_id": round_id, "state": "IN_PROGRESS", "result_status": "none"},
            "diagnostics": {"slots": rows},
            "reads": reads if reads is not None else {r["slot"]: read(r["raw_card"]) for r in rows}}


def extras_of(frame, fresh=("dealer_1", "dealer_2"), origin=(0, 0)):
    return {"read_frame": frame, "frame": None, "origin": origin, "fresh": list(fresh)}


# -- what every poll records -------------------------------------------------------------------------------------------------------

def test_every_poll_records_the_dealer_slots_with_what_the_data_path_carries(boxes):
    rows = [row("dealer_1", "7C", "7C", boxes["dealer_1"], ink="black"), row("dealer_2", "9D", "9C", boxes["dealer_2"],
                                                                                 region_source="carried from an earlier layout")]
    line = rd.ReadWatch().observe(12.5, 7, payload_of(rows), extras_of(None, fresh=["dealer_1"]))
    assert line["poll"] == 0 and set(line["slots"]) == {"dealer_1", "dealer_2"} and line["mismatch"] == ["dealer_2"]
    one, two = line["slots"]["dealer_1"], line["slots"]["dealer_2"]
    assert (one["raw"], one["memory"], one["agrees"], one["ink"]) == ("7C", "7C", True, "black")
    assert (two["raw"], two["memory"], two["agrees"]) == ("9D", "9C", False)
    assert two["confidence"] == 0.91 and two["suit_margin"] == 0.085 and two["rank_margin"] == 0.36
    assert two["box"] == [962, 307, 98, 122] and two["box_source"] == "carried from an earlier layout"
    assert (one["read_this_poll"], two["read_this_poll"]) == (True, False)       # only dealer_1 was read afresh
    assert two["dealer_face"] == [0, 4, 98, 107] and two["status"] == "CONFIRMED" and two["shown"] == "9C"
    assert two["memory_readings"] == 3 and two["memory_best"] == 0.93 and two["flags"] == ["MEMORY MISMATCH"]
    assert two["ink"] == UNAVAILABLE                      # the tracker was not asked for the working: said so, not guessed


def test_the_poll_index_counts_up_and_the_line_survives_json():
    watch = rd.ReadWatch()
    lines = [watch.observe(float(i), 7, payload_of([row("dealer_1", "7C", "7C")]), None) for i in range(3)]
    assert [line["poll"] for line in lines] == [0, 1, 2]
    assert json.loads(json.dumps(lines[0]))["slots"]["dealer_1"]["raw"] == "7C"


def test_numpy_values_in_an_update_still_write_as_json():
    rows = [row("dealer_1", "7C", "7C", confidence=np.float32(0.9123456), memory_readings=np.int64(3))]
    reads = {"dealer_1": read("7C", confidence=np.float32(0.9123456), rank_margin=np.float64(0.36))}
    line = rd.ReadWatch().observe(1.0, 7, payload_of(rows, reads=reads), None)
    assert json.loads(json.dumps(line))["slots"]["dealer_1"]["confidence"] == pytest.approx(0.9123, abs=1e-4)


# -- what is missing is said to be missing -----------------------------------------------------------------------------------------

def test_a_field_the_data_path_does_not_carry_is_unavailable_and_never_guessed():
    line = rd.ReadWatch().observe(1.0, 7, {"state": "RIVER"}, {})
    assert line["unavailable"] == "no per-slot diagnostics in this update"
    for slot in ("dealer_1", "dealer_2"):
        shown = line["slots"][slot]
        for key in ("raw", "read_as", "confidence", "rank_margin", "suit_margin", "ink", "memory", "memory_readings", "memory_best",
                    "shown", "status", "flags", "box_source", "read_this_poll", "dealer_face"):
            assert shown[key] == UNAVAILABLE, (slot, key)
        assert shown["agrees"] is None and shown["box"] is None


def test_reads_without_diagnostics_give_the_raw_reading_and_an_unavailable_memory():
    payload = {"state": "RIVER", "reads": {"dealer_1": read("7C"), "dealer_2": read("9D", confident=False)}}
    line = rd.ReadWatch().observe(1.0, 7, payload, {"fresh": None})
    assert line["slots"]["dealer_1"]["raw"] == "7C" and line["slots"]["dealer_1"]["memory"] == UNAVAILABLE
    assert line["slots"]["dealer_2"]["raw"] is None and line["slots"]["dealer_2"]["read_as"] == "9D"    # not confident: no raw card
    assert line["slots"]["dealer_1"]["agrees"] is None and line["slots"]["dealer_1"]["read_this_poll"] == UNAVAILABLE


@pytest.mark.parametrize("raw, memory", [("7C", "7C"), (None, "7C"), ("7C", None), (None, None)])
def test_agreeing_unconfident_or_unremembered_readings_are_not_disagreements(raw, memory):
    watch, saver = rd.ReadWatch(saver=Saver()), None
    line = watch.observe(1.0, 7, payload_of([row("dealer_1", raw, memory)]), extras_of(None))
    assert "mismatch" not in line and watch.entries == [] and watch.summary()["mismatch_polls"] == 0


# -- what a disagreement saves -----------------------------------------------------------------------------------------------------

def test_a_disagreement_is_saved_once_per_round_slot_and_pair_and_counted_every_time(frame, boxes):
    saver = Saver()
    watch = rd.ReadWatch(saver=saver)
    for _ in range(3):                                                    # the same flip for three polls: one piece of evidence
        watch.observe(1.0, 7, payload_of([row("dealer_2", "9D", "9C", boxes["dealer_2"])]), extras_of(frame))
    watch.observe(2.0, 7, payload_of([row("dealer_2", "9H", "9C", boxes["dealer_2"])]), extras_of(frame))   # a different reading
    watch.observe(3.0, 8, payload_of([row("dealer_2", "9D", "9C", boxes["dealer_2"])], round_id=8), extras_of(frame))   # a new round
    assert len(saver.saved) == 3
    entries = watch.summary()["entries"]
    assert [(e["round"], e["raw"], e["occurrences"], len(e["captures"])) for e in entries] == [(7, "9D", 3, 1), (7, "9H", 1, 1),
                                                                                              (8, "9D", 1, 1)]
    assert watch.summary()["mismatch_polls"] == 5 and watch.summary()["captures_saved"] == 3


def test_a_disagreement_on_any_slot_is_saved_not_just_the_dealers(frame, boxes):
    saver = Saver()
    watch = rd.ReadWatch(saver=saver)
    line = watch.observe(1.0, 7, payload_of([row("river", "4D", "4C", boxes["dealer_1"])]), extras_of(frame))
    assert line["mismatch"] == ["river"] and len(saver.saved) == 1 and saver.saved[0][2]["slot"] == "river"
    assert line["slots"]["river"]["capture"] == saver.saved[0][0]          # and the poll's line points at it


def test_the_capture_limit_stops_saving_but_every_disagreement_is_still_recorded(frame, boxes):
    saver = Saver()
    watch = rd.ReadWatch(saver=saver, max_captures=2)
    for number, memory in enumerate(("9C", "9S", "9H", "9D")):
        watch.observe(float(number), 7, payload_of([row("dealer_2", "9X", memory, boxes["dealer_2"])]), extras_of(frame))
    summary = watch.summary()
    assert len(saver.saved) == 2 and summary["captures_saved"] == 2 and summary["skipped_by_limit"] == 2
    assert summary["distinct_mismatches"] == 4 and [len(e["captures"]) for e in summary["entries"]] == [1, 1, 0, 0]


def test_with_no_saver_disagreements_are_recorded_and_nothing_is_saved(frame, boxes):
    watch = rd.ReadWatch(saver=None)
    watch.observe(1.0, 7, payload_of([row("dealer_2", "9D", "9C", boxes["dealer_2"])]), extras_of(frame))
    summary = watch.summary()
    assert summary["distinct_mismatches"] == 1 and summary["captures_saved"] == 0 and summary["capturing"] is False


def test_a_capture_holds_the_box_crop_the_wider_crop_and_what_the_trackers_own_read_path_says_of_it(frame, boxes):
    saver = Saver()
    box = boxes["dealer_1"]
    watch = rd.ReadWatch(saver=saver)
    # the tracker read 7C here and the memory holds 7S: a disagreement whose pixels really do read as 7C
    watch.observe(12.5, 7, payload_of([row("dealer_1", "7C", "7S", box)]), extras_of(frame))
    name, images, info = saver.saved[0]
    assert name == "round7_dealer_1_raw7C_mem7S_t12.5"
    x, y, w, h = box["left"], box["top"], box["width"], box["height"]
    assert np.array_equal(images["crop"], frame[y:y + h, x:x + w])
    assert images["context"].shape[0] > images["crop"].shape[0] and images["context"].shape[1] > images["crop"].shape[1]
    assert info["crop_box_in_frame"] == [x, y, x + w, y + h] and info["frame_source"] == "the frame the tracker read this poll"
    assert (info["round"], info["slot"], info["poll"], info["t"], info["state"]) == (7, "dealer_1", 0, 12.5, "RIVER")
    assert info["lifecycle"] == "IN_PROGRESS" and info["result_status"] == "none" and "T" in info["wall_clock"]
    assert info["slot_read"]["raw"] == "7C" and info["slot_read"]["memory"] == "7S"
    replay = info["replay"]
    assert replay["card"] == "7C" and replay["ink"] == "black" and replay["confident"] is True and replay["confidence"] > 0.9
    assert replay["suit_scores"][0][0] == "C" and replay["dealer_face"] is not None
    assert info["replay_reproduces_live_reading"] is True
    probe = info["colour_probe"]
    assert probe["index_strip_ink_pixels"] > 0 and probe["ink_red_like_share"] == 0.0
    assert probe["would_test_red_on_whole_strip_mask"] is False
    json.dumps(info)


def test_a_reading_the_pixels_do_not_reproduce_is_said_so(frame, boxes):
    saver = Saver()
    rd.ReadWatch(saver=saver).observe(1.0, 7, payload_of([row("dealer_1", "7D", "7C", boxes["dealer_1"])]), extras_of(frame))
    assert saver.saved[0][2]["replay"]["card"] == "7C" and saver.saved[0][2]["replay_reproduces_live_reading"] is False


def test_the_colour_probe_sees_red_ink_inside_the_index_strip(frame, boxes):
    box = boxes["dealer_1"]
    crop = frame[box["top"]:box["top"] + box["height"], box["left"]:box["left"] + box["width"]].copy()
    clean = rd.colour_probe(crop)
    nail = crop.copy()
    nail[12:30, 8:30] = (40, 40, 210)                     # a patch of red where the index is printed, on a COPY (a probe test, not evidence)
    marked = rd.colour_probe(nail)
    assert marked["crop_red_like_share"] > clean["crop_red_like_share"] + 0.02        # the patch is 396 of 11,956 pixels
    assert marked["ink_red_like_share"] > clean["ink_red_like_share"]


def test_the_crop_follows_the_frames_origin(boxes):
    frame = np.zeros((300, 400, 3), np.uint8)
    frame[100:140, 150:200] = (10, 200, 30)               # the box, once the monitor's origin (-100, -50) is taken off
    saver = Saver()
    region = {"left": 50, "top": 50, "width": 50, "height": 40}
    watch = rd.ReadWatch(saver=saver)
    watch.observe(1.0, 7, payload_of([row("dealer_2", "9D", "9C", region)]), extras_of(frame, origin=(-100, -50)))
    _, images, info = saver.saved[0]
    assert info["crop_box_in_frame"] == [150, 100, 200, 140] and (images["crop"] == (10, 200, 30)).all()
    assert info["replay"]["card"] is None and info["replay"]["confident"] is False      # not a card: the replay says so
    assert "crop_red_like_share" in info["colour_probe"] and watch.errors == 0 and info["replay_reproduces_live_reading"] is False


def test_a_capture_without_a_frame_or_a_box_keeps_the_metadata_and_says_what_is_missing(frame, boxes):
    saver = Saver()
    watch = rd.ReadWatch(saver=saver)
    watch.observe(1.0, 7, payload_of([row("dealer_2", "9D", "9C", boxes["dealer_2"])]), {"fresh": []})
    watch.observe(2.0, 7, payload_of([row("dealer_1", "7D", "7C", None)]), extras_of(frame))
    assert [info["crop"].split(":")[0] for _, _, info in saver.saved] == [UNAVAILABLE, UNAVAILABLE]
    assert all(images == {} for _, images, _ in saver.saved) and saver.saved[0][2]["frame_source"] == UNAVAILABLE


def test_without_the_ticks_own_frame_the_latest_grab_is_used_and_labelled(frame, boxes):
    saver = Saver()
    rd.ReadWatch(saver=saver).observe(1.0, 7, payload_of([row("dealer_1", "7C", "7S", boxes["dealer_1"])]),
                                      {"frame": frame, "origin": (0, 0), "fresh": ["dealer_1"]})
    assert "latest grab" in saver.saved[0][2]["frame_source"] and "crop_box_in_frame" in saver.saved[0][2]


# -- evidence is never overwritten ------------------------------------------------------------------------------------------------

def test_the_harness_saver_writes_a_new_folder_and_never_over_a_file_that_is_there(tmp_path):
    harness = load_harness()
    saver = harness.ReadSaver(str(tmp_path))
    crop = np.full((20, 10, 3), 7, np.uint8)
    assert not (tmp_path / "read_mismatches").exists()                    # made on the first save only
    first = saver("round7_dealer_2_rawAD_memAC_t5.0", {"crop": crop, "context": crop}, {"a": 1})
    folder = tmp_path / "read_mismatches"
    assert first == "round7_dealer_2_rawAD_memAC_t5.0"
    assert sorted(os.listdir(folder)) == sorted([first + "_context.png", first + "_crop.png", first + ".json"])
    before = {name: (folder / name).read_bytes() for name in os.listdir(folder)}
    second = saver("round7_dealer_2_rawAD_memAC_t5.0", {"crop": crop + 1, "context": crop + 1}, {"a": 2})
    third = saver("round7_dealer_2_rawAD_memAC_t5.0", {"crop": crop + 2}, {"a": 3})
    assert second == first + "_2" and third == first + "_3"
    assert all((folder / name).read_bytes() == data for name, data in before.items())            # the first save is untouched
    assert json.load(open(folder / (second + ".json")))["files"] == [second + "_crop.png", second + "_context.png"]


def test_a_file_that_is_already_there_under_any_of_the_three_names_is_not_overwritten(tmp_path):
    harness = load_harness()
    folder = tmp_path / "read_mismatches"
    folder.mkdir()
    (folder / "x_context.png").write_bytes(b"precious")
    name = harness.ReadSaver(str(tmp_path))("x", {"crop": np.zeros((4, 4, 3), np.uint8), "context": np.zeros((4, 4, 3), np.uint8)}, {})
    assert name == "x_2" and (folder / "x_context.png").read_bytes() == b"precious"


def test_the_harness_takes_a_capture_limit_and_zero_means_none():
    harness = load_harness()
    assert harness.arguments([]).max_read_captures == 40
    assert harness.arguments(["--max-read-captures", "0"]).max_read_captures == 0
    assert harness.arguments(["--max-read-captures", "5"]).max_read_captures == 5


# -- it changes nothing ------------------------------------------------------------------------------------------------------------

def test_the_update_and_the_frame_are_left_exactly_as_they_were(frame, boxes):
    own = frame.copy()
    own.flags.writeable = False                                           # a write to the frame would raise
    rows = [row("dealer_1", "7C", "7S", boxes["dealer_1"]), row("dealer_2", "9C", "9C", boxes["dealer_2"])]
    payload, extras = payload_of(rows), extras_of(own)
    before = (copy.deepcopy(payload), copy.deepcopy({k: v for k, v in extras.items() if k != "read_frame"}), own.tobytes())
    saver = Saver()
    watch = rd.ReadWatch(saver=saver)
    watch.observe(1.0, 7, payload, extras)
    assert watch.errors == 0 and len(saver.saved) == 1
    assert payload == before[0] and {k: v for k, v in extras.items() if k != "read_frame"} == before[1] and own.tobytes() == before[2]


def test_the_recognisers_answer_for_the_same_pixels_is_the_same_after_a_capture(frame, boxes):
    box = boxes["dealer_1"]
    crop = frame[box["top"]:box["top"] + box["height"], box["left"]:box["left"] + box["width"]].copy()
    answer = card_recognizer.read_slot(crop)
    rd.ReadWatch(saver=Saver()).observe(1.0, 7, payload_of([row("dealer_1", "7C", "7S", box)]), extras_of(frame))
    assert card_recognizer.read_slot(crop) == answer
    # the policy's own numbers, as written down - not a snapshot taken here, which an earlier test could already have disturbed
    assert (card_recognizer.SUIT_MARGIN, card_recognizer.RANK_MARGIN, card_recognizer.CORROBORATED_SUIT_MARGIN) == (0.05, 0.10, 0.02)
    assert (card_recognizer.SHAPE_SPADE_MIN, card_recognizer.SHAPE_CLUB_MAX) == (0.38, 0.22)


def test_no_recognition_runs_on_an_ordinary_poll_and_one_read_runs_per_capture(frame, boxes, monkeypatch):
    calls = []
    real = card_recognizer.read_slot
    monkeypatch.setattr(card_recognizer, "read_slot", lambda *a, **k: calls.append(1) or real(*a, **k))
    watch = rd.ReadWatch(saver=Saver())
    for _ in range(5):                                                    # agreeing polls: the watch does no recognition at all
        watch.observe(1.0, 7, payload_of([row("dealer_1", "7C", "7C", boxes["dealer_1"])]), extras_of(frame))
    assert calls == []
    for _ in range(4):                                                    # one flip, four polls: one capture, one replayed read
        watch.observe(2.0, 7, payload_of([row("dealer_1", "7C", "7S", boxes["dealer_1"])]), extras_of(frame))
    assert len(calls) == 1


def test_the_recorder_gives_the_same_rounds_and_polls_with_or_without_the_watch(run):
    payloads = []
    for table, mode, polls in ((BUILD_UP, "missing", 3), (FULL, "partial", 3), (FULL, "agree", 4)):
        run.panel_mode = mode
        for _ in range(polls):
            payloads.append(run.poll(table))
    for number, payload in enumerate(payloads):
        payload["emitted_mono"] = 100.0 + number
    originals = copy.deepcopy(payloads)
    plain, watched = rv.RoundRecorder(), rv.RoundRecorder(read_watch=rd.ReadWatch(saver=Saver()))
    for payload in payloads:
        plain.observe(payload)
    for payload in payloads:
        watched.observe(payload, {"fresh": [], "origin": (0, 0)})
    assert payloads == originals                                          # no update was touched
    assert plain.finish() == watched.finish()
    strip = lambda lines: [{k: v for k, v in line.items() if k != "slot_reads"} for line in lines]
    assert strip(plain.polls) == strip(watched.polls)
    assert all("slot_reads" in line for line in watched.polls) and not any("slot_reads" in line for line in plain.polls)


def test_the_watch_imports_nothing_that_captures_sleeps_writes_or_phones_home():
    source = open(os.path.join(ROOT, "read_diagnostics.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0] + "." + ".".join(alias.name for alias in node.names))
    forbidden = {"time", "threading", "socket", "requests", "urllib", "mss", "pyautogui", "pynput", "subprocess", "tracker", "app",
                 "capture", "psycopg", "sqlite3", "webbrowser", "win32api", "ctypes", "keyboard", "os"}
    assert not {name for name in imported if name.split(".")[0] in forbidden}, imported
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree)
                                                                                 if isinstance(node, ast.Attribute)}
    for banned in ("sleep", "imwrite", "grab", "grab_full_screen", "open", "click", "press", "write", "makedirs", "environ", "connect"):
        assert banned not in names, banned


# -- a failure in it never stops a run --------------------------------------------------------------------------------------------

def test_a_saver_that_raises_costs_the_capture_and_nothing_else(frame, boxes):
    def broken(name, images, info):
        raise OSError("disk full")
    watch = rd.ReadWatch(saver=broken)
    line = watch.observe(1.0, 7, payload_of([row("dealer_2", "9D", "9C", boxes["dealer_2"])]), extras_of(frame))
    assert line["mismatch"] == ["dealer_2"] and "capture" not in line["slots"]["dealer_2"]
    summary = watch.summary()
    assert summary["captures_saved"] == 0 and summary["diagnostic_errors"] == 1 and "disk full" in summary["first_error"]
    assert watch.observe(2.0, 7, payload_of([row("dealer_2", "9D", "9C", boxes["dealer_2"])]), extras_of(frame))["poll"] == 1


def test_a_replay_that_raises_still_saves_the_crop(frame, boxes, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("templates missing")
    monkeypatch.setattr(card_recognizer, "read_slot", broken)
    saver = Saver()
    watch = rd.ReadWatch(saver=saver)
    watch.observe(1.0, 7, payload_of([row("dealer_1", "7C", "7S", boxes["dealer_1"])]), extras_of(frame))
    _, images, info = saver.saved[0]
    assert "crop" in images and info["replay"]["error"].startswith("RuntimeError") and "colour_probe" in info
    assert watch.errors == 1 and "replay_reproduces_live_reading" not in info


@pytest.mark.parametrize("payload", [None, {}, {"diagnostics": None}, {"diagnostics": {"slots": None}},
                                     {"diagnostics": {"slots": [None, "x", 3, {"slot": "dealer_1"}]}, "reads": None},
                                     {"diagnostics": {"slots": [{"slot": "dealer_1", "raw_card": "7C", "memory_card": "7S",
                                                                  "region": {"left": "x"}}]}}])
def test_malformed_updates_give_a_line_and_never_raise(payload, frame):
    line = rd.ReadWatch(saver=Saver()).observe(1.0, 7, payload, extras_of(frame))
    assert line["poll"] == 0 and ("slots" in line or "error" in line)


def test_a_failure_inside_the_watch_is_counted_and_returned_as_a_line(monkeypatch):
    def broken(*a, **k):
        raise KeyError("a field nobody expected")
    watch = rd.ReadWatch(saver=Saver())
    monkeypatch.setattr(rd, "slot_line", broken)
    line = watch.observe(1.0, 7, payload_of([row("dealer_1", "7C", "7C")]), None)
    assert line["poll"] == 0 and line["error"].startswith("KeyError")
    assert watch.summary()["diagnostic_errors"] == 1 and "KeyError" in watch.summary()["first_error"]
    monkeypatch.undo()
    assert "error" not in watch.observe(2.0, 7, payload_of([row("dealer_1", "7C", "7C")]), None)       # and the next poll is fine


def test_a_watch_that_blows_up_does_not_stop_the_recorder(run, monkeypatch):
    class Exploding:
        def observe(self, *a, **k):
            raise RuntimeError("boom")

        def summary(self):
            return {}

    recorder = rv.RoundRecorder(read_watch=Exploding())
    run.panel_mode = "missing"
    payload = run.poll(BUILD_UP)
    payload["emitted_mono"] = 1.0
    line = recorder.observe(payload, {"fresh": []})
    assert line["slot_reads"]["error"].startswith("RuntimeError") and recorder.polls == [line]


# -- through the recorder and onto disk ---------------------------------------------------------------------------------------------

def test_the_recorder_puts_the_dealer_line_in_every_poll_and_the_summary_in_the_evidence(run, tmp_path):
    watch = rd.ReadWatch(saver=Saver())
    recorder = rv.RoundRecorder(read_watch=watch)
    for number in range(4):
        run.panel_mode = "missing"
        payload = run.poll(BUILD_UP)
        payload["emitted_mono"] = 100.0 + number
        recorder.observe(payload, {"fresh": ["dealer_1"], "origin": (0, 0)})
    assert all(set(line["slot_reads"]["slots"]) == {"dealer_1", "dealer_2"} for line in recorder.polls)
    folder = tmp_path / "evidence"
    rv.write_evidence(str(folder), recorder, {"mode": "test"})
    assert sorted(os.listdir(folder)) == ["polls.jsonl", "rounds.json", "summary.json"]
    summary = json.load(open(folder / "summary.json"))
    assert summary["read_evidence"]["polls_watched"] == 4 and summary["read_evidence"]["diagnostic_errors"] == 0
    assert all("slot_reads" in json.loads(line) for line in open(folder / "polls.jsonl"))


def test_a_recorder_without_a_read_watch_writes_exactly_what_it_wrote_before(run, tmp_path):
    recorder = rv.RoundRecorder()
    run.panel_mode = "missing"
    payload = run.poll(BUILD_UP)
    payload["emitted_mono"] = 1.0
    recorder.observe(payload, {"fresh": []})
    rv.write_evidence(str(tmp_path / "e"), recorder)
    assert "read_evidence" not in json.load(open(tmp_path / "e" / "summary.json"))
    assert "slot_reads" not in json.loads(open(tmp_path / "e" / "polls.jsonl").readline())


def test_the_harness_notes_the_ticks_frame_without_touching_the_read():
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    wrapper = source.split("def read_table_noted():")[1].split("tracker._read_table = read_table_noted")[0]
    assert "result = read_table()" in wrapper and wrapper.rstrip().endswith("return result")
    assert wrapper.count("result =") == 1 and wrapper.count("result") == 2      # assigned once from the real read, returned as it came
    assert "tracker._frame =" not in wrapper and "tracker._timing[" not in wrapper and ".clear()" in wrapper.split("except")[1]
    assert "tracker._frame" in wrapper and "tracker._timing.get(\"fresh\")" in wrapper
    assert "grab" not in wrapper and "sleep" not in wrapper and "imwrite" not in wrapper
    assert 'ReadSaver(evidence) if args.max_read_captures > 0 else None' in source


SCREENSHOT = os.path.join(ROOT, "Screenshot 2026-09-02 152130.png")


@pytest.mark.skipif(not os.path.exists(SCREENSHOT), reason="no casino screenshot in the project folder")
def test_the_harness_replay_keeps_its_results_and_adds_the_dealer_lines_and_no_capture_without_a_disagreement(tmp_path):
    out = tmp_path / "evidence"
    result = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "validate_rounds.py"), "--replay", SCREENSHOT,
                             "--replay-phases", "7,3", "--out", str(out)], capture_output=True, text=True, cwd=ROOT, timeout=240)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    summary = json.load(open(out / "summary.json"))
    assert summary["rounds_completed"] == 1 and summary["rounds_confirmed"] == 1 and summary["alert_without_confirmed_result"] == 0
    evidence = summary["read_evidence"]
    assert evidence["diagnostic_errors"] == 0 and evidence["capturing"] is True and evidence["polls_watched"] == summary["run"]["updates"]
    assert evidence["distinct_mismatches"] == 0                                          # a still table never disagrees with itself
    memory = evidence["memory_events"]                                                   # but its memory is seeded, and the dealer's seeds are kept
    assert memory["events"] > 0 and memory["seeds"] > 0 and memory["captures_saved"] > 0 and memory["seeds_whose_final_value_differs"] == 0
    names = os.listdir(out / "read_mismatches")
    assert names and not any("_raw" in name for name in names) and all("_seed" in name or "_switch" in name for name in names)
    assert all(e["slot"].startswith("dealer") or (e["confidence"] is not None and e["confidence"] < 0.8) or e["skipped"] is not None
               for e in memory["entries"] if e["capture"])
    lines = [json.loads(line) for line in open(out / "polls.jsonl")]
    boxed = [line["slot_reads"]["slots"]["dealer_1"] for line in lines if line["slot_reads"]["slots"]["dealer_1"]["box"]]
    assert boxed and all(shown["agrees"] in (True, None) and shown["read_this_poll"] in (True, False) for shown in boxed)
    assert any(shown["raw"] and shown["raw"] == shown["memory"] for shown in boxed)
    assert "## Raw readings that disagreed with the card memory" in open(out / "report.md", encoding="utf-8").read()
