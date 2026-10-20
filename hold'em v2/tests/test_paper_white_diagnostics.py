"""The paper-white guard on the dealer-fusion fallback (recognition/table_layout.recover_fused_dealer_pair) and its diagnostics (dealer_diagnostics.py).

Why: in a practice session (PHT-validation-20261019-163721) the fallback fired 42 times, 36 of them during the flop, on the dealer's two
FACE-DOWN cards - their blue-lattice backs pass the face test (V > 150, S < 90) and touch, so they fuse and split like a face-up pair. What
tells them apart: of the pixels the face test accepts, the share that are paper white (V > 200, S < 35) - 0.000 to 0.018 for 29 saved backs,
0.42 to 0.89 for white faces (the 0.42 a face under a large hand). The fallback now returns no pair unless BOTH halves are at least
FUSED_HALF_PAPER_MIN (0.20) paper white, measured on the recovered box at full resolution; a half that cannot be measured is refused. These tests
pin the measurement, the guard (backs refused at every gap, valid and hand-covered face-up pairs still recovered with the same boxes, the boundary,
the ceiling above which valid pairs would be lost), that nothing else about the fallback changed, and where the measured shares are recorded.
"""

import ast
import inspect
import json
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import dealer_diagnostics as dd                                   # noqa: E402
import round_lifecycle as rl                                      # noqa: E402,F401
import round_validation as rv                                     # noqa: E402
from recognition import table_layout as tl                        # noqa: E402
import external_fixtures as ext                           # noqa: E402 - fixtures kept outside the repository skip when absent

from test_round_lifecycle import FULL, PLAYER, FLOP, TURN, RIVER, run   # noqa: E402,F401 - the run fixture

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
ROUND45 = os.path.join(FIXTURES, "dealer_fusion", "round45_result-panel_t391.6.png")
PAPER = os.path.join(FIXTURES, "paper_white")
BUILD_UP = {**PLAYER, **FLOP, **TURN, **RIVER}
BACKS = ("r20_a", "r20_b", "r20_c", "r79")                       # real saved card backs, a pair each
FACE_UPS = ("a", "b")                                            # real saved face-up dealer pairs that reach the fallback (6D+2S; 8D+QD)
HANDS = ("face_8H_finger", "face_2S_red_nail", "face_5H_hand", "face_QH_finger")     # real faces under a hand or a red nail
GAPS = (0, 2, 4)
LEFTS = (860, 872)                                               # the live activations' left edges were 858-860 and 872-874


def image(name):
    picture = cv2.imread(os.path.join(PAPER, name))
    assert picture is not None, name
    return picture


@pytest.fixture(scope="module")
def round45():
    return ext.load_frame(ROUND45)


@pytest.fixture(scope="module")
def clean_faces(round45):
    """Round 45's own two dealer cards, cut from the real frame at the boxes the tracker found: a clean face-up pair."""
    regions = tl.locate(round45)["regions"]
    return [round45[regions[s]["top"]:regions[s]["top"] + regions[s]["height"], regions[s]["left"]:regions[s]["left"] + regions[s]["width"]].copy()
            for s in ("dealer_1", "dealer_2")]


def with_pair(round45, first, second, gap=2, left=860, top=310):
    """The Round 45 frame with its face-up dealer pair painted out and two REAL saved crops put where the pair was.

    A composite, not a real frame: the crops are real, placed at the geometry the live activations had. Touching (gap 0 to 4) they fuse
    in the finder's mask, which is what the fallback is for.
    """
    out = round45.copy()
    felt = np.median(round45[620:640, 1380:1400].reshape(-1, 3), axis=0).astype(round45.dtype)
    out[290:450, 800:1120] = felt
    out[top:top + first.shape[0], left:left + first.shape[1]] = first
    right = left + first.shape[1] + gap
    out[top:top + second.shape[0], right:right + second.shape[1]] = second
    return out


