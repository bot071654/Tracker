"""The diagnostics-only record of what the paper-white guard REFUSED (table_layout.recover_fused_dealer_pair's optional `report`,
dealer_diagnostics' mirror and DealerWatch's bounded record).

Why: the guard is silent by design - a refused pair is simply "no dealer boxes". A practice session therefore could not show that the
guard fired on the dealer's face-down cards, with what shares, against what line, or why. This record says so: for every geometrically
valid fused pair the guard refused, each half's measured share, the configured threshold and the reason (below threshold, or an invalid
measurement), once per distinct refusal per round with the repeats counted, bounded per round and per run. None of it can change what
the fallback returns: the report is an optional argument the tracker never passes, filled after the decision was made.
"""

import inspect
import json
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import dealer_diagnostics as dd                                   # noqa: E402
import round_validation as rv                                     # noqa: E402
from recognition import table_layout as tl                        # noqa: E402

from test_paper_white_diagnostics import (BACKS, BUILD_UP, FACE_UPS, FULL, GAPS, HANDS, LEFTS, ROOT, clean_faces, image, round45,   # noqa: E402,F401
                                          run, with_backs, with_faces, with_pair)

STATE = {"regions": ["dealer_1", "dealer_2"], "region_sources": {}, "layout_source": "dynamic", "seen": [None, None], "present": [0.5, 0.5]}


def prepared(frame):
    """What locate() hands the fallback: the frame, the board's row, the card height and the table's boxes (None when it would not ask)."""
    boxes = tl.find_card_boxes(frame)
    card_height = tl.dominant_card_size(boxes)
    table = [b for b in boxes if (1 - tl.SIZE_TOLERANCE) * card_height <= b[3] <= (1 + tl.SIZE_TOLERANCE) * card_height]
    return frame, tl.community_row(tl.group_rows(table, card_height)), card_height, table


def ask(frame, **kw):
    return tl.recover_fused_dealer_pair(*prepared(frame), **kw)


# -- the production function: an optional report, the same answer --------------------------------------------------------------------

def test_a_refused_back_pair_is_reported_with_each_halfs_share_the_line_and_the_reason(round45):
    report = {}
    assert ask(with_backs(round45, "r20_a"), report=report) is None
    assert report["candidate"] is True and report["accepted"] is False and report["reason"] == tl.REFUSAL_BELOW_THRESHOLD == "below threshold"
    assert report["threshold"] == tl.FUSED_HALF_PAPER_MIN == 0.20
    assert [h["slot"] for h in report["halves"]] == ["dealer_1", "dealer_2"]
    assert all(h["share"] is not None and h["share"] <= 0.05 and h["status"] == "below threshold" for h in report["halves"])
    assert all(len(h["box"]) == 4 and all(isinstance(v, int) for v in h["box"]) for h in report["halves"])


def test_the_reported_boxes_are_the_boxes_the_fallback_would_have_returned_without_the_guard(round45, monkeypatch):
    frame = with_backs(round45, "r20_b")
    report = {}
    assert ask(frame, report=report) is None
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.0)                               # the guard off: the pair it would have returned
    assert [h["box"] for h in report["halves"]] == [list(box) for box in ask(frame)]


def test_a_faceup_pair_is_accepted_and_reported_as_such(round45):
    report = {}
    pair = ask(round45, report=report)
    assert pair is not None and report["accepted"] is True and report["reason"] == "accepted" and report["threshold"] == 0.20
    assert [h["box"] for h in report["halves"]] == [list(box) for box in pair]
    assert all(h["status"] == "accepted" and h["share"] >= 0.7 for h in report["halves"])


def test_a_shape_that_is_not_a_geometrically_valid_pair_leaves_the_report_empty(round45):
    blank = np.zeros_like(round45)
    blank[:] = (60, 90, 40)                                                            # felt, no card shapes at all
    boxes = tl.find_card_boxes(round45)
    report = {}
    assert tl.recover_fused_dealer_pair(blank, tl.community_row(tl.group_rows(boxes, 122)), 122, boxes, report=report) is None
    assert report == {}                                                                # the guard was never asked: nothing to record
    report = {}
    assert tl.recover_fused_dealer_pair(None, None, 122, [], report=report) is None and report == {}
    assert tl.recover_fused_dealer_pair(round45, [], 122, [], report=report) is None and report == {}


