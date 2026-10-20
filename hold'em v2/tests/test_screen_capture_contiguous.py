"""Captured frames are contiguous BGR arrays (capture/screen_capture._to_bgr), with exactly the pixels they always had.

MSS hands over BGRA. `_to_bgr` used to slice the alpha channel off (`frame[:, :, :3]`), which leaves a VIEW with a 4-byte pixel stride:
every OpenCV call on it then copies the whole picture first. On a 1080p frame `table_layout.locate` took 22 ms on that view and 7 ms on a
contiguous array, and a whole tracker tick about 275 ms against 252 ms. The conversion is now one `cvtColor`, contiguous, and these tests pin
that it gives the same pixels as the old one for every input, for both public grabs, and that the tracker reads and decides identically
from either kind of frame.
"""

import glob
import os
import queue

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import tracker as tracker_module                                  # noqa: E402
from capture import screen_capture as sc                          # noqa: E402
from recognition import table_layout                              # noqa: E402
from tracker import Tracker                                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS = sorted(glob.glob(os.path.join(ROOT, "Screenshot*.png")))


def old_to_bgr(shot):
    """The conversion as it was before: the first three channels as a strided view."""
    frame = np.array(shot, dtype=np.uint8)
    return frame[:, :, :3]


def bgra(height, width, seed=0):
    return np.random.default_rng(seed).integers(0, 256, (height, width, 4), dtype=np.uint8)


# -- the conversion -----------------------------------------------------------------------------------------------------------------

def test_the_result_is_a_contiguous_bgr_array_and_the_old_one_was_not():
    shot = bgra(120, 200)
    now, before = sc._to_bgr(shot), old_to_bgr(shot)
    assert before.flags["C_CONTIGUOUS"] is False and before.strides == (800, 4, 1)          # what every OpenCV call used to copy first
    assert now.flags["C_CONTIGUOUS"] and now.flags["OWNDATA"] and now.shape == (120, 200, 3) and now.dtype == np.uint8
    assert now.strides == (600, 3, 1)


@pytest.mark.parametrize("height, width, seed", [(1, 1, 0), (1, 37, 1), (53, 1, 2), (7, 9, 3), (64, 64, 4), (123, 457, 5), (1080, 1920, 6)])
def test_every_pixel_is_exactly_what_the_old_conversion_gave(height, width, seed):
    shot = bgra(height, width, seed)
    now, before = sc._to_bgr(shot), old_to_bgr(shot)
    assert now.shape == before.shape and now.dtype == before.dtype
    assert np.array_equal(now, before) and np.array_equal(now, shot[:, :, :3])


def test_the_alpha_channel_never_leaks_in_whatever_it_holds():
    shot = bgra(30, 40, 7)
    for alpha in (0, 1, 127, 255):
        shot[:, :, 3] = alpha
        assert np.array_equal(sc._to_bgr(shot), shot[:, :, :3])
    extremes = np.zeros((4, 4, 4), np.uint8)
    extremes[..., :3] = [[[0, 128, 255]] * 4] * 4
    extremes[..., 3] = 255
    assert np.array_equal(sc._to_bgr(extremes), extremes[:, :, :3])


def test_the_result_does_not_share_memory_with_the_shot():
    shot = bgra(20, 30, 8)
    before = shot.copy()
    result = sc._to_bgr(shot)
    result[:] = 7
    assert np.array_equal(shot, before)                                                    # writing the frame never reaches the grab
    kept = sc._to_bgr(shot)
    expected = kept.copy()
    shot[:] = 0
    assert np.array_equal(kept, expected)                                                  # and the grab changing never reaches the frame


@pytest.mark.parametrize("make", [lambda a: np.asfortranarray(a), lambda a: a[::2, ::3], lambda a: a.swapaxes(0, 1).swapaxes(0, 1),
                                  lambda a: np.lib.stride_tricks.as_strided(a, a.shape, a.strides)])
def test_oddly_laid_out_shots_convert_to_the_same_pixels(make):
    shot = make(bgra(40, 50, 9))
    assert np.array_equal(sc._to_bgr(shot), old_to_bgr(shot)) and sc._to_bgr(shot).flags["C_CONTIGUOUS"]


def test_a_shot_object_that_converts_to_an_array_works_like_an_array():
    class Shot:                                                                            # what mss.ScreenShot offers numpy: __array__
        def __init__(self, data):
            self.data = data

        def __array__(self, dtype=None, copy=None):
            return self.data if dtype is None else self.data.astype(dtype)
    data = bgra(25, 35, 10)
    assert np.array_equal(sc._to_bgr(Shot(data)), old_to_bgr(Shot(data))) and sc._to_bgr(Shot(data)).flags["C_CONTIGUOUS"]


def test_three_channel_input_is_still_accepted_and_returned_contiguous():
    three = np.random.default_rng(11).integers(0, 256, (20, 30, 3), dtype=np.uint8)
    for view in (three, np.asfortranarray(three)):
        result = sc._to_bgr(view)
        assert np.array_equal(result, three) and result.flags["C_CONTIGUOUS"]


def test_three_channel_input_is_copied_too_so_the_result_never_shares_memory_with_the_shot():
    shot = np.random.default_rng(12).integers(0, 256, (20, 30, 3), dtype=np.uint8)          # already contiguous: a naive ascontiguousarray would hand it back
    before = shot.copy()
    result = sc._to_bgr(shot)
    assert result is not shot and not np.shares_memory(result, shot)
    result[:] = 9
    assert np.array_equal(shot, before)
    kept = sc._to_bgr(shot)
    shot[:] = 0
    assert np.array_equal(kept, before)


def test_input_that_was_never_an_image_fails_the_way_it_always_did():
    for bad in (np.zeros((10, 10), np.uint8), np.zeros(10, np.uint8)):
        with pytest.raises(IndexError):
            old_to_bgr(bad)
        with pytest.raises(IndexError):
            sc._to_bgr(bad)


# -- the public grabs, through a fake MSS -----------------------------------------------------------------------------------------------

class FakeScreen:
    """A BGRA 'screen' and the two things the grabs ask of an MSS instance: .monitors and .grab(box)."""

    def __init__(self, width=300, height=200, seed=21):
        self.pixels = bgra(height, width, seed)
        self.monitors = [{"left": 0, "top": 0, "width": width, "height": height},
                         {"left": 0, "top": 0, "width": width, "height": height}]
        self.grabbed = []

    def grab(self, box):
        self.grabbed.append(dict(box))
        left, top = int(box["left"]), int(box["top"])
        right, bottom = left + int(box["width"]), top + int(box["height"])
        if right > self.pixels.shape[1] or bottom > self.pixels.shape[0] or left < 0 or top < 0:
            raise RuntimeError("outside the screen")
        return self.pixels[top:bottom, left:right].copy()


@pytest.fixture
def screen(monkeypatch):
    fake = FakeScreen()
    monkeypatch.setattr(sc, "_sct", lambda: fake)
    return fake


def test_grab_full_screen_returns_the_contiguous_picture_with_the_old_pixels(screen):
    frame = sc.grab_full_screen(1)
    assert frame.flags["C_CONTIGUOUS"] and frame.shape == (200, 300, 3) and frame.dtype == np.uint8
    assert np.array_equal(frame, old_to_bgr(screen.pixels))


@pytest.mark.parametrize("monitor", [0, 1, 5, -3])
def test_grab_full_screen_keeps_choosing_the_monitor_as_before(screen, monitor):
    frame = sc.grab_full_screen(monitor)
    assert frame.flags["C_CONTIGUOUS"] and np.array_equal(frame, old_to_bgr(screen.pixels))
    expected = monitor if 0 <= monitor < len(screen.monitors) else 0
    assert screen.grabbed[-1] == screen.monitors[expected]


def test_grab_full_screen_still_reports_a_failed_capture_as_a_capture_error(monkeypatch):
    class Broken:
        monitors = [{"left": 0, "top": 0, "width": 10, "height": 10}] * 2

        def grab(self, box):
            raise OSError("no screen")
    monkeypatch.setattr(sc, "_sct", lambda: Broken())
    with pytest.raises(sc.CaptureError):
        sc.grab_full_screen(1)


REGIONS = {"flop_1": {"left": 10, "top": 20, "width": 50, "height": 60}, "flop_2": {"left": 70, "top": 20, "width": 50, "height": 60},
           "dealer_1": {"left": 130, "top": 90, "width": 40, "height": 70}}


def region_pixels(screen, region):
    return old_to_bgr(screen.pixels[region["top"]:region["top"] + region["height"], region["left"]:region["left"] + region["width"]])


def test_grab_region_returns_a_contiguous_crop_with_the_old_pixels(screen):
    for region in REGIONS.values():
        image = sc.grab_region(region)
        assert image.flags["C_CONTIGUOUS"] and image.shape == (region["height"], region["width"], 3)
        assert np.array_equal(image, region_pixels(screen, region))


def test_grab_regions_gives_every_slot_a_contiguous_picture_and_a_failing_slot_none(screen):
    regions = dict(REGIONS, river={"left": 290, "top": 190, "width": 50, "height": 50}, turn={"left": 5, "top": 5, "width": 0, "height": 10})
    frames = sc.grab_regions(regions)
    assert set(frames) == set(regions)
    for slot in REGIONS:
        assert frames[slot].flags["C_CONTIGUOUS"] and np.array_equal(frames[slot], region_pixels(screen, REGIONS[slot]))
    assert frames["river"] is None and frames["turn"] is None                              # off the screen, and no area: None, as before


def test_grab_regions_fast_crops_contiguous_pixels_out_of_one_grab(screen):
    frames = sc.grab_regions_fast(REGIONS)
    assert len(screen.grabbed) == 1                                                        # one combined grab, as before
    for slot, region in REGIONS.items():
        assert np.array_equal(frames[slot], region_pixels(screen, region))
        assert frames[slot].dtype == np.uint8 and frames[slot].shape == (region["height"], region["width"], 3)


def test_grab_regions_fast_falls_back_to_separate_grabs_when_scattered(screen):
    scattered = {"a": {"left": 0, "top": 0, "width": 10, "height": 10}, "b": {"left": 280, "top": 180, "width": 10, "height": 10}}
    frames = sc.grab_regions_fast(scattered)
    assert len(screen.grabbed) == 2 and all(frames[s].flags["C_CONTIGUOUS"] for s in scattered)


# -- the tracker reads and decides identically from either kind of frame ----------------------------------------------------------------

needs_screenshot = pytest.mark.skipif(not SCREENSHOTS, reason="no casino screenshot in the project folder")


@pytest.fixture(scope="module")
def table():
    return cv2.imread(SCREENSHOTS[0])


def as_bgra(frame):
    shot = np.empty(frame.shape[:2] + (4,), np.uint8)
    shot[:, :, :3] = frame
    shot[:, :, 3] = 255
    return shot


@needs_screenshot
def test_locate_finds_the_same_slots_on_the_view_and_on_the_contiguous_frame(table):
    shot = as_bgra(table)
    view, contiguous = old_to_bgr(shot), sc._to_bgr(shot)
    assert not view.flags["C_CONTIGUOUS"] and contiguous.flags["C_CONTIGUOUS"]
    assert table_layout.locate(view) == table_layout.locate(contiguous)
    assert table_layout.find_card_boxes(view) == table_layout.find_card_boxes(contiguous)


@needs_screenshot
def test_the_tracker_reads_the_same_cards_and_reaches_the_same_states_from_either_kind_of_frame(table, monkeypatch):
    rng = np.random.default_rng(5)
    shots = [as_bgra(np.clip(table.astype(np.int16) + rng.integers(-2, 3, table.shape, dtype=np.int16), 0, 255).astype(np.uint8)) for _ in range(6)]
    shown = {"shot": None, "convert": None}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: shown["convert"](shown["shot"]))
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(Tracker, "_store", lambda self, cards, reads=None: None)

    def make():
        made = Tracker({"monitor": 1, "regions": {}, "game_window_title": "", "clear_frames": 3, "dealer_debug": False,
                        "debug_save_failures": False}, queue.Queue())
        made._own_rects = lambda: []
        made.focus = type("Focus", (), {"holding_own_window": False, "state": "GAME_ACTIVE"})()
        made._record_tick = lambda *a, **k: None
        return made

    def run(convert):
        made, outcomes = make(), []
        shown["convert"] = convert
        for shot in shots:
            shown["shot"] = shot
            made._tick()
            update = None
            while not made.events.empty():
                kind, payload = made.events.get_nowait()
                if kind == "update":
                    update = payload
            reads = {slot: (read.get("card"), read.get("confident"), round(read.get("confidence") or 0.0, 4)) for slot, read in update["reads"].items()}
            outcomes.append((sorted((update["cards"] or {}).items()), update["state"], update["round_id"], reads, sorted(made._last_regions or {}),
                             (update["lifecycle"] or {}).get("state"), (update["lifecycle"] or {}).get("result_status")))
        return outcomes

    strided, contiguous = run(old_to_bgr), run(sc._to_bgr)
    assert strided == contiguous
    assert any(cards for cards, *_ in contiguous) and contiguous[-1][1] != "WAITING"          # it really read a table, not nothing