def with_backs(round45, pair, gap=2, left=860):
    return with_pair(round45, image("back_%s_dealer_1.png" % pair), image("back_%s_dealer_2.png" % pair), gap, left)


def with_faces(round45, name, gap=2, left=860):
    return with_pair(round45, image("faceup_%s_dealer_1.png" % name), image("faceup_%s_dealer_2.png" % name), gap, left)


def dealer_regions(frame):
    layout = tl.locate(frame)
    return {slot: (r["left"], r["top"], r["width"], r["height"]) for slot, r in (layout or {}).get("regions", {}).items() if slot.startswith("dealer")}, layout


# -- the measurement itself ---------------------------------------------------------------------------------------------------------

def patch_of(*blocks):
    """A patch of solid colours: each block is (BGR colour, how many 1-pixel columns of it); 10 rows high."""
    columns = [np.tile(np.array(colour, np.uint8), (10, 1, 1)) for colour, count in blocks for _ in range(count)]
    return np.concatenate(columns, axis=1)


WHITE = (255, 255, 255)           # V 255, S 0: paper white
GREY = (190, 190, 190)            # V 190, S 0: passes the face test, is not paper white (V <= 200)
LATTICE = (190, 170, 150)         # V 190, S 54: passes the face test (S < 90), not paper white (S >= 35) - like a card back
DARK = (30, 30, 30)               # V 30: fails the face test (ink, felt)


def test_the_share_is_paper_white_pixels_over_the_pixels_the_face_test_accepts():
    patch = patch_of((WHITE, 40), (GREY, 20), (LATTICE, 20), (DARK, 20))
    result = dd.paper_white_share(patch)
    assert result == {"face_pixels": 800, "paper_pixels": 400, "share": 0.5}          # dark pixels are not in the denominator at all
    assert tl._paper_white_share(patch) == 0.5                                       # and the guard measures the very same thing


def test_the_edges_of_the_two_tests_are_exclusive_as_written():
    def hsv_patch(h, s, v):
        return cv2.cvtColor(np.full((10, 10, 3), (h, s, v), np.uint8), cv2.COLOR_HSV2BGR)
    # In HSV before the round trip to BGR: V > 150 and S < 90 for the face test, V > 200 and S < 35 for paper.
    assert dd.paper_white_share(hsv_patch(20, 20, 231))["share"] == 1.0              # clearly paper
    assert dd.paper_white_share(hsv_patch(20, 20, 190))["share"] == 0.0              # a face pixel, but not bright enough
    assert dd.paper_white_share(hsv_patch(20, 60, 231))["share"] == 0.0              # bright but coloured: not paper
    assert dd.paper_white_share(hsv_patch(20, 20, 120))["share"] is None             # not even a face pixel
    assert dd.paper_white_share(hsv_patch(20, 120, 231))["share"] is None            # too coloured for the face test


def test_the_guards_numbers_and_the_diagnostics_numbers_are_one_set():
    assert (tl.FACE_VALUE_MIN, tl.FACE_SATURATION_MAX) == (150, 90)                  # the investigation's numbers were taken against these
    assert (tl.PAPER_VALUE_MIN, tl.PAPER_SATURATION_MAX, tl.FUSED_HALF_PAPER_MIN) == (200, 35, 0.20)
    assert (dd.PAPER_VALUE_MIN, dd.PAPER_SATURATION_MAX) == (tl.PAPER_VALUE_MIN, tl.PAPER_SATURATION_MAX)


BAD_PATCHES = [None, np.zeros((0, 0, 3), np.uint8), np.zeros((5, 0, 3), np.uint8), np.zeros((10, 10), np.uint8), np.zeros((10, 10, 4), np.uint8),
               np.zeros((10, 10, 3, 1), np.uint8), "a card", 3, [[1, 2, 3]], patch_of((DARK, 30))]