@pytest.mark.parametrize("shares, reason, statuses", [
    ((None, None), "invalid measurement", ["invalid measurement", "invalid measurement"]),
    ((None, 0.9), "invalid measurement", ["invalid measurement", "accepted"]),
    ((0.9, None), "invalid measurement", ["accepted", "invalid measurement"]),
    ((None, 0.01), "invalid measurement", ["invalid measurement", "below threshold"]),       # invalid outranks below, and each half says its own
    ((0.01, None), "invalid measurement", ["below threshold", "invalid measurement"]),
    ((0.01, 0.9), "below threshold", ["below threshold", "accepted"]),
    ((0.9, 0.0), "below threshold", ["accepted", "below threshold"]),
    ((0.1999999, 0.9), "below threshold", ["below threshold", "accepted"]),
])
def test_missing_or_invalid_measurements_are_named_and_each_half_keeps_its_own_status(round45, monkeypatch, shares, reason, statuses):
    values = iter(shares)
    monkeypatch.setattr(tl, "_paper_white_share", lambda patch: next(values))
    report = {}
    assert ask(round45, report=report) is None
    assert report["accepted"] is False and report["reason"] == reason and [h["status"] for h in report["halves"]] == statuses
    assert [h["share"] for h in report["halves"]] == list(shares)


@pytest.mark.parametrize("shares", [(0.2, 0.2), (0.2, 0.9), (1.0, 1.0)])
def test_the_boundary_and_above_are_accepted_in_the_report_too(round45, monkeypatch, shares):
    values = iter(shares)
    monkeypatch.setattr(tl, "_paper_white_share", lambda patch: next(values))
    report = {}
    assert ask(round45, report=report) is not None
    assert report["accepted"] is True and report["reason"] == "accepted" and [h["status"] for h in report["halves"]] == ["accepted", "accepted"]


def test_the_report_follows_the_productions_threshold_not_a_copy_of_it(round45, monkeypatch):
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.95)
    report = {}
    assert ask(round45, report=report) is None
    assert report["threshold"] == 0.95 and report["reason"] == "below threshold"


# -- the return value is what it always was ------------------------------------------------------------------------------------------

def test_the_fallbacks_signature_only_gained_an_optional_report_and_the_tracker_never_passes_one():
    signature = inspect.signature(tl.recover_fused_dealer_pair)
    assert list(signature.parameters) == ["frame", "community", "card_height", "table", "scale", "report"]
    assert signature.parameters["report"].default is None and signature.parameters["scale"].default == tl.DETECT_SCALE
    assert "report" not in inspect.getsource(tl.locate)                                  # production calls it without one
    for path in ("tracker.py", "app.py"):
        assert "recover_fused_dealer_pair" not in open(os.path.join(ROOT, path), encoding="utf-8").read()


def test_with_or_without_a_report_every_case_gives_the_same_answer(round45, clean_faces):
    frames = [round45] + [with_backs(round45, pair, gap, left) for pair in BACKS for gap in GAPS for left in LEFTS]
    frames += [with_faces(round45, name, gap, left) for name in FACE_UPS for gap in GAPS for left in LEFTS]
    frames += [with_pair(round45, image(hand + ".png"), clean_faces[1], gap, 860) for hand in HANDS for gap in GAPS]
    refused = accepted = 0
    for frame in frames:
        without = ask(frame)
        report = {}
        with_report = ask(frame, report=report)
        assert with_report == without, "the report changed the answer"
        if report:
            refused += int(not report["accepted"])
            accepted += int(report["accepted"])
            assert report["accepted"] == (without is not None)                           # the report agrees with the answer, always
    assert refused >= 20 and accepted >= 10                                              # both kinds of case really were exercised


def test_a_report_that_cannot_be_written_to_changes_nothing_and_raises_nothing(round45):
    class Broken(dict):
        def update(self, *a, **k):
            raise RuntimeError("cannot record")

        def __setitem__(self, key, value):
            raise RuntimeError("cannot record")

    assert ask(round45, report=Broken()) == ask(round45)                                 # accepted pair: the same pair
    assert ask(with_backs(round45, "r20_a"), report=Broken()) is None                    # refused pair: still refused
    assert ask(round45, report=None) == ask(round45)


