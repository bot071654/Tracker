"""The tracker window on somebody else's laptop.

The bug these cover: a window that asked for a fixed 430x900 wherever the
window manager felt like putting it. On a 1366x768 laptop that is taller than
the screen and can be placed half over the right-hand edge - and the right-hand
edge is where the Scenario section lives, so it was the first thing to go.

Every resolution here is checked without owning that screen: the sums in
ui.window_geometry take a screen size and return a rectangle, so a 1366x768
laptop can be tested from any machine. The tests that build the real window
pass a screen in the same way, and are skipped where there is no display.
"""

import json

import pytest

from ui.window_geometry import (
    BASE_CONTENT_HEIGHT, BASE_CONTENT_WIDTH, FALLBACK_SCREEN, Geometry, MIN_HEIGHT,
    MIN_WIDTH, WindowStateStore, coerce_geometry, get_adaptive_window_geometry,
    is_on_screen, layout_mode, machine_key, needs_horizontal_scroll,
    needs_vertical_scroll, parse_geometry, resize_within_screen, screen_size,
    validate_saved_geometry, work_area_for,
)

# Every laptop this has to work on, plus the desktop it was written on.
RESOLUTIONS = [
    (1920, 1080),   # the machine it was developed on
    (1600, 900),
    (1536, 864),    # a common Windows-scaled laptop
    (1440, 900),
    (1366, 768),    # the one that clipped the Scenario section
    (1280, 800),
    (1280, 720),
    (1152, 864),
    (1024, 768),    # small laptop
    (1024, 600),    # netbook
    (800, 600),     # smaller than the window has ever been
]


def store(tmp_path, node="test-machine"):
    return WindowStateStore(path=str(tmp_path / "window_state.json"), node=node)


# -- the window fits the screen, whatever the screen is ------------------------

@pytest.mark.parametrize("screen_width,screen_height", RESOLUTIONS)
def test_the_window_is_wholly_on_screen_at_every_resolution(screen_width, screen_height):
    geometry = get_adaptive_window_geometry(screen_width, screen_height)

    assert geometry.x >= 0 and geometry.y >= 0
    assert geometry.right <= screen_width, "the Scenario side would be off the screen"
    assert geometry.bottom <= screen_height
    assert is_on_screen(geometry, screen_width, screen_height)


@pytest.mark.parametrize("screen_width,screen_height", RESOLUTIONS)
def test_the_window_is_readable_but_does_not_bury_the_table(screen_width, screen_height):
    geometry = get_adaptive_window_geometry(screen_width, screen_height)

    # Wide enough to read, unless the screen itself is narrower than that.
    assert geometry.width >= min(MIN_WIDTH, screen_width)
    assert geometry.height >= min(MIN_HEIGHT, screen_height)
    # ...and never so wide that the poker table underneath is unusable.
    assert geometry.width <= max(MIN_WIDTH, screen_width * 0.4)
    assert geometry.width <= BASE_CONTENT_WIDTH
    assert geometry.height <= BASE_CONTENT_HEIGHT


def test_a_1920x1080_screen_still_gets_the_size_it_always_had():
    """Nothing changes on the machine this was written on."""
    geometry = get_adaptive_window_geometry(1920, 1080)
    assert (geometry.width, geometry.height) == (BASE_CONTENT_WIDTH, BASE_CONTENT_HEIGHT)


def test_1366x768_loses_height_rather_than_the_scenario_section():
    """The laptop that started this. The window gets shorter, not clipped."""
    geometry = get_adaptive_window_geometry(1366, 768)

    assert geometry.height < BASE_CONTENT_HEIGHT     # the content scrolls instead
    assert geometry.height <= 768
    assert geometry.width == BASE_CONTENT_WIDTH      # the Scenario column is intact
    assert geometry.bottom <= 768 and geometry.right <= 1366


def test_1536x864_fits_without_being_shrunk_sideways():
    geometry = get_adaptive_window_geometry(1536, 864)

    assert geometry.width == BASE_CONTENT_WIDTH
    assert geometry.height < BASE_CONTENT_HEIGHT
    assert is_on_screen(geometry, 1536, 864)


@pytest.mark.parametrize("screen_width,screen_height", [(1024, 768), (1024, 600),
                                                        (800, 600), (640, 480)])
def test_small_laptops_get_a_narrower_window_not_one_hanging_off_the_edge(
        screen_width, screen_height):
    geometry = get_adaptive_window_geometry(screen_width, screen_height)

    assert geometry.width < BASE_CONTENT_WIDTH
    assert is_on_screen(geometry, screen_width, screen_height)


def test_the_taskbar_is_kept_clear():
    """With a work area, the window stops above the taskbar instead of behind it."""
    work_area = (0, 0, 1920, 1040)       # a 40-pixel taskbar along the bottom
    geometry = get_adaptive_window_geometry(1920, 1080, work_area=work_area)

    assert geometry.bottom <= 1040
    assert is_on_screen(geometry, 1920, 1080, work_area=work_area)


