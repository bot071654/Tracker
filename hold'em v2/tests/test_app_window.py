"""The real tracker window, laid out for somebody else's laptop.

These build the actual UI and ask it where it put itself and how it arranged
its content. The screen is passed in, so the 1366x768 laptop that used to clip
the Scenario section is tested from whatever machine happens to run the suite.

Tk reports a window that has never been shown at the size its content asked
for rather than the size the window manager will give it, so these check
App.requested_geometry - the size and position the app asks for - which is the
decision under test.
"""

import pytest

tk = pytest.importorskip("tkinter")

from ui.window_geometry import (  # noqa: E402
    Geometry, MIN_HEIGHT, MIN_WIDTH, WindowStateStore, is_on_screen,
    needs_vertical_scroll,
)

LAPTOPS = [(1920, 1080), (1536, 864), (1366, 768), (1280, 720), (1024, 600)]


@pytest.fixture(autouse=True)
def no_background_work(monkeypatch):
    """Build the window without its startup trips to PostgreSQL.

    Nothing here is about the database, and a window per resolution would
    otherwise leave a background thread per window querying it.
    """
    from app import App

    monkeypatch.setattr(App, "_startup_checks", lambda self: None)
    monkeypatch.setattr(App, "_refresh_statistics", lambda self: None)


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:            # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:                   # pragma: no cover - already closed
        pass


def build(root, tmp_path, screen, saved=None, node="test-machine"):
    """The tracker laid out for ``screen``, remembering nothing but ``saved``."""
    from app import App

    store = WindowStateStore(path=str(tmp_path / "window_state.json"), node=node)
    if saved is not None:
        store.save(saved, screen[0], screen[1])
    app = App(root, screen=screen, work_area=(0, 0, screen[0], screen[1]),
              window_state=store)
    root.update()
    return app


# -- where the window ends up -------------------------------------------------

@pytest.mark.parametrize("screen", LAPTOPS)
def test_the_whole_window_is_on_screen_on_every_laptop(root, tmp_path, screen):
    app = build(root, tmp_path, screen)
    geometry = app.requested_geometry

    assert is_on_screen(geometry, screen[0], screen[1]), geometry.as_string()
    # The Scenario section lives on the window's right-hand edge.
    assert geometry.right <= screen[0]
    assert geometry.bottom <= screen[1]
    # And it does not bury the table it sits beside.
    assert geometry.width <= max(MIN_WIDTH, screen[0] * 0.4)


def test_a_1920x1080_screen_gets_the_window_it_always_had(root, tmp_path):
    app = build(root, tmp_path, (1920, 1080))

    assert app.scenario_stacked is False          # panel beside the cards
    assert app.button_columns == 2
    # All of the content, or as much of the screen as it is allowed to take.
    # (Which of the two depends on the display scaling of the machine running
    # the tests, so both are accepted.)
    assert app.requested_geometry.height >= min(app._content_height, 1080 * 0.9)


def test_the_scenario_panel_is_reflowed_not_clipped_on_a_small_laptop(root, tmp_path):
    app = build(root, tmp_path, (1024, 600))

    assert app.scenario_stacked is True           # under the cards, still on screen
    assert app.scenario_panel.winfo_manager() == "pack"
    assert app.scenario_panel.pack_info()["side"] == "top"
    # With the whole width to itself its text is no longer in a 212-pixel strip.
    assert app.scenario_panel.wraplength > 212
    assert is_on_screen(app.requested_geometry, 1024, 600)


def test_the_panel_goes_back_beside_the_cards_when_there_is_room(root, tmp_path):
    app = build(root, tmp_path, (1024, 600))
    assert app.scenario_stacked is True

    app._apply_layout(900)                        # a window dragged wider

    assert app.scenario_stacked is False
    assert app.scenario_panel.pack_info()["side"] == "left"


def test_nothing_is_dropped_at_the_smallest_size(root, tmp_path):
    """Buttons, cards, Scenario, Result Panel and the scenario text all stay."""
    app = build(root, tmp_path, (1024, 600))
    for _ in range(20):
        app.shrink_window()
    root.update()

    assert app.requested_geometry.width == MIN_WIDTH
    assert app.button_columns == 1                # one to a row, none cut off
    for button in app.buttons:
        assert button.winfo_manager() == "grid"
    for widget in (app.card_table, app.scenario_panel, app.verification_label,
                   app.buttons_frame):
        assert widget.winfo_manager() in ("pack", "grid")
    for label in app._wrapping_labels:
        assert label.cget("wraplength") > 0
        assert label.winfo_manager() == "pack"
    # Every card row is still there.
    for slot in ("player_1", "flop_1", "turn", "river", "dealer_1"):
        assert app.card_vars[slot].get() == "--"


# -- content taller than the screen -------------------------------------------

def test_content_taller_than_the_screen_scrolls_instead_of_disappearing(root, tmp_path):
    app = build(root, tmp_path, (1366, 768))

    assert needs_vertical_scroll(app._content_height, app.requested_geometry.height)
    assert app._vscroll_shown is True
    assert app.vscroll.winfo_manager() == "grid"
    # The whole column is reachable: the scroll region covers all of it.
    region = [int(float(value)) for value in
              str(app.canvas.cget("scrollregion")).split()]
    assert region[3] - region[1] >= app.content.winfo_reqheight()


def test_a_long_scenario_does_not_push_anything_off_the_bottom(root, tmp_path):
    app = build(root, tmp_path, (1366, 768))
    before = app.content.winfo_reqheight()

    # The SCENARIO panel is the one scenario area now, and it is inside the
    # scrolling column, so a long decision is what can push the rest down.
    app.scenario_panel.details_var.set(
        "\n".join("Matched: SCENARIO_%d" % n for n in range(20)))
    root.update()

    assert app.content.winfo_reqheight() > before      # the column simply got taller
    assert app._vscroll_shown is True
    region = [int(float(value)) for value in
              str(app.canvas.cget("scrollregion")).split()]
    assert region[3] - region[1] >= app.content.winfo_reqheight()


def test_a_tall_screen_needs_no_scrollbar(root, tmp_path):
    app = build(root, tmp_path, (2560, 1600))

    assert needs_vertical_scroll(app._content_height,
                                 app.requested_geometry.height) is False


# -- remembering the last size and position -----------------------------------

def test_the_last_size_and_position_come_back(root, tmp_path):
    app = build(root, tmp_path, (1366, 768), saved=Geometry(400, 640, 900, 40))

    assert app.requested_geometry == Geometry(400, 640, 900, 40)


def test_a_saved_position_from_a_monitor_that_is_gone_is_ignored(root, tmp_path):
    """Saved on a second screen at x=2400; this laptop only has one."""
    app = build(root, tmp_path, (1366, 768), saved=Geometry(430, 900, 2400, 100))

    assert is_on_screen(app.requested_geometry, 1366, 768)
    assert app.requested_geometry == app.adaptive_geometry()


def test_a_saved_window_hanging_off_the_right_is_brought_back(root, tmp_path):
    app = build(root, tmp_path, (1366, 768), saved=Geometry(430, 640, 1200, 40))

    assert is_on_screen(app.requested_geometry, 1366, 768)
    assert app.requested_geometry.right <= 1366
    assert app.requested_geometry.width == 430


def test_a_window_that_was_never_shown_is_not_remembered(root, tmp_path):
    """A withdrawn window's geometry is not its real one, so it is not saved."""
    app = build(root, tmp_path, (1366, 768))

    assert app._save_window_state() is False
    assert app.window_state.load(1366, 768) is None


