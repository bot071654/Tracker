"""Interactive calibration: pick the nine card regions on the real screen.

Nothing is hard-coded.  A frozen screenshot is shown full-screen and the user
drags a box around each card in turn; the boxes are stored in config.json as
absolute screen coordinates, so recalibrating after the browser moves or the
resolution changes is just a matter of running this again.
"""

import ctypes
import logging
import os
import sys
import tempfile
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import cv2

from capture.screen_capture import CaptureError, crop, grab_full_screen
from config.settings import CARD_SLOTS, ROOT, SLOT_LABELS, save_config
from poker.hand_evaluator import is_valid_card, normalize_card
from recognition.card_recognizer import save_glyph_templates

logger = logging.getLogger(__name__)

# The optional bottom-left result boxes, used only for validation.
RESULT_SLOTS = (
    [("player_result_%d" % i, "Player result card %d" % i) for i in range(1, 6)]
    + [("dealer_result_%d" % i, "Dealer result card %d" % i) for i in range(1, 6)]
)

MIN_REGION_PIXELS = 8

# Where the calibration prompts sit. They ask about the cards, so they must not
# be over the cards - and the corner that is clear of the table differs from
# one screen to the next, so the user picks it and it is remembered.
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
DEFAULT_CORNER = "top-left"
CORNER_MARGIN = 24

# How long to let the desktop repaint after this app's windows are hidden.
# Windows repaints what was underneath a closed window asynchronously, so a
# capture taken immediately still shows the prompt that was just dismissed.
SETTLE_SECONDS = 0.4


def make_dpi_aware():
    """Stop Windows display scaling from shifting captured coordinates.

    Without this, Tkinter reports logical pixels while MSS captures physical
    ones, so every calibrated box would be offset on a scaled display.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:  # noqa: BLE001 - older Windows, or already set
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _monitor_origin(monitor_index):
    """Absolute (left, top) of the captured monitor."""
    try:
        import mss

        with mss.mss() as sct:
            index = monitor_index if 0 <= monitor_index < len(sct.monitors) else 0
            area = sct.monitors[index]
            return area["left"], area["top"]
    except Exception:  # noqa: BLE001
        return 0, 0


def _monitor_bounds(monitor_index):
    """Absolute (left, top, width, height) of the captured monitor."""
    try:
        import mss

        with mss.mss() as sct:
            index = monitor_index if 0 <= monitor_index < len(sct.monitors) else 0
            area = sct.monitors[index]
            return area["left"], area["top"], area["width"], area["height"]
    except Exception:  # noqa: BLE001
        return None


def corner_position(corner, size, bounds, margin=CORNER_MARGIN):
    """Top-left (x, y) that puts a `size` window in `corner` of `bounds`.

    `size` is (width, height) and `bounds` is (left, top, width, height) - the
    monitor being calibrated, which on a second screen does not start at zero.
    """
    width, height = size
    left, top, screen_width, screen_height = bounds

    x = left + margin if "left" in corner else left + screen_width - width - margin
    y = top + margin if "top" in corner else top + screen_height - height - margin
    # A prompt taller or wider than the screen must still start on it.
    return max(left, int(x)), max(top, int(y))


def next_corner(corner):
    """The corner that "Move to next corner" moves to."""
    try:
        index = CORNERS.index(corner)
    except ValueError:
        return DEFAULT_CORNER
    return CORNERS[(index + 1) % len(CORNERS)]


def _toplevels(widget):
    """Every Tk/Toplevel window under `widget`, including itself."""
    found = []
    if isinstance(widget, (tk.Tk, tk.Toplevel)):
        found.append(widget)
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return found
    for child in children:
        found.extend(_toplevels(child))
    return found


def _root_of(widget):
    """The application's root window, from any widget."""
    while getattr(widget, "master", None) is not None:
        widget = widget.master
    return widget


def _capture_clean(parent, monitor):
    """Grab the screen with none of this app's own windows in the picture.

    Hiding them is not enough on its own. The desktop underneath is repainted
    asynchronously, so without a pause the prompt the user just dismissed is
    still there in the picture - printed across the very cards they are about
    to be asked to draw boxes around.
    """
    root = _root_of(parent)
    hidden = []
    for window in _toplevels(root):
        try:
            if window.winfo_viewable():
                window.withdraw()
                hidden.append(window)
        except tk.TclError:      # a window destroyed while we were walking
            continue

    try:
        root.update()
        time.sleep(SETTLE_SECONDS)
        root.update()
        return grab_full_screen(monitor)
    finally:
        for window in reversed(hidden):
            try:
                window.deiconify()
            except tk.TclError:
                continue
        try:
            root.update_idletasks()
        except tk.TclError:
            pass


