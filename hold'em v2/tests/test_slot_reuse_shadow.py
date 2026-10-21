"""Shadow mode for table-slot read reuse: it measures a reuse rule and changes nothing.

The rule (recognition/slot_reuse.py) says when a board / player slot's last read could be reused instead of reading the slot again. In
shadow mode the tracker still reads every slot exactly as before; the module only records, per slot per poll, why reuse would have been
refused or - when it would have been allowed - whether the fresh read agreed. These tests pin both halves: the measurement is right, and
it cannot touch recognition, card memory, votes, the lifecycle (so the alert and the voice that follow it), read counts, or the disk.
"""

import builtins
import glob
import json
import logging
import os
import queue

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from recognition import slot_reuse as sr                                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLOT = "flop_1"
BOX = (700, 430, 108, 122)


# -- the module on its own ---------------------------------------------------------------------------------------------------------------

def deck_image(card):
    from fake_cards import fonts_available, render_card
    if not fonts_available():
        pytest.skip("no fonts to render the deck")
    return render_card(card, 108, 122, "side")


def noisy(image, sigma, seed):
    rng = np.random.default_rng(seed)
    return np.clip(image.astype(np.float32) + rng.normal(0, sigma, image.shape), 0, 255).astype(np.uint8)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def state(card="7H", settled=True, held=False):
    return {"card": card, "settled": settled, "held": held}


def read(card="7H", confident=True, present=True):
    return {"card": card if confident else None, "confident": confident, "present": present, "confidence": 0.9 if confident else 0.3}


def step(shadow, image, slot=SLOT, memory=None, fresh=None, covered=False, layout=True, generation=0, box=BOX):
    """One poll in which only `slot` has a picture; returns the reason recorded for it."""
    before = dict(shadow.reasons)
    images = {} if image is None else {slot: image}
    shadow.observe(images, {slot} if covered else set(), {slot: fresh if fresh is not None else read()}, layout, generation,
                   {slot: memory if memory is not None else state()}, {slot: box})
    delta = {key: shadow.reasons.get(key, 0) - before.get(key, 0) for key in shadow.reasons}
    delta[sr.R_NO_PICTURE] = delta.get(sr.R_NO_PICTURE, 0) - (len(sr.SHADOW_SLOTS) - 1 if image is not None else len(sr.SHADOW_SLOTS) - 1)
    return [key for key, count in delta.items() if count > 0][0] if any(count > 0 for count in delta.values()) else sr.R_NO_PICTURE


def patient(**kw):
    """A rule with the age limits out of the way, so a test sees only the picture and the safeguards under test."""
    return sr.ShadowReuse(clock=Clock(), max_age_polls=10 ** 6, max_age_seconds=10 ** 9, **kw)


def test_the_first_poll_has_no_reference_and_an_identical_picture_is_then_eligible_and_agrees():
    image = deck_image("7H")
    shadow = patient()
    assert step(shadow, image) == sr.R_NO_REFERENCE
    assert step(shadow, image.copy()) == sr.ELIGIBLE
    summary = shadow.summary()
    assert summary["eligible"] == 1 and summary["eligible_outcomes"] == {"agree": 1} and summary["disagreements"] == 0


@pytest.mark.parametrize("sigma", [1, 2, 3, 5])
def test_ordinary_noise_stays_eligible_and_always_agrees(sigma):
    image = deck_image("KS")
    shadow = patient()
    step(shadow, image, memory=state("KS"), fresh=read("KS"))
    for seed in range(20):
        assert step(shadow, noisy(image, sigma, seed), memory=state("KS"), fresh=read("KS")) == sr.ELIGIBLE
    assert shadow.summary()["eligible_outcomes"] == {"agree": 20} and shadow.summary()["disagreements"] == 0


def test_every_different_card_is_refused_by_the_picture_and_never_counted_as_a_disagreement():
    base = deck_image("7S")
    others = [rank + suit for rank in "A23456789JQK" for suit in "SHDC"] + ["10S", "10H", "10D", "10C"]
    refused = 0
    for card in others:
        if card == "7S":
            continue
        shadow = patient()
        step(shadow, base, memory=state("7S"), fresh=read("7S"))
        reason = step(shadow, deck_image(card), memory=state("7S"), fresh=read(card))
        assert reason in (sr.R_CHANGED_MAX, sr.R_CHANGED_SHARE, sr.R_CHANGED_MEAN), (card, reason)
        assert shadow.summary()["disagreements"] == 0 and shadow.summary()["eligible"] == 0
        assert sr.O_CHANGED in shadow.summary()["differences_by_fresh_result"]
        refused += 1
    assert refused >= 50


