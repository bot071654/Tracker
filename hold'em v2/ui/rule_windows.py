"""The two rule builders: pre-round scenarios, and the flop decision.

Each window is a few dropdowns, an Add rule button, and the list of rules you
have already added. Rules are checked top to bottom and the first match wins,
so the list order is the priority order.
"""

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from poker import scenarios

logger = logging.getLogger(__name__)


class _RuleWindow(tk.Toplevel):
    """Shared frame: some choosers, an Add button, and the current rules."""

    section = None
    heading = ""
    explanation = ""

    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        self.on_change = on_change
        self.scenarios = scenarios.load()

        self.title(self.heading)
        self.resizable(False, False)
        self.attributes("-topmost", True)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=self.heading,
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(frame, text=self.explanation, wraplength=520,
                  justify="left", foreground="#444").pack(anchor="w", pady=(2, 10))

        self.choosers = ttk.Frame(frame)
        self.choosers.pack(fill="x")
        self.build_choosers(self.choosers)

        ttk.Button(frame, text="Add rule", command=self.add_rule).pack(
            anchor="w", pady=(10, 0))

        ttk.Separator(frame).pack(fill="x", pady=10)
        header = ttk.Frame(frame)
        header.pack(fill="x")
        ttk.Label(header, text="Rules, in the order they are checked:",
                  font=("Segoe UI", 10, "bold")).pack(side="left")

        self.listbox = tk.Listbox(frame, height=8, width=72,
                                  font=("Segoe UI", 9), activestyle="none")
        self.listbox.pack(fill="both", expand=True, pady=(4, 6))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Move up", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(buttons, text="Move down", command=lambda: self.move(1)).pack(
            side="left", padx=4)
        ttk.Button(buttons, text="Remove", command=self.remove_rule).pack(side="left")

        default = ttk.Frame(frame)
        default.pack(fill="x", pady=(10, 0))
        ttk.Label(default, text="When no rule matches:").pack(side="left")
        self.default_var = tk.StringVar(
            value=scenarios.ACTION_LABELS[self.body()["default"]]
        )
        chooser = ttk.Combobox(
            default, textvariable=self.default_var, state="readonly", width=14,
            values=[scenarios.ACTION_LABELS[a] for a in self.actions()],
        )
        chooser.pack(side="left", padx=6)
        chooser.bind("<<ComboboxSelected>>", self.change_default)

        self.refresh()

    # -- subclasses fill these in -----------------------------------------

    def actions(self):
        raise NotImplementedError

    def build_choosers(self, parent):
        raise NotImplementedError

    def make_rule(self):
        raise NotImplementedError

    def describe(self, rule):
        raise NotImplementedError

    # -- shared behaviour --------------------------------------------------

    def body(self):
        return self.scenarios[self.section]

    def action_from_label(self, label):
        for action, text in scenarios.ACTION_LABELS.items():
            if text == label and action in self.actions():
                return action
        return self.actions()[0]

    def refresh(self):
        self.listbox.delete(0, "end")
        rules = self.body()["rules"]
        if not rules:
            self.listbox.insert("end", "  (no rules yet - every round uses the default)")
            return
        for index, rule in enumerate(rules, start=1):
            self.listbox.insert("end", "  %d.  %s" % (index, self.describe(rule)))

    def commit(self):
        scenarios.save(self.scenarios)
        self.refresh()
        if self.on_change:
            self.on_change()

    def add_rule(self):
        try:
            rule = self.make_rule()
        except ValueError as exc:
            messagebox.showwarning("Add rule", str(exc), parent=self)
            return

        rules = self.body()["rules"]
        if any(self.same_conditions(rule, existing) for existing in rules):
            messagebox.showinfo(
                "Add rule",
                "There is already a rule for that situation. Remove it first, "
                "or change this one - only the first match is ever used.",
                parent=self,
            )
            return

        rules.append(rule)
        logger.info("Scenario rule added: %s", rule["name"])
        self.commit()

    def same_conditions(self, one, other):
        keys = set(one) | set(other)
        keys.discard("action")
        keys.discard("name")
        return all(one.get(key) == other.get(key) for key in keys)

    def selection(self):
        picked = self.listbox.curselection()
        if not picked or not self.body()["rules"]:
            return None
        index = picked[0]
        return index if index < len(self.body()["rules"]) else None

    def remove_rule(self):
        index = self.selection()
        if index is None:
            messagebox.showinfo("Remove", "Select a rule first.", parent=self)
            return
        removed = self.body()["rules"].pop(index)
        logger.info("Scenario rule removed: %s", removed.get("name"))
        self.commit()

    def move(self, step):
        index = self.selection()
        if index is None:
            return
        rules = self.body()["rules"]
        target = index + step
        if not 0 <= target < len(rules):
            return
        rules[index], rules[target] = rules[target], rules[index]
        self.commit()
        self.listbox.selection_set(target)

    def change_default(self, _event=None):
        self.body()["default"] = self.action_from_label(self.default_var.get())
        self.commit()


class PreRoundRules(_RuleWindow):
    """Rules about the round that just finished."""

    section = "preround"
    heading = "Scenarios - before the round"
    explanation = (
        "Decide whether to ante the next round based on the round that just "
        "finished. Note that each round is dealt from a fresh shuffle, so what "
        "happened last round cannot predict the next one - the backtest will "
        "show you what any rule here actually does across your recorded hands."
    )

    def actions(self):
        return scenarios.PREROUND_ACTIONS

    def build_choosers(self, parent):
        self.winner_var = tk.StringVar(value=scenarios.ANY)
        self.streak_var = tk.StringVar(value=scenarios.ANY)
        self.dealer_var = tk.StringVar(value=scenarios.ANY)
        self.player_var = tk.StringVar(value=scenarios.ANY)
        self.action_var = tk.StringVar(value=scenarios.ACTION_LABELS[scenarios.SKIP])

        rows = [
            ("If previous round winner was", self.winner_var,
             scenarios.WINNER_CHOICES),
            ("and Player wins in a row are", self.streak_var,
             scenarios.STREAK_CHOICES),
            ("and previous round Dealer's hand was", self.dealer_var, scenarios.HAND_CHOICES),
            ("and previous round Player's hand was", self.player_var, scenarios.HAND_CHOICES),
            ("then", self.action_var,
             [scenarios.ACTION_LABELS[a] for a in self.actions()]),
        ]
        for index, (label, variable, values) in enumerate(rows):
            ttk.Label(parent, text=label, width=34).grid(
                row=index, column=0, sticky="w", pady=3)
            ttk.Combobox(parent, textvariable=variable, values=values,
                         state="readonly", width=22).grid(row=index, column=1, sticky="w")

    def make_rule(self):
        streak = scenarios.streak_from_choice(self.streak_var.get())
        if (self.dealer_var.get() == scenarios.ANY
                and self.player_var.get() == scenarios.ANY
                and self.winner_var.get() == scenarios.ANY
                and not streak):
            raise ValueError(
                "That rule would match every round. Choose a winner, a run of "
                "Player wins, or a hand for the dealer or the player."
            )
        return scenarios.preround_rule(
            self.dealer_var.get(), self.player_var.get(),
            self.action_from_label(self.action_var.get()),
            previous_winner=self.winner_var.get(),
            player_win_streak=streak,
        )

    def describe(self, rule):
        return scenarios.describe_preround(rule)


class FlopRules(_RuleWindow):
    """Rules about the decision made once the flop is out."""

    section = "flop"
    heading = "Scenarios - after the flop"
    explanation = (
        "Once your two cards and the flop are showing, decide whether to play "
        "on or fold. The made hand alone is not much to go on - two cards are "
        "still to come - so the second box asks what you are drawing to, worked "
        "out from the cards on the table."
    )

    def actions(self):
        return scenarios.FLOP_ACTIONS

    def build_choosers(self, parent):
        self.hand_var = tk.StringVar(value="High Card")
        self.condition_var = tk.StringVar(value=scenarios.ANY)
        self.action_var = tk.StringVar(value=scenarios.ACTION_LABELS[scenarios.FOLD])

        rows = [
            ("If the hand is", self.hand_var, scenarios.HAND_CHOICES),
            ("and on the table there is", self.condition_var, scenarios.CONDITION_CHOICES),
            ("then", self.action_var,
             [scenarios.ACTION_LABELS[a] for a in self.actions()]),
        ]
        for index, (label, variable, values) in enumerate(rows):
            ttk.Label(parent, text=label, width=34).grid(
                row=index, column=0, sticky="w", pady=3)
            ttk.Combobox(parent, textvariable=variable, values=values,
                         state="readonly", width=28).grid(row=index, column=1, sticky="w")

    def make_rule(self):
        return scenarios.flop_rule(
            self.hand_var.get(), self.condition_var.get(),
            self.action_from_label(self.action_var.get()),
        )

    def describe(self, rule):
        return scenarios.describe_flop_rule(rule)