def test_the_frame_is_not_touched_by_recording(round45):
    own = with_backs(round45, "r20_a")
    own.flags.writeable = False
    before = own.tobytes()
    ask(own, report={})
    assert own.tobytes() == before


# -- the diagnostics mirror -------------------------------------------------------------------------------------------------------------

def test_the_mirror_records_a_back_refusal_in_the_diagnostics_and_the_compact_line(round45):
    diagnostics = dd.slot_assignment_diagnostics(with_backs(round45, "r20_a"))
    assert diagnostics["dealer_pair_recovered"] is False and diagnostics["dealer_pair_refused"] is True
    candidate = diagnostics["fusion_candidate"]
    assert candidate["accepted"] is False and candidate["reason"] == "below threshold" and candidate["threshold"] == 0.20
    assert [h["slot"] for h in candidate["halves"]] == ["dealer_1", "dealer_2"] and all(h["paper_white"] <= 0.05 for h in candidate["halves"])
    compact = dd.compact(diagnostics)
    assert compact["recovered"] is False and "halves" not in compact
    assert compact["refused"] == {"reason": "below threshold", "threshold": 0.2, "paper_white": [h["paper_white"] for h in candidate["halves"]],
                                  "status": ["below threshold", "below threshold"]}
    json.dumps(compact)
    json.dumps(diagnostics["fusion_candidate"])


def test_the_mirror_records_nothing_as_refused_for_an_accepted_pair_or_for_no_candidate(round45):
    accepted = dd.slot_assignment_diagnostics(round45)
    assert accepted["dealer_pair_recovered"] is True and accepted["dealer_pair_refused"] is False
    assert accepted["fusion_candidate"]["accepted"] is True and "refused" not in dd.compact(accepted) and "halves" in dd.compact(accepted)
    apart = dd.slot_assignment_diagnostics(with_backs(round45, "r20_a", gap=40))          # the ordinary pairing took them: the fallback never ran
    assert apart["fusion_candidate"] is None and apart["dealer_pair_refused"] is False and "refused" not in dd.compact(apart)


def test_the_mirror_uses_the_productions_own_threshold_and_words(round45, monkeypatch):
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.5)
    candidate = dd.slot_assignment_diagnostics(round45)["fusion_candidate"]
    assert candidate["threshold"] == 0.5                                                  # follows the guard, not a copy
    source = open(os.path.join(ROOT, "dealer_diagnostics.py"), encoding="utf-8").read()
    for literal in ("below threshold", "invalid measurement", "0.20", "FUSED_HALF_PAPER_MIN ="):
        assert literal not in source, literal                                              # no second set of reasons or numbers to drift


@pytest.mark.parametrize("report", [None, {}, {"candidate": False}, {"candidate": True}, {"candidate": True, "accepted": False, "reason": "x",
                                                                                          "threshold": "high", "halves": []},
                                    {"candidate": True, "accepted": False, "reason": "x", "threshold": 0.2, "halves": [{"slot": "d"}]}, "report", 7])
def test_a_malformed_report_gives_no_record_rather_than_a_guess(report):
    assert dd.candidate_record(report) is None


# -- the bounded, de-duplicated record in the round's summary --------------------------------------------------------------------------

def watch_with(frames_by_round, round45=None):
    watch, records = dd.DealerWatch(), {}
    t = 0.0
    for round_id, frames in frames_by_round:
        record = records.setdefault(round_id, {})
        for frame in frames:
            t += 0.5
            watch.observe(t, round_id, "FLOP", {}, STATE, frame, record)
    return watch, records