def test_a_rule_loose_enough_to_reuse_across_different_cards_is_counted_as_a_disagreement():
    shadow = patient(max_diff=255, pixel_share=1.0, mean_limit=255.0)
    step(shadow, deck_image("7S"), memory=state("7S"), fresh=read("7S"))
    assert step(shadow, deck_image("7C"), memory=state("7S"), fresh=read("7C")) == sr.ELIGIBLE
    summary = shadow.summary()
    assert summary["disagreements"] == 1 and summary["eligible_outcomes"] == {"disagree": 1}
    assert summary["disagreement_examples"][0]["outcome"] == "disagree"
    assert set(summary["disagreement_examples"][0]) == {"slot_type", "outcome", "max", "mean", "share_pct", "age_polls"}


def test_a_slot_that_has_gone_empty_under_an_unchanged_picture_is_counted_as_unsafe():
    shadow = patient()
    image = deck_image("5C")
    step(shadow, image, memory=state("5C"), fresh=read("5C"))
    assert step(shadow, image, memory=state("5C"), fresh=read(confident=False, present=False)) == sr.ELIGIBLE
    assert shadow.summary()["eligible_outcomes"] == {"empty now": 1} and shadow.summary()["disagreements"] == 1


def test_a_fresh_read_that_is_present_but_not_confident_is_neither_agreement_nor_disagreement():
    shadow = patient()
    image = deck_image("9D")
    step(shadow, image, memory=state("9D"), fresh=read("9D"))
    step(shadow, image, memory=state("9D"), fresh=read(confident=False, present=True))
    summary = shadow.summary()
    assert summary["eligible_outcomes"] == {"fresh not confident": 1} and summary["disagreements"] == 0


@pytest.mark.parametrize("name, second, expected", [
    ("covered", dict(covered=True), sr.R_COVERED),
    ("unsettled", dict(memory=state("7H", settled=False)), sr.R_UNSETTLED),
    ("memory holds another card", dict(memory=state("2C")), sr.R_MEMORY_DIFFERS),
    ("held", dict(memory=state("7H", held=True)), sr.R_HELD),
    ("moved", dict(box=(702, 430, 108, 122)), sr.R_MOVED),
    ("reset", dict(generation=1), sr.R_RESET),
    ("layout not found", dict(layout=False), sr.R_LAYOUT),
])
def test_a_covered_unsettled_moved_held_or_reset_slot_is_refused(name, second, expected):
    image = deck_image("7H")
    shadow = patient()
    step(shadow, image)
    assert step(shadow, image, **second) == expected
    assert shadow.summary()["eligible"] == 0


def test_a_reference_that_was_not_decisive_is_refused_even_when_the_picture_is_identical():
    image = deck_image("7H")
    shadow = patient()
    step(shadow, image, fresh=read(confident=False, present=True))
    assert step(shadow, image) == sr.R_NOT_DECISIVE


def test_a_different_size_is_refused():
    shadow = patient()
    step(shadow, deck_image("7H"))
    assert step(shadow, cv2.resize(deck_image("7H"), (100, 112))) == sr.R_SIZE


def test_the_reference_is_dropped_by_covering_a_missing_picture_or_a_lost_layout_so_nothing_older_is_trusted():
    image = deck_image("7H")
    for interruption in (dict(covered=True), dict(layout=False)):
        shadow = patient()
        step(shadow, image)
        assert step(shadow, image, **interruption) in (sr.R_COVERED, sr.R_LAYOUT)
        assert step(shadow, image) == sr.R_NO_REFERENCE             # the picture before the interruption is not a reference
    shadow = patient()
    step(shadow, image)
    assert step(shadow, None) == sr.R_NO_PICTURE
    assert step(shadow, image) == sr.R_NO_REFERENCE


def test_a_reference_is_reused_a_bounded_number_of_polls_and_then_read_again():
    clock = Clock()
    shadow = sr.ShadowReuse(clock=clock, max_age_polls=3, max_age_seconds=1000)
    image = deck_image("7H")
    step(shadow, image)
    assert [step(shadow, image) for _ in range(3)] == [sr.ELIGIBLE] * 3
    assert step(shadow, image) == sr.R_AGE                                # the 4th reuse is refused: a real read would be made
    assert step(shadow, image) == sr.ELIGIBLE                             # and the new reference starts counting afresh


