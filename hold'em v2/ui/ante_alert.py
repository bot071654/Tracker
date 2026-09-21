"""The ANTE alert: tells you, loudly, what your pre-round rules say as betting opens.

Display only. It never clicks, types or sends anything to the game - you place
the bet yourself. It reads nothing the tracker has not already sent the window,
and decides nothing: the ANTE / SKIP comes from poker/scenarios.decide_preround,
the same rules shown under "Your scenarios say:".

When it fires
-------------
Betting opens when the table has been cleared after a hand. So a new betting
window is `empty_polls` updates in a row with no card on the table, after the
table last showed cards (or the first empty table after the tracker starts).
Each betting window alerts once. The banner goes away when the next deal
appears (the player's cards are on the table - betting has closed), after
`timeout` seconds, when you dismiss it, or when the tracker stops.

AnteAlertLogic is plain Python so the timing can be tested without a display;
AnteAlertBanner is the window.
"""

import logging
import time
import tkinter as tk

from poker import scenarios as scenario_rules

logger = logging.getLogger(__name__)

WAITING = "WAITING"
SHOW, HIDE = "show", "hide"

COLOURS = {scenario_rules.ANTE: ("#0b7a3b", "#ffffff"),     # green: ante
           scenario_rules.SKIP: ("#5f6368", "#ffffff")}     # grey: skip


class AnteAlertLogic:
    """Decides when the banner appears and disappears. No Tk, no clock of its own."""

    def __init__(self, empty_polls=3, timeout=25.0):
        self.empty_polls = max(1, int(empty_polls))
        self.timeout = float(timeout)
        self._empty = 0
        self._had_cards = False
        self._started = False            # a first empty table after start counts
        self.showing = None              # (action, reason, shown_at) while visible
        self.alerts = 0

    def reset(self):
        """Tracker stopped or restarted: forget the table, hide anything shown."""
        event = self._hide("tracker stopped") if self.showing else None
        self._empty = 0
        self._had_cards = False
        self._started = False
        return event

    def observe(self, state, seen, decide, now=None):
        """Fold in one tracker update.

        `seen` is this poll's raw readings (slot -> card or None), `state` the
        tracker's state, and `decide()` returns (action, reason) from the
        pre-round rules - only called when an alert is actually due. Returns
        None, (SHOW, action, reason) or (HIDE, why).
        """
        now = time.time() if now is None else now
        table_empty = state == WAITING and not any((seen or {}).values())

        if self.showing:
            if not table_empty and any((seen or {}).get(slot) for slot in ("player_1", "player_2")):
                return self._hide("cards dealt - betting closed")
            if now - self.showing[2] >= self.timeout:
                return self._hide("timed out")

        if not table_empty:
            self._empty = 0
            self._had_cards = True
            return None

        self._empty += 1
        due = self._empty == self.empty_polls and (self._had_cards or not self._started)
        if not due:
            return None
        self._had_cards = False
        self._started = True
        action, reason = decide()
        if action not in COLOURS:
            logger.warning("[ANTE ALERT] unknown pre-round action %r - no alert", action)
            return None
        self.showing = (action, reason, now)
        self.alerts += 1
        logger.info("[ANTE ALERT] betting open -> %s (%s)", action.upper(), reason)
        return SHOW, action, reason

    def dismiss(self):
        return self._hide("dismissed") if self.showing else None

    def _hide(self, why):
        self.showing = None
        return HIDE, why


def preround_decision(scenarios, last_record, history):
    """(action, reason) from the user's pre-round rules."""
    action, rule = scenario_rules.decide_preround(scenarios, last_record, history)
    if rule:
        reason = rule["name"]
    elif last_record:
        reason = "default rule"
    else:
        reason = "default rule - no finished round recorded yet"
    return action, reason


def play_sound(action):
    """A short system sound, asynchronously. Silent where winsound is unavailable."""
    try:
        import winsound
    except ImportError:
        return
    alias = "SystemExclamation" if action == scenario_rules.ANTE else "SystemAsterisk"
    try:
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except RuntimeError:
        pass


class AnteAlertBanner:
    """A large, always-on-top banner near the top of the screen. Never takes focus."""

    def __init__(self, root, width=520, height=110, top=70, sound=True):
        self.root = root
        self.sound = sound
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)            # no title bar, no taskbar button
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        left = max(0, (root.winfo_screenwidth() - width) // 2)
        self.window.geometry("%dx%d+%d+%d" % (width, height, left, top))
        self.title_var = tk.StringVar()
        self.reason_var = tk.StringVar()
        self.frame = tk.Frame(self.window, bd=0)
        self.frame.pack(fill="both", expand=True)
        self.title_label = tk.Label(self.frame, textvariable=self.title_var,
                                    font=("Segoe UI", 30, "bold"))
        self.title_label.pack(pady=(10, 0))
        self.reason_label = tk.Label(self.frame, textvariable=self.reason_var,
                                     font=("Segoe UI", 10), wraplength=width - 20)
        self.reason_label.pack()
        self.visible = False
        self.on_dismiss = None
        for widget in (self.window, self.frame, self.title_label, self.reason_label):
            widget.bind("<Button-1>", self._clicked)

    def show(self, action, reason):
        background, foreground = COLOURS[action]
        self.title_var.set("ANTE NOW" if action == scenario_rules.ANTE else "SKIP THIS ROUND")
        self.reason_var.set("%s   (click to dismiss)" % reason)
        for widget in (self.window, self.frame, self.title_label, self.reason_label):
            widget.configure(background=background)
        for widget in (self.title_label, self.reason_label):
            widget.configure(foreground=foreground)
        self.window.deiconify()
        self.window.lift()
        self.visible = True
        if self.sound:
            play_sound(action)

    def hide(self):
        self.window.withdraw()
        self.visible = False

    def _clicked(self, _event=None):
        self.hide()
        if self.on_dismiss:
            self.on_dismiss()

    def destroy(self):
        try:
            self.window.destroy()
        except tk.TclError:
            pass
