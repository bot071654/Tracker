"""The dealer pair that the mask fused into one shape (table_layout.recover_fused_dealer_pair).

THE FAILURE (Round 45 of a practice session, frame and diagnostics in tests/fixtures/dealer_fusion/)

Two dealer cards, face up side by side and plainly separate in the picture, were never given dealer slots. The finder's raw
bright mask held two components about 6 px apart; its 5 x 5 close joined them across a thin bridge into one shape 208 x 110
(aspect 1.89), which was rejected like any sleeve. No pair above the board, no dealer cards read, no completed round - while the
result panel showed the dealer's hand.

THE FIX, AND ITS BOUNDARIES

A second chance for the dealer's pair only: in the strip above the board, when the ordinary pairing found nothing there, one
shape that is two cards wide and a card tall, with exactly two bodies of bright columns and an all-but-empty valley between them,
is split back into the two cards it is. It says only WHERE; the boxes go through the same recogniser, margins, dealer gate and
confirmation as every other card. Nothing about the board, the player, carried boxes, thresholds, the lifecycle or the alert
changes. These tests use the real Round 45 frame, synthetic variants of it, the real Round 0 frame (a different failure: the
BOARD's cards fused) and the original casino screenshot.
"""

import json
import os
import queue

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import dealer_diagnostics as dd                                 # noqa: E402
import round_lifecycle as rl                                    # noqa: E402
import round_validation as rv                                   # noqa: E402
import tracker as tracker_module                                # noqa: E402
from poker import scenarios as scenario_rules                   # noqa: E402
from recognition import card_recognizer as cr                   # noqa: E402
from recognition import table_layout as tl                      # noqa: E402
import external_fixtures as ext                           # noqa: E402 - fixtures kept outside the repository skip when absent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "dealer_fusion")
ROUND45 = os.path.join(FIXTURES, "round45_result-panel_t391.6.png")
ROUND0 = os.path.join(FIXTURES, "round0_result-panel_t22.2.png")
ORIGINAL = os.path.join(ROOT, "Screenshot 2026-09-02 152130.png")
needs_fixtures = pytest.mark.skipif(not ext.available(ROUND45), reason=ext.skip_reason(ROUND45))


@pytest.fixture(scope="module", autouse=True)
def templates():
    cr.load_templates(force=True)


@pytest.fixture(scope="module")
def round45():
    return ext.load_frame(ROUND45)


@pytest.fixture(scope="module")
def round0():
    return ext.load_frame(ROUND0)


@pytest.fixture
def without_the_fallback(monkeypatch):
    """table_layout exactly as it was before: the recovery returns nothing."""
    monkeypatch.setattr(tl, "recover_fused_dealer_pair", lambda *a, **k: None)


def felt_of(frame):
    return np.median(frame[620:640, 1380:1400].reshape(-1, 3), axis=0).astype(frame.dtype)


def without_dealer_cards(frame):
    """Round 45's frame with the dealer pair painted out (the fused shape's extent comes from the saved diagnostics)."""
    out = frame.copy()
    out[290:440, 835:1075] = felt_of(frame)
    return out


def paint(frame, boxes, value=235):
    out = frame.copy()
    for x, y, w, h in boxes:
        out[y:y + h, x:x + w] = value
    return out


def pair_on_felt(frame, gap=6, widths=(92, 92), height=112, y=308, x=855, bridge=True):
    """Two white card-sized blocks side by side over the board, on a frame with no dealer cards."""
    base = without_dealer_cards(frame)
    boxes, left = [], x
    for width in widths:
        boxes.append((left, y, width, height))
        left += width + gap
    out = paint(base, boxes)
    if bridge and len(boxes) == 2:                       # a thin bright line across the gap at the cards' feet
        out[y + height - 5:y + height - 2, boxes[0][0] + boxes[0][2] - 1:boxes[1][0] + 1] = 235
    return out


def bridged(frame, rows, gap=6, y=308, height=112, x=855):
    """Two cards joined by a bright bridge `rows` pixels high in the gap, at their feet."""
    out = pair_on_felt(frame, gap=gap, bridge=False)
    out[y + height - rows:y + height, x + 92 - 1:x + 92 + gap + 1] = 235
    return out


