"""The Scenario Engine's decision, shown in the main window. Display only.

This layer calculates nothing. It formats the "scenario" dict the tracker
already puts in every update (poker/scenario_engine.evaluate_round) and puts
it on screen. It never clicks, bets or sends anything.

scenario_view() is plain Python, so what is shown can be tested without a
display; ScenarioPanel only touches its widgets when that view changes.
"""

import textwrap
import tkinter as tk
from tkinter import ttk

from poker import scenario_engine as se

DASH = "--"

COLOURS = {se.PLAY: "#0b6", se.DONT_PLAY: "#b00020", se.WAIT: "#777777"}
QUALIFICATION_COLOURS = {True: "#0b6", False: "#b00020", None: "#777777"}

# The panel sits beside the Current Hand cards, where about 35 characters of
# Consolas 8 fit. Nothing here is longer than that.
FONT = ("Consolas", 8)
HEADER_FONT = ("Segoe UI", 9, "bold")
VALUE_FONT = ("Segoe UI", 8, "bold")
WRAP = 212
LINE = 34                         # characters of FONT per line

UNAVAILABLE = "Scenario engine not running"


def _cards(cards):
    return " ".join(card or DASH for card in cards or []) or DASH


def _matched_lines(names):
    """"Matched:" and the names, as many to a line as fit, so the panel stays
    beside the cards instead of pushing the window's content down."""
    lines, line = [], "Matched:"
    for name in names or ["none"]:
        candidate = "%s %s" % (line, name) if line.endswith(":") else "%s, %s" % (line, name)
        if len(candidate) <= LINE:
            line = candidate
        else:
            lines.append(line + ("," if not line.endswith(":") else ""))
            line = "  " + name
    lines.append(line)
    return lines


def _reason_lines(reason):
    """"Reason:" and the engine's own sentence, wrapped to the panel's width.

    The engine's reason names every scenario that matched, which runs past a
    hundred characters on a busy flop - far wider than the strip beside the
    cards. Wrapped here for the same reason _matched_lines wraps: so the panel
    stays beside the table instead of pushing the window's content down.
    """
    if not reason:
        return []
    return textwrap.wrap("Reason: %s" % reason, width=LINE,
                         subsequent_indent="  ")


def scenario_view(scenario):
    """What the panel shows for one "scenario" payload, as plain strings.

    Returns a tuple (decision, decision colour, details, qualification,
    dealer details, qualification colour) so two views can be compared with ==.
    A WAIT shows no cards and no match at all - neither this round's partial
    reading nor anything left from the last round.
    """
    if not scenario:
        return ("Decision: %s" % se.WAIT, COLOURS[se.WAIT],
                "Reason:\n%s" % UNAVAILABLE, "Qualification: PENDING", "",
                QUALIFICATION_COLOURS[None])

    decision = scenario.get("decision") or se.WAIT
    if decision not in COLOURS:
        decision = se.WAIT

    if decision == se.WAIT:
        details = "Reason:\n%s" % (scenario.get("reason") or se.WAIT_REASON)
        # Which cards are holding it up and why, e.g. "player_2: no card (UNKNOWN)".
        missing = scenario.get("wait_reasons") or []
        if missing:
            details += "\n" + "\n".join(missing[:3])
            if len(missing) > 3:
                details += "\n+%d more" % (len(missing) - 3)
    else:
        primary = scenario.get("primary_scenario") or se.NONE
        if primary == se.NONE:
            primary = "NONE - hand %s" % (scenario.get("detected_hand") or DASH)
        lines = ["Scenario: %s" % primary,
                 "Player: %s  Flop: %s" % (_cards(scenario.get("player_cards")),
                                           _cards(scenario.get("flop_cards")))]
        lines += _matched_lines(scenario.get("matched_scenarios"))
        # The engine's own sentence for why it decided that - "matched
        # PLAYER_PAIR", or why nothing matched. Shown verbatim: this panel
        # explains the decision, it does not word it. A WAIT already had its
        # reason above; this is the same field for the other two decisions.
        lines += _reason_lines(scenario.get("reason"))
        details = "\n".join(lines)

    qualified = scenario.get("dealer_qualified")
    if qualified is None:
        qualification, dealer = "Qualification: PENDING", ""
    else:
        qualification = "Qualification: %s" % ("QUALIFIED" if qualified else "NOT QUALIFIED")
        dealer = "Cards: %s  Hand: %s" % (_cards(scenario.get("dealer_cards")),
                                          scenario.get("dealer_hand") or DASH)

    return ("Decision: %s" % decision.replace("_", " "), COLOURS[decision],
            details, qualification, dealer, QUALIFICATION_COLOURS[qualified])


