"""Where the tracker window goes, and how big it is, on whatever screen it finds.

The tracker used to ask for a fixed 430x900 at whatever position the window
manager felt like. On a 1366x768 laptop that is taller than the screen, and a
window manager is free to place it half off the right-hand edge - which is
exactly where the Scenario section lives, so it was the first thing to vanish.

Nothing here draws anything. It is plain arithmetic over a screen rectangle, so
the behaviour on a 1366x768 laptop can be tested on any machine, without a
display and without editing coordinates by hand.

The rules, in order:

* the window never leaves the work area (the desktop minus the taskbar), so the
  Scenario section on its right-hand edge is always on screen;
* it never takes more than about a third of the screen's width, so it does not
  bury the poker table it sits beside;
* it is as tall as the content wants, up to what the screen allows - the app
  scrolls whatever is left over rather than hiding it;
* a size and position remembered from last time is used only after it has been
  checked against the screen that is actually attached now.
"""

import json
import logging
import os
import platform
import re
import sys
from collections import namedtuple

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "config", "window_state.json")

# The size the window asks for when the screen has room for all of it. This is
# the old fixed size: on a large screen nothing about the window changes.
BASE_CONTENT_WIDTH = 430
BASE_CONTENT_HEIGHT = 900

# Below this the rows stop being readable, so the window stops shrinking and
# the content scrolls instead. Never applied above the screen's own size: a
# minimum wider than the screen would be another way of going off the edge.
MIN_WIDTH = 320
MIN_HEIGHT = 360

# Gap left between the window and the edges of the work area.
MARGIN = 16

# At most a third of the width, so the table stays usable underneath, and
# nearly all of the height, because the content is a tall single column.
MAX_WIDTH_FRACTION = 0.34
MAX_HEIGHT_FRACTION = 0.95

# Used only when the screen cannot be measured at all (no display, a Tk that
# reports nonsense). Deliberately small: too small only costs a scrollbar,
# while too large puts the Scenario section off the edge again.
FALLBACK_SCREEN = (1280, 720)

_GEOMETRY_RE = re.compile(r"^\s*(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)\s*$")


class Geometry(namedtuple("Geometry", "width height x y")):
    """A window rectangle in screen pixels."""

    __slots__ = ()

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    def as_string(self):
        """The Tk form, e.g. "430x900+1474+16"."""
        return "%dx%d+%d+%d" % (self.width, self.height, self.x, self.y)

    def as_dict(self):
        return {"width": self.width, "height": self.height, "x": self.x, "y": self.y}


def _clamp(value, low, high):
    if high < low:
        return low
    return max(low, min(high, value))


def parse_geometry(text):
    """A Tk geometry string as a Geometry, or None when it is not one.

    Tk reports a negative coordinate as "+-10", and accepts "-10" for ten
    pixels in from the right; both are read here as the plain number, which is
    what a window that has been dragged off the top-left reports.
    """
    if isinstance(text, Geometry):
        return text
    if not isinstance(text, str):
        return None
    match = _GEOMETRY_RE.match(text)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    coords = []
    for token in (match.group(3), match.group(4)):
        coords.append(-int(token[1:]) if token[0] == "-" else int(token[1:]))
    if width <= 0 or height <= 0:
        return None
    return Geometry(width, height, coords[0], coords[1])


def coerce_geometry(value):
    """A Geometry from a Geometry, a Tk string, a dict or a 4-tuple, or None."""
    if value is None:
        return None
    if isinstance(value, Geometry):
        return value
    if isinstance(value, str):
        return parse_geometry(value)
    try:
        if isinstance(value, dict):
            parts = [value["width"], value["height"], value["x"], value["y"]]
        else:
            parts = list(value)
            if len(parts) != 4:
                return None
        numbers = []
        for part in parts:
            if isinstance(part, bool) or not isinstance(part, (int, float)):
                return None
            numbers.append(int(part))
    except (KeyError, TypeError, ValueError):
        return None
    if numbers[0] <= 0 or numbers[1] <= 0:
        return None
    return Geometry(*numbers)


# -- the screen ---------------------------------------------------------------