def dealer_slots(layout):
    return [slot for slot in ("dealer_1", "dealer_2") if layout and slot in layout["regions"]]


def box(region):
    return region["left"], region["top"], region["width"], region["height"]


# -- 1. Round 45: the two cards are recovered ---------------------------------------------------------------------------------

@needs_fixtures
def test_round_45_the_fused_shape_is_split_into_two_dealer_boxes(round45):
    layout = tl.locate(round45)
    assert layout["recovered"] == ["dealer_1", "dealer_2"]
    first, second = box(layout["regions"]["dealer_1"]), box(layout["regions"]["dealer_2"])
    assert first[0] < second[0] and first[0] + first[2] <= second[0] + 6                   # left card, then right card, not overlapping
    for x, y, w, h in (first, second):
        assert 90 <= w <= 112 and 112 <= h <= 130                                          # card-sized
    sidecar = json.load(open(os.path.join(FIXTURES, "round45_result-panel_t391.6.json")))
    fused = [b["box"] for b in sidecar["diagnostics"]["dealer_zone"]["blobs"] if b["verdict"].startswith("rejected: aspect")
             and 150 < b["box"][2] < 250 and b["box"][1] > 250][0]                          # the 208 x 110 shape at (850, 308)
    left, right = min(first[0], second[0]), max(first[0] + first[2], second[0] + second[2])
    assert abs(left - fused[0]) <= 20 and abs(right - (fused[0] + fused[2])) <= 20        # together they are the shape that was rejected
    assert 850 <= first[0] <= 880 and 950 <= second[0] <= 975                              # where the 7 and the 9 are in the picture


@needs_fixtures
def test_round_45_without_the_fallback_there_are_no_dealer_slots_as_it_was(round45, without_the_fallback):
    layout = tl.locate(round45)
    assert dealer_slots(layout) == [] and "recovered" not in layout and layout["found"] == 7


@needs_fixtures
def test_the_diagnostic_mirror_reports_the_recovery_and_agrees_with_locate(round45):
    d = dd.slot_assignment_diagnostics(round45)
    layout = tl.locate(round45)
    assert d["dealer_pair_recovered"] and d["dealer_slots_assigned"] and sorted(d["slots"]) == sorted(layout["regions"])
    assert dd.compact(d)["recovered"] is True


# -- 2. the recovered boxes go through the existing recogniser; the fallback names no card -----------------------------------------

@needs_fixtures
def test_the_fallback_returns_only_geometry_and_never_calls_the_recogniser(round45, monkeypatch):
    calls = []
    for name in ("recognize_card", "read_slot"):
        original = getattr(cr, name)
        monkeypatch.setattr(cr, name, lambda *a, _o=original, **k: calls.append(1) or _o(*a, **k))
    tl.locate(round45)
    assert calls == []
    source = open(os.path.join(ROOT, "recognition", "table_layout.py"), encoding="utf-8").read()
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("recogni" in line for line in imports) and "read_slot(" not in source and "recognize_card(" not in source
    boxes = tl.recover_fused_dealer_pair(round45, *recovery_inputs(round45))
    assert len(boxes) == 2 and all(len(b) == 4 and all(isinstance(v, int) for v in b) for b in boxes)


def recovery_inputs(frame):
    boxes = tl.find_card_boxes(frame)
    height = tl.dominant_card_size(boxes)
    table = [b for b in boxes if (1 - tl.SIZE_TOLERANCE) * height <= b[3] <= (1 + tl.SIZE_TOLERANCE) * height]
    return tl.community_row(tl.group_rows(table, height)), height, table


def drive(frame, monkeypatch, ticks=8, stores=None, seed=None):
    """The real Tracker polling one frame `ticks` times; returns its last update."""
    shown = {"frame": frame}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["frame"])
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    made = tracker_module.Tracker({"monitor": 1, "regions": {}, "game_window_title": "", "clear_frames": 3, "stable_frames": 2},
                                  queue.Queue())
    made._own_rects = lambda: []
    made._record_tick = lambda *a, **k: None
    made._store = lambda cards, reads=None: (stores.append(dict(cards)) if stores is not None else None)
    made.shown = shown
    last = None
    for _ in range(ticks):
        made._tick()
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                last = payload
    return made, last


