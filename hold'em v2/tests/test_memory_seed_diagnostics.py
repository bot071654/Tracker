"""Card memory being seeded, or switching value, in the harness's read evidence (read_diagnostics.ReadWatch).

A confident first reading seeds a slot's memory; a later reading can switch it. In a practice session the dealer's 5 of hearts was first
remembered as a 5 of diamonds (0.7365, on a carried box): the memory was the wrong one and the later raw reading the right one, and the
pixels behind the wrong first reading were never saved because nothing disagreed with it yet. These tests pin what is recorded for each
seed and switch, what is captured (the dealer's slots always, other slots only when weak, a cap of its own), and that the existing
disagreement capture is unchanged beside it.
"""

import copy
import json
import os

import pytest

cv2 = pytest.importorskip("cv2")

import read_diagnostics as rd                                    # noqa: E402
import round_validation as rv                                    # noqa: E402

from test_read_diagnostics import (BUILD_UP, ROOT, Saver, boxes, extras_of, frame, load_harness, payload_of, read, row,   # noqa: E402,F401
                                   run)

TRACKING = ("flop_1", "flop_2", "flop_3", "turn", "river")


def seed_watch(**kw):
    seeds, disagreements = Saver(), Saver()
    kw.setdefault("seed_saver", seeds)
    return rd.ReadWatch(saver=disagreements, **kw), seeds, disagreements


def step(watch, t, rows, frame=None, round_id=7, fresh=("dealer_1", "dealer_2"), reads=None):
    return watch.observe(t, round_id, payload_of(rows, round_id=round_id, reads=reads), extras_of(frame, fresh=fresh))


def test_a_seed_is_recorded_and_captured_once_and_a_repeated_value_is_nothing(frame, boxes):
    watch, seeds, _ = seed_watch()
    step(watch, 1.0, [row("dealer_1", None, None, boxes["dealer_1"])], frame)
    assert watch.summary()["memory_events"]["events"] == 0 and seeds.saved == []              # nothing remembered yet
    line = step(watch, 2.0, [row("dealer_1", "7C", "7C", boxes["dealer_1"], memory_readings=1, memory_best=0.91)], frame)
    for number in range(3):
        step(watch, 3.0 + number, [row("dealer_1", "7C", "7C", boxes["dealer_1"])], frame)   # the same value, poll after poll
    assert line["memory_events"] == [{"slot": "dealer_1", "kind": "seed", "value": "7C", "capture": "round7_dealer_1_seed7C_t2.0", "skipped": None}]
    assert line["slots"]["dealer_1"]["seed_capture"] == "round7_dealer_1_seed7C_t2.0"
    memory = watch.summary()["memory_events"]
    assert (memory["events"], memory["seeds"], memory["switches"], memory["captures_saved"]) == (1, 1, 0, 1) and len(seeds.saved) == 1
    name, images, info = seeds.saved[0]
    assert set(images) == {"crop", "context"} and info["kind"] == "seed" and info["slot"] == "dealer_1" and info["round"] == 7
    assert info["memory_event"]["value"] == "7C" and info["memory_event"]["previous"] is None and info["memory_event"]["confidence"] == 0.91
    assert "not known at capture time" in info["memory_event"]["final_memory"]
    assert info["slot_read"]["box"] == [860, 305, 98, 122] and info["slot_read"]["box_source"] == "this frame"
    assert info["replay"]["card"] == "7C"                                                        # the pixels really are a 7 of clubs


def test_a_switch_is_recorded_with_what_it_switched_from_and_a_return_to_a_captured_value_is_a_duplicate(frame, boxes):
    watch, seeds, _ = seed_watch()
    box = boxes["dealer_1"]
    for t, value in ((1.0, "7C"), (2.0, "7S"), (3.0, "7C")):
        step(watch, t, [row("dealer_1", value, value, box)], frame)
    entries = watch.summary()["memory_events"]["entries"]
    assert [(e["kind"], e["value"], e["previous"]) for e in entries] == [("seed", "7C", None), ("switch", "7S", "7C"), ("switch", "7C", "7S")]
    assert [bool(e["capture"]) for e in entries] == [True, True, False] and entries[2]["skipped"].startswith("duplicate")
    assert len(seeds.saved) == 2 and {i["kind"] for _, _, i in seeds.saved} == {"seed", "switch"}
    assert seeds.saved[1][2]["memory_event"]["previous"] == "7C"