@pytest.mark.parametrize("bad", BAD_PATCHES)
def test_an_empty_or_invalid_patch_is_unmeasured_not_a_guess_and_never_raises(bad):
    assert dd.paper_white_share(bad) == {"face_pixels": 0, "paper_pixels": 0, "share": None}
    assert tl._paper_white_share(bad) is None


def test_a_patch_the_conversion_cannot_handle_is_unmeasured_too():
    assert dd.paper_white_share(np.full((10, 10, 3), 0.5, np.float64))["share"] in (None, 0.0, 1.0)   # never an exception
    assert tl._paper_white_share(np.full((10, 10, 3), 0.5, np.float64)) in (None, 0.0, 1.0)


def test_the_real_crops_fall_where_the_investigation_measured_them():
    backs = [dd.paper_white_share(image("back_%s_dealer_%d.png" % (pair, n)))["share"] for pair in BACKS for n in (1, 2)]
    assert len(backs) == 8 and max(backs) <= 0.05 and min(backs) >= 0.0                # saved backs: 0.000 - 0.018 in the investigation
    hands = {name: dd.paper_white_share(image(name + ".png"))["share"] for name in HANDS}
    assert all(0.3 <= share <= 0.95 for share in hands.values()), hands                # faces under a hand: 0.42 - 0.77
    assert min(hands.values()) > 5 * max(backs)                                        # a wide gap between the two kinds of card
    assert max(backs) < tl.FUSED_HALF_PAPER_MIN < min(hands.values())                  # and the guard's line sits in it
    for name in HANDS:                                                                 # the guard's measurement agrees with the recorded one
        assert tl._paper_white_share(image(name + ".png")) == pytest.approx(dd.paper_white_share(image(name + ".png"))["share"], abs=1e-4)


def test_the_measurement_does_not_touch_its_input():
    patch = patch_of((WHITE, 10), (LATTICE, 10))
    patch.flags.writeable = False                                                      # a write would raise
    before = patch.tobytes()
    assert dd.paper_white_share(patch)["share"] == 0.5 and tl._paper_white_share(patch) == 0.5 and patch.tobytes() == before


# -- the halves of a recovered pair -------------------------------------------------------------------------------------------------

def test_a_valid_fused_faceup_pair_has_two_paper_white_halves(round45):
    boxes = tl.find_card_boxes(round45)
    pair = tl.recover_fused_dealer_pair(round45, tl.community_row(tl.group_rows(boxes, 122)), 122, boxes)
    assert pair is not None                                                            # the guard lets the valid pair through
    halves = dd.recovered_halves(round45, pair)
    assert [h["slot"] for h in halves] == ["dealer_1", "dealer_2"] and all(h["source"] == "fusion fallback" for h in halves)
    assert [h["box"] for h in halves] == [list(map(int, box)) for box in pair]
    assert all(h["share"] is not None and h["share"] > 0.7 and h["face_pixels"] > 4000 for h in halves)       # Round 45: about 0.85
    assert all(h["share"] >= tl.FUSED_HALF_PAPER_MIN for h in halves)


@pytest.mark.parametrize("pair", [None, [], [(10, 10, 50, 50)], "ab", [(5000, 5000, 50, 50), (6000, 6000, 50, 50)], [(0, 0, 0, 0), (1, 1, 0, 5)],
                                  [(-100, -100, 50, 50), (-10, -10, 5, 5)], [("x", 1, 2, 3), (1, 2, 3, 4)], [None, None]])
def test_a_missing_or_impossible_half_is_reported_unmeasured_and_does_not_raise(round45, pair):
    halves = dd.recovered_halves(round45, pair)
    assert len(halves) <= 2 and all(set(h) >= {"slot", "box", "source", "face_pixels", "paper_pixels", "share"} for h in halves)
    assert all(h["share"] is None for h in halves if h["box"] is None or h["box"][2] * h["box"][3] == 0 or h["box"][0] > 4000)


