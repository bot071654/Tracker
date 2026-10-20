"""The harness's result-panel thumbnail measurements (panel_diagnostics.py): changed against unchanged cards, red against black.

Why: the panel reader's cache treats a new thumbnail as "already read" when it differs from a cached picture by at most PanelTracker.CACHE_TOLERANCE
(4). Whether that is safe was judged from 215 refused black-suit thumbnails and one pair of frames. These tests pin what the new, opt-in measurement
records - differences from the previous and first picture, a mirror of the cache's own hit/miss rule, labels that are given ONLY when two independent
readers agree about a card at a position - that its evidence is bounded, thumbnail-sized and kept in a folder of its own, and that none of it can
change what the tracker reads or decides. No test here saves a whole frame, reads a screen, or touches the tracker.
"""

import ast
import copy
import importlib.util
import json
import os
import zlib

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import panel_diagnostics as pnl                                   # noqa: E402
import round_validation as rv                                     # noqa: E402
from recognition import result_panel as rp                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 34, 47                                                     # a panel thumbnail, as the casino draws it
TOL = 4                                                           # the tracker's tolerance today; every test that depends on it says so


def load_harness():
    spec = importlib.util.spec_from_file_location("validate_rounds_panel", os.path.join(ROOT, "tools", "validate_rounds.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- a small, fully controlled table -------------------------------------------------------------------------------------------------------------

CARDS = {"player_1": "AS", "player_2": "KH", "flop_1": "2C", "flop_2": "7D", "flop_3": "9S", "turn": "JH", "river": "3D",
         "dealer_1": "QC", "dealer_2": "4H"}
ROWS = {"player": ["AS", "KH", "2C", "7D", "9S"], "dealer": ["QC", "4H", "2C", "7D", "9S"]}


def box(side, index):
    return {"left": 10 + 40 * index, "top": 10 if side == "player" else 100, "width": W, "height": H}


def picture(seed):
    return np.random.RandomState(seed).randint(0, 256, (H, W, 3)).astype(np.uint8)


def pic(card):
    """The same card always has the same picture; different cards have wholly different ones."""
    return picture(zlib.crc32(card.encode()) % 100000)


def noisy(picture_, seed, pixels=6, amount=2):
    """The live panel's noise: a few pixels a grey level or two off (at most `amount`, so two such copies differ by at most 2 * amount)."""
    out = picture_.copy().astype(int)
    rng = np.random.RandomState(seed)
    for spot in rng.choice(H * W, size=pixels, replace=False):
        out[spot // W, spot % W] += rng.choice([-amount, amount])
    return np.clip(out, 0, 255).astype(np.uint8)


def update(pictures, readings=None, rows=None, cards=None, state="COMPLETE", status="confirmed", round_id=1, stale=(), regions=None):
    """(payload, extras) for one poll. pictures: {(side, index): HxWx3}; readings: {(side, index): (card, accepted, queued)}."""
    frame = np.zeros((220, 420, 3), np.uint8)
    sides = {}
    for side in pnl.SIDES:
        reads, index = [], 0
        while (side, index) in pictures:
            where = (regions or {}).get((side, index), box(side, index))
            if where["width"] <= 400:
                frame[where["top"]:where["top"] + H, where["left"]:where["left"] + W] = pictures[(side, index)]
            card, accepted, queued = (readings or {}).get((side, index), (None, False, False))
            reads.append({"card": card, "confident": accepted, "queued": queued, "status": "CONFIRMED" if accepted else "UNKNOWN", "region": where})
            index += 1
        sides[side] = {"reads": reads, "confirmed": list((rows or {}).get(side) or []), "stale": side in stale, "state": "VISIBLE_COMPLETE"}
    payload = {"state": "COMPLETE", "lifecycle": {"state": state, "round_id": round_id, "result_status": status}, "cards": dict(CARDS if cards is None else cards),
               "panels": {"sides": sides}}
    return payload, {"read_frame": frame, "origin": (0, 0)}


def play(watch, rows=None, cards=None, polls=3, t0=0.0, round_id=1, status="confirmed", state="COMPLETE", stale=(), readings_for=None, noise=True):
    """`polls` polls of a panel showing `rows` (each card's own picture, with the casino's noise), all read and accepted unless readings_for says otherwise."""
    rows = ROWS if rows is None else rows
    entries = []
    for number in range(polls):
        pictures = {(side, i): (noisy(pic(card), number * 31 + i + (side == "dealer") * 7) if noise else pic(card)) for side in rows for i, card in enumerate(rows[side])}
        readings = {(side, i): (card, True, False) for side in rows for i, card in enumerate(rows[side])}
        if readings_for:
            readings = readings_for(number, readings)
        payload, extras = update(pictures, readings, rows, cards, state, status, round_id, stale)
        entries.append(watch.observe(t0 + number * 0.5, round_id, payload, extras))
    return entries


def watch_of(**kwargs):
    kwargs.setdefault("tolerance", TOL)
    kwargs.setdefault("pool_size", 64)
    return pnl.PanelWatch(**kwargs)


class Saver:
    def __init__(self, fail=False):
        self.saved, self.fail = [], fail

    def __call__(self, name, images, info):
        if self.fail:
            raise OSError("disk full")
        self.saved.append((name, images, info))
        return name


# -- the statistic and the mirror ----------------------------------------------------------------------------------------------------------------

def test_the_difference_is_the_caches_own_statistic():
    a = picture(1)
    assert pnl.max_diff(a, a.copy()) == 0
    for shift in (1, 2, 7, 40):
        assert pnl.max_diff(a, np.clip(a.astype(int) + shift, 0, 255).astype(np.uint8)) >= 1
    shifted = a.copy()
    shifted[3, 4, 1] = int(shifted[3, 4, 1]) ^ 0x0F
    expected = abs(int(a[3, 4, 1]) - int(shifted[3, 4, 1]))
    assert expected > 0 and pnl.max_diff(a, shifted) == expected and pnl.max_diff(shifted, a) == expected          # symmetric, any channel, any pixel
    assert pnl.max_diff(a, a[:, :-1]) is None and pnl.max_diff(a, None) is None             # different sizes cannot be compared


def test_the_mirror_agrees_with_the_real_cache_at_every_difference():
    """The mirror's hit/miss must be the tracker's: checked against PanelTracker._cached_read itself, picture by picture."""
    tracker = rp.PanelTracker()
    tracker.CACHE_TOLERANCE = TOL
    original = pic("AS")
    tracker._cache[(original.shape, hash(original.tobytes()))] = (original.copy(), {"card": "AS", "confident": True})
    for amount in (0, 1, 2, 3, TOL, TOL + 1, 8, 19, 60):
        near = original.astype(int)
        near[0, 0, 0] = near[0, 0, 0] + amount if near[0, 0, 0] + amount <= 255 else near[0, 0, 0] - amount
        near = near.astype(np.uint8)
        watch = watch_of()
        payload, extras = update({("player", 0): original}, {("player", 0): ("AS", True, False)})
        watch.observe(0.0, 1, payload, extras)
        payload, extras = update({("player", 0): near}, {("player", 0): ("AS", True, False)})
        entry = watch.observe(0.5, 1, payload, extras)["player_0"]
        assert entry["nd"] == amount and entry["hit"] == (tracker._cached_read(near) is not None) == (amount <= TOL), amount


def test_a_missed_picture_becomes_a_cached_one_a_hit_does_not_and_a_queued_one_never_does():
    watch = watch_of()
    first = {("player", 0): pic("AS"), ("player", 1): pic("KH")}
    payload, extras = update(first, {("player", 0): ("AS", True, False), ("player", 1): (None, False, True)})        # the second is queued
    entry = watch.observe(0.0, 1, payload, extras)
    assert entry["player_0"]["hit"] is False and entry["player_1"]["hit"] is False
    assert [e.id for e in watch._pool] == [0]                                  # only the one that was read became a cached picture
    payload, extras = update({("player", 0): noisy(pic("AS"), 1)}, {("player", 0): ("AS", True, False)})
    assert watch.observe(0.5, 1, payload, extras)["player_0"]["hit"] is True      # within the tolerance of the cached one
    assert len(watch._pool) == 1                                               # ...so nothing new was cached


def test_the_mirror_forgets_the_oldest_picture_when_it_is_full():
    watch = watch_of(pool_size=3)
    for number, card in enumerate(("AS", "KH", "2C", "7D")):
        payload, extras = update({("player", 0): pic(card)}, {("player", 0): (card, True, False)}, round_id=number)
        watch.observe(number * 0.5, number, payload, extras)
    assert [e.id for e in watch._pool] == [1, 2, 3]


def test_the_mirror_takes_the_trackers_numbers_unless_told_otherwise():
    watch = pnl.PanelWatch()
    assert (watch.tolerance, watch.pool_size) == (rp.PanelTracker.CACHE_TOLERANCE, rp.PanelTracker.CACHE_SIZE)
    assert pnl.PanelWatch(tolerance=0, pool_size=5).tolerance == 0


# -- differences from the previous and the first picture -----------------------------------------------------------------------------------------

def test_differences_from_the_previous_poll_and_from_the_first_picture_at_a_position():
    watch = watch_of()
    base = pic("AS")
    steps = [base, np.clip(base.astype(int) + 3, 0, 255).astype(np.uint8), np.clip(base.astype(int) + 9, 0, 255).astype(np.uint8)]
    seen = []
    for number, picture_ in enumerate(steps):
        payload, extras = update({("player", 0): picture_}, {("player", 0): ("AS", True, False)})
        seen.append(watch.observe(number * 0.5, 1, payload, extras)["player_0"])
    assert [(e["pd"], e["id"]) for e in seen] == [(None, None), (pnl.max_diff(steps[1], steps[0]),) * 2, (pnl.max_diff(steps[2], steps[1]), pnl.max_diff(steps[2], steps[0]))]
    assert seen[1]["pd"] <= 3 and seen[2]["id"] > seen[2]["pd"]


def test_a_position_that_vanishes_starts_again_when_it_returns():
    watch = watch_of()
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)})
    watch.observe(0.0, 1, payload, extras)
    payload, extras = update({}, {})
    assert watch.observe(0.5, 1, payload, extras) == {}
    payload, extras = update({("player", 0): pic("KH")}, {("player", 0): ("KH", True, False)})
    back = watch.observe(1.0, 1, payload, extras)["player_0"]
    assert back["pd"] is None and back["id"] is None                          # nothing from before it vanished is compared with it


