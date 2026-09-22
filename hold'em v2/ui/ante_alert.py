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
from ui.decision_banner import DecisionBanner

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


# The banner itself now lives in ui/decision_banner.py, because it is no
# longer about the ante: it is the window's one decision banner, and it shows
# whichever decision is current. The name is kept so that everything already
# referring to it keeps working - there is one widget, under two names.
AnteAlertBanner = DecisionBanner
