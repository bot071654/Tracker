"""The local TEST poker table and the real window checks made before a click.

No mouse movement: buttons are invoked directly, and the controller's
validation is asked about points on the real window.
"""

import json
import time

import pytest

from automation import mouse_controller as mc

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    from automation.test_poker_ui import TestPokerTable

    status = str(tmp_path_factory.mktemp("table") / "status.json")
    config = mc.MouseControllerConfig(automation_enabled=True, status_file=status,
                                      test_window_position=[30, 60]).validate()
    ui = TestPokerTable(root, config)
    for _ in range(10):
        root.update()
        time.sleep(0.02)
    ui.write_status()
    yield ui, config
    try:
        ui.close()
    except tk.TclError:
        pass


def test_buttons_update_last_action_round_and_heartbeat(table):
    ui, config = table
    ui.buttons[mc.ANTE].invoke()
    assert ui.last_var.get() == "Last Action:\nANTE" and ui.round_var.get() == "Round:\n1"
    ui.buttons[mc.PLAY_BUTTON].invoke()
    assert ui.last_var.get() == "Last Action:\nPLAY" and ui.round_var.get() == "Round:\n1"
    status = json.load(open(config.status_file))
    assert status["title"] == "TEST POKER TABLE"
    assert status["counts"] == {mc.ANTE: 1, mc.BONUS: 0, mc.PLAY_BUTTON: 1}
    assert status["round"] == 1 and status["last_action"] == "PLAY"


def test_real_validation_accepts_the_centre_of_a_visible_button(table):
    ui, config = table
    ui.root.lift()
    ui.root.update()
    ui.write_status()
    target = mc.TestTableTarget(config)
    centres = ui.centres()
    assert set(centres) == {mc.ANTE, mc.BONUS, mc.PLAY_BUTTON}, "ANTE, BONUS and PLAY are shown"
    for button in mc.CLICKABLE:
        ok, reason = target.validate(button, centres[button])
        assert ok, reason
    ok, reason = target.validate(mc.BONUS, centres[mc.BONUS])
    assert not ok and "not a TEST action button" in reason


def test_real_validation_refuses_a_point_outside_the_button(table):
    ui, config = table
    ui.write_status()
    left, top, right, bottom = ui.button_rects()[mc.PLAY_BUTTON]
    ok, reason = mc.TestTableTarget(config).validate(mc.PLAY_BUTTON, [right + 40, bottom + 40])
    assert not ok and "outside" in reason


def test_real_validation_refuses_a_stale_or_missing_table(table, tmp_path):
    ui, config = table
    missing = mc.MouseControllerConfig(status_file=str(tmp_path / "none.json")).validate()
    assert mc.TestTableTarget(missing).validate(mc.ANTE, [1, 1])[0] is False
    stale_path = tmp_path / "stale.json"
    status = json.load(open(config.status_file))
    status["heartbeat"] = time.time() - 60
    stale_path.write_text(json.dumps(status))
    stale = mc.MouseControllerConfig(status_file=str(stale_path)).validate()
    ok, reason = mc.TestTableTarget(stale).validate(mc.ANTE, ui.centres()[mc.ANTE])
    assert not ok and "stale" in reason


def test_button_centres_come_from_the_running_table(table):
    ui, config = table
    ui.write_status()
    target = mc.TestTableTarget(config)
    assert target.button_centre(mc.ANTE) == ui.centres()[mc.ANTE]
    assert target.button_centre(mc.PLAY_BUTTON) == ui.centres()[mc.PLAY_BUTTON]


def test_regression_an_always_on_top_window_over_the_table_does_not_block_clicks(table):
    """The reported failure: the tracker window (always on top, 430x900) opens over
    the test table, so every click was refused as 'another window covers'."""
    import ctypes

    ui, config = table
    ui.write_status()
    left, top, right, bottom = ui.button_rects()[mc.ANTE]
    cover = tk.Toplevel(ui.root)
    cover.title("Poker Hand Tracker (stand-in)")
    cover.geometry("%dx%d+%d+%d" % (400, 400, max(0, left - 40), max(0, top - 60)))
    cover.attributes("-topmost", True)
    for _ in range(10):
        cover.update()
        time.sleep(0.02)
    cover_hwnd = int(cover.wm_frame(), 16)
    point = ui.centres()[mc.ANTE]
    try:
        assert mc._window_at(*point) == cover_hwnd, "precondition: the stand-in covers the button"
        foreground = ctypes.windll.user32.GetForegroundWindow()
        ok, reason = mc.TestTableTarget(config).validate(mc.ANTE, point)
        assert ok, reason
        assert mc._window_at(*point) == int(ui.root.wm_frame(), 16)
        assert ctypes.windll.user32.GetForegroundWindow() == foreground, "raising must not take focus"
    finally:
        cover.destroy()


def test_regression_a_window_from_another_process_is_refused(table, tmp_path):
    """TEST mode must not target a window that is not the test table's own."""
    import subprocess
    import sys

    ui, config = table
    impostor = subprocess.Popen([sys.executable, "-c",
                                 "import tkinter as tk; w = tk.Tk(); w.title('TEST POKER TABLE'); "
                                 "w.geometry('200x200+600+300'); w.after(8000, w.destroy); w.mainloop()"])
    try:
        hwnd = None
        for _ in range(100):
            found = ctypes_find("TEST POKER TABLE", exclude=int(ui.root.wm_frame(), 16))
            if found:
                hwnd = found
                break
            time.sleep(0.05)
        assert hwnd, "impostor window did not appear"
        status = json.load(open(config.status_file))
        status.update(hwnd=hwnd, heartbeat=time.time())        # pid is still this process
        path = tmp_path / "impostor.json"
        path.write_text(json.dumps(status))
        fake = mc.MouseControllerConfig(status_file=str(path)).validate()
        ok, reason = mc.TestTableTarget(fake).validate(mc.ANTE, ui.centres()[mc.ANTE])
        assert not ok and "does not belong" in reason
    finally:
        impostor.terminate()


def ctypes_find(title, exclude):
    import ctypes
    from ctypes import wintypes

    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, 256)
        if buffer.value == title and hwnd != exclude and ctypes.windll.user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return found[0] if found else None


def test_real_validation_refuses_a_minimised_table(table):
    ui, config = table
    ui.root.iconify()
    ui.root.update()
    time.sleep(0.2)
    ui.write_status()
    ok, reason = mc.TestTableTarget(config).validate(mc.ANTE, ui.centres()[mc.ANTE])
    ui.root.deiconify()
    ui.root.update()
    assert not ok and "not visible" in reason