def test_a_picture_of_another_size_is_not_compared_and_is_counted():
    watch = watch_of()
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)})
    watch.observe(0.0, 1, payload, extras)
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)}, regions={("player", 0): dict(box("player", 0), width=W - 1)})
    entry = watch.observe(0.5, 1, payload, extras)["player_0"]
    assert entry["pd"] is None and watch.shape_changed == 1 and entry["nd"] is None


# -- red and black ---------------------------------------------------------------------------------------------------------------------------------

def test_colours_come_from_the_suit_and_unknown_is_unknown():
    assert [pnl.colour_of(card) for card in ("AH", "10D", "7S", "3C", None, "", "??")] == ["red", "red", "black", "black", "unknown", "unknown", "unknown"]


def test_readings_are_counted_by_what_the_reader_did_and_the_colour_it_proposed():
    watch = watch_of()
    pictures = {("player", i): pic(c) for i, c in enumerate(("AS", "KH", "2C", "7D"))}
    readings = {("player", 0): ("AS", True, False), ("player", 1): ("KH", False, False), ("player", 2): (None, False, True), ("player", 3): ("7D", True, False)}
    payload, extras = update(pictures, readings)
    watch.observe(0.0, 1, payload, extras)
    counts = watch.summary()["readings"]
    assert (counts["accepted"], counts["refused"], counts["queued"]) == (2, 1, 1)
    assert counts["by_kind_and_colour_of_the_proposed_card"] == {"accepted/black": 1, "accepted/red": 1, "refused/red": 1, "queued/unknown": 1}


