"""Screen capture helpers built on MSS.

Only the small calibrated card regions are grabbed during tracking; the full
screen is captured only for calibration and for "Test Recognition".
"""

import logging
import threading

import numpy as np

try:
    import mss
except ImportError:  # pragma: no cover - exercised only without the dependency
    mss = None

logger = logging.getLogger(__name__)

_local = threading.local()


class CaptureError(RuntimeError):
    """Raised when the screen cannot be captured."""


def _sct():
    """One MSS instance per thread (MSS objects are not thread-safe)."""
    if mss is None:
        raise CaptureError("mss is not installed; run: pip install -r requirements.txt")
    instance = getattr(_local, "sct", None)
    if instance is None:
        instance = mss.mss()
        _local.sct = instance
    return instance


def _to_bgr(shot):
    """MSS returns BGRA; OpenCV wants BGR."""
    frame = np.array(shot, dtype=np.uint8)
    return frame[:, :, :3]


def screen_size():
    """(width, height) of the whole virtual screen, or None if unavailable."""
    try:
        virtual = _sct().monitors[0]
        return virtual["width"], virtual["height"]
    except Exception as exc:  # noqa: BLE001 - capture backends raise many types
        logger.error("Could not read screen size: %s", exc)
        return None


def monitor_origin(monitor=1):
    """Absolute (left, top) of a monitor.

    A capture of one monitor starts at this point, so it is what turns a
    position inside that picture into a position on the screen - and back.
    """
    try:
        sct = _sct()
        index = monitor if 0 <= monitor < len(sct.monitors) else 0
        area = sct.monitors[index]
        return area["left"], area["top"]
    except Exception as exc:  # noqa: BLE001 - callers fall back to the origin
        logger.debug("Could not read the monitor origin: %s", exc)
        return 0, 0


def grab_full_screen(monitor=1):
    """Capture a whole monitor as a BGR image."""
    try:
        sct = _sct()
        index = monitor if 0 <= monitor < len(sct.monitors) else 0
        return _to_bgr(sct.grab(sct.monitors[index]))
    except Exception as exc:  # noqa: BLE001
        raise CaptureError("Full screen capture failed: %s" % exc) from exc


def grab_region(region):
    """Capture one region as a BGR image.

    `region` is {"left", "top", "width", "height"} in absolute screen
    coordinates (the format written by the calibrator).
    """
    try:
        box = {
            "left": int(region["left"]),
            "top": int(region["top"]),
            "width": int(region["width"]),
            "height": int(region["height"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptureError("Invalid region %r: %s" % (region, exc)) from exc

    if box["width"] <= 0 or box["height"] <= 0:
        raise CaptureError("Region has no area: %r" % (region,))

    try:
        return _to_bgr(_sct().grab(box))
    except Exception as exc:  # noqa: BLE001
        raise CaptureError("Capture failed for %r: %s" % (region, exc)) from exc


def grab_regions(regions):
    """Capture several named regions.

    Returns slot -> image, with None for any region that could not be grabbed
    (a single bad region must not stop the tracker).
    """
    frames = {}
    for slot, region in regions.items():
        try:
            frames[slot] = grab_region(region)
        except CaptureError as exc:
            logger.warning("%s", exc)
            frames[slot] = None
    return frames


def grab_regions_fast(regions):
    """Capture several nearby regions with a single screen grab.

    Grabbing nine card regions separately costs about 80 ms, almost all of it
    per-grab overhead; one grab of the box that contains them costs about 12 ms.
    That headroom is what lets the tracker poll several times a second, which
    matters when the dealer's hand keeps sweeping over the cards.

    Falls back to grabbing each region on its own when they are too scattered
    for a shared box to be worthwhile, or when the combined grab fails.
    """
    if not regions:
        return {}

    try:
        boxes = {slot: {key: int(region[key]) for key in
                        ("left", "top", "width", "height")}
                 for slot, region in regions.items()}
    except (KeyError, TypeError, ValueError):
        return grab_regions(regions)

    left = min(box["left"] for box in boxes.values())
    top = min(box["top"] for box in boxes.values())
    right = max(box["left"] + box["width"] for box in boxes.values())
    bottom = max(box["top"] + box["height"] for box in boxes.values())

    wanted = sum(box["width"] * box["height"] for box in boxes.values())
    combined = max(1, (right - left) * (bottom - top))
    if combined > wanted * 8:
        return grab_regions(regions)

    try:
        frame = grab_region({"left": left, "top": top,
                             "width": right - left, "height": bottom - top})
    except CaptureError as exc:
        logger.warning("Combined capture failed (%s); falling back", exc)
        return grab_regions(regions)

    return {slot: crop(frame, box, origin=(left, top)) for slot, box in boxes.items()}


def crop(image, region, origin=(0, 0)):
    """Crop a region out of an already-captured full-screen image.

    `origin` is the absolute screen coordinate of the image's top-left pixel,
    so that absolute region coordinates line up with a monitor capture.
    """
    left = max(0, int(region["left"]) - int(origin[0]))
    top = max(0, int(region["top"]) - int(origin[1]))
    right = min(image.shape[1], left + int(region["width"]))
    bottom = min(image.shape[0], top + int(region["height"]))
    if right <= left or bottom <= top:
        return None
    return image[top:bottom, left:right]
