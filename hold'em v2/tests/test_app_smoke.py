"""Smoke test: the UI builds and tears down without errors."""

import pytest

tk = pytest.importorskip("tkinter")


def test_main_window_builds_and_closes(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)

    from app import App
    from ui.window_geometry import WindowStateStore

    root.withdraw()
    # This test shows the window, and closing it remembers where it was, so it
    # is given its own file instead of writing to config/window_state.json.
    app = App(root, window_state=WindowStateStore(
        path=str(tmp_path / "window_state.json"), node="test-machine"))
    root.update()

    assert app.status_var.get() == "Status: STOPPED"
    assert app.card_vars["player_1"].get() == "--"
    assert app.player_hand_var.get() == "--"

    # Always on top of the browser, without a grab that would capture input.
    assert bool(root.attributes("-topmost")) is True
    assert root.grab_current() is None
    root.iconify()
    root.update()
    root.deiconify()
    root.update()
    assert bool(root.attributes("-topmost")) is True

    app.open_live_debug()
    root.update()
    assert bool(app.debug_window.attributes("-topmost")) is True

    # Tk variables outliving the destroyed window are finalised later, which
    # pytest reports as an unraisable exception while collecting. That is
    # teardown noise from Tkinter, not a fault in the application - and forcing
    # collection here to avoid it crashes the Tcl interpreter.
    app.on_close()


def test_the_rule_windows_build_and_close():
    """Both scenario builders open, list their rules, and shut down cleanly."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    root.withdraw()

    from ui.rule_windows import FlopRules, PreRoundRules

    for window_class in (PreRoundRules, FlopRules):
        window = window_class(root)
        root.update()
        assert window.listbox.size() >= 1          # rules, or the empty note
        assert window.body()["default"] in window.actions()
        window.destroy()

    root.update()
    root.destroy()
