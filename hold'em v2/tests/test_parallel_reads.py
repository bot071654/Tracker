"""Reading the table's slots side by side: faster, and exactly the same answers.

`read_table` reads the slots that have a picture on a small pool of threads (OpenCV releases the interpreter lock while it matches) and
puts the results back in slot order. Every slot is read from its own picture by the same function as before, so nothing about WHAT is read
changes. These tests pin "exactly the same": slot by slot, detail by detail, in order, and the way a failure surfaces.
"""

import glob
import os
import queue
import threading

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import tracker as tracker_module                                          # noqa: E402
from recognition import card_recognizer as cr                             # noqa: E402
from tracker import CARD_SLOTS, read_table, read_threads                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = sorted(glob.glob(os.path.join(ROOT, "tests", "fixtures", "live_cards", "*.png")))


@pytest.fixture(scope="module", autouse=True)
def templates():
    cr.load_templates(force=True)


@pytest.fixture(scope="module")
def pictures():
    images = [cv2.imread(path) for path in LIVE]
    assert images and all(image is not None for image in images)
    return images


def nine(pictures, offset=0):
    return {slot: pictures[(index + offset) % len(pictures)] for index, slot in enumerate(CARD_SLOTS)}


def equal(a, b):
    """Deep equality that also compares the numpy pictures a detailed read carries."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.shape == b.shape and a.dtype == b.dtype and bool(np.array_equal(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(equal(a[key], b[key]) for key in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return type(a) == type(b) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def comparable(reads):
    return {slot: {key: value for key, value in read.items() if key != "ms"} for slot, read in reads.items()}


# -- reading side by side -------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("workers", [2, 3, 4, 8])
def test_side_by_side_reads_equal_one_by_one_reads_in_every_detail_and_in_the_same_order(pictures, workers):
    for offset in range(len(pictures)):
        images = nine(pictures, offset)
        one_cards, one_reads = read_table({"parallel_reads": False}, images)
        many_cards, many_reads = read_table({"read_threads": workers}, images)
        assert many_cards == one_cards
        assert comparable(many_reads) == comparable(one_reads)
        assert list(many_cards) == list(one_cards) == list(CARD_SLOTS) and list(many_reads) == list(CARD_SLOTS)


def test_the_detail_the_live_diagnostics_ask_for_is_identical_too(pictures):
    images = nine(pictures)
    one = read_table({"parallel_reads": False, "live_diagnostics": True}, images)
    many = read_table({"read_threads": 4, "live_diagnostics": True}, images)
    assert many[0] == one[0] and equal(comparable(many[1]), comparable(one[1]))
    assert any(isinstance(read.get("detail", {}).get("rank_glyph"), np.ndarray) or "detail" in read for read in many[1].values())


def test_slots_without_a_picture_and_the_dealer_cut_are_handled_as_before(pictures):
    images = nine(pictures)
    images["flop_2"] = None
    images["river"] = None
    for config in ({"parallel_reads": False}, {"read_threads": 4}, {"read_threads": 4, "dealer_face_cut": False}):
        reference = dict(config, parallel_reads=False)
        assert comparable(read_table(config, images)[1]) == comparable(read_table(reference, images)[1])
    cards, reads = read_table({"read_threads": 4}, images)
    assert cards["flop_2"] is None and reads["flop_2"]["present"] is False and "dealer_face" in reads["dealer_1"]


def test_the_pictures_handed_in_are_not_modified(pictures):
    images = nine(pictures)
    before = {slot: image.copy() for slot, image in images.items()}
    read_table({"read_threads": 4}, images)
    assert all(np.array_equal(images[slot], before[slot]) for slot in images)


def test_many_polls_and_several_tracker_threads_at_once_always_give_the_one_serial_answer(pictures):
    expected = [comparable(read_table({"parallel_reads": False}, nine(pictures, offset))[1]) for offset in range(len(pictures))]
    failures = []

    def worker(seed):
        for round_ in range(12):
            offset = (seed + round_) % len(pictures)
            got = comparable(read_table({"read_threads": 4}, nine(pictures, offset))[1])
            if got != expected[offset]:
                failures.append((seed, round_))

    threads = [threading.Thread(target=worker, args=(seed,)) for seed in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []


def test_a_single_picture_or_one_thread_never_touches_the_pool(pictures, monkeypatch):
    monkeypatch.setattr(tracker_module, "_read_pool", lambda workers: pytest.fail("the pool was used"))
    read_table({"read_threads": 1}, nine(pictures))
    read_table({"parallel_reads": False}, nine(pictures))
    read_table({"read_threads": 4}, {"flop_1": pictures[0]})
    read_table({"read_threads": 4}, {})


def test_how_many_threads_is_configurable_and_never_below_one():
    assert read_threads({"parallel_reads": False}) == 1
    assert read_threads({"read_threads": 3}) == 3
    assert read_threads({"read_threads": 0}) == 1
    assert 1 <= read_threads({}) <= 4


def test_a_failing_read_surfaces_like_a_serial_one_and_the_first_failing_slot_in_order_wins(pictures, monkeypatch):
    def broken(image, *args, **kwargs):
        if image is not None and image is images["turn"]:
            raise cr.TemplatesMissingError("turn failed")
        if image is not None and image is images["river"]:
            raise RuntimeError("river failed")
        return real(image, *args, **kwargs)

    images = {slot: pictures[index % len(pictures)].copy() for index, slot in enumerate(CARD_SLOTS)}
    real = tracker_module.read_slot
    monkeypatch.setattr(tracker_module, "read_slot", broken)
    for config in ({"parallel_reads": False}, {"read_threads": 4}):
        with pytest.raises(cr.TemplatesMissingError, match="turn failed"):                # turn comes before river in slot order
            read_table(config, images)


def test_tracker_polls_are_identical_with_side_by_side_reads_and_with_one_by_one_reads(monkeypatch):
    from test_slot_reuse_shadow import SCREENSHOTS, digest, make_tracker
    if not SCREENSHOTS:
        pytest.skip("no casino screenshot in the project folder")
    table = cv2.imread(SCREENSHOTS[0])
    rng = np.random.default_rng(21)
    frames = [table] * 3 + [np.clip(table.astype(np.int16) + rng.normal(0, 2, table.shape), 0, 255).astype(np.uint8) for _ in range(6)]
    results = {}
    for parallel in (False, True):
        with monkeypatch.context() as scope:
            made = make_tracker(scope, False, [], parallel_reads=parallel, read_threads=4)
            results[parallel] = ([digest(made.poll(frame)) for frame in frames], dict(made.memory.cards),
                                 repr(sorted((slot, sorted(tally.items())) for slot, tally in made.memory.votes.items())))
    assert results[True] == results[False]