def test_a_half_partly_outside_the_frame_is_measured_on_the_part_that_is_inside():
    frame = np.full((100, 100, 3), 255, np.uint8)                                      # all paper
    halves = dd.recovered_halves(frame, [(-50, 10, 100, 60), (80, 80, 100, 100)])      # one over the left edge, one over the corner
    assert [h["share"] for h in halves] == [1.0, 1.0]
    assert [h["face_pixels"] for h in halves] == [50 * 60, 20 * 20] and halves[0]["box"] == [-50, 10, 100, 60]


# -- the guard: face-down backs are refused ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("pair", BACKS)
@pytest.mark.parametrize("gap", GAPS)
@pytest.mark.parametrize("left", LEFTS)
def test_real_card_backs_are_refused_at_every_gap_and_position(round45, pair, gap, left):
    composite = with_backs(round45, pair, gap, left)
    regions, layout = dealer_regions(composite)
    assert layout is not None and "recovered" not in layout and regions == {}, (pair, gap, left)      # no pair, no dealer boxes
    diagnostics = dd.slot_assignment_diagnostics(composite)
    assert diagnostics["dealer_pair_recovered"] is False and diagnostics["recovered_halves"] == []
    assert "halves" not in dd.compact(diagnostics) and dd.compact(diagnostics)["recovered"] is False


@pytest.mark.parametrize("pair", BACKS)
@pytest.mark.parametrize("gap", GAPS)
def test_the_guard_alone_is_what_refuses_the_backs_without_it_the_same_pair_is_recovered(round45, pair, gap, monkeypatch):
    composite = with_backs(round45, pair, gap)
    assert "recovered" not in tl.locate(composite)
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.0)                               # the guard off: the old behaviour
    regions, layout = dealer_regions(composite)
    assert layout["recovered"] == ["dealer_1", "dealer_2"] and set(regions) == {"dealer_1", "dealer_2"}
    halves = dd.slot_assignment_diagnostics(composite)["recovered_halves"]
    assert len(halves) == 2 and all(h["share"] is not None and h["share"] <= 0.05 for h in halves)   # near zero: why the guard refuses them


def test_backs_that_are_far_enough_apart_for_the_ordinary_pairing_are_not_the_guards_business(round45):
    separate = with_backs(round45, "r20_a", gap=40)                                    # unchanged: the ordinary finder takes them, as before
    regions, layout = dealer_regions(separate)
    assert set(regions) == {"dealer_1", "dealer_2"} and "recovered" not in layout
    diagnostics = dd.slot_assignment_diagnostics(separate)
    assert diagnostics["dealer_pair_recovered"] is False and diagnostics["recovered_halves"] == [] and "halves" not in dd.compact(diagnostics)
    assert dd.compact(None) is None


# -- the guard: valid and hand-covered face-up pairs are still recovered, with the same boxes -----------------------------------------------

def test_the_real_round_45_frame_is_still_recovered(round45):
    regions, layout = dealer_regions(round45)
    assert layout["recovered"] == ["dealer_1", "dealer_2"] and regions["dealer_1"][0] == 860 and regions["dealer_2"][0] == 962
    assert regions["dealer_1"][2:] == (98, 122) and regions["dealer_2"][2:] == (98, 122)


@pytest.mark.parametrize("name", FACE_UPS)
@pytest.mark.parametrize("gap", GAPS)
@pytest.mark.parametrize("left", LEFTS)
def test_real_faceup_pairs_are_still_recovered_with_exactly_the_boxes_they_had(round45, name, gap, left, monkeypatch):
    composite = with_faces(round45, name, gap, left)
    guarded, layout = dealer_regions(composite)
    assert layout["recovered"] == ["dealer_1", "dealer_2"] and set(guarded) == {"dealer_1", "dealer_2"}, (name, gap, left)
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.0)
    unguarded, _ = dealer_regions(composite)
    assert guarded == unguarded                                                        # the guard decided; it moved nothing
    halves = dd.slot_assignment_diagnostics(composite)["recovered_halves"]
    assert all(h["share"] is not None and h["share"] >= 0.7 for h in halves)