def test_a_taskbar_down_the_side_moves_the_window_too():
    work_area = (0, 0, 1820, 1080)       # 100 pixels of taskbar on the right
    geometry = get_adaptive_window_geometry(1920, 1080, work_area=work_area)

    assert geometry.right <= 1820


def test_display_scaling_makes_the_window_bigger_not_the_text_smaller():
    """At 150% the window asks for half as much again, while still fitting."""
    plain = get_adaptive_window_geometry(1920, 1080)
    scaled = get_adaptive_window_geometry(1920, 1080, scale=1.5)

    assert scaled.width > plain.width and scaled.height > plain.height
    assert is_on_screen(scaled, 1920, 1080)


def test_the_screen_is_measured_not_assumed():
    """No display, no guessing at 1920x1080: the conservative size is used."""
    assert screen_size(None) == FALLBACK_SCREEN

    class DeadWindow:
        def winfo_screenwidth(self):
            raise RuntimeError("no display")

        def winfo_screenheight(self):     # pragma: no cover - never reached
            raise RuntimeError("no display")

    assert screen_size(DeadWindow()) == FALLBACK_SCREEN

    class Laptop:
        def winfo_screenwidth(self):
            return 1366

        def winfo_screenheight(self):
            return 768

    assert screen_size(Laptop()) == (1366, 768)


def test_a_nonsense_work_area_is_ignored():
    assert work_area_for(1366, 768, (0, 0, 0, 0)) == (0, 0, 1366, 768)
    assert work_area_for(1366, 768, None) == (0, 0, 1366, 768)
    # A work area from a screen that is no longer attached is cut down to this one.
    assert work_area_for(1366, 768, (0, 0, 3840, 2160)) == (0, 0, 1366, 768)


# -- a position remembered from last time -------------------------------------

def test_a_saved_position_that_still_fits_is_used_as_it_is():
    saved = {"width": 400, "height": 700, "x": 900, "y": 40}
    restored = validate_saved_geometry(saved, 1366, 768)

    assert restored == Geometry(400, 700, 900, 40)


def test_a_saved_position_off_the_screen_is_refused():
    """The second monitor it was saved on is not attached to this laptop."""
    saved = {"width": 430, "height": 900, "x": 2400, "y": 100}

    assert validate_saved_geometry(saved, 1366, 768) is None


@pytest.mark.parametrize("saved", [
    {"width": 430, "height": 700, "x": -600, "y": 40},      # off to the left
    {"width": 430, "height": 700, "x": 40, "y": -900},      # above the top
    {"width": 430, "height": 700, "x": 40, "y": 2000},      # below the bottom
])
def test_a_saved_position_entirely_off_the_screen_falls_back_to_the_adaptive_one(saved):
    assert validate_saved_geometry(saved, 1366, 768) is None
    # ...and the caller's fallback is on screen.
    assert is_on_screen(get_adaptive_window_geometry(1366, 768), 1366, 768)


def test_a_saved_window_hanging_over_an_edge_is_pulled_back_on():
    """Exactly the state that used to clip the Scenario section."""
    saved = {"width": 430, "height": 700, "x": 1200, "y": 40}   # 264 px off the right
    restored = validate_saved_geometry(saved, 1366, 768)

    assert restored is not None
    assert is_on_screen(restored, 1366, 768)
    assert restored.right == 1366
    assert restored.width == 430               # nothing was cut off it


def test_a_saved_window_larger_than_this_screen_is_shrunk_to_fit():
    saved = {"width": 430, "height": 1000, "x": 0, "y": 0}     # saved on a tall screen
    restored = validate_saved_geometry(saved, 1366, 768)

    assert restored is not None
    assert restored.height <= 768
    assert is_on_screen(restored, 1366, 768)


def test_a_saved_window_behind_the_taskbar_comes_back_above_it():
    work_area = (0, 0, 1366, 728)
    saved = {"width": 400, "height": 700, "x": 900, "y": 60}    # bottom at 760
    restored = validate_saved_geometry(saved, 1366, 768, work_area=work_area)

    assert restored.bottom <= 728


@pytest.mark.parametrize("saved", [None, {}, "nonsense", {"width": 0, "height": 0,
                                                          "x": 0, "y": 0},
                                   {"width": "wide", "height": 10, "x": 0, "y": 0},
                                   [1, 2, 3]])
def test_a_missing_or_broken_saved_position_is_refused(saved):
    assert validate_saved_geometry(saved, 1366, 768) is None