class _CornerDialog(tk.Toplevel):
    """A prompt that opens in a corner rather than over the middle of the screen.

    Tkinter's own message boxes centre themselves, which during calibration is
    exactly where the cards are. This is the same thing moved out of the way,
    with a button to move it again when the corner it chose is the one with the
    cards in it.
    """

    def __init__(self, parent, title, message, buttons, config):
        super().__init__(parent)
        self.settings = config if isinstance(config, dict) else {}
        self.monitor = int(self.settings.get("monitor", 1) or 1)
        self.result = None

        self.title(title)
        self.attributes("-topmost", True)
        self.resizable(False, False)

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title,
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text=message, wraplength=380,
                  justify="left").pack(anchor="w", pady=(6, 12))

        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Button(row, text="Move to next corner",
                   command=self._move).pack(side="left")
        for text, value in reversed(buttons):
            ttk.Button(row, text=text,
                       command=lambda choice=value: self._choose(choice)).pack(
                side="right", padx=(6, 0))

        self.protocol("WM_DELETE_WINDOW", lambda: self._choose(None))
        self.bind("<Escape>", lambda _event: self._choose(None))
        self.bind("<Return>", lambda _event: self._choose(buttons[0][1]))
        self._place()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:      # another grab is already active
            pass

    @property
    def corner(self):
        corner = self.settings.get("dialog_corner")
        return corner if corner in CORNERS else DEFAULT_CORNER

    def _place(self):
        self.update_idletasks()
        size = (max(self.winfo_width(), self.winfo_reqwidth()),
                max(self.winfo_height(), self.winfo_reqheight()))

        bounds = _monitor_bounds(self.monitor)
        if bounds is None:
            bounds = (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())

        self.geometry("+%d+%d" % corner_position(self.corner, size, bounds))

    def _move(self):
        self.settings["dialog_corner"] = next_corner(self.corner)
        # Remember the choice, but never let a saved preference break a prompt.
        if "regions" in self.settings:
            try:
                save_config(self.settings)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not remember the prompt corner: %s", exc)
        self._place()

    def _choose(self, value):
        self.result = value
        self.destroy()

    def run(self):
        self.wait_window()
        return self.result


def show_info(parent, title, message, config=None):
    """Corner-anchored "OK" box, in place of messagebox.showinfo."""
    _CornerDialog(parent, title, message, [("OK", True)], config).run()


def ask_yes_no(parent, title, message, config=None):
    """Corner-anchored Yes/No. Closing the window counts as No."""
    return _CornerDialog(
        parent, title, message, [("Yes", True), ("No", False)], config
    ).run() is True