# -- labels: given only when two readers agree, never because a round was confirmed -----------------------------------------------------------------

def labelled_and_reasons(watch):
    summary = watch.summary()
    return summary["labelled_samples"], summary["unlabelled_samples"], summary["unlabelled_reasons"]


def test_a_confirmed_round_whose_thumbnails_all_agree_is_labelled_in_full():
    watch = watch_of()
    play(watch)
    labelled, unlabelled, reasons = labelled_and_reasons(watch)
    assert (labelled, unlabelled, reasons) == (30, 0, {})
    out = watch.summary()["labelled"]
    assert out["cache_would_reuse_a_different_cards_read"] == 0 and out["wrong_reuse_examples"] == []
    for colour in ("red", "black"):
        assert out["consecutive_polls_same_card"][colour]["max"] <= 2 * 2                      # the casino's noise, here at most two levels each way
        assert out["nearest_cached_picture_of_a_different_card"][colour]["min"] > 100          # no other card's picture is anywhere near


@pytest.mark.parametrize("status", ["none", "unconfirmed", "conflict"])
def test_a_round_that_is_not_confirmed_labels_nothing(status):
    watch = watch_of()
    play(watch, status=status)
    assert labelled_and_reasons(watch) == (0, 30, {"round not confirmed": 30})


def test_a_confirmed_round_alone_does_not_label_a_refused_thumbnail():
    watch = watch_of()
    play(watch, readings_for=lambda n, readings: {**readings, ("player", 0): ("AS", False, False)})
    labelled, unlabelled, reasons = labelled_and_reasons(watch)
    assert (labelled, unlabelled) == (27, 3) and reasons == {"reading refused or absent": 3}


