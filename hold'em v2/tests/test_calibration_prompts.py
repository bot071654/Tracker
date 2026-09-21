"""The calibration prompts must not cover the cards, or land in the picture.

Two faults, both of which made calibration hard to do:

    * the prompt opened centred - which on a casino table is exactly where the
      cards are;
    * the screenshot was taken before the app hid its own windows and without
      waiting for the desktop to repaint, so the prompt that had just been
      dismissed was still printed across the picture the boxes are drawn on.

The placement rules are checked without a display, so they hold wherever the
tests run. The window behaviour underneath them needs a real Tk and skips
without one, the same way the existing UI smoke test does.
"""

import pytest

from calibration.calibrator import (
    CORNERS, CORNER_MARGIN, DEFAULT_CORNER, corner_position, next_corner,
)

SCREEN = (0, 0, 1920, 1080)
PROMPT = (420, 260)


# -- where a prompt goes (no display needed) ----------------------------------

@pytest.mark.parametrize("corner", CORNERS)
def test_a_prompt_lands_in_the_corner_it_was_asked_for(corner):
    x, y = corner_position(corner, PROMPT, SCREEN)
    assert (x < 960) == ("left" in corner), "wrong side of the screen"
    assert (y < 540) == ("top" in corner), "wrong half of the screen"


@pytest.mark.parametrize("corner", CORNERS)
def test_a_prompt_is_never_centred_over_the_table(corner):
    """The original fault: a centred box sits right on top of the cards."""
    x, y = corner_position(corner, PROMPT, SCREEN)
    horizontally_central = 480 < x + PROMPT[0] // 2 < 1440
    vertically_central = 270 < y + PROMPT[1] // 2 < 810
    assert not (horizontally_central and vertically_central)


@pytest.mark.parametrize("corner", CORNERS)
def test_a_prompt_stays_fully_on_the_screen(corner):
    x, y = corner_position(corner, PROMPT, SCREEN)
    left, top, width, height = SCREEN
    assert left <= x and x + PROMPT[0] <= left + width
    assert top <= y and y + PROMPT[1] <= top + height


def test_a_prompt_is_inset_from_the_edge():
    """Flush against the edge reads as a glitch, and can sit under the taskbar."""
    assert corner_position("top-left", PROMPT, SCREEN) == (CORNER_MARGIN,
                                                           CORNER_MARGIN)


def test_a_prompt_bigger_than_the_screen_still_starts_on_it():
    x, y = corner_position("bottom-right", (900, 700), (0, 0, 400, 300))
    assert x >= 0 and y >= 0


def test_a_prompt_follows_the_monitor_being_calibrated():
    """A second screen does not start at zero, and the boxes are absolute."""
    second = (1920, 0, 1920, 1080)
    x, y = corner_position("top-left", PROMPT, second)
    assert x >= 1920, "the prompt opened on the wrong monitor"


def test_every_corner_is_reachable_by_clicking_move():
    """Cards in the corner the prompt chose must not trap the user."""
    seen, corner = [], DEFAULT_CORNER
    for _ in range(len(CORNERS)):
        seen.append(corner)
        corner = next_corner(corner)
    assert sorted(seen) == sorted(CORNERS)
    assert corner == DEFAULT_CORNER, "the corners must cycle back round"


def test_an_unknown_corner_falls_back_rather_than_breaking():
    """config.json is hand-editable, so it can say anything."""
    assert next_corner("middle") == DEFAULT_CORNER
    assert corner_position("nonsense", PROMPT, SCREEN) == corner_position(
        "bottom-right", PROMPT, SCREEN), "an unknown corner must still land"


# -- the window and the capture (needs a display) -----------------------------

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


@pytest.fixture()
def clean(root):
    yield root
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except tk.TclError:
            continue
    try:
        root.withdraw()
        root.update()
    except tk.TclError:
        pass