def test_geometry_strings_survive_a_round_trip():
    assert parse_geometry("430x900+1474+16") == Geometry(430, 900, 1474, 16)
    assert parse_geometry("430x900+-12+16") == Geometry(430, 900, -12, 16)
    assert parse_geometry("430x900-12+16") == Geometry(430, 900, -12, 16)
    assert Geometry(430, 900, 1474, 16).as_string() == "430x900+1474+16"
    assert parse_geometry("not a geometry") is None
    assert parse_geometry(None) is None
    assert coerce_geometry((430, 900, 10, 20)) == Geometry(430, 900, 10, 20)
    assert coerce_geometry(Geometry(1, 2, 3, 4)) == Geometry(1, 2, 3, 4)


# -- what each machine remembers ----------------------------------------------

def test_a_size_and_position_come_back_on_the_same_machine_and_screen(tmp_path):
    saved = store(tmp_path)
    assert saved.save(Geometry(400, 700, 900, 40), 1366, 768) is True

    assert store(tmp_path).load(1366, 768) == Geometry(400, 700, 900, 40)


def test_another_machine_or_another_screen_starts_from_the_adaptive_size(tmp_path):
    store(tmp_path).save(Geometry(400, 700, 900, 40), 1366, 768)

    assert store(tmp_path).load(1920, 1080) is None            # a different screen
    assert store(tmp_path, node="other-laptop").load(1366, 768) is None


def test_each_machine_keeps_its_own_entry(tmp_path):
    store(tmp_path, node="desk").save(Geometry(430, 900, 1474, 16), 1920, 1080)
    store(tmp_path, node="laptop").save(Geometry(400, 690, 920, 16), 1366, 768)

    assert store(tmp_path, node="desk").load(1920, 1080) == Geometry(430, 900, 1474, 16)
    assert store(tmp_path, node="laptop").load(1366, 768) == Geometry(400, 690, 920, 16)
    assert machine_key(1366, 768, "laptop") == "laptop@1366x768"


def test_an_unreadable_or_missing_state_file_is_not_fatal(tmp_path):
    missing = WindowStateStore(path=str(tmp_path / "nothing.json"), node="test")
    assert missing.load(1366, 768) is None

    path = tmp_path / "window_state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert store(tmp_path).load(1366, 768) is None
    # A corrupt file is replaced rather than crashing the shutdown.
    assert store(tmp_path).save(Geometry(400, 700, 10, 10), 1366, 768) is True
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_a_position_that_cannot_be_saved_is_reported_not_raised(tmp_path):
    unwritable = WindowStateStore(path=str(tmp_path / "a-file" / "x.json"), node="t")
    (tmp_path / "a-file").write_text("I am a file, not a directory", encoding="utf-8")

    assert unwritable.save(Geometry(400, 700, 10, 10), 1366, 768) is False


# -- resizing by hand ---------------------------------------------------------

def test_growing_the_window_stops_at_the_screen():
    geometry = Geometry(400, 700, 900, 40)
    for _ in range(20):
        geometry = resize_within_screen(geometry, 1366, 768, 1.1)

    assert is_on_screen(geometry, 1366, 768)
    assert geometry.width <= 1366 and geometry.height <= 768


def test_shrinking_the_window_stops_at_a_readable_size():
    geometry = Geometry(400, 700, 900, 40)
    for _ in range(20):
        geometry = resize_within_screen(geometry, 1366, 768, 1 / 1.1)

    assert (geometry.width, geometry.height) == (MIN_WIDTH, MIN_HEIGHT)
    assert is_on_screen(geometry, 1366, 768)


def test_resizing_a_window_docked_to_the_right_keeps_it_on_screen():
    docked = get_adaptive_window_geometry(1366, 768)
    grown = resize_within_screen(docked, 1366, 768, 1.3)

    assert is_on_screen(grown, 1366, 768)


# -- how the content arranges itself -----------------------------------------

def test_a_wide_enough_window_keeps_the_scenario_panel_beside_the_cards():
    mode = layout_mode(412, side_by_side_width=395, two_column_button_width=323)

    assert mode["stack_scenario"] is False
    assert mode["single_column_buttons"] is False


def test_a_narrow_window_moves_the_scenario_panel_under_the_cards():
    mode = layout_mode(330, side_by_side_width=395, two_column_button_width=323)

    assert mode["stack_scenario"] is True       # reflowed, not cut off
    assert mode["single_column_buttons"] is False


def test_the_narrowest_window_puts_the_buttons_one_to_a_row():
    mode = layout_mode(302, side_by_side_width=395, two_column_button_width=323)

    assert mode["stack_scenario"] is True
    assert mode["single_column_buttons"] is True


def test_content_taller_than_the_window_scrolls():
    # The content is about 830 pixels tall; a 1366x768 laptop cannot show it all.
    geometry = get_adaptive_window_geometry(1366, 768)

    assert needs_vertical_scroll(830, geometry.height) is True
    assert needs_vertical_scroll(830, get_adaptive_window_geometry(
        1920, 1080).height) is False
    assert needs_horizontal_scroll(412, 412) is False
    assert needs_horizontal_scroll(500, 412) is True