def test_the_final_memory_value_follows_the_round_and_marks_a_seed_that_did_not_last(frame, boxes):
    watch, _, _ = seed_watch()
    box = boxes["dealer_2"]
    for t, value in ((1.0, "5D"), (2.0, "5D"), (3.0, "5H"), (4.0, "5H")):
        step(watch, t, [row("dealer_2", value, value, box)], frame)
    memory = watch.summary()["memory_events"]
    assert [(e["value"], e["final_memory"], e["final_differs"]) for e in memory["entries"]] == [("5D", "5H", True), ("5H", "5H", False)]
    assert memory["seeds_whose_final_value_differs"] == 1


def test_a_new_round_starts_the_tracking_afresh(frame, boxes):
    watch, seeds, _ = seed_watch()
    box = boxes["dealer_1"]
    step(watch, 1.0, [row("dealer_1", "7C", "7C", box)], frame, round_id=7)
    step(watch, 2.0, [row("dealer_1", "7C", "7C", box)], frame, round_id=7)
    step(watch, 3.0, [row("dealer_1", "7C", "7C", box)], frame, round_id=8)          # the same card dealt again next round: a seed, not a repeat
    step(watch, 4.0, [row("dealer_1", "9D", "9D", box)], frame, round_id=8)
    entries = watch.summary()["memory_events"]["entries"]
    assert [(e["round"], e["kind"], e["value"], bool(e["capture"])) for e in entries] == [(7, "seed", "7C", True), (8, "seed", "7C", True),
                                                                                         (8, "switch", "9D", True)]
    assert entries[0]["final_memory"] == "7C" and entries[1]["final_memory"] == "9D"        # each round keeps its own final value
    assert len(seeds.saved) == 3


def test_an_empty_memory_in_the_middle_of_a_round_is_not_an_event_and_the_same_value_returning_is_not_a_second_seed(frame, boxes):
    watch, _, _ = seed_watch()
    box = boxes["dealer_1"]
    for t, value in ((1.0, "7C"), (2.0, None), (3.0, "7C"), (4.0, None), (5.0, "7S")):
        step(watch, t, [row("dealer_1", None, value, box)], frame)
    assert [(e["kind"], e["value"]) for e in watch.summary()["memory_events"]["entries"]] == [("seed", "7C"), ("switch", "7S")]


def test_off_the_dealers_slots_only_a_weak_or_unknown_seed_is_captured(frame, boxes):
    watch, seeds, _ = seed_watch()
    box = boxes["dealer_1"]
    unknown = row("river", "9H", "9H", box)
    unknown.pop("memory_best")
    step(watch, 1.0, [row("flop_1", "KS", "KS", box, memory_best=0.93), row("flop_2", "QH", "QH", box, memory_best=0.7365),
                      row("flop_3", "2D", "2D", box, memory_best=0.80), row("turn", "4C", "4C", box, memory_best=0.7999),
                      row("dealer_2", "AC", "AC", box, memory_best=0.99), unknown], frame, reads={})
    by_slot = {e["slot"]: e for e in watch.summary()["memory_events"]["entries"]}
    assert {s: bool(e["capture"]) for s, e in by_slot.items()} == {"flop_1": False, "flop_2": True, "flop_3": False, "turn": True, "dealer_2": True,
                                                                 "river": True}
    assert by_slot["flop_1"]["skipped"].startswith("not eligible") and by_slot["flop_3"]["skipped"].startswith("not eligible")   # 0.80 is not below 0.80
    assert by_slot["river"]["confidence"] is None and len(seeds.saved) == 4
    assert [i["memory_event"]["confidence_unavailable"] for _, _, i in seeds.saved] == [False, False, False, True]