def windows_work_area():
    """The desktop minus the taskbar as (left, top, right, bottom), or None.

    Windows only. Without it the window would be sized against the full screen
    height and its last couple of rows would sit behind the taskbar.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes

        rect = ctypes.wintypes.RECT()
        spi_getworkarea = 0x0030
        ok = ctypes.windll.user32.SystemParametersInfoW(
            spi_getworkarea, 0, ctypes.byref(rect), 0)
        if not ok:
            return None
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception as exc:  # noqa: BLE001 - any failure just means "unknown"
        logger.debug("Could not read the Windows work area: %s", exc)
        return None


def screen_size(root=None):
    """The current screen size in pixels, measured rather than assumed."""
    if root is not None:
        try:
            width, height = int(root.winfo_screenwidth()), int(root.winfo_screenheight())
            if width > 0 and height > 0:
                return width, height
        except Exception as exc:  # noqa: BLE001 - no display, or a dead window
            logger.debug("Could not measure the screen: %s", exc)
    return FALLBACK_SCREEN


def screen_scaling(root=None):
    """Display scaling as a multiplier, 1.0 at 96 dpi.

    With per-monitor DPI awareness on (calibration.make_dpi_aware) Tk reports
    the monitor's real dpi, and a window measured in raw pixels would come out
    a third too small on a 150% display. Clamped, because a wrong answer here
    should shift the size a little, not produce a window nobody can use.
    """
    if root is None:
        return 1.0
    try:
        return _clamp(float(root.winfo_fpixels("1i")) / 96.0, 0.5, 4.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read the display scaling: %s", exc)
        return 1.0


def work_area_for(screen_width, screen_height, work_area=None):
    """The usable rectangle, defaulting to the whole screen.

    A work area that does not overlap the screen it was given (a stale reading,
    a monitor that has since been unplugged) is discarded.
    """
    if work_area:
        left, top, right, bottom = (int(value) for value in work_area)
        if right > left and bottom > top:
            left = _clamp(left, 0, max(0, screen_width - 1))
            top = _clamp(top, 0, max(0, screen_height - 1))
            right = _clamp(right, left + 1, screen_width)
            bottom = _clamp(bottom, top + 1, screen_height)
            return (left, top, right, bottom)
    return (0, 0, max(1, int(screen_width)), max(1, int(screen_height)))


# -- the sums -----------------------------------------------------------------

def get_adaptive_window_geometry(screen_width=None, screen_height=None,
                                 content_width=BASE_CONTENT_WIDTH,
                                 content_height=BASE_CONTENT_HEIGHT,
                                 scale=1.0, margin=MARGIN,
                                 max_width_fraction=MAX_WIDTH_FRACTION,
                                 max_height_fraction=MAX_HEIGHT_FRACTION,
                                 work_area=None, root=None, dock="right"):
    """A safe width, height, x and y for the tracker on the screen in use.

    ``content_width``/``content_height`` are what the window would like - by
    default the size the tracker has always asked for, or the size the built
    UI actually measures. The result is never larger than the screen allows and
    is always wholly inside the work area, so the Scenario section on the right
    of the window cannot be pushed off the edge.

    Pass ``screen_width``/``screen_height`` to size for a given screen (that is
    how the laptop resolutions are tested); leave them out and the screen is
    measured from ``root``, or from the fallback when there is no display.

    ``dock`` is which side of the work area the window is put against: the
    tracker goes on the right, beside the table; a second window that would
    otherwise land on top of it asks for the left.
    """
    if screen_width is None or screen_height is None:
        screen_width, screen_height = screen_size(root)
    screen_width, screen_height = max(1, int(screen_width)), max(1, int(screen_height))
    if work_area is None and root is not None:
        work_area = windows_work_area()
    left, top, right, bottom = work_area_for(screen_width, screen_height, work_area)
    available_width, available_height = right - left, bottom - top

    margin = max(0, int(margin))
    if available_width <= margin * 2 or available_height <= margin * 2:
        margin = 0

    wanted_width = max(1, int(round(content_width * scale)))
    wanted_height = max(1, int(round(content_height * scale)))

    limit_width = max(1, min(int(available_width * max_width_fraction),
                             available_width - margin * 2))
    limit_height = max(1, min(int(available_height * max_height_fraction),
                              available_height - margin * 2))

    # The minimum wins over the "a third of the screen" cap - a window too
    # narrow to read is worse than one that covers a little more of the table -
    # but never over the screen itself.
    floor_width = min(int(round(MIN_WIDTH * scale)), available_width)
    floor_height = min(int(round(MIN_HEIGHT * scale)), available_height)
    width = _clamp(min(wanted_width, limit_width), floor_width, available_width)
    height = _clamp(min(wanted_height, limit_height), floor_height, available_height)

    # Docked to the right of the work area: the window's own right-hand edge,
    # where the Scenario section sits, is the edge that has to stay visible.
    if dock == "left":
        x = left + margin
    else:
        x = left + available_width - width - margin
    y = top + margin
    x = _clamp(x, left, left + available_width - width)
    y = _clamp(y, top, top + available_height - height)
    return Geometry(width, height, int(x), int(y))


def is_on_screen(geometry, screen_width, screen_height, work_area=None):
    """True when the whole window sits inside the usable screen area."""
    geometry = coerce_geometry(geometry)
    if geometry is None:
        return False
    left, top, right, bottom = work_area_for(screen_width, screen_height, work_area)
    return (geometry.x >= left and geometry.y >= top
            and geometry.right <= right and geometry.bottom <= bottom)


def _overlaps(geometry, rect):
    left, top, right, bottom = rect
    return (geometry.x < right and geometry.right > left
            and geometry.y < bottom and geometry.bottom > top)


def validate_saved_geometry(saved, screen_width, screen_height, work_area=None,
                            margin=MARGIN):
    """A remembered size and position, corrected for this screen, or None.

    None means the saved state cannot be used and the caller should fall back
    to :func:`get_adaptive_window_geometry`: it is missing, malformed, or it
    lies entirely off this screen - the second monitor it was saved on is not
    attached to this laptop.

    A window that merely hangs over an edge is pulled back on instead, shrunk
    first if this screen is smaller than the one it was saved on. That is the
    case that used to clip the Scenario section.
    """
    geometry = coerce_geometry(saved)
    if geometry is None:
        return None
    screen_width, screen_height = max(1, int(screen_width)), max(1, int(screen_height))
    rect = work_area_for(screen_width, screen_height, work_area)
    left, top, right, bottom = rect
    available_width, available_height = right - left, bottom - top

    if not _overlaps(geometry, rect):
        logger.info("Ignoring the remembered window position %s: it is off this "
                    "screen (%dx%d).", geometry.as_string(), screen_width, screen_height)
        return None

    width = _clamp(geometry.width, min(MIN_WIDTH, available_width), available_width)
    height = _clamp(geometry.height, min(MIN_HEIGHT, available_height), available_height)
    x = _clamp(geometry.x, left, left + available_width - width)
    y = _clamp(geometry.y, top, top + available_height - height)
    corrected = Geometry(width, height, int(x), int(y))
    if corrected != geometry:
        logger.info("Moved the remembered window %s back on screen as %s.",
                    geometry.as_string(), corrected.as_string())
    return corrected


def resize_within_screen(geometry, screen_width, screen_height, factor=1.0,
                         work_area=None):
    """``geometry`` scaled by ``factor`` and kept on screen.

    What the window's own "bigger"/"smaller" buttons do, so that a manual
    resize cannot walk the window off the edge either.
    """
    geometry = coerce_geometry(geometry)
    if geometry is None:
        return None
    rect = work_area_for(screen_width, screen_height, work_area)
    left, top, right, bottom = rect
    available_width, available_height = right - left, bottom - top
    width = _clamp(int(round(geometry.width * factor)),
                   min(MIN_WIDTH, available_width), available_width)
    height = _clamp(int(round(geometry.height * factor)),
                    min(MIN_HEIGHT, available_height), available_height)
    x = _clamp(geometry.x, left, left + available_width - width)
    y = _clamp(geometry.y, top, top + available_height - height)
    return Geometry(width, height, int(x), int(y))


# -- what each laptop remembers ----------------------------------------------

def machine_key(screen_width, screen_height, node=None):
    """The name a saved position is filed under: this machine, this screen.

    Keyed by resolution as well as machine so that plugging in a different
    monitor - or a colleague opening the same checkout on their laptop - starts
    from the adaptive size rather than from somebody else's window.
    """
    if node is None:
        try:
            node = platform.node()
        except Exception:  # noqa: BLE001
            node = ""
    return "%s@%dx%d" % (node or "unknown", int(screen_width), int(screen_height))


class WindowStateStore:
    """Reads and writes config/window_state.json. Never raises."""

    def __init__(self, path=STATE_PATH, node=None):
        self.path = path
        self.node = node

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("Could not read %s: %s", self.path, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        windows = data.get("windows")
        return windows if isinstance(windows, dict) else {}

    def load(self, screen_width, screen_height, name="main"):
        """The geometry saved for this machine and screen, or None."""
        entry = self._read().get(machine_key(screen_width, screen_height, self.node))
        if not isinstance(entry, dict):
            return None
        return coerce_geometry(entry.get(name))

    def save(self, geometry, screen_width, screen_height, name="main"):
        """Remember this window's size and position. True when it was written."""
        geometry = coerce_geometry(geometry)
        if geometry is None:
            return False
        windows = self._read()
        key = machine_key(screen_width, screen_height, self.node)
        entry = windows.get(key)
        if not isinstance(entry, dict):
            entry = {}
        entry[name] = geometry.as_dict()
        windows[key] = entry
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "windows": windows}, handle, indent=2)
        except OSError as exc:
            logger.info("Could not save the window position to %s: %s", self.path, exc)
            return False
        return True


# -- how the content arranges itself inside the window ------------------------

def layout_mode(available_width, side_by_side_width, two_column_button_width):
    """How the content should be arranged in ``available_width`` pixels.

    Both answers are a plain comparison against what the widgets themselves
    asked for, so the thresholds follow the font and the display scaling
    instead of being guessed here.

    ``stack_scenario``: the Scenario panel goes underneath the card table
    rather than beside it, so it is never the part squeezed off the right.
    ``single_column_buttons``: the buttons go one to a row rather than two.
    """
    return {
        "stack_scenario": available_width < side_by_side_width,
        "single_column_buttons": available_width < two_column_button_width,
    }


def needs_vertical_scroll(content_height, viewport_height):
    """True when the content is taller than the window can show."""
    try:
        return int(content_height) > int(viewport_height)
    except (TypeError, ValueError):
        return False


def needs_horizontal_scroll(content_width, viewport_width):
    """True when even the reflowed content is wider than the window."""
    try:
        return int(content_width) > int(viewport_width)
    except (TypeError, ValueError):
        return False