def test_the_same_refusal_on_every_poll_is_one_record_with_the_polls_counted(round45):
    backs = with_backs(round45, "r20_a")
    jitter = [with_backs(round45, "r20_a", gap=2, left=860 + shift) for shift in (0, 1, 2, 0, 1, 2)]      # boxes that only wobble within the grid
    watch, records = watch_with([(1, [backs] * 12 + jitter)])
    summary = records[1]["dealer_boxes"]
    assert summary["refused_polls"] == 18 and summary["refusal_reasons"] == {"below threshold": 18}
    assert len(summary["refusals"]) == 1 and summary["refusals_dropped"] == 0
    entry = summary["refusals"][0]
    assert entry["polls"] == 18 and entry["reason"] == "below threshold" and entry["threshold"] == 0.20 and entry["state"] == "FLOP"
    assert entry["last_t"] > entry["t"] and [h["slot"] for h in entry["halves"]] == ["dealer_1", "dealer_2"] and all(h["box"] for h in entry["halves"])
    assert summary["refused_paper_white_min"] <= summary["refused_paper_white_max"] <= 0.05 and summary["refused_halves_unmeasured"] == 0
    json.dumps(records[1])


def test_a_different_refusal_in_the_same_round_is_its_own_record(round45, monkeypatch):
    first = with_backs(round45, "r20_a")
    elsewhere = with_backs(round45, "r20_a", gap=2, left=884)                                  # the same backs, far from where they were
    watch, records = watch_with([(1, [first, first, elsewhere, elsewhere, first])])
    entries = records[1]["dealer_boxes"]["refusals"]
    assert len(entries) == 2 and [e["polls"] for e in entries] == [3, 2]
    values = iter([None, 0.9] * 5)
    monkeypatch.setattr(tl, "_paper_white_share", lambda patch: next(values))
    watch2, records2 = watch_with([(1, [round45, round45])])                                   # an unmeasurable half: another reason, another record
    summary = records2[1]["dealer_boxes"]
    assert summary["refusal_reasons"] == {"invalid measurement": 2} and summary["refused_halves_unmeasured"] == 2 and len(summary["refusals"]) == 1
    assert summary["refusals"][0]["halves"][0]["status"] == "invalid measurement" and summary["refusals"][0]["halves"][0]["paper_white"] is None


def test_each_round_keeps_its_own_distinct_refusals_and_a_new_round_starts_afresh(round45):
    backs = with_backs(round45, "r20_a")
    watch, records = watch_with([(1, [backs] * 4), (2, [backs] * 3)])
    assert [len(records[r]["dealer_boxes"]["refusals"]) for r in (1, 2)] == [1, 1]
    assert [records[r]["dealer_boxes"]["refusals"][0]["polls"] for r in (1, 2)] == [4, 3]


def test_the_records_kept_per_round_are_capped_and_the_rest_are_counted(round45, monkeypatch):
    monkeypatch.setattr(dd.DealerWatch, "MAX_REFUSALS_PER_ROUND", 2)
    distinct = [with_backs(round45, "r20_a", gap=2, left=left) for left in (860, 884, 908, 932, 956)]
    watch, records = watch_with([(1, distinct + distinct)])
    summary = records[1]["dealer_boxes"]
    kept = len(summary["refusals"])
    assert kept <= 2 and summary["refused_polls"] == 10 and summary["refusals_dropped"] >= 1
    assert sum(e["polls"] for e in summary["refusals"]) + summary["refusals_dropped"] == 10          # nothing lost: kept polls plus counted ones


def test_the_records_kept_per_run_are_capped_across_rounds(round45, monkeypatch):
    monkeypatch.setattr(dd.DealerWatch, "MAX_REFUSALS_PER_RUN", 3)
    backs = with_backs(round45, "r20_a")
    watch, records = watch_with([(round_id, [backs]) for round_id in range(1, 7)])
    kept = [len(records[r]["dealer_boxes"]["refusals"]) for r in range(1, 7)]
    dropped = [records[r]["dealer_boxes"]["refusals_dropped"] for r in range(1, 7)]
    assert sum(kept) == 3 and sum(dropped) == 3 and all(records[r]["dealer_boxes"]["refused_polls"] == 1 for r in range(1, 7))