def test_a_reference_is_reused_for_a_bounded_time_and_then_read_again():
    clock = Clock()
    shadow = sr.ShadowReuse(clock=clock, max_age_polls=1000, max_age_seconds=1.5)
    image = deck_image("7H")
    step(shadow, image)
    clock.now += 1.0
    assert step(shadow, image) == sr.ELIGIBLE
    clock.now += 0.6
    assert step(shadow, image) == sr.R_AGE


def test_the_reference_only_moves_when_a_real_read_would_have_happened():
    seven, king = deck_image("7H"), deck_image("KD")
    shadow = patient()
    step(shadow, seven, memory=state("7H"), fresh=read("7H"))
    assert step(shadow, seven, memory=state("7H"), fresh=read("7H")) == sr.ELIGIBLE
    assert step(shadow, king, memory=state("7H"), fresh=read("KD")) in (sr.R_CHANGED_MAX, sr.R_CHANGED_SHARE, sr.R_CHANGED_MEAN)
    assert step(shadow, king, memory=state("KD"), fresh=read("KD")) == sr.ELIGIBLE      # compared with the king, not the seven
    assert shadow.summary()["eligible_outcomes"] == {"agree": 2}


def test_dealer_slots_are_not_measured():
    shadow = patient()
    image = deck_image("7H")
    shadow.observe({"dealer_1": image, "dealer_2": image}, set(), {"dealer_1": read(), "dealer_2": read()}, True, 0, {}, {})
    assert shadow.summary()["slot_checks"] == len(sr.SHADOW_SLOTS) and "dealer_1" not in json.dumps(shadow.summary())


def test_the_summary_is_numbers_and_fixed_words_only_and_nothing_is_written(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("shadow mode wrote something")

    image = deck_image("QH")                                          # rendering needs the font files; the measurement must not need any file
    monkeypatch.setattr(cv2, "imwrite", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(np, "save", forbidden)
    monkeypatch.chdir(tmp_path)
    shadow = patient()
    for seed in range(10):
        step(shadow, noisy(image, 3, seed), memory=state("QH"), fresh=read("QH"))
    text = shadow.log_line()
    monkeypatch.undo()
    summary = json.loads(text.split(" ", 1)[1])
    assert text.startswith("SHADOW_REUSE ") and len(text) < 6000
    assert list(tmp_path.iterdir()) == []

    def walk(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                assert isinstance(key, str)
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)
        else:
            assert isinstance(value, (int, float, str, type(None), bool))

    walk(summary)
    assert not any(word in json.dumps(summary).lower() for word in ("crop", "image", "screenshot", "pixel_data", "base64"))


def test_the_module_has_no_way_to_store_a_picture():
    source = open(os.path.join(ROOT, "recognition", "slot_reuse.py"), encoding="utf-8").read()
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    for token in ("imwrite", "open(", ".tofile", "np.save", "pickle", "write(", "logging", "getLogger", "base64", "imencode"):
        assert token not in code, token


def test_a_measurement_that_fails_is_counted_and_never_raised():
    shadow = patient()
    shadow.observe(None, set(), {}, True, 0, {}, {})                 # not even a dict of pictures
    assert shadow.summary()["errors"] == 1


def test_the_measurement_costs_well_under_a_poll():
    shadow = patient()
    image = deck_image("7H")
    for seed in range(30):
        shadow.observe({slot: noisy(image, 2, seed) for slot in sr.SHADOW_SLOTS}, set(), {slot: read() for slot in sr.SHADOW_SLOTS}, True, 0,
                       {slot: state() for slot in sr.SHADOW_SLOTS}, {slot: BOX for slot in sr.SHADOW_SLOTS})
    assert shadow.summary()["overhead_ms_per_poll"] < 20


# -- inside the tracker -------------------------------------------------------------------------------------------------------------------

SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))
needs_table = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project folder")


class Focus:
    holding_own_window = False


@pytest.fixture(scope="module")
def table():
    return cv2.imread(SCREENSHOTS[0])