def test_the_seed_cap_is_separate_from_the_disagreement_cap(frame, boxes):
    watch, seeds, disagreements = seed_watch(max_seed_captures=3, max_captures=3)
    for number, raw in enumerate(("6C", "6D", "6H")):                                    # three disagreements first: their cap of three is used up
        step(watch, float(number), [row("dealer_1", raw, "AS", boxes["dealer_1"])], frame)      # (and the first sight seeds dealer_1's memory: one seed)
    for number, value in enumerate(("5C", "5D", "5H")):                                  # then three more seeds/switches on another slot
        step(watch, 10.0 + number, [row("dealer_2", value, value, boxes["dealer_2"])], frame)
    memory = watch.summary()["memory_events"]
    # four events, a seed cap of three: the disagreement captures having used up THEIR cap does not touch it, and the reverse
    assert (memory["events"], memory["captures_saved"], memory["skipped_by_limit"]) == (4, 3, 1) and len(seeds.saved) == 3
    assert [e["skipped"] for e in memory["entries"]] == [None, None, None, "limit"]
    assert watch.summary()["captures_saved"] == 3 and len(disagreements.saved) == 3 and watch.summary()["skipped_by_limit"] == 0


def test_the_default_caps_are_the_proposed_ones():
    assert rd.MAX_SEED_CAPTURES == 40 and rd.SEED_CONFIDENCE_BELOW == 0.80 and rd.MAX_CAPTURES == 40
    assert rd.ReadWatch().max_seed_captures == 40


def test_the_event_list_is_bounded_and_the_overflow_is_counted(frame, boxes, monkeypatch):
    monkeypatch.setattr(rd, "MAX_MEMORY_EVENTS", 3)
    watch, _, _ = seed_watch()
    for number in range(6):
        step(watch, float(number), [row("dealer_1", "7C", "7%s" % "CDHSAK"[number], boxes["dealer_1"])], frame)
    memory = watch.summary()["memory_events"]
    assert memory["events"] == 3 and memory["events_dropped"] == 3


def test_without_a_seed_saver_or_with_a_zero_cap_events_are_listed_and_nothing_is_saved(frame, boxes):
    for watch in (rd.ReadWatch(saver=Saver()), rd.ReadWatch(saver=Saver(), seed_saver=Saver(), max_seed_captures=0)):
        step(watch, 1.0, [row("dealer_1", "7C", "7C", boxes["dealer_1"])], frame)
        memory = watch.summary()["memory_events"]
        assert memory["events"] == 1 and memory["captures_saved"] == 0 and memory["capturing"] is False
        assert memory["entries"][0]["skipped"] == "not capturing"


def test_the_r20_sequence_from_the_practice_session(frame, boxes):
    """Empty memory, then a 5 of diamonds at 0.7365 on a carried box, then the 5 of hearts that was really there."""
    watch, seeds, disagreements = seed_watch()
    box = boxes["dealer_2"]
    carried = dict(region_source="carried from an earlier layout")
    step(watch, 193.0, [row("dealer_2", None, None, box, **carried)], frame, round_id=20)                                  # empty memory
    line = step(watch, 193.31, [row("dealer_2", "5D", "5D", box, memory_readings=1, memory_best=0.7365, status="CONFIRMING", shown_card=None,
                                    confidence=0.7365, suit_margin=0.0516, **carried)], frame, round_id=20,
                reads={"dealer_2": read("5D", confidence=0.7365, suit_margin=0.0516, rank_margin=0.2216)})                 # the wrong seed
    step(watch, 194.32, [row("dealer_2", "5H", "5D", box, memory_readings=1, memory_best=0.7365, status="CONFIRMING", shown_card=None)],
         frame, round_id=20)                                                                                               # the right reading disagrees
    step(watch, 195.0, [row("dealer_2", "5H", "5H", box, memory_readings=2, memory_best=0.83)], frame, round_id=20)         # memory switches
    step(watch, 195.5, [row("dealer_2", "5H", "5H", box, memory_readings=3, memory_best=0.83)], frame, round_id=20)         # and stays
    assert line["memory_events"][0]["kind"] == "seed" and line["memory_events"][0]["capture"] == "round20_dealer_2_seed5D_t193.3"
    memory = watch.summary()["memory_events"]
    assert [(e["kind"], e["value"], e["previous"], e["final_memory"], e["final_differs"]) for e in memory["entries"]] == [
        ("seed", "5D", None, "5H", True), ("switch", "5H", "5D", "5H", False)]
    assert memory["seeds_whose_final_value_differs"] == 1 and memory["captures_saved"] == 2
    seed = seeds.saved[0][2]
    assert seed["memory_event"]["confidence"] == 0.7365 and seed["slot_read"]["confidence"] == 0.7365 and seed["slot_read"]["suit_margin"] == 0.0516
    assert seed["slot_read"]["box_source"] == "carried from an earlier layout" and seed["slot_read"]["box"] == [962, 307, 98, 122]
    assert seed["slot_read"]["read_this_poll"] is True and seed["round"] == 20 and seed["state"] == "RIVER"
    # the disagreement on the next poll is still captured, by the existing mechanism, under its own name and its own cap
    assert [s[0] for s in disagreements.saved] == ["round20_dealer_2_raw5H_mem5D_t194.3"] and disagreements.saved[0][2]["kind"] == "disagreement"
    assert [s[0] for s in seeds.saved] == ["round20_dealer_2_seed5D_t193.3", "round20_dealer_2_switch5H_t195.0"]
    initial = memory["entries"][0]["initial"]
    assert initial["raw"] == "5D" and initial["confidence"] == 0.7365 and initial["box_source"] == "carried from an earlier layout"