def test_a_thumbnail_read_as_another_card_than_the_final_one_at_its_position_stays_unlabelled():
    watch = watch_of()
    play(watch, readings_for=lambda n, readings: {**readings, ("player", 0): ("5S", True, False)})
    assert labelled_and_reasons(watch) == (27, 3, {"not the final confirmed card at this position": 3})


def test_a_card_that_appears_twice_in_the_final_row_is_not_labelled():
    rows = {"player": ["AS", "AS", "2C", "7D", "9S"], "dealer": ROWS["dealer"]}
    watch = watch_of()
    play(watch, rows=rows)
    labelled, unlabelled, reasons = labelled_and_reasons(watch)
    assert (labelled, unlabelled) == (24, 6) and reasons == {"card repeated in the final row": 6}


def test_a_card_the_table_does_not_hold_for_that_seat_is_not_labelled():
    rows = {"player": ["5S", "KH", "2C", "7D", "9S"], "dealer": ROWS["dealer"]}                   # the panel row says 5S; the table never saw it
    watch = watch_of()
    play(watch, rows=rows)
    assert labelled_and_reasons(watch) == (27, 3, {"not among the table's confirmed cards for this seat": 3})


def test_a_row_that_never_reached_five_cards_labels_nothing_of_its_side():
    rows = {"player": ROWS["player"][:4], "dealer": ROWS["dealer"]}
    watch = watch_of()
    play(watch, rows=rows)
    assert labelled_and_reasons(watch) == (15, 12, {"no complete confirmed panel row": 12})


def test_a_stale_row_is_never_labelled():
    watch = watch_of()
    play(watch, stale=("player",))
    assert labelled_and_reasons(watch) == (15, 15, {"stale panel row (an earlier round's)": 15})


def test_samples_outside_any_round_are_unlabelled_and_say_so():
    watch = watch_of()
    play(watch, state="WAITING", polls=1)
    assert labelled_and_reasons(watch) == (0, 10, {"outside any round": 10})


def test_labels_are_decided_when_the_round_ends_and_a_provisional_summary_changes_nothing():
    watch = watch_of()
    play(watch, round_id=1)
    first, second = watch.summary(), watch.summary()
    assert first == second and first["rounds"]["still_open_evaluated_provisionally"] == 1 and first["labelled_samples"] == 30
    play(watch, round_id=2, t0=10.0, state="IN_PROGRESS", status="none")                      # the next round begins: round 1 is evaluated for good
    after = watch.summary()
    assert after["rounds"]["evaluated"] == 1 and after["rounds"]["still_open_evaluated_provisionally"] == 1
    assert after["labelled_samples"] == 30 and after["unlabelled_reasons"] == {"round not confirmed": 30}


# -- changed against unchanged cards ----------------------------------------------------------------------------------------------------------------

def test_a_card_that_changes_between_consecutive_polls_is_measured_apart_from_one_that_does_not():
    watch = watch_of()
    play(watch, round_id=1, polls=3)
    rows2 = {"player": ["5S", "KH", "2C", "7D", "9S"], "dealer": ROWS["dealer"]}
    cards2 = dict(CARDS, player_1="5S")
    play(watch, rows=rows2, cards=cards2, round_id=2, t0=10.0, polls=3)
    out = watch.summary()["labelled"]
    assert out["consecutive_polls_different_card"]["black"]["n"] == 1                         # AS -> 5S at one position, one pair
    assert out["consecutive_polls_different_card"]["black"]["min"] > 100
    assert out["consecutive_polls_same_card"]["black"]["max"] <= 4 and out["consecutive_polls_same_card"]["red"]["n"] > 0