@needs_fixtures
def test_through_the_tracker_the_recovered_boxes_are_read_by_the_normal_pipeline_and_the_round_completes(round45, monkeypatch):
    stores = []
    made, update = drive(round45, monkeypatch, stores=stores)
    assert [update["seen"]["dealer_1"], update["seen"]["dealer_2"]] == ["7C", "9C"]          # what the dealer panel says
    assert update["state"] == "COMPLETE" and update["lifecycle"]["state"] == rl.COMPLETE
    assert stores and stores[0]["dealer_1"] == "7C"                                          # through the unchanged stable-frames gate


@needs_fixtures
def test_without_the_fallback_the_same_frame_never_completes(round45, monkeypatch, without_the_fallback):
    stores = []
    made, update = drive(round45, monkeypatch, stores=stores)
    assert update["seen"]["dealer_1"] is None and update["state"] == "RIVER" and stores == []


# -- 3 + 4. what must NOT be split ----------------------------------------------------------------------------------------------------

@needs_fixtures
def test_a_synthetic_fused_pair_is_recovered_so_the_rule_is_not_the_one_frame(round45):
    layout = tl.locate(pair_on_felt(round45, gap=6))
    assert layout.get("recovered") == ["dealer_1", "dealer_2"]
    assert dealer_slots(layout) == ["dealer_1", "dealer_2"]


@needs_fixtures
def test_a_pair_far_enough_apart_is_found_by_the_ordinary_pairing_not_the_fallback(round45):
    layout = tl.locate(pair_on_felt(round45, gap=18, bridge=False))
    assert dealer_slots(layout) == ["dealer_1", "dealer_2"] and "recovered" not in layout


NOT_TO_SPLIT = {
    "an extra card-sized block beside the pair": lambda f: paint(pair_on_felt(f, gap=6), [(1058, 308, 92, 112)]),
    # (each of the next three is stopped by ONE rule only - the others would let it through - so bypassing that rule fails it)
    "a third, small body attached (only the body count stops it)": lambda f: paint(pair_on_felt(f, gap=6, widths=(86, 86)), [(1036, 330, 38, 80)]),
    "a bridge a quarter of the cards' height (only the valley rule stops it)": lambda f: bridged(f, rows=28),
    "a card and a two-thirds-width card (only the likeness rule stops it)": lambda f: pair_on_felt(f, gap=6, widths=(92, 66)),
    "three cards fused": lambda f: pair_on_felt(f, gap=6, widths=(92, 92, 92)),
    "one solid wide block, no valley": lambda f: paint(without_dealer_cards(f), [(855, 308, 190, 112)]),
    "one card only": lambda f: paint(without_dealer_cards(f), [(855, 308, 92, 112)]),
    "two narrow strips": lambda f: paint(without_dealer_cards(f), [(855, 308, 30, 112), (891, 308, 30, 112)]),
    "a card and a half-width card": lambda f: pair_on_felt(f, gap=6, widths=(92, 56)),
    "a wide shape only half a card tall": lambda f: paint(without_dealer_cards(f), [(855, 330, 92, 50), (953, 330, 92, 50)]),
    "a fused pair far from the board": lambda f: paint(without_dealer_cards(f), [(1450, 200, 92, 112), (1548, 200, 92, 112)]),
    "a valley too wide to have been closed over": lambda f: pair_on_felt(f, gap=40, bridge=False),
    "nothing at all": lambda f: without_dealer_cards(f),
}


@needs_fixtures
@pytest.mark.parametrize("case", sorted(NOT_TO_SPLIT))
def test_shapes_that_are_not_clearly_two_cards_are_not_split(round45, case):
    layout = tl.locate(NOT_TO_SPLIT[case](round45))
    assert layout is not None and "recovered" not in layout
    if case != "a valley too wide to have been closed over":
        assert dealer_slots(layout) == []                                            # (a wide gap leaves two ordinary cards)