def action_text(action):
    """(text, colour) for the Action Controller status, or None when it is off."""
    if not action:
        return None
    if action.get("halted"):
        return "TEST Action: HALTED (fail-safe)", "#b00020"
    last = action.get("last") or {}
    if last.get("round") != action.get("round") or last.get("action") in (None, "NONE"):
        return "TEST Action: NONE", "#777777"
    result = last.get("result")
    word = {"CLICKED": "CLICKED", "REQUESTED": "CLICKING"}.get(result, "NONE")
    colour = {"CLICKED": "#0b6", "CLICKING": "#777777"}.get(word, "#b00020")
    return "TEST Action: %s %s" % (last["action"], word), colour


class ScenarioPanel(ttk.Frame):
    """SCENARIO and DEALER, compact, for the space beside the card table."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._view = None
        self.updates = 0                 # how often the widgets were actually changed

        self.decision_var = tk.StringVar()
        self.details_var = tk.StringVar()
        self.qualification_var = tk.StringVar()
        self.dealer_var = tk.StringVar()

        self.wraplength = WRAP
        ttk.Label(self, text="SCENARIO", font=HEADER_FONT).grid(row=0, column=0, sticky="w")
        self.decision_label = ttk.Label(self, textvariable=self.decision_var,
                                        font=HEADER_FONT)
        self.decision_label.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.details_label = ttk.Label(self, textvariable=self.details_var, font=FONT,
                                       justify="left", wraplength=WRAP)
        self.details_label.grid(row=1, column=0, columnspan=2, sticky="w")
        # Its own row frame, so "SCENARIO" does not widen the dealer's first column.
        dealer_row = ttk.Frame(self)
        dealer_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Label(dealer_row, text="DEALER", font=HEADER_FONT).pack(side="left")
        self.qualification_label = ttk.Label(dealer_row, textvariable=self.qualification_var,
                                             font=VALUE_FONT)
        self.qualification_label.pack(side="left", padx=(6, 0))
        self.dealer_label = ttk.Label(self, textvariable=self.dealer_var, font=FONT,
                                      justify="left", wraplength=WRAP)
        self.dealer_label.grid(row=3, column=0, columnspan=2, sticky="w")
        # Only shown while the Action Controller is enabled.
        self.action_var = tk.StringVar()
        self._action_view = None
        self.action_label = ttk.Label(self, textvariable=self.action_var, font=VALUE_FONT)
        self.action_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.action_label.grid_remove()
        self.reset()

    def reset(self):
        """Back to WAIT with nothing from an earlier round on screen."""
        self.show(None)
        self.show_action(None)

    def set_wraplength(self, pixels=None):
        """How wide the panel's text may run before it wraps.

        Beside the cards that is the width it was designed for; when the window
        is too narrow for two columns and the panel moves underneath them, it
        is given the whole window instead of staying in a 212-pixel strip.
        None restores the designed width.
        """
        wrap = WRAP if pixels is None else max(WRAP, int(pixels))
        if wrap == self.wraplength:
            return False
        self.wraplength = wrap
        self.details_label.configure(wraplength=wrap)
        self.dealer_label.configure(wraplength=wrap)
        return True

    def show_action(self, action):
        """The Action Controller's status, or nothing when automation is off."""
        text = action_text(action)
        if text == self._action_view:
            return False
        self._action_view = text
        if text is None:
            self.action_label.grid_remove()
            return True
        self.action_var.set(text[0])
        self.action_label.configure(foreground=text[1])
        self.action_label.grid()
        return True

    def show(self, scenario):
        """Show one "scenario" payload. Does nothing when the view is unchanged."""
        view = scenario_view(scenario)
        if view == self._view:
            return False
        self._view = view
        decision, colour, details, qualification, dealer, dealer_colour = view
        self.decision_var.set(decision)
        self.decision_label.configure(foreground=colour)
        self.details_var.set(details)
        self.qualification_var.set(qualification)
        self.qualification_label.configure(foreground=dealer_colour)
        self.dealer_var.set(dealer)
        # An empty dealer line takes no height.
        if dealer:
            self.dealer_label.grid()
        else:
            self.dealer_label.grid_remove()
        self.updates += 1
        return True