def test_every_composite_with_a_clean_or_hand_covered_face_the_fallback_recovers_is_still_recovered_unchanged(round45, clean_faces, monkeypatch):
    clean_a, clean_b = clean_faces
    hands = {name: image(name + ".png") for name in HANDS}
    pairs = [(n, hands[n], clean_b) for n in HANDS] + [(n, clean_a, hands[n]) for n in HANDS]
    pairs += [("%s|%s" % (a, b), hands[a], hands[b]) for i, a in enumerate(HANDS) for b in HANDS[i + 1:]]
    recovered = shares = 0
    lowest = 1.0
    for label, first, second in pairs:
        for left in LEFTS:
            for gap in GAPS:
                composite = with_pair(round45, first, second, gap, left)
                with monkeypatch.context() as off:                                     # the guard switched off, only inside this block
                    off.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.0)
                    unguarded, layout = dealer_regions(composite)
                    halves = dd.slot_assignment_diagnostics(composite)["recovered_halves"]
                if not (layout and layout.get("recovered")):
                    continue                                                           # not a fusion case: the guard is not involved
                guarded, layout = dealer_regions(composite)                            # the module's own threshold, whatever it is
                assert layout.get("recovered") == ["dealer_1", "dealer_2"] and guarded == unguarded, (label, left, gap)
                recovered += 1
                lowest = min(lowest, min(h["share"] for h in halves))
    assert recovered >= 20, recovered                                                  # a real set of cases, not an empty loop
    assert lowest >= 0.40                                                              # the lowest hand-covered half is about twice the guard's line


# -- the boundary, an unmeasurable half, and the ceiling ---------------------------------------------------------------------------------

def feed_shares(monkeypatch, values):
    calls = iter(values)
    monkeypatch.setattr(tl, "_paper_white_share", lambda patch: next(calls))


@pytest.mark.parametrize("shares, accepted", [((0.20, 0.20), True), ((0.2000001, 0.9), True), ((0.9, 0.20), True), ((0.1999999, 0.9), False),
                                              ((0.9, 0.1999999), False), ((0.0, 0.9), False), ((0.9, 0.0), False), ((None, 0.9), False),
                                              ((0.9, None), False), ((None, None), False), ((1.0, 1.0), True)])
def test_the_line_is_at_least_a_fifth_for_both_halves_and_an_unmeasurable_half_refuses(round45, monkeypatch, shares, accepted):
    feed_shares(monkeypatch, shares)
    boxes = tl.find_card_boxes(round45)
    pair = tl.recover_fused_dealer_pair(round45, tl.community_row(tl.group_rows(boxes, 122)), 122, boxes)
    assert (pair is not None) is accepted


def test_a_measurement_that_raises_is_unmeasurable_so_the_pair_is_refused_and_locate_still_answers(round45, monkeypatch):
    class Exploding:
        @property
        def ndim(self):
            raise RuntimeError("bad patch")
    assert tl._paper_white_share(Exploding()) is None and dd.paper_white_share(Exploding())["share"] is None
    feed_shares(monkeypatch, (None, None))                                              # what the guard sees for two unmeasurable halves
    assert "recovered" not in tl.locate(round45)


def test_the_guard_reads_only_the_recovered_boxes_and_a_box_off_the_frame_counts_as_unmeasurable(round45, monkeypatch):
    seen = []
    original = tl._paper_white_share
    monkeypatch.setattr(tl, "_paper_white_share", lambda patch: seen.append(patch.shape) or original(patch))
    tl.locate(round45)
    assert len(seen) == 2 and all(shape[0] <= 130 and shape[1] <= 110 for shape in seen)        # two card-sized patches, nothing larger
    assert tl._paper_white_share(round45[5000:5100, 5000:5100]) is None