@needs_fixtures
def test_the_sleeves_and_hands_left_in_the_zone_are_not_mistaken_for_cards():
    """The original casino frame with the dealer's cards removed leaves the cuffs and sleeves (aspect 1.2) in the zone."""
    if not os.path.exists(ORIGINAL):
        pytest.skip("no casino screenshot")
    frame = cv2.imread(ORIGINAL)
    regions = tl.locate(frame)["regions"]
    painted = frame.copy()
    for slot in ("dealer_1", "dealer_2"):
        r = regions[slot]
        painted[r["top"] - 8:r["top"] + r["height"] + 8, r["left"] - 8:r["left"] + r["width"] + 8] = felt_of(frame)
    layout = tl.locate(painted)
    assert layout is not None and dealer_slots(layout) == [] and "recovered" not in layout


# -- 5 + 6. nothing else changes ----------------------------------------------------------------------------------------------------------

@needs_fixtures
def test_every_frame_the_ordinary_path_handles_is_located_exactly_as_before(round45, round0, monkeypatch):
    frames = {"the original casino frame": cv2.imread(ORIGINAL) if os.path.exists(ORIGINAL) else None,
              "round 0 (the board fused)": round0, "round 45 without its dealer cards": without_dealer_cards(round45),
              "a pair found ordinarily": pair_on_felt(round45, gap=18, bridge=False),
              "a single dealer card": paint(without_dealer_cards(round45), [(855, 308, 92, 112)])}
    with_fallback = {name: tl.locate(f) for name, f in frames.items() if f is not None}
    monkeypatch.setattr(tl, "recover_fused_dealer_pair", lambda *a, **k: None)
    before = {name: tl.locate(f) for name, f in frames.items() if f is not None}
    assert with_fallback == before
    assert all("recovered" not in (layout or {}) for layout in with_fallback.values())


@needs_fixtures
def test_board_cards_that_fuse_are_still_not_repaired_by_this_patch(round0, monkeypatch):
    """Round 0: the 10 and 3 of the board fused, the board row split 1 + 2, no layout. That is a different failure."""
    assert tl.locate(round0) is None
    d = dd.slot_assignment_diagnostics(round0)
    assert d["community"] is None and not d["dealer_pair_recovered"] and d["slots"] == []
    assert any(b["verdict"].startswith("rejected: aspect") and b["box"][2] > 190 for b in d["blobs"])   # the fused board blob is still rejected


# -- 7. refusals stay ------------------------------------------------------------------------------------------------------------------------

@needs_fixtures
def test_an_ambiguous_dealer_card_in_the_recovered_box_is_still_refused(round45, monkeypatch):
    layout = tl.locate(round45)
    smeared = round45.copy()
    r = layout["regions"]["dealer_1"]
    smeared[r["top"]:r["top"] + r["height"], r["left"]:r["left"] + r["width"]] = cv2.GaussianBlur(
        round45[r["top"]:r["top"] + r["height"], r["left"]:r["left"] + r["width"]], (0, 0), 4.0)
    stores = []
    made, update = drive(smeared, monkeypatch, stores=stores)
    assert update["seen"]["dealer_1"] is None and update["cards"].get("dealer_1") is None
    assert update["state"] != "COMPLETE" and stores == []                      # the dealer's gate and the round both held
    assert update["seen"]["dealer_2"] == "9C"                                  # the card that reads well still does


def test_the_thresholds_and_the_dealer_gate_are_exactly_what_they_were():
    assert (cr.SUIT_MARGIN, cr.RANK_MARGIN, cr.CORROBORATED_SUIT_MARGIN) == (0.05, 0.10, 0.02)
    assert (tl.ASPECT_RANGE, tl.MIN_FILL, tl.SIZE_TOLERANCE, tl.MIN_CARD_WIDTH, tl.MIN_CARD_HEIGHT) == ((0.55, 1.05), 0.70, 0.25, 25, 35)
    assert tl.DETECT_SCALE == 0.5 and tl.ROW_GAP_LIMIT == 1.5
    made = tracker_module.Tracker({"monitor": 1, "regions": {}, "game_window_title": ""}, queue.Queue())
    assert made.config.get("dealer_min_confidence", 0.80) == 0.80


