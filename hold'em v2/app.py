"""Poker Hand Tracker - simple desktop UI.

Reads the cards visible on screen, works out the player's and dealer's final
hands, and records each completed hand in PostgreSQL and Excel. It only reads
the screen; it never interacts with the game.
"""

import logging
import os
import queue
import time
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2

from calibration.calibrator import learn_templates, make_dpi_aware, run_calibration
from capture.screen_capture import (
    CaptureError, crop, grab_full_screen, monitor_origin,
)
from config.settings import (
    CARD_SLOTS, EXCEL_PATH, SLOT_LABELS, is_calibrated, load_config, setup_logging,
)
from database import db
from export import excel_export
from poker import analysis
from poker import scenarios as scenario_rules
from poker.board_features import summarise
from poker.hand_record import build_hand_record, hands_so_far
from poker import teaching
from recognition import result_panel, table_layout
from recognition.dealer_watch import describe_slot
from recognition.card_recognizer import TemplatesMissingError, template_status
from tracker import Tracker, card_status, derive_state, read_table
from ui.ante_alert import SHOW, AnteAlertBanner, AnteAlertLogic, preround_decision
from ui.decision_banner import DecisionBanner
from voice.announcer import Announcer
from voice.events import VoiceEvents
from ui.rule_windows import (
    FlopRules, PreRoundRules, TeachScenarioWindow,
)
from ui.scenario_panel import ScenarioPanel
from ui.window_geometry import (
    BASE_CONTENT_HEIGHT, BASE_CONTENT_WIDTH, MIN_HEIGHT, MIN_WIDTH,
    WindowStateStore, get_adaptive_window_geometry, layout_mode, parse_geometry,
    resize_within_screen, screen_scaling, screen_size, validate_saved_geometry,
    windows_work_area,
)

logger = logging.getLogger(__name__)

DASH = "--"

# Room left for the scrollbar and the frame's padding when a wrapping label is
# told how wide it may be.
WRAP_PADDING = 34
MIN_WRAP = 180
SCROLLBAR_ALLOWANCE = 18

# "Fit screen" asks for more height than any screen has, so the sums hand back
# the tallest window this screen allows.
FIT_TO_SCREEN = 100_000

# How many finished rounds to keep for the pre-round rules. Only a run of
# consecutive player wins is ever counted, and a rule asking for a longer run
# than this cannot be built in the rule window.
HISTORY_ROUNDS = 10


class App:
    def __init__(self, root, screen=None, work_area=None, window_state=None):
        """Build the window.

        ``screen``/``work_area`` are only passed by the tests, to lay the
        window out for a resolution other than the one this machine has; left
        out, both are measured from the display in use.
        """
        self.root = root
        self.config = load_config()
        self.events = queue.Queue()
        self.tracker = Tracker(self.config, self.events)
        self.card_vars = {}
        self.scenarios = scenario_rules.load()
        self.last_record = None       # the round that just finished
        self.layout_source = None     # where the last reading's boxes came from
        self.last_panels = {}         # what the result panels last showed
        self.debug_window = None      # the Live Recognition Debug window, when open
        self._dealer_displayed = {}   # the dealer cards last put on screen
        self.scenario_history = {}    # how each situation has gone before
        # Rounds played, most recent first. A rule about consecutive wins needs
        # more than the last round, and only the last few are ever looked at.
        self.history = []

        # What Teach / Correct Scenario captures from. The last update the
        # tracker sent, kept so the button can freeze the scenario that was on
        # screen when it was pressed rather than re-reading the table.
        #
        # `teach_session` is bumped every time tracking starts. CardMemory's
        # generation - the round id - is not reset by stopping and starting, so
        # the round id alone cannot tell a correction captured before a restart
        # from one captured after it. The pair can.
        self.latest_payload = None
        self.teach_session = 0

        # The voice. Announces what the tracker has already decided, on its own
        # thread, and is fed from this window's event loop rather than from the
        # tracker - so nothing about it can reach the recognition loop. With
        # voice_enabled false the Announcer starts no thread and creates no
        # speech engine, and VoiceEvents returns immediately.
        self.announcer = Announcer(
            enabled=bool(self.config.get("voice_enabled", True)),
            rate=self.config.get("voice_rate"),
            volume=self.config.get("voice_volume"),
            voice=self.config.get("voice_name") or None,
        )
        self.voice_events = VoiceEvents(self.announcer)

        # Where this window may sit, measured now rather than assumed. The
        # tests pass a screen in to lay the window out for another laptop.
        self.screen = tuple(screen) if screen else screen_size(root)
        if work_area is not None:
            self.work_area = tuple(work_area)
        elif screen is None:
            # The desktop minus the taskbar, so the last rows of the window do
            # not end up behind it.
            self.work_area = windows_work_area()
        else:
            # Laid out for a screen this machine does not have: this machine's
            # taskbar says nothing about that one.
            self.work_area = None
        self.requested_geometry = None    # the size and position last asked for
        self.scaling = screen_scaling(root)
        self.window_state = (window_state if window_state is not None
                             else WindowStateStore())
        self._wrapping_labels = []    # labels whose text follows the window's width
        self._scrollbar_width = SCROLLBAR_ALLOWANCE   # measured once the UI exists
        self._layout = None           # the arrangement currently on screen
        self._laid_out_for = None     # the width that arrangement was chosen for

        root.title("Poker Hand Tracker")
        # Sized and placed for this screen once the content exists, below. The
        # window is resizable now: a fixed size was what made a small laptop
        # unusable, with no way out but editing the source.
        root.resizable(True, True)
        # Stay visible above the browser while it is being used. Topmost only
        # sets where the window sits in the stacking order: clicks on other
        # windows still go to them, it never takes focus, and the window can
        # still be moved, minimised and restored as usual.
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._apply_initial_geometry()
        # The ANTE alert: a banner and a sound when betting opens, saying what
        # the pre-round rules recommend. Display only - the bet is placed by
        # hand. Off with config["ante_alert"] = False.
        self.ante_alert = None
        self.ante_banner = None
        self.decision_banner = None
        if self.config.get("ante_alert", True):
            self.ante_alert = AnteAlertLogic(
                empty_polls=int(self.config.get("ante_alert_empty_polls", 3)),
                timeout=float(self.config.get("ante_alert_seconds", 25)))
            # ONE banner for every decision. AnteAlertBanner is DecisionBanner
            # under its old name, so this is a single widget in a single
            # place; self.ante_banner is kept as a second name for it because
            # the pre-round path and its tests already use that name.
            self.decision_banner = AnteAlertBanner(
                root, sound=bool(self.config.get("ante_alert_sound", True)))
            self.ante_banner = self.decision_banner
            self.decision_banner.on_dismiss = self.ante_alert.dismiss
        self._poll_events()
        self._startup_checks()
        self._refresh_statistics()
        self._refresh_scenario_history()

    # -- layout ------------------------------------------------------------

    def _build_ui(self):
        # Everything sits on a scrolling canvas. On a screen with no room for
        # the whole column - a 768-pixel-high laptop, say - the rest scrolls
        # into view instead of being cut off at the bottom.
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0, takefocus=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vscroll = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.hscroll = ttk.Scrollbar(outer, orient="horizontal",
                                     command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self._set_vscroll,
                              xscrollcommand=self._set_hscroll)
        self._vscroll_shown = False
        self._hscroll_shown = False

        frame = ttk.Frame(self.canvas, padding=12)
        self.content = frame
        self._content_id = self.canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", self._on_content_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel)
        self.root.bind_all("<Button-5>", self._on_mousewheel)

        ttk.Label(frame, text="Poker Hand Tracker",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")

        self.status_var = tk.StringVar(value="Status: STOPPED")
        ttk.Label(frame, textvariable=self.status_var,
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4, 8))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.buttons_frame = buttons
        self.start_button = ttk.Button(buttons, text="Start Tracker", command=self.start)
        self.stop_button = ttk.Button(buttons, text="Stop Tracker", command=self.stop,
                                      state="disabled")
        # In order. Two to a row while there is room for two, one to a row when
        # there is not, so a narrow window loses nothing off the right-hand side.
        self.buttons = [
            self.start_button,
            self.stop_button,
            ttk.Button(buttons, text="Calibrate", command=self.calibrate),
            ttk.Button(buttons, text="Test Recognition", command=self.test_recognition),
            ttk.Button(buttons, text="Export Excel", command=self.export_excel),
            ttk.Button(buttons, text="Live Recognition Debug",
                       command=self.open_live_debug),
            ttk.Button(buttons, text="Scenarios: before the round",
                       command=self.edit_preround_rules),
            ttk.Button(buttons, text="Scenarios: after the flop",
                       command=self.edit_flop_rules),
            ttk.Button(buttons, text="Teach / Correct Scenario",
                       command=self.teach_scenario),
        ]
        self.button_columns = None
        self._grid_buttons(2)

        # Resizing by hand, for when the automatic size is not the one wanted.
        # The window can also be dragged by its edges; these are here so the
        # size can be changed without hunting for a two-pixel border.
        size_row = ttk.Frame(frame)
        size_row.pack(fill="x", pady=(6, 0))
        ttk.Label(size_row, text="Window:").pack(side="left")
        ttk.Button(size_row, text="−", width=3,
                   command=self.shrink_window).pack(side="left", padx=(4, 0))
        ttk.Button(size_row, text="+", width=3,
                   command=self.grow_window).pack(side="left", padx=(2, 0))
        ttk.Button(size_row, text="Fit screen",
                   command=self.fit_window_to_screen).pack(side="left", padx=(6, 0))
        ttk.Button(size_row, text="Reset",
                   command=self.reset_window_size).pack(side="left", padx=(2, 0))
        # Voice controls. A row of its own, like the window-size row, so the
        # button grid the layout tests measure is left as it was.
        voice_row = ttk.Frame(frame)
        voice_row.pack(fill="x", pady=(6, 0))
        ttk.Label(voice_row, text="Voice:").pack(side="left")
        self.voice_var = tk.StringVar(value=self.announcer.state())
        ttk.Label(voice_row, textvariable=self.voice_var,
                  width=10).pack(side="left", padx=(4, 0))
        self.voice_buttons = {
            "pause": ttk.Button(voice_row, text="Pause", width=7,
                                command=self.pause_voice),
            "resume": ttk.Button(voice_row, text="Resume", width=8,
                                 command=self.resume_voice),
            "stop": ttk.Button(voice_row, text="Stop", width=6,
                               command=self.stop_voice),
            "mute": ttk.Button(voice_row, text="Mute", width=7,
                               command=self.toggle_mute),
        }
        for button in self.voice_buttons.values():
            button.pack(side="left", padx=(4, 0))
        self._refresh_voice_state()

        self.root.bind("<Control-minus>", lambda _event: self.shrink_window())
        self.root.bind("<Control-plus>", lambda _event: self.grow_window())
        self.root.bind("<Control-equal>", lambda _event: self.grow_window())
        self.root.bind("<Control-Key-0>", lambda _event: self.reset_window_size())

        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="Current Hand:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")

        # The cards take the left third of this row and the Scenario Engine's
        # decision uses the space beside them, as long as the window is wide
        # enough for both. When it is not, the Scenario panel moves underneath
        # the cards rather than being pushed off the right-hand edge.
        hand_row = ttk.Frame(frame)
        hand_row.pack(fill="x", pady=(4, 0))
        self.hand_row = hand_row
        table = ttk.Frame(hand_row)
        self.card_table = table
        self.scenario_panel = ScenarioPanel(hand_row)
        self.scenario_stacked = None
        self._stack_scenario(False)
        rows = [
            ("Player:", ["player_1", "player_2"]),
            ("Flop:", ["flop_1", "flop_2", "flop_3"]),
            ("Turn:", ["turn"]),
            ("River:", ["river"]),
            ("Dealer:", ["dealer_1", "dealer_2"]),
        ]
        for index, (label, slots) in enumerate(rows):
            ttk.Label(table, text=label, width=8).grid(row=index, column=0, sticky="w")
            for column, slot in enumerate(slots):
                var = tk.StringVar(value=DASH)
                self.card_vars[slot] = var
                ttk.Label(table, textvariable=var, width=5,
                          font=("Consolas", 11, "bold")).grid(
                    row=index, column=column + 1, sticky="w")

        ttk.Separator(frame).pack(fill="x", pady=10)
        results = ttk.Frame(frame)
        results.pack(fill="x")
        self.player_hand_var = tk.StringVar(value=DASH)
        self.dealer_hand_var = tk.StringVar(value=DASH)
        ttk.Label(results, text="Player Hand:", width=13).grid(row=0, column=0, sticky="w")
        ttk.Label(results, textvariable=self.player_hand_var,
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(results, text="Dealer Hand:", width=13).grid(row=1, column=0, sticky="w")
        ttk.Label(results, textvariable=self.dealer_hand_var,
                  font=("Segoe UI", 10, "bold")).grid(row=1, column=1, sticky="w")
        self.winner_var = tk.StringVar(value=DASH)
        ttk.Label(results, text="Winner:", width=13).grid(row=2, column=0, sticky="w")
        ttk.Label(results, textvariable=self.winner_var,
                  font=("Segoe UI", 10, "bold")).grid(row=2, column=1, sticky="w")

        # The casino's own result panel, shown beside what the table says. The
        # panel is the source for each seat's final hand; the table is the
        # source for which card sits where. A disagreement is shown, not hidden.
        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="Result Panel:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        panel = ttk.Frame(frame)
        panel.pack(fill="x", pady=(4, 0))
        self.panel_vars = {}
        for index, side in enumerate(("player", "dealer")):
            ttk.Label(panel, text="%s best 5:" % side.title(), width=13).grid(
                row=index, column=0, sticky="w")
            var = tk.StringVar(value=DASH)
            self.panel_vars[side] = var
            ttk.Label(panel, textvariable=var, font=("Consolas", 10, "bold")).grid(
                row=index, column=1, sticky="w")
        self.verification_var = tk.StringVar(value=DASH)
        self.verification_label = self._wrapping(ttk.Label(
            frame, textvariable=self.verification_var, justify="left"))
        self.verification_label.pack(anchor="w", fill="x", pady=(2, 0))

        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="Your scenarios say:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.scenario_var = tk.StringVar(value=DASH)
        self._wrapping(ttk.Label(
            frame, textvariable=self.scenario_var, justify="left",
            foreground="#0b6", font=("Segoe UI", 10, "bold"))
        ).pack(anchor="w", fill="x", pady=(2, 0))

        self.detected_var = tk.StringVar(value=DASH)
        self._wrapping(ttk.Label(
            frame, textvariable=self.detected_var, justify="left",
            font=("Segoe UI", 9))).pack(anchor="w", fill="x", pady=(4, 0))

        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="Last Saved Hand:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.last_saved_var = tk.StringVar(value=DASH)
        self._wrapping(ttk.Label(
            frame, textvariable=self.last_saved_var,
            justify="left")).pack(anchor="w", fill="x", pady=(2, 0))

        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="Results so far:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.stats_var = tk.StringVar(value="No completed rounds yet.")
        self._wrapping(ttk.Label(
            frame, textvariable=self.stats_var, justify="left",
            font=("Consolas", 9))).pack(anchor="w", fill="x", pady=(2, 0))

        self.message_var = tk.StringVar(value="")
        self._wrapping(ttk.Label(
            frame, textvariable=self.message_var, justify="left",
            foreground="#b00020")).pack(anchor="w", fill="x", side="bottom",
                                        pady=(10, 0))

    def _wrapping(self, label):
        """Register a label whose text should re-wrap with the window."""
        self._wrapping_labels.append(label)
        return label

    def _grid_buttons(self, columns):
        """Lay the buttons out two to a row, or one when the window is narrow."""
        if columns == self.button_columns:
            return
        self.button_columns = columns
        for index, button in enumerate(self.buttons):
            button.grid(row=index // columns, column=index % columns,
                        sticky="ew", padx=2, pady=2)
        for column in (0, 1):
            self.buttons_frame.columnconfigure(
                column, weight=1 if column < columns else 0)

    def _stack_scenario(self, stacked):
        """Put the Scenario panel under the cards, or back beside them."""
        if stacked == self.scenario_stacked:
            return
        self.scenario_stacked = stacked
        self.card_table.pack_forget()
        self.scenario_panel.pack_forget()
        if stacked:
            self.card_table.pack(side="top", anchor="w")
            self.scenario_panel.pack(side="top", anchor="w", fill="x", pady=(6, 0))
        else:
            self.card_table.pack(side="left", anchor="n")
            self.scenario_panel.pack(side="left", anchor="n", padx=(8, 0))

    # -- size and position -------------------------------------------------

    def _measure_content(self):
        """What the finished content asks for, in this display's pixels.

        Measured rather than written down, so display scaling and a different
        font size move the thresholds with them.
        """
        padding = 24                      # the content frame's own padding
        scale = self.scaling
        try:
            self.content.update_idletasks()
            # Measured, not assumed: a scrollbar is half as wide again on a
            # display scaled to 150%, and guessing it too narrow is enough to
            # push the Scenario panel out from beside the cards.
            self._scrollbar_width = max(SCROLLBAR_ALLOWANCE,
                                        int(self.vscroll.winfo_reqwidth()))
            wanted_width = int(self.content.winfo_reqwidth())
            wanted_height = int(self.content.winfo_reqheight())
            side_by_side = (int(self.card_table.winfo_reqwidth())
                            + int(self.scenario_panel.winfo_reqwidth()) + 8 + padding)
            two_column = int(self.buttons_frame.winfo_reqwidth()) + padding
        except tk.TclError:               # pragma: no cover - a dead window
            wanted_width = wanted_height = side_by_side = two_column = 0
            self._scrollbar_width = SCROLLBAR_ALLOWANCE

        # A window that has never been drawn reports 1 pixel; fall back to the
        # size the tracker has always used rather than to nonsense.
        self._content_width = (wanted_width + self._scrollbar_width
                               if wanted_width > 100
                               else int(BASE_CONTENT_WIDTH * scale))
        self._content_height = (wanted_height if wanted_height > 100
                                else int(BASE_CONTENT_HEIGHT * scale))
        self._side_by_side_width = (side_by_side if side_by_side > 100
                                    else int(BASE_CONTENT_WIDTH * scale))
        self._two_column_width = (two_column if two_column > 100
                                  else int(BASE_CONTENT_WIDTH * scale))

    def adaptive_geometry(self, content_width=None, content_height=None):
        """A safe width, height, x and y for this machine's screen."""
        screen_width, screen_height = self.screen
        return get_adaptive_window_geometry(
            screen_width, screen_height,
            content_width=(self._content_width if content_width is None
                           else content_width),
            content_height=(self._content_height if content_height is None
                            else content_height),
            work_area=self.work_area)

    def _apply_initial_geometry(self):
        """Size and place the window for the screen that is attached now.

        Last time's size and position are used when they still make sense on
        this screen; otherwise, and on a machine that has never run it, the
        window is sized from the screen and docked to the right, where the
        whole of it - the Scenario section included - is visible.
        """
        self._measure_content()
        screen_width, screen_height = self.screen
        saved = None
        try:
            saved = self.window_state.load(screen_width, screen_height)
        except Exception as exc:          # noqa: BLE001 - a bad file is not fatal
            logger.info("Could not read the remembered window position: %s", exc)
        geometry = validate_saved_geometry(saved, screen_width, screen_height,
                                           self.work_area)
        if geometry is None:
            geometry = self.adaptive_geometry()
        else:
            logger.info("Restored the window at %s", geometry.as_string())
        try:
            self.root.minsize(min(MIN_WIDTH, geometry.width),
                              min(MIN_HEIGHT, geometry.height))
        except tk.TclError:               # pragma: no cover
            pass
        self._apply_geometry(geometry)

    def _apply_geometry(self, geometry):
        """Move and size the window, then re-arrange the content to match."""
        if geometry is None:
            return
        self.requested_geometry = geometry
        try:
            self.root.geometry(geometry.as_string())
            self.root.update_idletasks()
        except tk.TclError:               # pragma: no cover - a dead window
            return
        self._apply_layout(force=True)

    def current_geometry(self):
        """Where the window is now, as a Geometry, or None."""
        try:
            return parse_geometry(self.root.winfo_geometry())
        except tk.TclError:               # pragma: no cover
            return None

    def _effective_geometry(self):
        """The size and position to work from.

        The real one while the window is on screen - the user may have dragged
        it since - and otherwise the one last asked for, because Tk reports an
        unmapped window at the size its content wants rather than the size it
        has been given.
        """
        try:
            if self.root.winfo_viewable():
                current = self.current_geometry()
                if current is not None:
                    return current
        except tk.TclError:               # pragma: no cover
            pass
        return self.requested_geometry or self.current_geometry()

    def _viewport_width(self):
        """How many pixels wide the scrolling area is, or is about to be.

        A window that is not on screen yet reports the size its content asked
        for rather than the size the window manager will give it, so until it
        is visible the width asked for is the one to arrange the content by.
        """
        try:
            if self.root.winfo_viewable():
                width = int(self.canvas.winfo_width())
                if width > 1:
                    return width
        except tk.TclError:               # pragma: no cover
            pass
        geometry = self._effective_geometry()
        if geometry is not None and geometry.width > 1:
            return max(1, geometry.width
                       - (self._scrollbar_width if self._vscroll_shown else 0))
        return self.screen[0]

    def _apply_layout(self, width=None, force=False):
        """Arrange the content for the width available to it.

        Narrow enough and the Scenario panel moves below the cards and the
        buttons go one to a row, so the right-hand side is reflowed rather
        than cut off. The wrapping text follows the width either way.
        """
        if width is None:
            width = self._viewport_width()
        if width <= 1:
            return
        if not force and width == self._laid_out_for:
            return
        self._laid_out_for = width
        mode = layout_mode(width, self._side_by_side_width, self._two_column_width)
        if mode != self._layout:
            self._layout = mode
            self._stack_scenario(mode["stack_scenario"])
            self._grid_buttons(1 if mode["single_column_buttons"] else 2)
        wrap = max(MIN_WRAP, width - WRAP_PADDING)
        for label in self._wrapping_labels:
            label.configure(wraplength=wrap)
        # Beside the cards the panel keeps its designed width; underneath them
        # it has the whole window and the dealer line stops being cramped.
        self.scenario_panel.set_wraplength(wrap if mode["stack_scenario"] else None)

    def shrink_window(self):
        """The "-" button: a little smaller, still on screen."""
        self._resize_by(1.0 / 1.1)

    def grow_window(self):
        """The "+" button: a little larger, never larger than the screen."""
        self._resize_by(1.1)

    def _resize_by(self, factor):
        geometry = self._effective_geometry()
        if geometry is None:
            return
        self._apply_geometry(resize_within_screen(
            geometry, self.screen[0], self.screen[1], factor, self.work_area))

    def fit_window_to_screen(self):
        """As tall as this screen allows, for reading the whole column at once."""
        self._apply_geometry(self.adaptive_geometry(content_height=FIT_TO_SCREEN))

    def reset_window_size(self):
        """Back to the size and position worked out for this screen."""
        self._apply_geometry(self.adaptive_geometry())

    def _save_window_state(self):
        """Remember this window for next time, for this machine and screen."""
        try:
            if not self.root.winfo_exists() or not self.root.winfo_viewable():
                return False
            if self.root.state() not in ("normal", "zoomed"):
                return False
            geometry = self.current_geometry()
        except tk.TclError:               # pragma: no cover
            return False
        if geometry is None:
            return False
        try:
            return self.window_state.save(geometry, self.screen[0], self.screen[1])
        except Exception as exc:          # noqa: BLE001 - never block shutdown
            logger.info("Could not remember the window position: %s", exc)
            return False

    # -- scrolling ---------------------------------------------------------

    def _on_content_configure(self, _event=None):
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:               # pragma: no cover
            pass

    def _on_canvas_configure(self, _event=None):
        width = self._viewport_width()
        self._apply_layout(width)
        # At least as wide as the canvas, so the text fills the window, and
        # never narrower than the content needs, so nothing is cut off the
        # right-hand edge: what is left over scrolls.
        self.canvas.itemconfigure(
            self._content_id, width=max(width, int(self.content.winfo_reqwidth())))

    def _scrollbar(self, bar, shown_attribute, first, last, **grid):
        bar.set(first, last)
        try:
            needed = float(first) > 0.0 or float(last) < 1.0
        except (TypeError, ValueError):   # pragma: no cover
            needed = True
        if needed == getattr(self, shown_attribute):
            return needed
        setattr(self, shown_attribute, needed)
        if needed:
            bar.grid(**grid)
        else:
            bar.grid_remove()
        return needed

    def _set_vscroll(self, first, last):
        """Show the vertical scrollbar exactly when the content overflows."""
        self._scrollbar(self.vscroll, "_vscroll_shown", first, last,
                        row=0, column=1, sticky="ns")

    def _set_hscroll(self, first, last):
        self._scrollbar(self.hscroll, "_hscroll_shown", first, last,
                        row=1, column=0, sticky="ew")

    def _on_mousewheel(self, event):
        """Scroll the tracker, but only while the pointer is over it."""
        if not self._vscroll_shown:
            return
        widget = getattr(event, "widget", None)
        while widget is not None:
            if widget is self.canvas:
                break
            widget = getattr(widget, "master", None)
        else:
            return
        number = getattr(event, "num", None)
        if number == 4:
            steps = -1
        elif number == 5:
            steps = 1
        else:
            delta = getattr(event, "delta", 0)
            steps = -(delta // 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        try:
            self.canvas.yview_scroll(int(steps), "units")
        except tk.TclError:               # pragma: no cover
            pass

    # -- helpers -----------------------------------------------------------

    def show_detected(self, found):
        """Name the situations on the table, and how they have gone before.

        Naming them is the point: a recommendation with nothing behind it is
        hard to trust or to argue with. The percentages are history - what the
        recorded rounds did - and are labelled as such, with a warning when
        there are too few of them to mean anything.
        """
        if not found:
            self.detected_var.set(
                "Insufficient card data" if self.tracker.is_running() else DASH)
            return

        lines = []
        for entry in found[:4]:
            history = self.scenario_history.get(entry["key"])
            if history and history["triggered"]["rounds"]:
                note = "  (%d hands, player %.0f%%, %+.0f vs the rest%s)" % (
                    history["triggered"]["rounds"],
                    history["triggered"]["player_percent"],
                    history["advantage"],
                    ", small sample" if history["small_sample"] else "")
            else:
                note = "  (not seen before)"
            lines.append("- %s%s" % (entry["label"], note))
        self.detected_var.set("\n".join(lines))

    def _refresh_scenario_history(self):
        """Recount how each situation has turned out, off the UI thread."""
        def work():
            try:
                report = analysis.statistics(db.fetch_all_hands())
                self.events.put(("scenario_history", report["scenarios"]))
            except db.DatabaseError as exc:
                logger.info("Could not read the recorded hands: %s", exc)
        threading.Thread(target=work, daemon=True).start()

    def show_statistics(self, stats):
        """Show how the recorded rounds turned out. Counts only; no forecast."""
        if not stats or not stats["rounds"]:
            self.stats_var.set("No completed rounds yet.")
            return
        self.stats_var.set(
            "Completed rounds : %d\n"
            "Player wins      : %-4d (%.1f%%)\n"
            "Dealer wins      : %-4d (%.1f%%)\n"
            "Ties             : %-4d (%.1f%%)"
            % (stats["rounds"],
               stats["player"], stats["player_percent"],
               stats["dealer"], stats["dealer_percent"],
               stats["tie"], stats["tie_percent"])
        )

    def _refresh_statistics(self):
        """Recount from the stored hands, off the UI thread."""
        def work():
            try:
                self.events.put(("statistics", db.win_statistics()))
            except db.DatabaseError as exc:
                logger.info("Could not count results: %s", exc)
        threading.Thread(target=work, daemon=True).start()

    def set_status(self, text):
        self.status_var.set("Status: %s" % text)

    def set_message(self, text):
        self.message_var.set(text)

    def _startup_checks(self):
        """Run the checks off the UI thread - reaching PostgreSQL can take seconds."""
        self.set_message("Checking configuration...")
        threading.Thread(target=self._run_startup_checks, daemon=True).start()

    def _run_startup_checks(self):
        notes = []
        if not is_calibrated(self.config):
            notes.append(
                "Not calibrated - the cards will be found on screen instead. "
                "Calibrate only if they are not being picked up.")

        try:
            missing_ranks, missing_suits = template_status()
            if missing_ranks or missing_suits:
                notes.append(
                    "Missing templates (%s) - run tools/generate_templates.py."
                    % ", ".join(missing_ranks + missing_suits)
                )
        except Exception as exc:  # noqa: BLE001
            notes.append("Template problem: %s" % exc)

        ok, message = db.check_connection()
        if ok:
            # Asked, not built. Creating the schema here would mean a
            # developer pointed at the wrong database got one made for them
            # and never found out; the server's schema is set up once, by
            # tools/setup_database.py. See docs/DATABASE.md.
            schema_ok, schema_message = db.verify_schema()
            if not schema_ok:
                notes.append(schema_message)
            # Rounds already recorded are what a rule about consecutive wins
            # asks about, so a restart mid-session should not forget them.
            try:
                stored = db.fetch_all_hands()[-HISTORY_ROUNDS:]
                self.events.put(("history", list(reversed(stored))))
            except db.DatabaseError as exc:
                logger.info("Could not load the recent rounds: %s", exc)
        else:
            notes.append(message)

        try:
            excel_export.ensure_workbook()
        except excel_export.ExcelError as exc:
            notes.append(str(exc))

        # Report through the queue: Tk widgets are only touched on the main thread.
        self.events.put(("startup", "\n".join(notes)))

    # -- buttons -----------------------------------------------------------

    def start(self):
        # Calibration is no longer a prerequisite: the cards are found in the
        # frame, and calibrated boxes are only the fallback for the frames
        # where they cannot be. A table that is never found simply reads as
        # empty, which is the right answer rather than a reason to refuse.
        ok, message = db.check_connection()
        if not ok and not messagebox.askyesno(
            "PostgreSQL unavailable",
            "%s\n\nStart anyway? Completed hands will be retried, "
            "but nothing can be stored until the database is reachable."
            % message,
            parent=self.root,
        ):
            return

        self.set_message("")
        self.scenario_panel.reset()
        # A new tracking session: nothing captured before it can be taught, and
        # the scenario that was on screen when it stopped is not one now.
        self.teach_session += 1
        self.latest_payload = None
        if self.ante_alert is not None:
            self._apply_ante_event(self.ante_alert.reset())
        if self.decision_banner is not None:
            # Hidden, and with the last session's decision forgotten, until
            # this session's scenario engine produces one.
            self.decision_banner.start()
        self.tracker = Tracker(self.config, self.events)
        self.tracker.start()
        self.set_status("RUNNING")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")

    def stop(self):
        self.tracker.stop()
        self.set_status("STOPPED")
        # A stopped tracker's last decision is not a decision about the table now.
        self.scenario_panel.reset()
        # Nor is it something to teach a rule from. Dropped here as well as
        # refused in the dialog, so a window left open cannot activate against
        # a hand that finished before the tracker was stopped.
        self.latest_payload = None
        if self.ante_alert is not None:
            self._apply_ante_event(self.ante_alert.reset())
        if self.decision_banner is not None:
            # Not just cleared: closed. Updates queued before the tracker
            # stopped are still waiting to be drained, and the banner must
            # refuse them rather than reappear over whatever is on screen now.
            self.decision_banner.stop()
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

    def calibrate(self):
        was_running = self.tracker.is_running()
        if was_running:
            self.stop()
        updated = run_calibration(self.root, self.config)
        if updated:
            self.config = updated
            self.set_message("Calibration saved.")
        if was_running:
            self.start()

    def edit_preround_rules(self):
        PreRoundRules(self.root, on_change=self.reload_scenarios)

    def edit_flop_rules(self):
        FlopRules(self.root, on_change=self.reload_scenarios)

    def teach_scenario(self):
        """Correct the decision on screen, by writing one of the existing rules.

        Freezes the last update the tracker sent - the one the banner was drawn
        from - and hands it to the rule builder. Nothing is decided here and
        nothing is saved here; the window builds an ordinary rule and only
        writes it when someone presses Activate.
        """
        if not self.tracker.is_running():
            messagebox.showinfo(
                "Teach / Correct Scenario",
                "The tracker is not running, so there is no current scenario "
                "to correct.\n\nStart the tracker, wait for a decision, then "
                "press this again.\n\nRules can still be written by hand in "
                "the two Scenarios windows.",
                parent=self.root)
            return

        snapshot, why = teaching.capture(
            self.latest_payload, self.scenarios, self.teach_session,
            self.last_record, self.history)
        if snapshot is None:
            messagebox.showinfo("Teach / Correct Scenario", why, parent=self.root)
            return

        TeachScenarioWindow(
            self.root, snapshot, rules=self.scenarios,
            # Asked again at Save & Test and at Activate. The round id is the
            # tracker's own (CardMemory's generation), never one invented here.
            is_current=lambda: snapshot.is_current(
                self.teach_session,
                (self.latest_payload or {}).get("round_id"),
                self.tracker.is_running()),
            on_change=self.reload_scenarios)

    def reload_scenarios(self):
        self.scenarios = scenario_rules.load()

    def export_excel(self):
        try:
            rows = db.fetch_all_hands()
        except db.DatabaseError as exc:
            messagebox.showerror("Export Excel", str(exc), parent=self.root)
            return
        try:
            count = excel_export.rebuild(rows)
        except excel_export.ExcelError as exc:
            messagebox.showerror("Export Excel", str(exc), parent=self.root)
            return
        messagebox.showinfo(
            "Export Excel",
            "Wrote %d hand(s) to:\n%s" % (count, EXCEL_PATH),
            parent=self.root,
        )

    # -- test recognition --------------------------------------------------

    def open_live_debug(self):
        if self.debug_window is not None and self.debug_window.winfo_exists():
            self.debug_window.lift()
            return
        self.debug_window = LiveDebugWindow(self.root, self)

    def test_recognition(self):
        # Uncalibrated is fine here too - and this is the quickest way to see
        # whether the table is being found on a machine that has never been
        # calibrated at all.
        TestWindow(self.root, self)

    def read_once(self, image=None, origin=(0, 0)):
        """Read every region once, from the screen or from a supplied image.

        The cards are found in the picture the same way the tracker finds
        them, so what this reports is what tracking would see - falling back
        to the calibrated boxes only when the table cannot be made out.
        """
        monitor = int(self.config.get("monitor", 1) or 1)
        if image is None:
            image = grab_full_screen(monitor)
            origin = monitor_origin(monitor)

        layout = table_layout.locate(image, origin=origin)
        if table_layout.looks_plausible(layout):
            self.layout_source = "dynamic"
        else:
            layout = None
            self.layout_source = ("calibrated" if self.config.get("regions")
                                  else "none")
        regions = table_layout.merge(layout, self.config.get("regions") or {})

        images = {slot: crop(image, regions[slot], origin)
                  for slot in CARD_SLOTS if slot in regions}
        # The panels are read here too, so Test Recognition can show what the
        # slower, longer-lived source makes of the same moment.
        self.last_panels = result_panel.read_panels(image, origin)
        return read_table(self.config, images)

    # -- event pump --------------------------------------------------------

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_events)

    def _handle_event(self, kind, payload):
        if kind == "update":
            self._show_reading(payload)
            self._update_ante_alert(payload)
            window = self.debug_window
            if window is not None and window.winfo_exists():
                window.show(payload)
        elif kind == "saved":
            self._show_saved(payload)
        elif kind == "duplicate":
            self.set_message("Hand already recorded - not saved again.")
        elif kind == "startup":
            self.set_message(payload)
        elif kind == "statistics":
            self.show_statistics(payload)
        elif kind == "scenario_history":
            self.scenario_history = {entry["key"]: entry for entry in payload}
        elif kind == "history":
            # Only fill in what this session has not already seen: a round
            # finishing while the database was being read must not be lost.
            if not self.history:
                self.history = list(payload)
                self.last_record = self.history[0] if self.history else None
        elif kind == "warning":
            self.set_message(payload)
        elif kind == "error":
            self.set_message(payload)
            if self.tracker.is_running() is False:
                self.set_status("STOPPED")
                self.start_button.config(state="normal")
                self.stop_button.config(state="disabled")

    def _note_dealer_display(self, payload):
        """Log each dealer card as it reaches the screen, and how long it waited.

        The tracker stamps each update as it sends it; the window picks updates
        up on its own timer. If the tracker had a dealer card long before it
        appeared, this line says so - and the fault is not recognition.
        """
        emitted = payload.get("emitted_at")
        for slot in ("dealer_1", "dealer_2"):
            card = (payload.get("cards") or {}).get(slot)
            if card and self._dealer_displayed.get(slot) != card and emitted:
                logger.info("DEALER_GUI %s=%s displayed %.0fms after the tracker sent it",
                            slot, card, (time.time() - emitted) * 1000.0)
            self._dealer_displayed[slot] = card

    def _show_reading(self, payload):
        cards = payload["cards"]
        held = payload.get("held") or set()
        statuses = payload.get("statuses") or {}
        self._note_dealer_display(payload)
        for slot in CARD_SLOTS:
            self.card_vars[slot].set(
                card_cell(cards.get(slot), statuses.get(slot), slot in held))

        status = "RUNNING - %s" % payload["state"]
        if held:
            status += " (%d covered)" % len(held)
        self.set_status(status)

        if payload["uncertain"]:
            self.set_message(
                "Recognition uncertain: %s"
                % ", ".join(SLOT_LABELS[slot] for slot in payload["uncertain"])
            )
        elif self.message_var.get().startswith("Recognition uncertain"):
            self.set_message("")

        # Evaluate whatever is on the table: the player's hand can be worked
        # out as soon as the flop lands, and improves on the turn and river.
        # The dealer's only appears at the showdown, and the winner with it.
        progress = hands_so_far(cards)
        panels = payload.get("panels") or {}
        final = panels.get("outcome") or {}
        # Each seat's final hand comes from the result panel once it has a
        # confirmed five; until then the table's own evaluation is shown. The
        # label says which, so the two are never confused.
        self.player_hand_var.set(
            _sourced(final.get("player_hand"), progress["player_hand"]))
        self.dealer_hand_var.set(
            _sourced(final.get("dealer_hand"), progress["dealer_hand"]))

        source = final if final.get("winner") else progress
        if source["winner"]:
            verdict = "%s wins" % source["winner"]
            if source["winner"] == "Tie":
                verdict = "Tie"
            if source["qualified"] is False:
                verdict += " (dealer does not qualify)"
            verdict += "  (panel)" if source is final else "  (table)"
            self.winner_var.set(verdict)
        else:
            self.winner_var.set(DASH)
        self._show_panels(panels)

        # Worked out by the tracker's Scenario Engine; shown, never recalculated.
        self.scenario_panel.show(payload.get("scenario"))
        self.scenario_panel.show_action(payload.get("action"))
        self._show_scenario(cards, statuses)

        # The voice gets the same payload the window just drew, plus the
        # progress already computed above - it evaluates nothing of its own.
        # This only queues; the speaking happens on the voice thread.
        self.voice_events.observe(payload, progress)
        self._refresh_voice_state()

    def _show_panels(self, panels):
        """The result panel's two rows and how they fit the table."""
        if not hasattr(self, "panel_vars"):
            return
        sides = panels.get("sides") or {}
        verdicts = panels.get("verdicts") or {}
        for side, var in self.panel_vars.items():
            data = sides.get(side) or {}
            if not data or data.get("state") == result_panel.NOT_VISIBLE:
                var.set("not visible")
            elif data.get("stale"):
                var.set("previous round - ignored")
            elif data.get("confirmed"):
                var.set("%s%s" % (" ".join(data["confirmed"]),
                                  "" if data.get("complete") else "  (partial)"))
            else:
                var.set("%s  (reading)" % " ".join(
                    card or "?" for card in data.get("cards") or []))

        conflicts = [message for verdict, message in verdicts.values()
                     if verdict == "conflict"]
        agreeing = [side for side, (verdict, _) in verdicts.items()
                    if verdict == "consistent"]
        waiting = [side for side, (verdict, _) in verdicts.items()
                   if verdict in ("pending", "mismatch")]
        if conflicts:
            text, colour = "SOURCE CONFLICT: %s" % "; ".join(conflicts), "#b00020"
        elif agreeing or waiting:
            # Both halves are shown: a seat still waiting must not disappear
            # behind the other seat agreeing.
            parts = []
            if agreeing:
                parts.append("CONSISTENT (%s)" % ", ".join(agreeing))
            if waiting:
                parts.append("waiting (%s)" % ", ".join(waiting))
            text = "Table vs panel: %s" % " | ".join(parts)
            colour = "#0b6" if agreeing and not waiting else ""
        else:
            text, colour = DASH, ""
        self.verification_var.set(text)
        self.verification_label.configure(foreground=colour)

    def _show_scenario(self, cards, statuses=None):
        """What the user's own rules make of the table right now.

        This is a recommendation only - the app never touches the game.

        `statuses` is each slot's recognition status, so no rule is ever
        applied to a card the tracker has not settled. A card still arriving
        on the flop reads as something else for a poll or two, and a rule
        fired on it is a rule fired on the wrong card. The Scenario Engine
        panel beside this one waits for the same five cards; until now this
        one did not.
        """
        decision = scenario_rules.decide_from_cards(
            self.scenarios, cards, self.last_record, self.history,
            statuses=statuses or {},
        )
        features = decision["features"]

        if features:
            action = scenario_rules.ACTION_LABELS[decision["flop_action"]]
            reason = decision["flop_rule"]["name"] if decision["flop_rule"] else "default"
            self.scenario_var.set(
                "%s  ->  %s\n(%s)" % (summarise(features), action.upper(), reason)
            )
            self.show_detected(analysis.detect(features))
            return
        self.show_detected([])

        # The flop is showing but a card is not settled: say which one, rather
        # than a recommendation worked out from a card about to change.
        waiting = decision["waiting_for"]
        if waiting and all(cards.get(slot) for slot in scenario_rules.FLOP_SLOTS):
            self.scenario_var.set(
                "Reading the table\n(waiting for %s)"
                % ", ".join(SLOT_LABELS[slot] for slot in waiting))
            return

        if self.last_record:
            action = scenario_rules.ACTION_LABELS[decision["preround_action"]]
            rule = decision["preround_rule"]
            reason = rule["name"] if rule else "default"
            self.scenario_var.set(
                "Next round: %s\n(%s)" % (action.upper(), reason)
            )
        else:
            self.scenario_var.set("Waiting for the flop")

    # -- voice controls ----------------------------------------------------
    #
    # Every one of these affects the voice only. The tracker, the card
    # recognition, the scenario engine and the database carry on regardless -
    # none of them can even see the Announcer.

    def pause_voice(self):
        self.announcer.pause()
        self._refresh_voice_state()

    def resume_voice(self):
        self.announcer.resume()
        self._refresh_voice_state()

    def stop_voice(self):
        self.announcer.stop()
        self._refresh_voice_state()

    def toggle_mute(self):
        if self.announcer.is_muted():
            self.announcer.unmute()
        else:
            self.announcer.mute()
        self._refresh_voice_state()

    def _refresh_voice_state(self):
        """Put the voice's state on screen. Called on every update; cheap."""
        if not hasattr(self, "voice_var"):
            return
        state = self.announcer.state()
        self.voice_var.set(state)
        error = self.announcer.error
        if error and self.message_var.get() == "":
            self.set_message("Voice unavailable: %s" % error)
        enabled = self.announcer.enabled
        for name, button in self.voice_buttons.items():
            button.config(state="normal" if enabled else "disabled")
        if enabled:
            self.voice_buttons["mute"].config(
                text="Unmute" if self.announcer.is_muted() else "Mute")

    def _show_engine_decision(self, payload):
        """Put the Scenario Engine's current decision on the one banner.

        Only while the pre-round alert is not using it: betting opening is its
        own moment, and ANTE NOW should not be shoved aside by the WAIT that
        follows a second later.

        The decision and the round come from the payload the window was given
        - this reads them, it does not work anything out. The banner itself
        ignores a decision equal to the one already showing, so a decision
        repeated on every poll is drawn once.
        """
        if self.decision_banner is None or not self.tracker.is_running():
            return
        if self.ante_alert is not None and self.ante_alert.showing:
            return
        scenario = payload.get("scenario") or {}
        self.decision_banner.show(scenario.get("decision"),
                                  scenario.get("reason") or "",
                                  payload.get("round_id"))

    def _update_ante_alert(self, payload):
        """Show or hide the ANTE alert for this update. Never touches the game."""
        # Kept whether or not the ANTE alert is switched on: Teach / Correct
        # Scenario captures from this, and it is the payload the banner was
        # drawn from, so the two cannot disagree about what was on screen.
        self.latest_payload = payload
        if self.ante_alert is None:
            return
        event = self.ante_alert.observe(
            payload.get("state"), payload.get("seen"),
            lambda: preround_decision(self.scenarios, self.last_record, self.history))
        self._apply_ante_event(event)
        self._show_engine_decision(payload)

    def _apply_ante_event(self, event):
        if not event or self.ante_banner is None:
            return
        if event[0] == SHOW:
            self.ante_banner.show(event[1], event[2])
        else:
            self.ante_banner.hide()

    def _show_saved(self, payload):
        record, row = payload["record"], payload["row"]
        # The round that just finished is what the pre-round rules ask about,
        # so it has to be remembered here - this is the only place the app
        # learns that a round is complete.
        self.last_record = record
        self.history.insert(0, record)
        del self.history[HISTORY_ROUNDS:]
        self._refresh_statistics()
        self._refresh_scenario_history()
        self.last_saved_var.set(
            "#%s  %s\nPlayer: %s   Dealer: %s"
            % (row["id"], record["hand_fingerprint"],
               record["player_hand"], record["dealer_hand"])
        )
        self.set_message(payload.get("excel_error") or "")
        # The stored row is the same one the database got, so the voice cannot
        # announce a result that disagrees with what was recorded.
        self.voice_events.announce_result(record)

    # -- shutdown ----------------------------------------------------------

    def on_close(self):
        if self.tracker.is_running():
            self.tracker.stop()
        if self.decision_banner is not None:
            self.decision_banner.stop()
        # Ends the voice thread and releases the speech engine, so nothing is
        # left running to keep the process alive.
        self.announcer.shutdown()
        # Remember where this machine's user put the window, so the next run
        # opens where they left it rather than back at the default corner.
        self._save_window_state()
        logger.info("Application shutdown")
        self.root.destroy()