def test_the_prompt_opens_where_it_was_placed(clean, monkeypatch):
    import calibration.calibrator as calibrator

    monkeypatch.setattr(calibrator, "_monitor_bounds", lambda _m: SCREEN)
    dialog = calibrator._CornerDialog(
        clean, "Calibrate", "message", [("OK", True)],
        {"dialog_corner": "bottom-right"})
    dialog.grab_release()
    clean.update_idletasks()

    assert dialog.winfo_x() > 960 and dialog.winfo_y() > 540
    dialog.destroy()


def test_the_chosen_corner_is_remembered(clean, monkeypatch):
    import calibration.calibrator as calibrator

    monkeypatch.setattr(calibrator, "_monitor_bounds", lambda _m: SCREEN)
    saved = {}
    monkeypatch.setattr(calibrator, "save_config", saved.update)

    config = {"regions": {"dealer_1": {}}, "dialog_corner": "top-left"}
    dialog = calibrator._CornerDialog(
        clean, "Calibrate", "message", [("OK", True)], config)
    dialog.grab_release()
    dialog._move()

    assert config["dialog_corner"] == next_corner("top-left")
    assert saved.get("dialog_corner") == config["dialog_corner"]
    dialog.destroy()


def test_an_uncalibrated_config_is_not_written_to_disk(clean, monkeypatch):
    """Moving the prompt must never create a half-written config file."""
    import calibration.calibrator as calibrator

    monkeypatch.setattr(calibrator, "_monitor_bounds", lambda _m: SCREEN)
    written = []
    monkeypatch.setattr(calibrator, "save_config",
                        lambda cfg: written.append(cfg))

    dialog = calibrator._CornerDialog(
        clean, "Calibrate", "message", [("OK", True)], {})   # no "regions"
    dialog.grab_release()
    dialog._move()

    assert written == []
    dialog.destroy()


def test_the_picture_is_taken_with_the_app_out_of_it(clean, monkeypatch):
    """The fault behind "the pop up is covering the cards in the screenshot"."""
    import calibration.calibrator as calibrator

    clean.deiconify()
    prompt = tk.Toplevel(clean)
    prompt.geometry("300x200")
    clean.update()

    seen = {}

    def fake_grab(monitor=1):
        seen["root"] = clean.winfo_viewable()
        seen["prompt"] = prompt.winfo_viewable()
        return "picture"

    monkeypatch.setattr(calibrator, "grab_full_screen", fake_grab)
    monkeypatch.setattr(calibrator, "SETTLE_SECONDS", 0.01)

    assert calibrator._capture_clean(prompt, 1) == "picture"
    assert not seen["root"], "the main window was still on screen"
    assert not seen["prompt"], "the prompt was still on screen"

    clean.update()
    assert clean.winfo_viewable(), "the app was not brought back afterwards"
    assert prompt.winfo_viewable(), "the prompt was not brought back afterwards"


def test_the_desktop_is_given_time_to_repaint(clean, monkeypatch):
    """Hiding the prompt is not enough - Windows repaints behind it late."""
    import calibration.calibrator as calibrator

    clean.deiconify()
    clean.update()
    waited = []

    monkeypatch.setattr(calibrator.time, "sleep", waited.append)
    monkeypatch.setattr(calibrator, "grab_full_screen", lambda monitor=1: "picture")

    calibrator._capture_clean(clean, 1)
    assert waited and waited[0] > 0, "no pause before the capture"


def test_the_windows_come_back_even_when_the_capture_fails(clean, monkeypatch):
    import calibration.calibrator as calibrator
    from capture.screen_capture import CaptureError

    clean.deiconify()
    clean.update()

    def fails(monitor=1):
        raise CaptureError("no screen")

    monkeypatch.setattr(calibrator, "grab_full_screen", fails)
    monkeypatch.setattr(calibrator, "SETTLE_SECONDS", 0.01)

    with pytest.raises(CaptureError):
        calibrator._capture_clean(clean, 1)

    clean.update()
    assert clean.winfo_viewable(), "the app stayed hidden after a failed capture"