# -- 8. a missing dealer pair never becomes a confirmed round -----------------------------------------------------------------------------------

@needs_fixtures
def test_with_no_dealer_pair_the_round_never_completes_and_no_hand_is_stored(round45, monkeypatch):
    stores = []
    made, update = drive(without_dealer_cards(round45), monkeypatch, ticks=10, stores=stores)
    assert update["state"] == "RIVER" and update["lifecycle"]["state"] == rl.IN_PROGRESS
    assert update["lifecycle"]["result_status"] == rl.RESULT_NONE and update["lifecycle"]["result"] is None
    assert stores == []


@needs_fixtures
def test_a_round_with_no_dealer_pair_gets_no_next_round_alert(round45, monkeypatch):
    probe = rv.AlertProbe(scenarios={"preround": {"default": scenario_rules.ANTE, "rules": []}})
    made, update = drive(without_dealer_cards(round45), monkeypatch, ticks=6)
    cleared = round45.copy()
    cleared[300:935, 190:1730] = felt_of(round45)
    made.shown["frame"] = cleared
    events, clock = [], 100.0
    for _ in range(10):
        made._tick()
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                clock += 1.0
                payload["emitted_mono"] = clock
                events.append(probe.observe(payload))
    shown = [e[1] for e in events if e and e[0] == "show"]
    assert scenario_rules.ANTE not in shown and scenario_rules.SKIP not in shown


# -- 9. A2: carried dealer boxes are still forgotten when the table is not the same ------------------------------------------------------------

@needs_fixtures
def test_recovered_dealer_boxes_are_forgotten_like_any_others_when_the_table_moves(round45):
    made = tracker_module.Tracker({"monitor": 1, "regions": {}, "game_window_title": ""}, queue.Queue())
    layout = tl.locate(round45)
    first = made._with_dealer_boxes(layout)
    made._layout = first
    assert made._dealer_memory and "dealer_1" in made._dealer_memory["boxes"]                   # remembered from the recovered pair
    later = {"regions": {slot: dict(r) for slot, r in layout["regions"].items() if slot not in ("dealer_1", "dealer_2")},
             "card_height": layout["card_height"], "card_size": layout["card_size"], "found": 7}
    same = made._with_dealer_boxes(later)
    assert "dealer_1" in same["regions"] and same["carried"] == ["dealer_1", "dealer_2"]           # the table stood still: carried
    for slot in later["regions"]:
        later["regions"][slot]["left"] += 40                                                        # the next round, the table moved
    moved = made._with_dealer_boxes(later)
    assert "dealer_1" not in moved["regions"] and made._dealer_memory is None


# -- 10. lifecycle and the one alert ------------------------------------------------------------------------------------------------------------

@needs_fixtures
def test_a_round_completed_through_the_fallback_confirms_and_alerts_exactly_once(round45, monkeypatch):
    probe = rv.AlertProbe(scenarios={"preround": {"default": scenario_rules.ANTE, "rules": []}})
    made, update = drive(round45, monkeypatch, ticks=1)
    events, clock, statuses = [], 100.0, []

    def collect():
        nonlocal clock
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                clock += 1.0
                payload["emitted_mono"] = clock
                statuses.append(payload["lifecycle"]["result_status"])
                events.append(probe.observe(payload))

    for _ in range(12):                                          # the panels (both rows) confirm the result over a few polls
        made._tick()
        collect()
    assert rl.RESULT_CONFIRMED in statuses and statuses[-1] == rl.RESULT_CONFIRMED
    cleared = round45.copy()
    cleared[300:935, 190:1730] = felt_of(round45)
    made.shown["frame"] = cleared
    for _ in range(12):
        made._tick()
        collect()
    made.shown["frame"] = round45                                # a stray reappearance of the old frame does not alert again
    made._tick()
    collect()
    shown = [e[1] for e in events if e and e[0] == "show"]
    assert [s for s in shown if s in rv.ALERT_ACTIONS] and len([s for s in shown if s in rv.ALERT_ACTIONS]) == 1