class LiveDebugWindow(tk.Toplevel):
    """LIVE RECOGNITION DEBUG: this frame's raw reading of every slot, beside memory.

    A diagnostic view, not a control. It shows, poll by poll, what each slot's
    box read as in the current frame, what card memory holds, what the main
    window shows, where each box came from, the result panel, the table-versus-
    panel check, and every problem the diagnostics found. It can also switch on
    saving of annotated frames and card crops.
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Live Recognition Debug")
        # Sized for the screen in use rather than for a 1920-pixel one: on a
        # small laptop this opened wider and taller than the desktop. Docked
        # left, where it does not land on the tracker.
        self.geometry(get_adaptive_window_geometry(
            content_width=980, content_height=560, root=self, dock="left",
            max_width_fraction=0.9, max_height_fraction=0.9).as_string())
        # The main window is always on top, so this must be too or it opens behind it.
        self.attributes("-topmost", True)

        controls = ttk.Frame(self, padding=8)
        controls.pack(fill="x")
        self.save_var = tk.BooleanVar(value=bool(app.config.get("live_diagnostics")))
        ttk.Checkbutton(controls,
                        text="Save annotated frames and card crops to debug/",
                        variable=self.save_var, command=self._toggle).pack(side="left")

        self.text = tk.Text(self, font=("Consolas", 9), wrap="none")
        self.text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.text.insert("1.0", "Waiting for the tracker - press Start Tracker.")

    def _toggle(self):
        self.app.config["live_diagnostics"] = bool(self.save_var.get())

    def show(self, payload):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(debug_lines(payload)))


class TestWindow(tk.Toplevel):
    """One-shot recognition check, from the live screen or a saved screenshot."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Test Recognition")
        self.geometry(get_adaptive_window_geometry(
            content_width=460, content_height=420, root=self, dock="left",
            max_width_fraction=0.9, max_height_fraction=0.9).as_string())
        self.attributes("-topmost", True)

        controls = ttk.Frame(self, padding=8)
        controls.pack(fill="x")
        ttk.Button(controls, text="Read screen now",
                   command=self.read_screen).pack(side="left", padx=2)
        ttk.Button(controls, text="Read screenshot file...",
                   command=self.read_file).pack(side="left", padx=2)
        ttk.Button(controls, text="Teach cards...",
                   command=self.teach).pack(side="left", padx=2)

        self.text = tk.Text(self, height=20, font=("Consolas", 10), wrap="none")
        self.text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.read_screen()

    def _report(self, cards, reads, source):
        state = derive_state(cards)
        lines = ["Source: %s" % source,
                 "Cards found: %s" % (self.app.layout_source or "--"),
                 "State:  %s" % state, ""]
        lines.append("%-16s %-6s %-6s %-11s %s" % (
            "SLOT", "CARD", "CONF", "STATUS", "WHY"))
        for slot in CARD_SLOTS:
            read = reads[slot]
            memory = self.app.tracker.memory
            status, why = card_status(read, memory.support(slot), card=memory.cards.get(slot))
            lines.append("%-16s %-6s %-6.2f %-11s %s" % (
                SLOT_LABELS[slot],
                read["card"] or DASH,
                read["confidence"],
                status,
                why,
            ))

        if all(cards.get(slot) for slot in CARD_SLOTS):
            record = build_hand_record(cards)
            lines += [
                "",
                "Player hand: %s  (%s)" % (record["player_hand"],
                                           " ".join(record["player_best_five"])),
                "Dealer hand: %s  (%s)" % (record["dealer_hand"],
                                           " ".join(record["dealer_best_five"])),
                "Winner:      %s%s" % (
                    record["winner"],
                    "" if record["dealer_qualified"] else "  (dealer does not qualify)"),
                "Fingerprint: %s" % record["hand_fingerprint"],
            ]
        else:
            lines += ["", "Incomplete table - nothing would be saved."]

        panels = getattr(self.app, "last_panels", None) or {}
        if panels:
            lines += ["", "RESULT PANEL DETECTED"]
            for side in ("player", "dealer"):
                if side not in panels:
                    continue
                data = panels[side]
                lines.append("  %s:" % side.title())
                for index, read in enumerate(data["reads"], start=1):
                    lines.append("    card %d: %-5s confidence %.2f  %s%s" % (
                        index, read["card"] if read["confident"] else "UNKNOWN",
                        read["confidence"], result_panel.thumbnail_status(read),
                        "" if read["confident"] else "   (not used)"))
                if data["complete"]:
                    verdict, message = result_panel.verify(
                        side, {"confirmed": data["cards"]}, cards)
                    lines.append("    table vs panel: %s - %s" % (verdict.upper(), message))
                shown = [card for card in data["cards"] if card]
                lines.append("    result: %s" % (" ".join(shown) or "--"))
                if data["complete"]:
                    from poker.hand_evaluator import evaluate_hand
                    lines.append("    evaluated hand: %s"
                                 % evaluate_hand(data["cards"])["name"])
        else:
            lines += ["", "RESULT PANEL: none visible"]

        uncertain = [slot for slot in CARD_SLOTS
                     if reads[slot]["present"] and not reads[slot]["confident"]]
        if uncertain:
            lines += ["", "Recognition uncertain: %s" %
                      ", ".join(SLOT_LABELS[slot] for slot in uncertain),
                      "Use 'Teach cards...' to learn this table's artwork."]

        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))

    def read_screen(self):
        try:
            cards, reads = self.app.read_once()
        except TemplatesMissingError as exc:
            messagebox.showerror("Test Recognition", str(exc), parent=self)
            return
        except CaptureError as exc:
            messagebox.showerror("Test Recognition", str(exc), parent=self)
            return
        self._report(cards, reads, "live screen")

    def read_file(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Choose a full-screen screenshot",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
        )
        if not path:
            return
        image = cv2.imread(path)
        if image is None:
            messagebox.showerror("Test Recognition", "Could not read %s" % path, parent=self)
            return
        try:
            cards, reads = self.app.read_once(image)
        except TemplatesMissingError as exc:
            messagebox.showerror("Test Recognition", str(exc), parent=self)
            return
        self._report(cards, reads, os.path.basename(path))

    def teach(self):
        learn_templates(self, self.app.config)
        self.read_screen()