def test_a_threshold_set_too_high_would_throw_away_valid_pairs_the_ceiling_is_the_worst_face(round45, clean_faces, monkeypatch):
    assert "recovered" in tl.locate(round45)
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.95)                              # above every face: the real Round 45 frame is lost
    assert "recovered" not in tl.locate(round45)
    for name in FACE_UPS:
        assert "recovered" not in tl.locate(with_faces(round45, name))                  # and so are the other valid pairs
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.50)                              # above the heavily covered faces: those are lost, Round 45 is not
    assert "recovered" in tl.locate(round45)
    heavy = with_pair(round45, image("face_QH_finger.png"), clean_faces[1], 2)           # a face under a large finger (0.42) beside a clean one (0.86)
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.0)
    assert "recovered" in tl.locate(heavy)
    monkeypatch.setattr(tl, "FUSED_HALF_PAPER_MIN", 0.50)
    assert "recovered" not in tl.locate(heavy)                                         # 0.42 < 0.50: why the line is a fifth and not a half


def test_the_guard_does_not_change_the_frame(round45):
    own = with_backs(round45, "r20_a")
    own.flags.writeable = False
    before = own.tobytes()
    tl.locate(own)
    dd.slot_assignment_diagnostics(own)
    assert own.tobytes() == before


# -- nothing else about the fallback or the finder changed -------------------------------------------------------------------------------