def test_two_different_cards_whose_pictures_lie_within_the_tolerance_are_caught_as_a_wrong_reuse():
    """The case the whole measurement exists to find: the mirrored cache would give one card the other's read."""
    watch = watch_of()
    twin = np.clip(pic("AS").astype(int) + 3, 0, 255).astype(np.uint8)                          # the 'KH' picture is the 'AS' picture plus 3
    for number in range(3):
        pictures = {("player", 0): pic("AS"), ("player", 1): twin, ("player", 2): pic("2C"), ("player", 3): pic("7D"), ("player", 4): pic("9S")}
        readings = {("player", i): (card, True, False) for i, card in enumerate(ROWS["player"])}
        payload, extras = update(pictures, readings, {"player": ROWS["player"]})
        watch.observe(number * 0.5, 1, payload, extras)
    out = watch.summary()["labelled"]
    assert out["cache_would_reuse_a_different_cards_read"] >= 1
    example = out["wrong_reuse_examples"][0]
    assert (example["label"], example["would_reuse_the_read_of"], example["difference"]) == ("KH", "AS", 3)
    assert out["nearest_cached_picture_of_a_different_card"]["red"]["min"] <= 3


def test_a_card_that_is_read_again_although_it_was_cached_is_counted_with_how_far_off_it_was():
    watch = watch_of()
    bright = np.clip(pic("AS").astype(int) + 30, 0, 255).astype(np.uint8)                       # the same card, a highlight on it (the casino does this)
    for number, picture_ in enumerate((pic("AS"), bright, bright, bright)):
        pictures = {("player", i): (picture_ if i == 0 else pic(card)) for i, card in enumerate(ROWS["player"])}
        readings = {("player", i): (card, True, False) for i, card in enumerate(ROWS["player"])}
        payload, extras = update(pictures, readings, {"player": ROWS["player"]})
        watch.observe(number * 0.5, 1, payload, extras)
    out = watch.summary()["labelled"]
    assert out["read_again_although_the_same_card_was_cached"] == 1
    assert out["consecutive_polls_same_card"]["black"]["max"] >= 28


def test_unlabelled_observations_are_kept_apart_and_claim_nothing():
    watch = watch_of()
    play(watch, status="unconfirmed", polls=3)
    summary = watch.summary()
    assert summary["labelled"]["consecutive_polls_same_card"] == {} and summary["labelled_samples"] == 0
    unlabelled = summary["unlabelled"]["consecutive_polls_by_colour_of_the_proposed_card"]
    assert unlabelled["black"]["n"] > 0 and unlabelled["red"]["n"] > 0 and "make no claim" in summary["unlabelled"]["note"]


# -- bounded, thumbnail-only evidence ------------------------------------------------------------------------------------------------------------------

def churn(watch, polls):
    """Polls in which every thumbnail is a wholly new picture every time: the most evidence anything could ask to keep."""
    for number in range(polls):
        pictures = {(side, i): picture(number * 100 + i + (side == "dealer") * 50) for side in pnl.SIDES for i in range(5)}
        payload, extras = update(pictures, {(side, i): ("AS", True, False) for side in pnl.SIDES for i in range(5)}, round_id=1, state="IN_PROGRESS", status="none")
        watch.observe(number * 0.5, 1, payload, extras)