@pytest.fixture(scope="module")
def frames(table):
    rng = np.random.default_rng(11)
    return [np.clip(table.astype(np.int16) + rng.normal(0, 2, table.shape), 0, 255).astype(np.uint8) for _ in range(10)]


def make_tracker(monkeypatch, shadow, counts, every=200, **extra):
    import tracker as tracker_module
    shown = {"frame": None}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["frame"])
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module.Tracker, "_store", lambda self, cards, reads=None: None)
    real = tracker_module.read_table

    def counting(config, fresh):
        counts.append(sorted(fresh))
        return real(config, fresh)

    monkeypatch.setattr(tracker_module, "read_table", counting)
    made = tracker_module.Tracker({"monitor": 1, "regions": {}, "clear_frames": 3, "game_window_title": "", "shadow_reuse": shadow,
                                   "shadow_reuse_every": every, **extra}, queue.Queue())
    made.focus = Focus()

    def poll(frame):
        shown["frame"] = frame
        made._tick()
        sent = []
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                sent.append(payload)
        return sent

    made.poll = poll
    return made


def digest(sent):
    """What the window, the lifecycle, the alert and the voice are driven by, from one poll's updates."""
    out = []
    for payload in sent:
        reads = {slot: (r.get("present"), r.get("confident"), r.get("card"), round(r.get("confidence", 0.0), 6)) for slot, r in payload["reads"].items()}
        out.append({key: payload.get(key) for key in ("state", "round_id", "cards", "seen", "statuses", "uncertain", "scenario", "lifecycle", "action")}
                   | {"reads": reads})
    return out


@needs_table
def test_shadow_mode_changes_no_recognition_memory_vote_lifecycle_or_read_count(monkeypatch, table, frames):
    results = {}
    for shadow in (False, True):
        counts = []
        with monkeypatch.context() as scope:
            made = make_tracker(scope, shadow, counts)
            sequence = [digest(made.poll(frame)) for frame in [table] * 3 + frames]
            results[shadow] = (sequence, counts, dict(made.memory.cards), json.dumps(made.memory.votes, sort_keys=True), made.memory.generation)
    assert results[True][0] == results[False][0]                    # every payload the window, voice and alert consume
    assert results[True][1] == results[False][1]                    # the very same slots read on the very same polls
    assert results[True][2] == results[False][2] and results[True][3] == results[False][3] and results[True][4] == results[False][4]
    assert sum(len(c) for c in results[True][1]) > 0


@needs_table
def test_in_the_tracker_noise_is_eligible_with_no_disagreement_and_the_reasons_are_recorded(monkeypatch, table, frames):
    counts = []
    made = make_tracker(monkeypatch, True, counts)
    for frame in [table] * 3 + frames:
        made.poll(frame)
    summary = made._shadow.summary()
    assert summary["polls"] == 13 and summary["slot_checks"] == 13 * 7
    assert summary["eligible"] > 0 and summary["disagreements"] == 0
    assert summary["refusal_reasons"].get(sr.R_NO_REFERENCE, 0) > 0
    assert sr.ELIGIBLE not in summary["refusal_reasons"]
    assert sum(summary["refusal_reasons"].values()) + summary["eligible"] == summary["slot_checks"]
    assert summary["errors"] == 0


@needs_table
def test_in_the_tracker_a_card_replaced_in_place_is_never_reused_and_is_not_a_disagreement(monkeypatch, table):
    made = make_tracker(monkeypatch, True, [])
    for frame in [table] * 4:
        made.poll(frame)
    regions = made._layout["regions"]
    swapped = table.copy()
    a, b = regions["flop_1"], regions["turn"]
    height, width = min(a["height"], b["height"]), min(a["width"], b["width"])
    swapped[a["top"]:a["top"] + height, a["left"]:a["left"] + width] = table[b["top"]:b["top"] + height, b["left"]:b["left"] + width]
    before = dict(made._shadow.reasons)
    eligible_before = made._shadow.summary()["eligible"]
    made.poll(swapped)
    after = made._shadow.summary()
    refused = {reason: made._shadow.reasons.get(reason, 0) - before.get(reason, 0) for reason in made._shadow.reasons if reason != sr.ELIGIBLE}
    # the replaced slot is refused - its picture changed, or the layout re-found a different box around it - and nothing was reused wrongly
    assert sum(count for reason, count in refused.items() if reason.startswith("picture changed") or reason == sr.R_MOVED) >= 1
    assert after["eligible"] - eligible_before <= len(sr.SHADOW_SLOTS) - 1
    assert after["disagreements"] == 0