class _RegionSelector:
    """Full-screen screenshot on which the user drags one box per slot."""

    def __init__(self, parent, image_path, slots, origin, title):
        self.slots = slots
        self.origin = origin
        self.regions = {}
        self.index = 0
        self.cancelled = False
        self._start = None
        self._rect = None
        self._banner_at_top = True

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.attributes("-fullscreen", True)
        self.window.attributes("-topmost", True)
        self.window.configure(cursor="crosshair")

        self.photo = tk.PhotoImage(file=image_path)
        self.canvas = tk.Canvas(
            self.window, highlightthickness=0,
            width=self.photo.width(), height=self.photo.height(),
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.window.bind("<Escape>", self._on_cancel)
        self.window.bind("m", self._move_banner)
        self.window.bind("<BackSpace>", self._undo)
        self.window.focus_force()

        self._draw_banner()

    # -- drawing -----------------------------------------------------------

    def _draw_banner(self):
        self.canvas.delete("banner")
        if self.index >= len(self.slots):
            return
        _, label = self.slots[self.index]
        text = "Drag a box around:  %s        (%d of %d)" % (
            label, self.index + 1, len(self.slots)
        )
        hint = "Backspace = redo last    m = move this bar    Esc = cancel"

        height = self.photo.height()
        y = 40 if self._banner_at_top else height - 70
        self.canvas.create_rectangle(
            0, y - 34, self.photo.width(), y + 40, fill="#101820",
            outline="", tags="banner",
        )
        self.canvas.create_text(
            self.photo.width() // 2, y - 8, text=text, fill="#ffd166",
            font=("Segoe UI", 18, "bold"), tags="banner",
        )
        self.canvas.create_text(
            self.photo.width() // 2, y + 20, text=hint, fill="#9fb3c8",
            font=("Segoe UI", 11), tags="banner",
        )

    def _draw_saved(self):
        self.canvas.delete("saved")
        for slot, box in self.regions.items():
            left = box["left"] - self.origin[0]
            top = box["top"] - self.origin[1]
            self.canvas.create_rectangle(
                left, top, left + box["width"], top + box["height"],
                outline="#2ecc71", width=2, tags="saved",
            )
            self.canvas.create_text(
                left + 4, top - 10, text=slot, anchor="w", fill="#2ecc71",
                font=("Segoe UI", 9, "bold"), tags="saved",
            )

    # -- events ------------------------------------------------------------

    def _move_banner(self, _event=None):
        self._banner_at_top = not self._banner_at_top
        self._draw_banner()

    def _on_press(self, event):
        self._start = (event.x, event.y)
        if self._rect is not None:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff5c5c", width=2
        )

    def _on_drag(self, event):
        if self._rect is not None and self._start is not None:
            self.canvas.coords(self._rect, self._start[0], self._start[1], event.x, event.y)

    def _on_release(self, event):
        if self._start is None:
            return
        left, right = sorted((self._start[0], event.x))
        top, bottom = sorted((self._start[1], event.y))
        self._start = None
        if self._rect is not None:
            self.canvas.delete(self._rect)
            self._rect = None

        if right - left < MIN_REGION_PIXELS or bottom - top < MIN_REGION_PIXELS:
            return  # accidental click, ask for the same slot again

        slot, _ = self.slots[self.index]
        self.regions[slot] = {
            "left": int(left + self.origin[0]),
            "top": int(top + self.origin[1]),
            "width": int(right - left),
            "height": int(bottom - top),
        }
        self.index += 1
        self._draw_saved()
        if self.index >= len(self.slots):
            self.window.destroy()
        else:
            self._draw_banner()

    def _undo(self, _event=None):
        if self.index == 0:
            return
        self.index -= 1
        self.regions.pop(self.slots[self.index][0], None)
        self._draw_saved()
        self._draw_banner()

    def _on_cancel(self, _event=None):
        self.cancelled = True
        self.window.destroy()

    def run(self):
        self.window.wait_window()
        return None if self.cancelled else self.regions


def _screenshot_to_temp(parent, monitor):
    """Capture a monitor and write it to a temp PNG that Tk can display.

    The capture goes through _capture_clean, so the picture is taken only once
    this app's own windows are off the desktop and the desktop has finished
    repainting where they were.
    """
    image = _capture_clean(parent, monitor)
    path = os.path.join(tempfile.gettempdir(), "poker_tracker_calibration.png")
    cv2.imwrite(path, image)
    return path, image


def select_regions(parent, slots, monitor=1, title="Calibrate"):
    """Show the picker for the given (slot, label) pairs. Returns dict or None."""
    make_dpi_aware()
    path, _ = _screenshot_to_temp(parent, monitor)
    parent.withdraw()
    try:
        selector = _RegionSelector(parent, path, slots, _monitor_origin(monitor), title)
        return selector.run()
    finally:
        parent.deiconify()