def dealer_lines(payload):
    """DEALER LIVE DEBUG: both dealer slots, and where this poll's time went."""
    dealer = (payload or {}).get("dealer")
    if not dealer:
        return ["DEALER LIVE DEBUG   off (dealer_debug) or unavailable this poll", ""]
    lines = ["DEALER LIVE DEBUG   crops saved: %s" % dealer.get("saved", 0)]
    for slot in ("dealer_1", "dealer_2"):
        evidence = (dealer.get("slots") or {}).get(slot)
        marks = (dealer.get("marks_ms_ago") or {}).get(slot) or {}
        if evidence:
            lines.append("  " + describe_slot(evidence))
            lines.append("     ms ago - boxed %s  read %s  memory %s  shown %s  (recognition %s ms)" % (
                marks.get("boxed", "--"), marks.get("read", "--"), marks.get("memory", "--"),
                marks.get("shown", "--"), evidence.get("recognition_ms")))
    timing = (payload or {}).get("timing") or {}
    lines.append("  this poll ms - capture %.0f  roi %.0f  crop %.1f  recognition %.0f  memory %.2f  "
                 "gate %.2f  panel %.0f  diagnostics %.1f  total %.0f" % tuple(
                     timing.get(key, 0.0) for key in ("capture", "roi", "crop", "recognition",
                                                      "memory", "gate", "panel", "diagnostics",
                                                      "total")))
    for record in dealer.get("latency") or []:
        lines.append("  SHOWN %s=%s  boxed->read %s  read->memory %s  memory->shown %s  "
                     "boxed->shown %s ms" % (record["slot"], record["card"],
                                            record["boxed_to_read_ms"], record["read_to_memory_ms"],
                                            record["memory_to_shown_ms"], record["boxed_to_shown_ms"]))
    lines.append("")
    return lines