@pytest.mark.parametrize("cap", [1, 5, 12, 80])
def test_never_more_samples_are_saved_than_the_cap_and_every_skip_is_counted(cap):
    saver = Saver()
    watch = watch_of(saver=saver, max_captures=cap)
    churn(watch, 40)
    capture = watch.summary()["capture"]
    assert len(saver.saved) == capture["saved"] <= cap
    assert capture["saved"] + sum(capture["skipped_by_limit"].values()) > cap                  # churn asks for far more than any cap, and says so
    assert capture["limit"] == cap
    for kind, share in pnl.KIND_SHARE.items():
        assert capture["saved_by_kind"].get(kind, 0) <= max(1, -(-int(cap * share * 100) // 100)), kind              # ceil(cap * share), never below one


def test_no_one_kind_of_sample_can_use_the_whole_cap():
    saver = Saver()
    watch = watch_of(saver=saver, max_captures=10)
    churn(watch, 40)
    by_kind = watch.summary()["capture"]["saved_by_kind"]
    assert by_kind.get("changed", 0) <= 5 and by_kind.get("first_sight", 0) <= 2 and sum(by_kind.values()) <= 10


def test_a_zero_cap_or_no_saver_saves_nothing_and_still_measures():
    for kwargs in ({"saver": Saver(), "max_captures": 0}, {"saver": None, "max_captures": 80}):
        watch = watch_of(**kwargs)
        churn(watch, 5)
        summary = watch.summary()
        assert summary["capture"]["saved"] == 0 and summary["capture"]["capturing"] is False and summary["thumbnail_samples"] == 50
        if kwargs["saver"] is not None:
            assert kwargs["saver"].saved == []


def test_steady_polls_are_sampled_sparsely():
    saver = Saver()
    watch = watch_of(saver=saver, max_captures=80)
    for number in range(60):
        payload, extras = update({("player", 0): noisy(pic("AS"), number)}, {("player", 0): ("AS", True, False)}, round_id=1, state="IN_PROGRESS", status="none")
        watch.observe(number * 0.5, 1, payload, extras)
    kinds = [info["kind"] for _name, _images, info in saver.saved]
    assert kinds.count("first_sight") == 1 and kinds.count("steady") == 3 and "changed" not in kinds        # 59 steady polls, one in 25


def test_a_saver_that_fails_costs_the_capture_and_nothing_else():
    watch = watch_of(saver=Saver(fail=True), max_captures=10)
    churn(watch, 3)
    summary = watch.summary()
    assert summary["capture"]["capture_failed"] > 0 and summary["capture"]["saved"] == 0 and summary["thumbnail_samples"] == 30 and summary["diagnostic_errors"] == 0


def test_what_is_saved_is_thumbnail_sized_owns_its_pixels_and_claims_no_label():
    saver = Saver()
    watch = watch_of(saver=saver, max_captures=20)
    churn(watch, 4)
    assert saver.saved
    for name, images, info in saver.saved:
        assert set(images) <= {"cur", "prev", "initial"} and "cur" in images
        for image in images.values():
            assert image.shape == (H, W, 3) and image.flags["OWNDATA"] and max(image.shape[:2]) <= pnl.MAX_THUMBNAIL_SIDE
        assert info["label"].startswith("decided when the round ends") and "ground truth" in info["label_rule"]
        assert "/" not in name and "\\" not in name and ".." not in name


def test_a_picture_too_large_to_be_a_thumbnail_is_neither_measured_nor_saved():
    saver = Saver()
    watch = watch_of(saver=saver, max_captures=20)
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)},
                             regions={("player", 0): {"left": 0, "top": 0, "width": 300, "height": 200}})
    assert watch.observe(0.0, 1, payload, extras) == {}
    assert watch.summary()["capture"]["not_thumbnail_sized_skipped"] == 1 and watch.summary()["thumbnail_samples"] == 0 and saver.saved == []


# -- it changes nothing ----------------------------------------------------------------------------------------------------------------------------------

def test_the_update_and_the_frame_are_left_exactly_as_they_were():
    watch = watch_of(saver=Saver(), max_captures=10)
    pictures = {(side, i): noisy(pic(card), i) for side in ROWS for i, card in enumerate(ROWS[side])}
    readings = {(side, i): (card, True, False) for side in ROWS for i, card in enumerate(ROWS[side])}
    payload, extras = update(pictures, readings, ROWS)
    extras["read_frame"].flags.writeable = False                                              # a write to the frame would raise
    before = (copy.deepcopy(payload), extras["read_frame"].tobytes(), extras["origin"])
    for number in range(3):
        watch.observe(number * 0.5, 1, payload, extras)
    watch.summary()
    assert payload == before[0] and extras["read_frame"].tobytes() == before[1] and extras["origin"] == before[2]


def test_the_saved_pictures_are_copies_so_they_outlive_the_trackers_frame():
    saver = Saver()
    watch = watch_of(saver=saver, max_captures=10)
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)})
    watch.observe(0.0, 1, payload, extras)
    saved = saver.saved[0][1]["cur"].copy()
    extras["read_frame"][:] = 0                                                                # the tracker reuses its frame; the evidence must not change
    assert (saver.saved[0][1]["cur"] == saved).all() and saved.any()


def test_malformed_updates_give_an_entry_and_never_raise():
    watch = watch_of()
    for payload, extras in (({}, {}), (None, None), ({"panels": None}, {"read_frame": None}), ({"panels": {"sides": {"player": {"reads": [None, {}, {"region": 5}]}}}},
                                                                                              {"read_frame": np.zeros((9, 9, 3), np.uint8)}),
                            ({"lifecycle": {"state": "COMPLETE", "round_id": 1}, "panels": {"sides": {"player": {"reads": [{"region": box("player", 0)}]}}}},
                             {"read_frame": np.zeros((4, 4, 3), np.uint8)})):
        assert isinstance(watch.observe(0.0, 1, payload, extras), dict)
    assert watch.summary()["thumbnail_samples"] == 0