def test_the_guard_lives_inside_the_fallback_and_nowhere_else_in_the_layout_code():
    source = open(os.path.join(ROOT, "recognition", "table_layout.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    users = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
             and any(isinstance(n, ast.Name) and n.id in ("FUSED_HALF_PAPER_MIN", "_paper_white_share") for n in ast.walk(node))}
    assert users == {"recover_fused_dealer_pair"}                                       # only the fallback consults the guard
    for function in (tl.locate, tl.find_card_boxes, tl._card_mask, tl.assign_slots, tl.group_rows, tl.dominant_card_size, tl.looks_plausible):
        assert "paper" not in inspect.getsource(function).lower(), function.__name__     # the ordinary finder is untouched
    assert "dealer_diagnostics" not in source and "import tracker" not in source


def test_the_fallbacks_other_numbers_are_the_ones_they_were():
    assert (tl.FUSED_WIDTH_CARDS, tl.FUSED_VALLEY_LEVEL, tl.FUSED_BODY_LEVEL, tl.FUSED_BODY_MIN_CARDS, tl.FUSED_VALLEY_MAX_CARDS,
            tl.FUSED_HALF_WIDTH_CARDS, tl.FUSED_PAIR_SIMILARITY) == ((1.7, 2.6), 0.10, 0.40, 0.4, 0.4, (0.6, 1.25), 0.88)
    assert (tl.DEALER_ZONE_HEIGHTS, tl.DEALER_ZONE_SIDE, tl.FACE_VALUE_MIN, tl.FACE_SATURATION_MAX, tl.MIN_FILL, tl.ASPECT_RANGE) == (
        2.2, 0.25, 150, 90, 0.70, (0.55, 1.05))


# -- where the measurement is recorded ------------------------------------------------------------------------------------------------------

def test_the_diagnostics_of_a_recovered_pair_carry_each_halfs_measurement(round45):
    diagnostics = dd.slot_assignment_diagnostics(round45)
    assert diagnostics["dealer_pair_recovered"] is True and len(diagnostics["recovered_halves"]) == 2
    compact = dd.compact(diagnostics)
    assert compact["recovered"] is True and [h["slot"] for h in compact["halves"]] == ["dealer_1", "dealer_2"]
    assert all(h["paper_white"] > 0.7 and h["box"] and h["source"] == "fusion fallback" and h["face_px"] > 4000 for h in compact["halves"])
    assert [h["paper_white"] for h in compact["halves"]] == [round(h["share"], 4) for h in diagnostics["recovered_halves"]]
    json.dumps(compact)


def test_a_recovered_faceup_pair_records_its_halves_and_a_refused_back_pair_records_none(round45):
    faceup = dd.compact(dd.slot_assignment_diagnostics(with_faces(round45, "a")))
    assert faceup["recovered"] is True and len(faceup["halves"]) == 2 and all(h["paper_white"] >= 0.7 for h in faceup["halves"])
    back = dd.compact(dd.slot_assignment_diagnostics(with_backs(round45, "r20_a")))
    assert back["recovered"] is False and "halves" not in back


def test_the_dealer_watch_counts_only_the_pairs_the_guard_let_through(round45):
    watch = dd.DealerWatch()
    record = {}
    state = {"regions": ["dealer_1", "dealer_2"], "region_sources": {}, "layout_source": "dynamic", "seen": [None, None], "present": [0.5, 0.5]}
    watch.observe(1.0, 1, "FLOP", {}, state, with_backs(round45, "r20_a"), record)          # the backs: refused, so nothing recovered, nothing measured
    watch.observe(2.0, 1, "RIVER", {}, state, round45, record)                                 # the real face-up pair
    watch.observe(3.0, 1, "RIVER", {}, state, with_faces(round45, "a"), record)               # another one
    watch.observe(4.0, 1, "RIVER", {}, state, None, record)                                    # a poll with no frame: no measurement, no failure
    summary = record["dealer_boxes"]
    assert summary["recovered_polls"] == 2 and summary["recovered_halves_unmeasured"] == 0
    assert 0.7 < summary["recovered_paper_white_min"] <= summary["recovered_paper_white_max"] <= 1.0


def test_a_round_without_a_recovery_reports_nothing_measured(round45):
    watch, record = dd.DealerWatch(), {}
    watch.observe(1.0, 1, "FLOP", {}, {"regions": [], "region_sources": {}, "layout_source": "dynamic", "seen": [None, None], "present": [0, 0]},
                  np.zeros((300, 400, 3), np.uint8), record)
    summary = record["dealer_boxes"]
    assert summary["recovered_polls"] == 0 and summary["recovered_paper_white_min"] is None and summary["recovered_paper_white_max"] is None


def test_through_the_recorder_the_poll_line_and_the_evidence_summary_carry_it(run, round45, tmp_path):
    watch = dd.DealerWatch()
    recorder = rv.RoundRecorder(dealer_watch=watch)
    run.panel_mode = "missing"
    for number, table in enumerate((BUILD_UP, BUILD_UP, FULL)):
        payload = run.poll(table)
        payload["emitted_mono"] = 100.0 + number
        frame = with_backs(round45, "r20_a") if number == 0 else round45                     # the backs first (refused), then the real pair twice
        recorder.observe(payload, {"frame": frame, "layout_source": "dynamic", "regions": ["dealer_1", "dealer_2"], "region_sources": {}})
    halves = [line["dealer_table"]["diag"].get("halves") for line in recorder.polls]
    assert halves[0] is None and all(h and len(h) == 2 and h[0]["paper_white"] > 0.7 for h in halves[1:])
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    boxes = summary["dealer_boxes"]
    assert boxes["recovered_polls"] == 2 and boxes["recovered_paper_white_min"] > 0.7 and boxes["recovered_paper_white_max"] > 0.7
    lines = [json.loads(line) for line in open(tmp_path / "e" / "polls.jsonl")]
    assert "halves" not in lines[0]["dealer_table"]["diag"] and all("halves" in line["dealer_table"]["diag"] for line in lines[1:])


def test_the_diagnostic_module_does_not_reach_into_the_trackers_decisions():
    source = open(os.path.join(ROOT, "dealer_diagnostics.py"), encoding="utf-8").read()
    for word in ("tracker", "memory", "alert", "lifecycle", "card_recognizer", "imwrite", "sleep", "grab"):
        assert ("import " + word) not in source and ("from " + word) not in source, word