def run_calibration(parent, config):
    """Full calibration flow. Returns the updated config, or None if cancelled."""
    monitor = int(config.get("monitor", 1))
    show_info(
        parent,
        "Calibrate",
        "The screen will be frozen as a picture.\n\n"
        "Drag a box around each card position in turn - draw the box around "
        "the whole card, not just its corner.\n\n"
        "Backspace redoes the last box, m moves the instruction bar, "
        "Esc cancels.\n\n"
        "This prompt sits in a corner so it does not cover the cards. If the "
        "cards are in that corner, click Move to next corner. The picture is "
        "taken only after this prompt has gone, so it is never in it.",
        config,
    )

    try:
        slots = [(slot, SLOT_LABELS[slot]) for slot in CARD_SLOTS]
        regions = select_regions(parent, slots, monitor, "Calibrate card positions")
    except CaptureError as exc:
        messagebox.showerror("Calibrate", "Could not capture the screen:\n%s" % exc,
                             parent=parent)
        return None

    if not regions:
        logger.info("Calibration cancelled")
        return None

    config["regions"] = regions
    from capture.screen_capture import screen_size

    size = screen_size()
    config["screen_size"] = list(size) if size else None
    save_config(config)
    logger.info("Calibrated %d regions on a %s screen", len(regions), config["screen_size"])

    if ask_yes_no(
        parent,
        "Optional validation",
        "Also calibrate the bottom-left Dealer/Player result boxes?\n\n"
        "This is optional. It lets the app compare its own reading against the "
        "result the casino shows, and warn on a mismatch.\n\n"
        "You will be asked for 10 more boxes (5 player, 5 dealer).",
        config,
    ):
        result_regions = select_regions(
            parent, RESULT_SLOTS, monitor, "Calibrate result boxes"
        )
        if result_regions:
            config["result_boxes"] = result_regions
            config["validate_with_result_boxes"] = True
            save_config(config)
            logger.info("Result-box validation calibrated")

    if ask_yes_no(
        parent,
        "Teach the card artwork",
        "Recognition works best when it has seen this casino's own cards.\n\n"
        "Deal a hand so cards are visible, then click Yes to label them once. "
        "You can also do this later from Test Recognition.",
        config,
    ):
        learn_templates(parent, config)

    return config


def _save_failed_card(image, answer):
    """Keep a card image that could not be taught, for looking at later."""
    folder = os.path.join(ROOT, "logs", "unread")
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "%s_%s.png" % (
            answer.replace("/", ""), time.strftime("%Y%m%d-%H%M%S")))
        cv2.imwrite(path, image)
        return path
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        logger.warning("Could not save the unread card image: %s", exc)
        return "(could not be saved)"


def learn_templates(parent, config):
    """Capture the calibrated regions and let the user label each visible card.

    Each labelled card adds a rank template and a suit template taken from the
    casino's own artwork, which is what makes recognition reliable.
    """
    regions = config.get("regions") or {}
    if not regions:
        messagebox.showwarning("Teach cards", "Calibrate the card positions first.",
                               parent=parent)
        return 0

    monitor = int(config.get("monitor", 1))
    try:
        # Same as calibration: the prompt that was just dismissed must be off
        # the desktop, and the desktop repainted, before the cards are grabbed.
        full = _capture_clean(parent, monitor)
    except CaptureError as exc:
        messagebox.showerror("Teach cards", "Could not capture the screen:\n%s" % exc,
                             parent=parent)
        return 0

    origin = _monitor_origin(monitor)
    learned = 0
    for slot in CARD_SLOTS:
        if slot not in regions:
            continue
        image = crop(full, regions[slot], origin)
        if image is None or image.size == 0:
            continue

        preview = os.path.join(tempfile.gettempdir(), "poker_tracker_learn.png")
        cv2.imwrite(preview, cv2.resize(image, None, fx=2, fy=2,
                                        interpolation=cv2.INTER_CUBIC))
        window = tk.Toplevel(parent)
        window.title("Teach cards - %s" % SLOT_LABELS[slot])
        window.attributes("-topmost", True)
        photo = tk.PhotoImage(file=preview)
        tk.Label(window, image=photo).pack(padx=10, pady=10)
        tk.Label(
            window,
            text="%s\n\nType the card shown (for example 8D, 10H, QS),\n"
                 "or leave empty to skip this one." % SLOT_LABELS[slot],
            justify="center",
        ).pack(padx=10)
        window.update()

        answer = simpledialog.askstring(
            "Teach cards", "%s =" % SLOT_LABELS[slot], parent=window
        )
        window.destroy()

        if not answer or not answer.strip():
            continue
        answer = answer.strip().upper()
        if not is_valid_card(answer):
            messagebox.showwarning("Teach cards", "%r is not a valid card - skipped."
                                   % answer, parent=parent)
            continue

        try:
            save_glyph_templates(image, normalize_card(answer))
            learned += 1
        except ValueError as exc:
            logger.warning("Could not learn %s: %s", answer, exc)
            messagebox.showwarning(
                "Teach cards",
                "Could not read the rank/suit corner of that card:\n%s" % exc,
                parent=parent,
            )

    if learned:
        messagebox.showinfo("Teach cards", "Learned %d card(s)." % learned, parent=parent)
    logger.info("Learned templates from %d card(s)", learned)
    return learned