def test_a_part_that_blows_up_is_counted_and_the_poll_still_gets_its_entry(monkeypatch):
    watch = watch_of()
    def boom(a, b):
        raise RuntimeError("the comparison failed")
    monkeypatch.setattr(pnl, "max_diff", boom)
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)})
    watch.observe(0.0, 1, payload, extras)
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)})
    entry = watch.observe(0.5, 1, payload, extras)
    assert entry["error"].startswith("RuntimeError") and watch.summary()["diagnostic_errors"] == 1 and watch.summary()["first_error"].startswith("RuntimeError")


def test_the_module_imports_nothing_that_captures_sleeps_writes_or_phones_home():
    tree = ast.parse(open(os.path.join(ROOT, "panel_diagnostics.py"), encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    forbidden = {"threading", "socket", "requests", "urllib", "mss", "pyautogui", "pynput", "subprocess", "tracker", "app", "capture", "psycopg", "sqlite3",
                 "webbrowser", "win32api", "keyboard", "shutil", "pathlib", "os", "tempfile"}
    assert not (imported & forbidden), imported
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for banned in ("sleep", "imwrite", "imread", "grab", "grab_full_screen", "open", "click", "press", "write", "makedirs", "environ", "connect", "setNumThreads"):
        assert banned not in names, banned


# -- the harness: the opt-in flag, the saver and where evidence goes -----------------------------------------------------------------------------------------

def test_the_flag_is_opt_in_and_the_cap_is_its_own():
    harness = load_harness()
    off = harness.arguments([])
    assert off.panel_diagnostics is False and off.max_panel_captures == 80
    on = harness.arguments(["--panel-diagnostics", "--max-panel-captures", "0"])
    assert on.panel_diagnostics is True and on.max_panel_captures == 0


def test_the_harness_wires_the_watch_only_when_asked_and_a_saver_only_above_a_zero_cap():
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    assert "if args.panel_diagnostics:" in source
    assert "saver=PanelSaver(evidence) if args.max_panel_captures > 0 else None, max_captures=args.max_panel_captures" in source
    assert "tolerance=result_panel.PanelTracker.CACHE_TOLERANCE, pool_size=result_panel.PanelTracker.CACHE_SIZE" in source
    assert "panel_watch=panel_watch" in source


def test_the_saver_keeps_to_a_folder_of_its_own_never_overwrites_and_will_not_save_a_frame(tmp_path):
    harness = load_harness()
    saver = harness.PanelSaver(str(tmp_path))
    assert not (tmp_path / "panel_thumbnails").exists()                                         # made on the first save only
    first = saver("0001_changed_player0_round1_t1.0", {"cur": pic("AS"), "prev": pic("KH")}, {"kind": "changed"})
    folder = tmp_path / "panel_thumbnails"
    assert sorted(os.listdir(folder)) == sorted([first + "_cur.png", first + "_prev.png", first + ".json"])
    before = {name: (folder / name).read_bytes() for name in os.listdir(folder)}
    second = saver("0001_changed_player0_round1_t1.0", {"cur": pic("2C")}, {"kind": "again"})
    assert second == first + "_2" and all((folder / name).read_bytes() == data for name, data in before.items())
    with pytest.raises(ValueError):
        saver("whole_frame", {"cur": np.zeros((220, 420, 3), np.uint8)}, {})                     # a whole frame: refused, and nothing written for it
    with pytest.raises(ValueError):
        saver("one_of_two", {"cur": pic("AS"), "prev": np.zeros((9, 400, 3), np.uint8)}, {})     # even beside a thumbnail
    assert not any(name.startswith("whole_frame") or name.startswith("one_of_two") for name in os.listdir(folder))


@pytest.mark.parametrize("name", ["../../escape", "..\\..\\escape", "C:\\Windows\\escape", "/etc/escape", "a/b\\c", "....hidden"])
def test_a_name_can_not_take_the_saver_out_of_its_folder(tmp_path, name):
    harness = load_harness()
    evidence = tmp_path / "evidence"
    saver = harness.PanelSaver(str(evidence))
    saver(name, {"cur": pic("AS")}, {})
    everything = [os.path.relpath(os.path.join(r, f), tmp_path) for r, _d, files in os.walk(tmp_path) for f in files]
    assert everything and all(path.replace("\\", "/").startswith("evidence/panel_thumbnails/") for path in everything), everything
    assert not any(".." in path.split("panel_thumbnails")[1] for path in everything)


def test_a_whole_run_leaves_thumbnails_and_nothing_else_in_the_evidence_folder(tmp_path):
    harness = load_harness()
    evidence = tmp_path / "e"
    watch = watch_of(saver=harness.PanelSaver(str(evidence)), max_captures=6)
    churn(watch, 10)
    files = [os.path.join(r, f) for r, _d, names in os.walk(evidence) for f in names]
    assert files and all(os.path.dirname(path) == str(evidence / "panel_thumbnails") for path in files)
    assert len([p for p in files if p.endswith(".json")]) == watch.summary()["capture"]["saved"] <= 6
    for path in files:
        if path.endswith(".png"):
            image = cv2.imread(path)
            assert image.shape == (H, W, 3)
    assert os.listdir(tmp_path) == ["e"]                                                       # nothing was written anywhere else


# -- through the recorder and the report ------------------------------------------------------------------------------------------------------------------

def fed(recorder, polls=6, status="confirmed"):
    for number in range(polls):
        pictures = {(side, i): noisy(pic(card), number * 13 + i) for side in ROWS for i, card in enumerate(ROWS[side])}
        readings = {(side, i): (card, True, False) for side in ROWS for i, card in enumerate(ROWS[side])}
        payload, extras = update(pictures, readings, ROWS, status=status)
        payload["emitted_mono"] = 100.0 + number * 0.5
        recorder.observe(payload, extras)


def test_through_the_recorder_every_poll_line_carries_thumbnail_entries_and_the_summary_and_report_say_so(tmp_path):
    harness = load_harness()
    recorder = rv.RoundRecorder(panel_watch=watch_of(saver=harness.PanelSaver(str(tmp_path / "e")), max_captures=10))
    fed(recorder)
    assert all("panel_thumbs" in line and "player_0" in line["panel_thumbs"] for line in recorder.polls)
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    assert summary["panel_diagnostics"]["thumbnail_samples"] == 60 and summary["panel_diagnostics"]["labelled_samples"] == 60
    assert all("panel_thumbs" in json.loads(line) for line in open(tmp_path / "e" / "polls.jsonl"))
    text = harness.report(rounds, summary, {"mode": "test", "evidence_dir": "x", "panel_reader": "p", "cache_tolerance": 1, "reads_per_poll": 2,
                                           "suit_margin": 0.05, "rank_margin": 0.1, "corroborated_suit_margin": 0.02})
    for expected in ("## Result-panel thumbnails", "### Labelled samples", "### Unlabelled observations", "not ground truth", "no whole frame is ever saved",
                     "a different card's read 0 time(s)"):
        assert expected in text, expected


def test_the_report_keeps_confirmed_labels_and_unlabelled_observations_apart(tmp_path):
    harness = load_harness()
    recorder = rv.RoundRecorder(panel_watch=watch_of())
    fed(recorder, status="unconfirmed")
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    text = harness.report(rounds, summary, {"mode": "test", "evidence_dir": "x", "panel_reader": "p", "cache_tolerance": 1, "reads_per_poll": 2,
                                           "suit_margin": 0.05, "rank_margin": 0.1, "corroborated_suit_margin": 0.02})
    assert "Labelled samples: 0." in text and '"round not confirmed": 60' in text and "colour of the PROPOSED card" in text


def test_a_recorder_without_the_watch_writes_what_it_wrote_before(tmp_path):
    recorder = rv.RoundRecorder()
    fed(recorder, polls=2)
    rv.write_evidence(str(tmp_path / "e"), recorder)
    assert "panel_diagnostics" not in json.load(open(tmp_path / "e" / "summary.json"))
    assert "panel_thumbs" not in json.loads(open(tmp_path / "e" / "polls.jsonl").readline())


def test_a_watch_that_blows_up_does_not_stop_the_recorder():
    class Exploding:
        def observe(self, *a, **k):
            raise RuntimeError("boom")

        def summary(self):
            return {}
    recorder = rv.RoundRecorder(panel_watch=Exploding())
    payload, extras = update({("player", 0): pic("AS")}, {("player", 0): ("AS", True, False)})
    payload["emitted_mono"] = 1.0
    line = recorder.observe(payload, extras)
    assert line["panel_thumbs"]["error"].startswith("RuntimeError") and recorder.polls == [line]


def test_the_watch_has_no_say_in_what_the_recorder_records_about_the_round(tmp_path):
    plain, watched = rv.RoundRecorder(), rv.RoundRecorder(panel_watch=watch_of())
    fed(plain)
    fed(watched)
    strip = lambda lines: [{k: v for k, v in line.items() if k != "panel_thumbs"} for line in lines]        # noqa: E731
    assert strip(plain.polls) == strip(watched.polls) and plain.finish() == watched.finish()