def test_a_first_sight_that_is_also_a_disagreement_gives_two_captures_under_two_names(frame, boxes):
    watch, seeds, disagreements = seed_watch()
    step(watch, 1.0, [row("dealer_2", "5H", "5D", boxes["dealer_2"])], frame)
    assert [s[0] for s in seeds.saved] == ["round7_dealer_2_seed5D_t1.0"] and [s[0] for s in disagreements.saved] == ["round7_dealer_2_raw5H_mem5D_t1.0"]


def test_unavailable_or_malformed_memory_values_are_not_events_and_never_raise(frame, boxes):
    watch, seeds, _ = seed_watch()
    box = boxes["dealer_1"]
    for memory in (None, "", 7, ["7C"], {"a": 1}):
        step(watch, 1.0, [row("dealer_1", None, memory, box)], frame)
    for payload in (None, {}, {"diagnostics": None}, {"diagnostics": {"slots": [None, "x", {"slot": "dealer_1"}, {"slot": "dealer_1", "memory_card": 5}]}}):
        assert "poll" in watch.observe(2.0, 7, payload, extras_of(frame))
    assert watch.summary()["memory_events"]["events"] == 0 and watch.errors == 0 and seeds.saved == []


def test_a_round_that_is_not_known_is_still_tracked(frame, boxes):
    watch, seeds, _ = seed_watch()
    for t, value in ((1.0, "7C"), (2.0, "7C"), (3.0, "7S")):
        watch.observe(t, None, payload_of([row("dealer_1", value, value, boxes["dealer_1"])], round_id=None), extras_of(frame))
    memory = watch.summary()["memory_events"]
    assert [(e["round"], e["kind"]) for e in memory["entries"]] == [(None, "seed"), (None, "switch")] and len(seeds.saved) == 2


def test_a_seed_capture_that_fails_is_marked_and_costs_nothing_else(frame, boxes):
    def broken(name, images, info):
        raise OSError("disk full")
    watch = rd.ReadWatch(saver=Saver(), seed_saver=broken)
    line = step(watch, 1.0, [row("dealer_1", "7C", "7C", boxes["dealer_1"])], frame)
    entry = watch.summary()["memory_events"]["entries"][0]
    assert entry["capture"] is None and entry["skipped"] == "capture failed" and "seed_capture" not in line["slots"]["dealer_1"]
    assert watch.summary()["diagnostic_errors"] == 1 and watch.summary()["memory_events"]["captures_saved"] == 0
    assert step(watch, 2.0, [row("dealer_1", "7S", "7S", boxes["dealer_1"])], frame)["poll"] == 1          # the next poll is fine