def debug_lines(payload):
    """The Live Recognition Debug text for one tracker update."""
    diag = (payload or {}).get("diagnostics")
    if not diag:
        return dealer_lines(payload) + ["Live diagnostics unavailable for this poll."]
    lines = dealer_lines(payload) + ["LIVE RECOGNITION DEBUG   round %s   state %s   boxes: %s   saved: %s" % (
        diag.get("round_id") if diag.get("round_id") is not None else "--",
        payload.get("state"), diag.get("layout_source") or "--", diag.get("saved", 0)), ""]
    lines.append("%-9s %-26s %-12s %-6s %-6s %-10s %-30s %s" % (
        "SLOT", "RAW FRAME  rank/suit/all", "SUIT SCORES", "MEMORY", "SHOWN",
        "STATUS", "BOX x,y wxh (from)", "FLAGS"))
    for row in diag.get("slots") or []:
        raw = "--"
        if row["read_as"]:
            raw = "%-4s %.2f/%.2f/%.2f%s" % (
                row["read_as"], row["rank_confidence"] or 0, row["suit_confidence"] or 0,
                row["confidence"] or 0, "" if row["raw_card"] else " refused")
        suits = " ".join("%s%.2f" % (label, score)
                         for label, score in row.get("suit_scores") or []) or "-"
        region = row["region"]
        box = ("%d,%d %dx%d (%s)" % (region["left"], region["top"], region["width"],
                                     region["height"], row["region_source"] or "?")
               if region else "no box")
        lines.append("%-9s %-26s %-12s %-6s %-6s %-10s %-30s %s" % (
            row["slot"], raw, suits, row["memory_card"] or "--", row["shown_card"] or "--",
            row["status"] or "--", box, " ".join(row["flags"])))
    lines += ["", "RESULT PANEL"]
    for side in ("player", "dealer"):
        data = (diag.get("panel") or {}).get(side) or {}
        shown = " ".join(card or "?" for card in data.get("cards") or []) or "--"
        lines.append("  %-7s %-22s confirmed %-18s %s%s" % (
            side.title() + ":", shown, " ".join(data.get("confirmed") or []) or "--",
            data.get("state") or "--", "  (previous round)" if data.get("stale") else ""))
    lines += ["", "VERIFICATION  CENTER vs PANEL"]
    verification = diag.get("verification") or {}
    for side in ("player", "dealer"):
        if side in verification:
            verdict, message = verification[side]
            lines.append("  %-7s %s - %s" % (side + ":", verdict.upper(), message))
    lines += ["", "PROBLEMS"]
    lines += ["  " + text for text in diag.get("problems") or []] or ["  none"]
    return lines


# What each recognition status looks like beside a card, so the window shows
# which card is missing and why: confirmed (or covered but confirmed), still
# being confirmed, a suit too close to call, or a card there but unreadable.
STATUS_MARKS = {"CONFIRMED": "✓", "HELD": "✓", "CONFIRMING": "…",
                "AMBIGUOUS": "?", "UNKNOWN": "✗"}


def card_cell(card, status=None, held=False):
    """The text for one card in the Current Hand grid, e.g. "AC ✓" or "-- ✗"."""
    mark = STATUS_MARKS.get(status)
    if card:
        if mark:
            # The cell is five characters wide: a ten's mark goes without the space.
            return ("%s%s" if len(card) > 2 else "%s %s") % (card, mark)
        # No status sent (an older update): a dot still marks a remembered card.
        return ("%s." % card) if held else card
    return "%s %s" % (DASH, mark) if status in ("AMBIGUOUS", "UNKNOWN") else DASH


def _sourced(panel_value, table_value):
    """A hand name labelled with where it came from."""
    if panel_value:
        return "%s  (panel)" % panel_value
    if table_value:
        return "%s  (table)" % table_value
    return DASH


def main():
    setup_logging()
    logger.info("Application startup")
    make_dpi_aware()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