@needs_table
def test_in_the_tracker_a_new_round_a_covered_slot_and_a_lost_table_are_all_refused(monkeypatch, table):
    made = make_tracker(monkeypatch, True, [])
    for frame in [table] * 4:
        made.poll(frame)
    made.memory.clear("test: a new round")
    made.poll(table)
    assert made._shadow.reasons.get(sr.R_RESET, 0) >= 1
    for _ in range(3):
        made.poll(table)
    box = made._layout["regions"]["flop_1"]
    made._own_rects = lambda: [(box["left"], box["top"], box["left"] + box["width"], box["top"] + box["height"])]
    made.poll(table)
    assert made._shadow.reasons.get(sr.R_COVERED, 0) >= 1
    made._own_rects = lambda: []
    made.poll(np.full_like(table, 20))
    assert made._shadow.reasons.get(sr.R_LAYOUT, 0) >= 1 or made._shadow.reasons.get(sr.R_NO_PICTURE, 0) >= 1
    assert made._shadow.summary()["disagreements"] == 0


@needs_table
def test_shadow_mode_can_be_turned_off_and_a_failing_measurement_never_stops_a_poll(monkeypatch, table):
    off = make_tracker(monkeypatch, False, [])
    assert off._shadow is None and off.poll(table)
    with monkeypatch.context() as scope:
        scope.setattr(sr.ShadowReuse, "observe", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        made = make_tracker(scope, True, [])
        sent = [made.poll(table) for _ in range(3)]
        assert all(sent) and len(made.memory.cards) == 9


@needs_table
def test_the_tracker_logs_a_numbers_only_summary_every_n_polls_and_on_stop(monkeypatch, table, caplog):
    made = make_tracker(monkeypatch, True, [], every=3)
    with caplog.at_level(logging.INFO, logger="tracker"):
        for _ in range(6):
            made.poll(table)
        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("SHADOW_REUSE ")]
        assert len(lines) == 2
        made.stop()
        assert len([r for r in caplog.records if r.getMessage().startswith("SHADOW_REUSE ")]) == 3
    for line in lines:
        json.loads(line.split(" ", 1)[1])


def test_nothing_outside_the_tracker_uses_the_shadow_module():
    for folder in ("ui", "voice", "poker", "desktop"):
        for path in glob.glob(os.path.join(ROOT, folder, "*.py")):
            assert "slot_reuse" not in open(path, encoding="utf-8").read(), path
    assert "slot_reuse" not in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()


def test_a_moved_box_records_how_far_it_moved_in_pixels_only():
    shadow = patient()
    image = deck_image("7H")
    step(shadow, image)
    assert step(shadow, image, box=(702, 430, 108, 122)) == sr.R_MOVED            # moved by 2 px: the reference becomes the new box ...
    assert step(shadow, image, box=(702, 430, 108, 122)) == sr.ELIGIBLE           # ... so staying there is not another move
    assert step(shadow, image, box=(700, 430, 108, 130)) == sr.R_MOVED            # 8 px taller
    assert shadow.summary()["box_shift_when_moved_px"] == {"1-2": 1, "4-8": 1}


def grey_card():
    """A mid-grey picture with some structure, so a small change is not lost to a black or white clip."""
    rng = np.random.default_rng(2)
    return np.clip(128 + rng.normal(0, 20, (122, 108, 3)), 0, 255).astype(np.uint8)


def test_a_few_moved_pixels_are_refused_by_the_share_limit_even_when_none_moved_far():
    image = grey_card()
    moved = image.copy()
    moved[10:16, 10:60] = np.clip(moved[10:16, 10:60].astype(np.int16) + 20, 0, 255).astype(np.uint8)       # 300 pixels, each by 20: under the 32 limit
    shadow = patient()
    step(shadow, image)
    assert step(shadow, moved) == sr.R_CHANGED_SHARE


def test_a_slight_overall_brightening_is_refused_by_the_mean_limit():
    image = grey_card()
    shadow = patient()
    step(shadow, image)
    brighter = np.clip(image.astype(np.int16) + 6, 0, 255).astype(np.uint8)                             # every pixel by 6: nothing over 12, mean 6
    assert step(shadow, brighter) == sr.R_CHANGED_MEAN