def test_a_failure_in_the_seed_tracking_does_not_cost_the_poll_its_disagreement_capture(frame, boxes, monkeypatch):
    watch, _, disagreements = seed_watch()

    def boom(*a, **k):
        raise KeyError("boom")
    monkeypatch.setattr(watch, "_memory_events", boom)
    line = step(watch, 1.0, [row("dealer_2", "9D", "9C", boxes["dealer_2"])], frame)
    assert line["mismatch"] == ["dealer_2"] and len(disagreements.saved) == 1 and "memory_events" not in line
    assert watch.summary()["diagnostic_errors"] == 1 and "KeyError" in watch.summary()["first_error"]


def test_a_seed_capture_leaves_the_update_and_the_frame_exactly_as_they_were(frame, boxes):
    own = frame.copy()
    own.flags.writeable = False
    payload = payload_of([row("dealer_1", "7C", "7C", boxes["dealer_1"]), row("dealer_2", "9C", "9C", boxes["dealer_2"])])
    extras = extras_of(own)
    before = (copy.deepcopy(payload), copy.deepcopy({k: v for k, v in extras.items() if k != "read_frame"}), own.tobytes())
    watch, seeds, _ = seed_watch()
    watch.observe(1.0, 7, payload, extras)
    assert watch.errors == 0 and len(seeds.saved) == 2
    assert payload == before[0] and {k: v for k, v in extras.items() if k != "read_frame"} == before[1] and own.tobytes() == before[2]


def test_seed_captures_go_through_the_harness_saver_without_overwriting_anything(frame, boxes, tmp_path):
    harness = load_harness()
    folder = tmp_path / "read_mismatches"
    folder.mkdir()
    (folder / "round7_dealer_1_seed7C_t1.0.json").write_text("precious")
    (folder / "round7_dealer_1_seed7C_t1.0_crop.png").write_bytes(b"precious too")
    watch = rd.ReadWatch(seed_saver=harness.ReadSaver(str(tmp_path)))
    step(watch, 1.0, [row("dealer_1", "7C", "7C", boxes["dealer_1"])], frame)
    assert (folder / "round7_dealer_1_seed7C_t1.0.json").read_text() == "precious"
    assert (folder / "round7_dealer_1_seed7C_t1.0_crop.png").read_bytes() == b"precious too"
    saved = watch.summary()["memory_events"]["entries"][0]["capture"]
    assert saved == "round7_dealer_1_seed7C_t1.0_2"
    assert json.load(open(folder / (saved + ".json")))["kind"] == "seed"


def test_the_harness_takes_a_seed_cap_of_its_own():
    harness = load_harness()
    assert harness.arguments([]).max_seed_captures == 40 and harness.arguments(["--max-seed-captures", "0"]).max_seed_captures == 0
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    assert "seed_saver=ReadSaver(evidence) if args.max_seed_captures > 0 else None" in source and "max_seed_captures=args.max_seed_captures" in source


def test_recorded_through_the_recorder_the_events_are_in_the_summary_the_polls_and_the_report(run, tmp_path):
    harness = load_harness()
    watch = rd.ReadWatch(seed_saver=Saver())
    recorder = rv.RoundRecorder(read_watch=watch)
    run.panel_mode = "missing"
    for number in range(3):
        payload = run.poll(BUILD_UP)
        payload["emitted_mono"] = 100.0 + number
        payload["diagnostics"] = {"slots": [row("flop_1", "KS", "KS", None, memory_best=0.6)]}
        recorder.observe(payload, {"fresh": [], "origin": (0, 0)})
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    assert summary["read_evidence"]["memory_events"]["events"] == 1
    assert any("memory_events" in json.loads(line)["slot_reads"] for line in open(tmp_path / "e" / "polls.jsonl"))
    text = harness.report(rounds, summary, {"mode": "test", "evidence_dir": "x", "panel_reader": "p", "cache_tolerance": 1, "reads_per_poll": 2,
                                           "suit_margin": 0.05, "rank_margin": 0.1, "corroborated_suit_margin": 0.02})
    assert "## Card memory seeded or switched" in text and "| KS |" in text
