"""The harness's per-poll timing, machine-load, crop-stability and table-loss evidence (poll_diagnostics.py).

Why: a practice session (PHT-validation-20261019-193150) had a median in-round tick of 578 ms, a largest poll gap of 2.24 s, every crop re-read on every
tick, and two rounds in which the dealer's cards were read on ONE poll before the table was lost within a second - and kept nothing that could say why:
timings only as 20-tick windows, no machine load, no measure of how much a card's pixels move between polls, no frame of the table being lost. These
tests pin what is recorded for each, that each part fails alone, that the evidence is bounded and never overwritten, and that none of it can change
what the tracker reads or decides.
"""

import ast
import copy
import importlib.util
import json
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import poll_diagnostics as pdx                                    # noqa: E402
import round_validation as rv                                     # noqa: E402

from test_round_lifecycle import FLOP, FULL, PLAYER, RIVER, TURN, run   # noqa: E402,F401 - the run fixture

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_UP = {**PLAYER, **FLOP, **TURN, **RIVER}


def load_harness():
    spec = importlib.util.spec_from_file_location("validate_rounds_poll", os.path.join(ROOT, "tools", "validate_rounds.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grey_frame(value=100, height=240, width=320):
    return np.full((height, width, 3), value, np.uint8)


def row(slot, left=10, top=20, width=50, height=60):
    return {"slot": slot, "region": {"left": left, "top": top, "width": width, "height": height}}


def payload_of(state="RIVER", lifecycle="IN_PROGRESS", seen=None, rows=None, reads=None, timing=None, round_id=7):
    return {"state": state, "lifecycle": {"state": lifecycle, "round_id": round_id, "result_status": "none"}, "seen": dict(seen or {}),
            "reads": dict(reads or {}), "diagnostics": {"slots": list(rows or [])}, "timing": dict(timing or {})}


def extras_of(frame=None, layout="dynamic", fresh=("dealer_1", "dealer_2")):
    return {"read_frame": frame, "origin": (0, 0), "layout_source": layout, "fresh": list(fresh)}


class Saver:
    def __init__(self):
        self.saved = []

    def __call__(self, name, images, info):
        self.saved.append((name, images, info))
        return name


# -- machine load -------------------------------------------------------------------------------------------------------------------

def sampler(times, processes, systems, cpus=4):
    clocks, procs, syss = iter(times), iter(processes), iter(systems)
    return pdx.CpuSampler(system_times=lambda: next(syss), clock=lambda: next(clocks), process_time=lambda: next(procs), cpus=cpus)


def test_the_cpu_percentages_are_the_machines_busy_share_and_this_processs_share_of_all_processors():
    s = sampler([0.0, 1.0, 3.0], [0.0, 0.5, 1.5], [(0, 0, 0), (600, 1000, 0), (700, 2000, 0)])
    assert s.sample() is None                                                              # nothing to compare with yet
    assert s.sample() == {"system": 40.0, "process": 12.5}                                 # 600 of 1000 ticks idle; 0.5 s of CPU in 1 s on 4 cpus
    assert s.sample() == {"system": 90.0, "process": 12.5}                                 # 100 of 1000 idle; 1.0 s of CPU in 2 s on 4 cpus


def test_a_machine_that_cannot_be_asked_still_gives_the_process_share():
    s = sampler([0.0, 2.0], [0.0, 2.0], [None, None], cpus=2)
    s.sample()
    assert s.sample() == {"system": None, "process": 50.0}


@pytest.mark.parametrize("systems", [[(0, 0, 0), (0, 0, 0)], [(500, 900, 100), (100, 200, 50)], [(0, 0, 0), None], [None, (0, 1, 1)]])
def test_counters_that_do_not_move_or_run_backwards_or_are_missing_give_no_system_figure(systems):
    s = sampler([0.0, 1.0], [0.0, 0.1], systems)
    s.sample()
    assert s.sample()["system"] is None


def test_an_idle_counter_that_runs_ahead_of_the_total_reads_zero_not_negative():
    s = sampler([0.0, 1.0], [0.0, 0.1], [(0, 0, 0), (1500, 600, 400)], cpus=4)             # 1500 idle ticks of 1000: the counters disagree
    s.sample()
    assert s.sample()["system"] == 0.0


def test_no_time_elapsed_gives_no_answer_and_percentages_are_clamped():
    s = sampler([5.0, 5.0, 6.0], [0.0, 0.0, 9.0], [(0, 0, 0), (0, 10, 0), (0, 20, 0)], cpus=1)
    s.sample()
    assert s.sample() is None
    assert s.sample() == {"system": 100.0, "process": 100.0}                               # 9 s of CPU in 1 s on 1 cpu cannot read above 100


def test_a_clock_that_raises_gives_none_never_an_exception():
    def broken():
        raise OSError("no clock")
    assert pdx.CpuSampler(clock=broken).sample() is None
    assert pdx.CpuSampler(process_time=broken).sample() is None


@pytest.mark.skipif(os.name != "nt", reason="the machine-wide CPU counters are read through the Windows API")
def test_the_real_windows_counters_give_sensible_numbers():
    first = pdx.windows_system_times()
    assert first is not None and len(first) == 3 and all(isinstance(v, int) and v >= 0 for v in first)
    s = pdx.CpuSampler()
    s.sample()
    total = 0
    for number in range(400000):
        total += number * number                                                           # a moment of work for the process share to be above zero
    sample = s.sample()
    assert sample is not None and 0.0 <= sample["system"] <= 100.0 and 0.0 <= sample["process"] <= 100.0 and total > 0
    later = pdx.windows_system_times()
    assert later[1] >= first[1] and later[2] >= first[2]


def test_off_windows_the_machine_counters_are_unavailable(monkeypatch):
    monkeypatch.setattr(pdx.os, "name", "posix")
    assert pdx.windows_system_times() is None


# -- stage timing ----------------------------------------------------------------------------------------------------------------------

def test_the_trackers_own_stage_timings_are_kept_with_the_names_the_timing_lines_use():
    timing = {"total": 580.04, "capture": 59.96, "roi": 93.2, "crop": 0.4, "recognition": 398.7, "memory": 1.0, "gate": 0.1, "panel": 17.5,
              "diagnostics": 3.3, "screen_diff": 2.345, "table_diff": 13.0, "still": 0}
    fields = pdx.timing_fields({"timing": timing}, {"fresh": ["a", "b", "c"]})
    assert fields == {"total": 580.0, "capture": 60.0, "locate": 93.2, "crop": 0.4, "read": 398.7, "memory": 1.0, "gate": 0.1, "panel": 17.5,
                      "diagnostics": 3.3, "screen_diff": 2.35, "table_diff": 13.0, "fresh": 3}


@pytest.mark.parametrize("payload, extras", [(None, None), ({}, {}), ({"timing": None}, {"fresh": None}), ({"timing": "x"}, {}), ({"timing": {"total": "slow"}}, {})])
def test_a_timing_the_update_does_not_carry_is_none_and_never_a_guess(payload, extras):
    fields = pdx.timing_fields(payload, extras)
    assert set(fields) >= {"total", "capture", "locate", "read", "panel", "fresh", "screen_diff", "table_diff"} and all(v is None for v in fields.values())


# -- how much a card's pixels move between polls ---------------------------------------------------------------------------------------

def test_identical_crops_are_zero_change_and_a_uniform_shift_is_exactly_that_many_grey_levels():
    watch, rows = pdx.CropChangeWatch(), [row("flop_1"), row("dealer_1", 100, 30, 40, 50)]
    assert watch.observe(grey_frame(100), (0, 0), rows) == {"flop_1": None, "dealer_1": None}      # the first poll has nothing to compare with
    assert watch.observe(grey_frame(100), (0, 0), rows) == {"flop_1": 0.0, "dealer_1": 0.0}
    assert watch.observe(grey_frame(103), (0, 0), rows) == {"flop_1": 3.0, "dealer_1": 3.0}
    assert watch.exact["flop_1"] == 1 and watch.compared["flop_1"] == 2


def test_only_the_changed_part_of_a_crop_counts_and_the_mean_is_over_the_whole_crop():
    watch, rows = pdx.CropChangeWatch(), [row("river", 0, 0, 10, 10)]
    first = grey_frame(100)
    second = first.copy()
    second[0:5, 0:10] = 140                                                                # half the crop moved by 40 levels
    watch.observe(first, (0, 0), rows)
    assert watch.observe(second, (0, 0), rows) == {"river": 20.0}


def test_a_box_that_moves_or_resizes_or_vanishes_is_not_compared_with_what_was_there_before():
    watch = pdx.CropChangeWatch()
    watch.observe(grey_frame(100), (0, 0), [row("turn", 10, 10, 40, 40)])
    assert watch.observe(grey_frame(100), (0, 0), [row("turn", 10, 10, 41, 40)]) == {"turn": None}          # resized
    assert watch.shape_changed["turn"] == 1
    assert watch.observe(grey_frame(100), (0, 0), []) == {}                                                 # no box this poll
    assert watch.observe(grey_frame(100), (0, 0), [row("turn", 10, 10, 41, 40)]) == {"turn": None}          # back: nothing stale to compare with
    assert watch.observe(grey_frame(100), (0, 0), [row("turn", 10, 10, 41, 40)]) == {"turn": 0.0}


@pytest.mark.parametrize("frame, rows", [(None, [row("flop_1")]), (grey_frame(), None), (grey_frame(), [None, "x", {"slot": "flop_1"}, {"slot": "nope", "region": {}}]),
                                         (grey_frame(), [row("flop_1", 5000, 5000)]), (grey_frame(), [row("flop_1", 10, 10, 0, 0)]),
                                         (grey_frame(), [{"slot": "flop_1", "region": {"left": "x", "top": 1, "width": 2, "height": 3}}])])
def test_missing_or_impossible_inputs_give_no_entries_and_never_raise(frame, rows):
    assert pdx.CropChangeWatch().observe(frame, (0, 0), rows) == {}


def test_the_origin_is_taken_off_the_boxes_like_the_trackers_own_crop():
    frame = grey_frame(50)
    frame[100:140, 150:200] = 200
    watch = pdx.CropChangeWatch()
    rows = [row("flop_2", 50, 50, 50, 40)]                                                  # screen coordinates; the monitor's origin is (-100, -50)
    watch.observe(frame, (-100, -50), rows)
    shifted = frame.copy()
    shifted[100:140, 150:200] = 210
    assert watch.observe(shifted, (-100, -50), rows) == {"flop_2": 10.0}


def test_the_summary_says_how_often_a_crop_is_identical_small_or_large():
    watch, rows = pdx.CropChangeWatch(), [row("flop_3")]
    base = 100
    watch.observe(grey_frame(base), (0, 0), rows)
    for shift in (0, 0, 1, 3, 9):                                                           # successive changes of 0, 0, 1, 2, 6 ... built from the running level
        base += shift
        watch.observe(grey_frame(base), (0, 0), rows)
    summary = watch.summary()["flop_3"]
    assert summary["comparisons"] == 5 and summary["share_identical"] == 0.4
    assert summary["median"] == 1.0 and summary["p90"] == 9.0
    assert (summary["share_le_0.5"], summary["share_le_1.0"], summary["share_le_2.0"], summary["share_le_4.0"], summary["share_le_8.0"]) == (0.4, 0.6, 0.6, 0.8, 0.8)


def test_values_kept_for_the_percentiles_are_bounded_but_every_comparison_is_counted(monkeypatch):
    monkeypatch.setattr(pdx, "CROP_DIFF_LIMIT", 3)
    watch, rows = pdx.CropChangeWatch(), [row("flop_1")]
    for level in range(100, 112):
        watch.observe(grey_frame(level), (0, 0), rows)
    assert len(watch.values["flop_1"]) == 3 and watch.compared["flop_1"] == 11 and watch.summary()["flop_1"]["comparisons"] == 11


def test_the_frame_is_left_alone_and_the_previous_crop_is_a_copy():
    frame = grey_frame(100)
    frame.flags.writeable = False                                                           # a write would raise
    watch, rows = pdx.CropChangeWatch(), [row("flop_1")]
    watch.observe(frame, (0, 0), rows)
    live = grey_frame(100)
    watch.observe(live, (0, 0), rows)
    live[:] = 250                                                                           # the tracker's frame changing afterwards...
    assert watch.observe(grey_frame(100), (0, 0), rows) == {"flop_1": 0.0}                  # ...does not reach what was remembered


# -- the dealer is read, the round does not complete, the table is lost --------------------------------------------------------------------

DEALER_ROWS = [row("dealer_1", 100, 30, 40, 50), row("dealer_2", 150, 30, 40, 50)]


def step(watch, t, round_id=7, state="RIVER", lifecycle="IN_PROGRESS", seen=None, layout="dynamic", frame=None, rows=DEALER_ROWS):
    frame = grey_frame(20 + int(t * 10) % 200) if frame is None else frame          # a level that differs poll to poll and fits a byte
    record = {"t": t, "state": state, "layout": layout}
    return watch.observe(t, round_id, payload_of(state, lifecycle, seen, rows, {"dealer_1": {"confidence": 0.8487}, "dealer_2": {"confidence": 0.9321}}, round_id=round_id),
                         extras_of(frame, layout), record)


def test_the_round_48_pattern_is_captured_the_dealer_read_on_one_poll_then_the_table_lost_within_a_second():
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    assert step(watch, 392.4, seen={}) is None
    assert step(watch, 393.11, seen={"dealer_1": "5H", "dealer_2": "2H"}) is None
    incident = step(watch, 393.81, layout="cached")
    assert incident["round"] == 7 and incident["seen_t"] == 393.11 and incident["lost_t"] == 393.81 and incident["seconds_between"] == 0.7
    assert incident["state_at_seen"] == "RIVER" and incident["layout_at_lost"] == "cached" and incident["lifecycle_at_lost"] == "IN_PROGRESS"
    assert incident["dealer_reads_at_seen"] == {"dealer_1": {"card": "5H", "confidence": 0.8487}, "dealer_2": {"card": "2H", "confidence": 0.9321}}
    name, images, info = saver.saved[0]
    assert name == "round7_dealer_seen_t393.1_table_lost_t393.8" and incident["capture"] == name
    assert set(images) == {"seen_frame", "lost_frame", "dealer_1", "dealer_2"}
    assert images["seen_frame"].shape == (240, 320, 3) and int(images["seen_frame"][0, 0, 0]) == 20 + 3931 % 200 and int(images["lost_frame"][0, 0, 0]) == 20 + 3938 % 200
    assert images["dealer_1"].shape == (50, 40, 3) and images["dealer_2"].shape == (50, 40, 3)
    assert info["kind"] == "dealer_read_then_table_lost" and [e["t"] for e in info["timeline"]] == [392.4, 393.11, 393.81]
    json.dumps(info, default=str)


def test_the_round_0_pattern_is_captured_when_the_table_goes_a_second_and_a_half_later():
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    step(watch, 16.33, seen={})
    step(watch, 18.11, seen={"dealer_1": "2H", "dealer_2": "5H"})
    assert step(watch, 18.99, seen={}) is None                                             # the table still found: nothing yet
    incident = step(watch, 19.55, layout="lost")
    assert incident["seconds_between"] == 1.44 and incident["layout_at_lost"] == "lost" and len(saver.saved) == 1


@pytest.mark.parametrize("scenario", ["completes_on_the_dealer_poll", "completes_later", "table_lost_too_late", "table_never_lost", "dealer_never_read",
                                      "another_round", "layout_still_dynamic"])
def test_nothing_is_captured_when_it_is_not_that_case(scenario):
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    if scenario == "completes_on_the_dealer_poll":
        step(watch, 10.0, state="COMPLETE", lifecycle="COMPLETE", seen={"dealer_1": "7S", "dealer_2": "8D"})
        step(watch, 10.6, state="COMPLETE", lifecycle="COMPLETE", layout="cached")
    elif scenario == "completes_later":
        step(watch, 10.0, seen={"dealer_1": "7S", "dealer_2": "8D"})
        step(watch, 10.4, state="COMPLETE", lifecycle="COMPLETE", seen={"dealer_1": "7S", "dealer_2": "8D"})
        step(watch, 11.0, state="COMPLETE", lifecycle="COMPLETE", layout="cached")
    elif scenario == "table_lost_too_late":
        step(watch, 10.0, seen={"dealer_1": "7S"})
        step(watch, 12.3, layout="cached")                                                  # 2.3 s later: more than the window
    elif scenario == "table_never_lost":
        for t in (10.0, 10.5, 11.0, 12.0, 14.0):
            step(watch, t, seen={"dealer_1": "7S"})
    elif scenario == "dealer_never_read":
        step(watch, 10.0, seen={})
        step(watch, 10.5, layout="cached")
    elif scenario == "another_round":
        step(watch, 10.0, round_id=7, seen={"dealer_1": "7S"})
        step(watch, 10.5, round_id=8, layout="cached")
    elif scenario == "layout_still_dynamic":
        step(watch, 10.0, seen={"dealer_1": "7S"})
        step(watch, 10.5, layout="dynamic")
    assert saver.saved == [] and watch.incidents == []


def test_a_dealer_read_on_the_very_poll_the_table_was_lost_does_not_arm_the_watch():
    watch = pdx.ExposureWatch(saver=Saver())
    assert step(watch, 10.0, seen={"dealer_1": "7S"}, layout="cached") is None
    assert step(watch, 10.4, layout="cached") is None and watch.incidents == []


def test_the_latest_dealer_poll_is_the_one_that_counts_and_the_window_runs_from_it():
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    for t in (10.0, 10.6, 11.2, 11.8):
        step(watch, t, seen={"dealer_1": "7S"})
    incident = step(watch, 13.0, layout="cached")                                           # 3.0 s after the first read but 1.2 s after the last
    assert incident["seen_t"] == 11.8 and incident["seconds_between"] == 1.2 and watch.watched == 4


def test_one_incident_per_round_and_a_new_round_gets_its_own():
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    step(watch, 10.0, round_id=7, seen={"dealer_1": "7S"})
    assert step(watch, 10.5, round_id=7, layout="cached") is not None
    step(watch, 10.8, round_id=7, seen={"dealer_1": "7S"})
    assert step(watch, 11.2, round_id=7, layout="cached") is None                          # the same round: not again
    step(watch, 50.0, round_id=8, seen={"dealer_1": "9C"})
    assert step(watch, 50.4, round_id=8, layout="cached") is not None
    assert [i["round"] for i in watch.incidents] == [7, 8] and len(saver.saved) == 2


def test_the_run_is_bounded_every_incident_is_listed_and_the_rest_are_counted():
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver, max_incidents=2)
    for round_id in (1, 2, 3, 4):
        step(watch, round_id * 10.0, round_id=round_id, seen={"dealer_1": "7S"})
        step(watch, round_id * 10.0 + 0.5, round_id=round_id, layout="cached")
    summary = watch.summary()
    assert len(saver.saved) == 2 and summary["captures_saved"] == 2 and summary["skipped_by_limit"] == 2 and len(summary["incidents"]) == 4
    assert [i.get("skipped") for i in summary["incidents"]] == [None, None, "limit", "limit"]


@pytest.mark.parametrize("make", [lambda: pdx.ExposureWatch(saver=None), lambda: pdx.ExposureWatch(saver=Saver(), max_incidents=0)])
def test_without_a_saver_or_with_a_zero_cap_the_incident_is_listed_and_nothing_is_saved(make):
    watch = make()
    step(watch, 10.0, seen={"dealer_1": "7S"})
    incident = step(watch, 10.5, layout="cached")
    assert incident["capture"] is None and incident["skipped"] == "not capturing" and watch.summary()["capturing"] is False


def test_a_saver_that_raises_costs_the_capture_and_nothing_else():
    def broken(name, images, info):
        raise OSError("disk full")
    watch = pdx.ExposureWatch(saver=broken)
    step(watch, 10.0, seen={"dealer_1": "7S"})
    incident = step(watch, 10.5, layout="cached")
    assert incident["capture"] is None and incident["skipped"] == "capture failed" and watch.saved == 0
    step(watch, 20.0, round_id=8, seen={"dealer_1": "7S"})
    assert step(watch, 20.5, round_id=8, layout="cached") is not None                      # the next round is still watched


def test_a_missing_frame_or_box_keeps_the_json_and_whatever_images_exist():
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    watch.observe(10.0, 7, payload_of(seen={"dealer_1": "7S"}, rows=[]), {"read_frame": None, "layout_source": "dynamic"}, {"t": 10.0})
    watch.observe(10.5, 7, payload_of(rows=[]), {"read_frame": grey_frame(), "layout_source": "cached"}, {"t": 10.5})
    name, images, info = saver.saved[0]
    assert set(images) == {"lost_frame"} and info["dealer_reads_at_seen"] == {"dealer_1": {"card": "7S", "confidence": None}}


def test_the_timeline_holds_only_the_polls_around_the_incident_and_is_bounded():
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    for number in range(40):
        step(watch, 100.0 + number * 0.3, seen={})
    step(watch, 112.0, seen={"dealer_1": "7S"})
    step(watch, 112.6, layout="cached")
    timeline = saver.saved[0][2]["timeline"]
    assert len(timeline) <= pdx.TIMELINE_POLLS and min(e["t"] for e in timeline) >= 112.0 - 3.0 and timeline[-1]["t"] == 112.6


def test_the_frames_are_only_read_never_written():
    seen_frame, lost_frame = grey_frame(120), grey_frame(130)
    for frame in (seen_frame, lost_frame):
        frame.flags.writeable = False
    before = (seen_frame.tobytes(), lost_frame.tobytes())
    saver = Saver()
    watch = pdx.ExposureWatch(saver=saver)
    step(watch, 10.0, seen={"dealer_1": "7S"}, frame=seen_frame)
    step(watch, 10.5, layout="cached", frame=lost_frame)
    assert (seen_frame.tobytes(), lost_frame.tobytes()) == before and len(saver.saved) == 1
    assert saver.saved[0][1]["dealer_1"].flags["OWNDATA"]                                  # the dealer crop is a copy, so it outlives the tracker's frame


def test_the_harness_saver_writes_a_folder_of_its_own_and_never_over_a_file_that_is_there(tmp_path):
    harness = load_harness()
    saver = harness.ExposureSaver(str(tmp_path))
    assert not (tmp_path / "table_loss").exists()                                           # made on the first save only
    images = {"seen_frame": grey_frame(10), "lost_frame": grey_frame(20), "dealer_1": grey_frame(30, 50, 40)}
    first = saver("round7_dealer_seen_t1.0_table_lost_t1.5", images, {"kind": "x"})
    folder = tmp_path / "table_loss"
    assert sorted(os.listdir(folder)) == sorted([first + "_seen_frame.png", first + "_lost_frame.png", first + "_dealer_1.png", first + ".json"])
    before = {name: (folder / name).read_bytes() for name in os.listdir(folder)}
    (folder / "round8_dealer_seen_t2.0_table_lost_t2.5_lost_frame.png").write_bytes(b"precious")
    second = saver("round7_dealer_seen_t1.0_table_lost_t1.5", dict(images, seen_frame=grey_frame(99)), {"kind": "y"})
    third = saver("round8_dealer_seen_t2.0_table_lost_t2.5", images, {"kind": "z"})
    assert second == first + "_2" and third == "round8_dealer_seen_t2.0_table_lost_t2.5_2"
    assert all((folder / name).read_bytes() == data for name, data in before.items())
    assert (folder / "round8_dealer_seen_t2.0_table_lost_t2.5_lost_frame.png").read_bytes() == b"precious"
    assert json.load(open(folder / (second + ".json")))["files"] == [second + "_seen_frame.png", second + "_lost_frame.png", second + "_dealer_1.png"]


# -- the hook the recorder calls --------------------------------------------------------------------------------------------------------------

class FixedCpu:
    def __init__(self, *values):
        self.values = list(values)

    def sample(self):
        return self.values.pop(0) if self.values else None


def test_a_polls_perf_entry_carries_the_gap_the_timings_the_load_and_the_crop_changes():
    watch = pdx.PollWatch(cpu=FixedCpu(None, {"system": 91.0, "process": 30.5}))
    rows = [row("flop_1")]
    first = watch.observe(10.0, 7, payload_of(rows=rows, timing={"total": 500.0, "roi": 90.0, "recognition": 380.0}), extras_of(grey_frame(100)), {})
    second = watch.observe(10.7, 7, payload_of(rows=rows, timing={"total": 600.0, "roi": 100.0, "recognition": 450.0}), extras_of(grey_frame(100)), {})
    assert first["gap_s"] is None and second["gap_s"] == 0.7
    assert first["cpu"] is None and second["cpu"] == {"system": 91.0, "process": 30.5}
    assert first["crop_diff"] == {"flop_1": None} and second["crop_diff"] == {"flop_1": 0.0}
    assert second["timing"]["total"] == 600.0 and second["timing"]["locate"] == 100.0 and second["timing"]["read"] == 450.0 and second["timing"]["fresh"] == 2
    json.dumps(second)


def test_the_summary_separates_rounds_from_idle_and_the_busy_machine_from_the_quiet_one():
    watch = pdx.PollWatch(cpu=FixedCpu(*[{"system": s, "process": 10.0} for s in (50.0, 90.0, 95.0, 30.0, 40.0)]))
    for t, lifecycle, total, state in ((0.0, "WAITING", 20.0, "WAITING"), (0.3, "IN_PROGRESS", 800.0, "FLOP"), (1.6, "IN_PROGRESS", 1200.0, "RIVER"),
                                       (1.9, "IN_PROGRESS", 300.0, "RIVER"), (2.2, "WAITING", 25.0, "WAITING")):
        watch.observe(t, 7, payload_of(state, lifecycle, timing={"total": total, "screen_diff": 4.0}), extras_of(), {})
    summary = watch.summary()
    assert summary["polls"] == 5 and summary["diagnostic_errors"] == 0
    assert summary["timing_ms"]["in_round"]["total"] == {"n": 3, "median": 800.0, "p90": 1200.0, "max": 1200.0}
    assert summary["timing_ms"]["idle"]["total"]["n"] == 2 and "screen_diff" not in summary["timing_ms"]["in_round"]       # grey levels are not milliseconds
    assert summary["gaps_s"]["max"] == 1.3 and summary["gaps_s"]["over_1s"] == 1 and summary["gaps_s"]["over_2s"] == 0
    cpu = summary["cpu_percent"]
    assert cpu["samples"] == 5 and cpu["in_round_tick_ms_when_system_busy_85_plus"]["median"] in (800.0, 1200.0) and cpu["in_round_tick_ms_when_system_below_85"]["n"] == 1


def test_each_part_fails_alone_and_the_others_survive(monkeypatch):
    class Broken:
        def sample(self):
            raise RuntimeError("no counters")
    watch = pdx.PollWatch(cpu=Broken())
    perf = watch.observe(1.0, 7, payload_of(rows=[row("flop_1")], timing={"total": 5.0}), extras_of(grey_frame()), {})
    assert perf["cpu"] is None and perf["timing"]["total"] == 5.0 and perf["crop_diff"] == {"flop_1": None}
    assert watch.errors == 1 and "RuntimeError" in watch.first_error
    monkeypatch.setattr(pdx, "timing_fields", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    perf = watch.observe(2.0, 7, payload_of(rows=[row("flop_1")]), extras_of(grey_frame()), {})
    assert perf["timing"] is None and perf["crop_diff"] == {"flop_1": 0.0} and watch.errors == 3


@pytest.mark.parametrize("payload, extras", [(None, None), ({}, {}), ({"lifecycle": None, "diagnostics": None, "seen": None}, {"read_frame": "not a frame"}),
                                             ({"timing": {"total": object()}}, {"origin": "x"})])
def test_malformed_updates_give_an_entry_and_never_raise(payload, extras):
    perf = pdx.PollWatch(cpu=FixedCpu()).observe(1.0, 7, payload, extras, {})
    assert "gap_s" in perf and "timing" in perf


def test_the_update_and_the_frame_are_left_exactly_as_they_were():
    frame = grey_frame(100)
    frame.flags.writeable = False
    payload = payload_of(rows=DEALER_ROWS, seen={"dealer_1": "7S"}, timing={"total": 10.0})
    extras = extras_of(frame)
    before = (copy.deepcopy(payload), {k: v for k, v in extras.items() if k != "read_frame"}, frame.tobytes())
    watch = pdx.PollWatch(cpu=FixedCpu(), exposure_saver=Saver())
    watch.observe(1.0, 7, payload, extras, {"dealer_table": {"diag": {"table_boxes": 9}}})
    assert payload == before[0] and {k: v for k, v in extras.items() if k != "read_frame"} == before[1] and frame.tobytes() == before[2]


def test_through_the_recorder_every_poll_line_carries_perf_and_the_summary_and_files_say_so(run, tmp_path):
    watch = pdx.PollWatch(cpu=pdx.CpuSampler())                                            # the real sampler: numbers on Windows, None elsewhere
    recorder = rv.RoundRecorder(poll_watch=watch)
    run.panel_mode = "missing"
    frame = grey_frame(100)
    for number in range(4):
        payload = run.poll(BUILD_UP)
        payload["emitted_mono"] = 100.0 + number * 0.5
        payload["timing"] = {"total": 250.0 + number, "roi": 15.0, "recognition": 220.0}
        recorder.observe(payload, {"read_frame": frame, "origin": (0, 0), "layout_source": "dynamic", "fresh": ["dealer_1"]})
    assert all("perf" in line and line["perf"]["timing"]["total"] is not None for line in recorder.polls)
    assert [line["perf"]["gap_s"] for line in recorder.polls] == [None, 0.5, 0.5, 0.5]
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    assert summary["poll_diagnostics"]["polls"] == 4 and summary["poll_diagnostics"]["timing_ms"]["in_round"]["total"]["n"] == 4
    assert all("perf" in json.loads(line) for line in open(tmp_path / "e" / "polls.jsonl"))
    harness = load_harness()
    text = harness.report(rounds, summary, {"mode": "test", "evidence_dir": "x", "panel_reader": "p", "cache_tolerance": 1, "reads_per_poll": 2,
                                           "suit_margin": 0.05, "rank_margin": 0.1, "corroborated_suit_margin": 0.02})
    assert "## Poll timing, machine load and crop stability" in text and "| total |" in text and "table lost within about 2 s" in text


def test_a_recorder_without_a_poll_watch_writes_what_it_wrote_before(run, tmp_path):
    recorder = rv.RoundRecorder()
    run.panel_mode = "missing"
    payload = run.poll(BUILD_UP)
    payload["emitted_mono"] = 1.0
    recorder.observe(payload, {"read_frame": grey_frame(), "layout_source": "dynamic", "fresh": []})
    rv.write_evidence(str(tmp_path / "e"), recorder)
    assert "poll_diagnostics" not in json.load(open(tmp_path / "e" / "summary.json")) and "perf" not in json.loads(open(tmp_path / "e" / "polls.jsonl").readline())


def test_a_watch_that_blows_up_does_not_stop_the_recorder(run):
    class Exploding:
        def observe(self, *a, **k):
            raise RuntimeError("boom")

        def summary(self):
            return {}
    recorder = rv.RoundRecorder(poll_watch=Exploding())
    run.panel_mode = "missing"
    payload = run.poll(BUILD_UP)
    payload["emitted_mono"] = 1.0
    line = recorder.observe(payload, {"read_frame": None})
    assert line["perf"]["error"].startswith("RuntimeError") and recorder.polls == [line]


def test_the_harness_takes_an_exposure_cap_of_its_own_and_wires_a_saver_only_when_it_is_above_zero():
    harness = load_harness()
    assert harness.arguments([]).max_exposure_captures == 6 and harness.arguments(["--max-exposure-captures", "0"]).max_exposure_captures == 0
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    assert "exposure_saver=ExposureSaver(evidence) if args.max_exposure_captures > 0 else None" in source
    assert "max_exposure=args.max_exposure_captures" in source and "poll_watch=poll_watch" in source


def test_the_module_imports_nothing_that_captures_sleeps_writes_or_phones_home():
    tree = ast.parse(open(os.path.join(ROOT, "poll_diagnostics.py"), encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    forbidden = {"threading", "socket", "requests", "urllib", "mss", "pyautogui", "pynput", "subprocess", "tracker", "app", "capture", "psycopg", "sqlite3",
                 "webbrowser", "win32api", "keyboard", "shutil", "pathlib"}
    assert not (imported & forbidden), imported
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for banned in ("sleep", "imwrite", "grab", "grab_full_screen", "open", "click", "press", "write", "makedirs", "environ", "connect", "setNumThreads"):
        assert banned not in names, banned


SCREENSHOT = os.path.join(ROOT, "Screenshot 2026-09-02 152130.png")


@pytest.mark.skipif(not os.path.exists(SCREENSHOT), reason="no casino screenshot in the project folder")
def test_the_harness_replay_keeps_its_results_and_adds_perf_to_every_poll(tmp_path):
    import subprocess
    import sys
    out = tmp_path / "evidence"
    result = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "validate_rounds.py"), "--replay", SCREENSHOT, "--replay-phases", "7,3",
                             "--out", str(out)], capture_output=True, text=True, cwd=ROOT, timeout=240)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    summary = json.load(open(out / "summary.json"))
    assert summary["rounds_completed"] == 1 and summary["rounds_confirmed"] == 1 and summary["alert_without_confirmed_result"] == 0
    perf = summary["poll_diagnostics"]
    assert perf["diagnostic_errors"] == 0 and perf["polls"] == summary["run"]["updates"] and perf["table_loss_after_dealer_read"]["capture_limit"] == 6
    assert perf["timing_ms"]["in_round"]["total"]["n"] > 0 and perf["cpu_percent"]["samples"] > 0
    lines = [json.loads(line) for line in open(out / "polls.jsonl")]
    assert all("perf" in line and line["perf"].get("timing") is not None for line in lines)
    still = perf["crop_change_grey_levels"]
    assert still and all(c["share_identical"] > 0.5 for c in still.values())                 # a replayed still picture does not move
    assert "## Poll timing, machine load and crop stability" in open(out / "report.md", encoding="utf-8").read()
    if (out / "table_loss").exists():                                                          # the replay clears the table: if it was raised, it is on disk
        assert all(name.startswith("round") for name in os.listdir(out / "table_loss"))