# -- resizing by hand ---------------------------------------------------------

def test_the_window_can_be_resized_and_put_back(root, tmp_path):
    app = build(root, tmp_path, (1366, 768))
    original = app.requested_geometry

    app.grow_window()
    assert app.requested_geometry.width > original.width
    assert is_on_screen(app.requested_geometry, 1366, 768)

    app.shrink_window()
    assert app.requested_geometry.width < app.adaptive_geometry().width * 1.1

    app.reset_window_size()
    assert app.requested_geometry == original


def test_fit_to_screen_uses_the_height_the_screen_allows(root, tmp_path):
    app = build(root, tmp_path, (1366, 768))

    app.fit_window_to_screen()

    assert app.requested_geometry.height >= app.adaptive_geometry().height
    assert is_on_screen(app.requested_geometry, 1366, 768)


def test_resizing_by_hand_never_goes_below_a_readable_size(root, tmp_path):
    app = build(root, tmp_path, (1366, 768))
    for _ in range(30):
        app.shrink_window()

    assert app.requested_geometry.width == MIN_WIDTH
    assert app.requested_geometry.height == MIN_HEIGHT


def test_the_window_is_resizable_and_still_on_top(root, tmp_path):
    app = build(root, tmp_path, (1366, 768))

    assert root.resizable() in ((1, 1), (True, True))
    assert bool(root.attributes("-topmost")) is True
    assert app.root.minsize()[0] <= app.requested_geometry.width