def test_an_accepted_pair_a_missing_frame_and_a_failing_diagnostic_record_no_refusal(round45, monkeypatch):
    watch, records = watch_with([(1, [round45, round45, None, np.zeros((50, 60, 3), np.uint8)])])
    summary = records[1]["dealer_boxes"]
    assert summary["refused_polls"] == 0 and summary["refusals"] == [] and summary["refused_paper_white_min"] is None
    assert summary["recovered_polls"] == 2
    monkeypatch.setattr(dd, "slot_assignment_diagnostics", lambda frame: (_ for _ in ()).throw(RuntimeError("diagnostic failed")))
    watch, records = watch_with([(1, [with_backs(round45, "r20_a")] * 2)])
    assert records[1]["dealer_boxes"]["refused_polls"] == 0                                     # a failure is not a refusal, and not a crash


def test_a_poll_line_carries_the_refusal_only_on_the_polls_that_refused(round45):
    watch, record = dd.DealerWatch(), {}
    refused = watch.observe(1.0, 1, "FLOP", {}, STATE, with_backs(round45, "r20_a"), record)
    accepted = watch.observe(2.0, 1, "RIVER", {}, STATE, round45, record)
    nothing = watch.observe(3.0, 1, "RIVER", {}, STATE, None, record)
    assert refused["diag"]["refused"]["reason"] == "below threshold" and "halves" not in refused["diag"]
    assert "refused" not in accepted["diag"] and "halves" in accepted["diag"]
    assert nothing["diag"] is None


# -- through the recorder, onto disk and into the report ---------------------------------------------------------------------------------

def test_through_the_recorder_the_summary_the_rounds_and_the_report_say_the_guard_fired(run, round45, tmp_path):
    spec = __import__("importlib.util").util.spec_from_file_location("validate_rounds_refusal", os.path.join(ROOT, "tools", "validate_rounds.py"))
    harness = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    watch = dd.DealerWatch()
    recorder = rv.RoundRecorder(dealer_watch=watch)
    run.panel_mode = "missing"
    for number, table in enumerate((BUILD_UP, BUILD_UP, BUILD_UP, FULL)):
        payload = run.poll(table)
        payload["emitted_mono"] = 100.0 + number
        frame = with_backs(round45, "r20_a") if number < 3 else round45                       # three polls of backs, then the real pair
        recorder.observe(payload, {"frame": frame, "layout_source": "dynamic", "regions": ["dealer_1", "dealer_2"], "region_sources": {}})
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    boxes = summary["dealer_boxes"]
    assert boxes["fusion_refused_polls"] == 3 and boxes["fusion_refusal_reasons"] == {"below threshold": 3} and boxes["fusion_refusals_dropped"] == 0
    assert 0 <= boxes["fusion_refused_paper_white_min"] <= boxes["fusion_refused_paper_white_max"] <= 0.05
    assert boxes["recovered_polls"] == 1 and boxes["recovered_paper_white_min"] > 0.7
    stored = json.load(open(tmp_path / "e" / "rounds.json"))[0]["dealer_boxes"]["refusals"]
    assert len(stored) == 1 and stored[0]["polls"] == 3 and stored[0]["threshold"] == 0.20
    lines = [json.loads(line)["dealer_table"]["diag"] for line in open(tmp_path / "e" / "polls.jsonl")]
    assert [("refused" in d, "halves" in d) for d in lines] == [(True, False)] * 3 + [(False, True)]
    text = harness.report(rounds, summary, {"mode": "test", "evidence_dir": "x", "panel_reader": "p", "cache_tolerance": 1, "reads_per_poll": 2,
                                           "suit_margin": 0.05, "rank_margin": 0.1, "corroborated_suit_margin": 0.02})
    assert "## Fusion candidates the paper-white guard refused" in text and "3 refused poll(s)" in text and "the line is 0.2" in text


def test_a_run_with_no_refusal_reports_nothing_new(run, round45, tmp_path):
    recorder = rv.RoundRecorder(dealer_watch=dd.DealerWatch())
    run.panel_mode = "missing"
    payload = run.poll(BUILD_UP)
    payload["emitted_mono"] = 100.0
    recorder.observe(payload, {"frame": round45, "layout_source": "dynamic", "regions": ["dealer_1", "dealer_2"], "region_sources": {}})
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    assert summary["dealer_boxes"]["fusion_refused_polls"] == 0 and summary["dealer_boxes"]["fusion_refused_paper_white_max"] is None
    assert summary["dealer_boxes"]["fusion_refusal_reasons"] == {} and summary["dealer_boxes"]["recovered_polls"] == 1
