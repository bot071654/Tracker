"""The two rule builders: pre-round scenarios, and the flop decision.

Each window is a few dropdowns, an Add rule button, and the list of rules you
have already added. Rules are checked top to bottom and the first match wins,
so the list order is the priority order.
"""

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from poker import scenarios
from poker import teaching

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
        # scenarios.METADATA_KEYS, so a rule taught from a live hand - which
        # carries a note saying which hand - still counts as the same rule as
        # the identical one added here by hand.
        return scenarios.same_question(one, other)

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


class TeachScenarioWindow(tk.Toplevel):
    """Correct the decision on screen, by writing one of the rules above.

    This is the same rule builder as the two windows above it, filled in from
    the hand being played instead of from dropdowns. It produces an ordinary
    rule, in the ordinary section of config/scenarios.json, evaluated by the
    ordinary engine - after which it can be moved, edited or removed in
    FlopRules or PreRoundRules like any other. There is no second rule store
    and no second evaluator; see poker/teaching.py.

    Nothing is written until Activate. Save & Test builds the proposed rule
    set in memory, runs the existing backtest over the recorded hands, and
    shows what would change.
    """

    def __init__(self, parent, snapshot, rules=None, is_current=None,
                 load_rows=None, on_change=None, save_path=None):
        super().__init__(parent)
        self.snapshot = snapshot
        # Where Activate writes. None means config/scenarios.json, which
        # is what the application wants; the tests point it at a temporary
        # file so running them can never touch the real rules.
        self.save_path = save_path
        self.rules = rules if rules is not None else scenarios.load()
        # How the window asks whether the captured scenario is still the live
        # one. Supplied by the application, which is the only thing that knows
        # the state of the tracker; without it the snapshot is taken at face
        # value, which is what the tests want.
        self.is_current = is_current or (lambda: (True, ""))
        self.load_rows = load_rows
        self.on_change = on_change

        self.scopes = teaching.available_scopes(snapshot)
        self.proposed = None            # the rule set Save & Test built
        self.proposed_rule = None
        self.proposed_index = None
        self.tested = False
        self.activated = False

        self.title("Teach / Correct Scenario")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self._build_scenario(frame)
        self._build_choice(frame)
        self._build_preview(frame)
        self._build_buttons(frame)
        self.refresh()

    # -- the captured scenario ---------------------------------------------

    def _build_scenario(self, frame):
        ttk.Label(frame, text="TEACH / CORRECT SCENARIO",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(frame, text="The decision below is the one that was on "
                              "screen when you pressed the button. It is held "
                              "still: if the round moves on, teaching is "
                              "cancelled rather than saved against the wrong "
                              "hand.",
                  wraplength=560, justify="left",
                  foreground="#444").pack(anchor="w", pady=(2, 10))

        ttk.Separator(frame).pack(fill="x", pady=(0, 8))
        ttk.Label(frame, text="CURRENT SCENARIO",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")

        snapshot = self.snapshot
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(4, 8))
        rows = [
            ("Round:", str(snapshot.round_id)),
            ("Stage:", snapshot.stage or "-"),
            ("Player Cards:", snapshot.player_cards()),
            ("Flop:", snapshot.flop_cards()),
            ("Turn:", snapshot.turn_card()),
            ("River:", snapshot.river_card()),
            ("Hand Type:", snapshot.hand or "-"),
            ("Current Decision:", self._decision_text()),
            ("Matched Rule:", snapshot.matched_name()),
            ("Rule Priority:", snapshot.priority() or "-  (the default)"),
        ]
        for index, (label, value) in enumerate(rows):
            ttk.Label(grid, text=label, width=17).grid(
                row=index, column=0, sticky="w", pady=1)
            ttk.Label(grid, text=value, font=("Segoe UI", 9, "bold"),
                      wraplength=400, justify="left").grid(
                row=index, column=1, sticky="w", pady=1)

    def _decision_text(self):
        """What the rules say, and what the engine said, when they differ.

        Both are shown because they are two different questions and the banner
        may have been displaying either. Only the first can be corrected by a
        rule, so only the first is called the current decision.
        """
        snapshot = self.snapshot
        text = scenarios.ACTION_LABELS[snapshot.action].upper()
        engine = snapshot.engine_decision
        if engine and engine != teaching.ENGINE_EQUIVALENT.get(snapshot.action):
            text += "      (the Scenario Engine says %s)" % engine
        return text

    # -- what to teach ------------------------------------------------------

    def _build_choice(self, frame):
        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="WHAT SHOULD THE BOT DO?",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.action_var = tk.StringVar(value=self.snapshot.actions()[0])
        choices = ttk.Frame(frame)
        choices.pack(anchor="w", pady=(4, 0))
        for column, action in enumerate(self.snapshot.actions()):
            label = scenarios.ACTION_LABELS[action].upper()
            equivalent = teaching.ENGINE_EQUIVALENT.get(action)
            if equivalent:
                label = "%s  (%s)" % (label, equivalent)
            ttk.Radiobutton(choices, text=label, value=action,
                            variable=self.action_var,
                            command=self.refresh).grid(row=0, column=column,
                                                       sticky="w", padx=(0, 16))
        ttk.Label(frame, text=teaching.UNREPRESENTABLE["wait"], wraplength=560,
                  justify="left", foreground="#777").pack(anchor="w", pady=(4, 0))

        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="TEACHING SCOPE",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text="What else has to be true for the correction to "
                              "apply. Narrowest first - the last entries "
                              "change the most hands.",
                  wraplength=560, justify="left",
                  foreground="#444").pack(anchor="w", pady=(2, 4))
        self.scope_list = tk.Listbox(frame, height=6, width=72,
                                     font=("Segoe UI", 9), activestyle="none",
                                     exportselection=False)
        for scope in self.scopes:
            self.scope_list.insert("end", "  %s" % scope.label)
        if self.scopes:
            self.scope_list.selection_set(0)
        self.scope_list.bind("<<ListboxSelect>>", lambda _event: self.refresh())
        self.scope_list.pack(fill="x", pady=(0, 4))
        ttk.Label(frame, text=teaching.UNREPRESENTABLE["exact_cards"],
                  wraplength=560, justify="left",
                  foreground="#777").pack(anchor="w")

    # -- preview, conflicts, priority ---------------------------------------

    def _build_preview(self, frame):
        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="RULE PREVIEW",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.preview = tk.Text(frame, height=9, width=74, wrap="word",
                               font=("Consolas", 9), relief="flat",
                               background="#f4f4f4")
        self.preview.pack(fill="x", pady=(4, 0))

        ttk.Separator(frame).pack(fill="x", pady=8)
        header = ttk.Frame(frame)
        header.pack(fill="x")
        ttk.Label(header, text="CONFLICT CHECK",
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(header,
                  text="   Place the new rule at position:").pack(side="left")
        self.position_var = tk.StringVar(value="1")
        self.position = ttk.Combobox(header, textvariable=self.position_var,
                                     state="readonly", width=5)
        self.position.pack(side="left", padx=6)
        # keep_position, or choosing a position would immediately reset
        # it to the suggested one and the chooser could never be used.
        self.position.bind("<<ComboboxSelected>>",
                           lambda _event: self.refresh(keep_position=True))
        self.conflicts = tk.Text(frame, height=8, width=74, wrap="word",
                                 font=("Consolas", 9), relief="flat",
                                 background="#f4f4f4")
        self.conflicts.pack(fill="x", pady=(4, 0))

    def _build_buttons(self, frame):
        ttk.Separator(frame).pack(fill="x", pady=8)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.test_button = ttk.Button(row, text="Save & Test",
                                      command=self.save_and_test)
        self.test_button.pack(side="left")
        self.activate_button = ttk.Button(row, text="Activate Rule",
                                          command=self.activate,
                                          state="disabled")
        self.activate_button.pack(side="left", padx=6)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")
        self.result = tk.Text(frame, height=14, width=74, wrap="word",
                              font=("Consolas", 9), relief="flat",
                              background="#f4f4f4")
        self.result.pack(fill="both", expand=True, pady=(8, 0))
        self._write(self.result,
                    ["Nothing has been saved. Save & Test runs the rule "
                     "against this hand and against every recorded hand, and "
                     "changes nothing."])

    # -- keeping the three panes in step ------------------------------------

    @staticmethod
    def _write(widget, lines):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", "\n".join(lines))
        widget.configure(state="disabled")

    def scope(self):
        """The scope currently selected, or None when there are none."""
        picked = self.scope_list.curselection()
        if not picked or picked[0] >= len(self.scopes):
            return self.scopes[0] if self.scopes else None
        return self.scopes[picked[0]]

    def action(self):
        return self.action_var.get()

    def rule(self):
        """The rule the current choices describe. Built, not saved."""
        scope = self.scope()
        return scope.build(self.action()) if scope else None

    def index(self):
        """Where the rule would go, as a 0-based index."""
        try:
            return max(0, int(self.position_var.get()) - 1)
        except (TypeError, ValueError):
            return 0

    def existing(self):
        return (self.rules.get(self.snapshot.section) or {}).get("rules") or []

    def refresh(self, keep_position=False):
        """Redraw the preview and the conflict check for the current choices.

        Called on every change. Decides nothing and saves nothing: it runs the
        rule builder and the conflict checker and prints what they say.
        """
        if self.activated:
            return
        rule = self.rule()
        if rule is None:
            self._write(self.preview, ["There is nothing to teach about this "
                                       "scenario."])
            self.test_button.config(state="disabled")
            return

        # The choices moved, so a rule set built from the old ones is stale.
        self.tested = False
        self.proposed = None
        self.activate_button.config(state="disabled")

        count = len(self.existing())
        positions = [str(number) for number in range(1, count + 2)]
        self.position.config(values=positions)
        if not keep_position or self.position_var.get() not in positions:
            suggested = teaching.suggested_index(
                self.rules, self.snapshot.section, rule)
            self.position_var.set(str(suggested + 1))

        self._show_preview(rule)
        self._show_conflicts(rule)

    def _show_preview(self, rule):
        scope = self.scope()
        lines = scope.describe(self.action())
        lines += ["", "-" * 62, "",
                  "CURRENT RULE:", "",
                  "    %s" % self.snapshot.matched_name(),
                  "", "NEW RULE:", "",
                  "    %s" % rule["name"],
                  "",
                  "    stored in config/scenarios.json, %s section, as:"
                  % self.snapshot.section]
        for key in sorted(rule):
            if key in ("name",):
                continue
            lines.append("        %-22s %r" % (key, rule[key]))
        self._write(self.preview, lines)

    def _show_conflicts(self, rule):
        index = self.index()
        findings = teaching.analyse(self.rules, self.snapshot.section, rule, index)
        suggested = teaching.suggested_index(self.rules, self.snapshot.section, rule)

        lines = []
        if not findings:
            lines.append("No conflict. At position #%d this rule can run, and "
                         "no existing rule stops running." % (index + 1))
        for finding in findings:
            lines.append("%s: %s" % (finding.kind.upper(), finding.message))
        lines += ["", "Suggested position: #%d." % (suggested + 1)]
        if suggested != index:
            lines.append("  Rules are checked top to bottom and the first "
                         "match wins, so a rule below something broader never "
                         "runs.")
        lines += ["", "ORDER AFTER THIS CHANGE:", ""]
        rules = list(self.existing())
        rules.insert(index, rule)
        for position, item in enumerate(rules, start=1):
            marker = "  [NEW]" if item is rule else ""
            lines.append("    %d. %s%s" % (position, item["name"], marker))
        lines.append("")
        lines.append("No existing rule is deleted or rewritten by this.")
        self._write(self.conflicts, lines)

        blocking = [finding for finding in findings if finding.blocking]
        self.test_button.config(state="disabled" if blocking else "normal")

    # -- save and test ------------------------------------------------------

    def _rows(self):
        """The recorded hands, for the backtest. Empty when unavailable."""
        if self.load_rows is not None:
            return self.load_rows()
        from database import db
        try:
            return db.fetch_all_hands()
        except db.DatabaseError as exc:
            logger.info("Recorded hands unavailable for the teaching backtest: %s",
                        exc)
            return []

    def _still_current(self):
        """True when the captured scenario is still the live one."""
        ok, why = self.is_current()
        if not ok:
            self._cancel(why)
        return ok

    def _cancel(self, why):
        """Refuse to go any further: the scenario has moved on."""
        self.tested = False
        self.proposed = None
        self.test_button.config(state="disabled")
        self.activate_button.config(state="disabled")
        self._write(self.result, [why, "",
                                  "Nothing was saved. Close this window, then "
                                  "press Teach / Correct Scenario again on the "
                                  "hand you want to correct."])

    def save_and_test(self):
        """Build the proposed rule set and show what it changes. Saves nothing."""
        if not self._still_current():
            return
        rule = self.rule()
        if rule is None:
            return
        index = self.index()
        blocking = [finding for finding
                    in teaching.analyse(self.rules, self.snapshot.section,
                                        rule, index)
                    if finding.blocking]
        if blocking:
            messagebox.showwarning("Save & Test", blocking[0].message, parent=self)
            return

        self.proposed_rule = rule
        self.proposed_index = index
        self.proposed = teaching.propose(
            self.rules, self.snapshot.section, rule, index)

        rows = self._rows()
        report = teaching.impact(self.rules, self.proposed, rows,
                                 self.snapshot.section, rule, self.snapshot)
        lines = ["SAVE & TEST - nothing has been written yet", ""]
        lines += teaching.describe_impact(report)
        lines += ["", "-" * 62, "",
                  "Press Activate Rule to save this as rule #%d of the %s "
                  "section." % (index + 1, self.snapshot.section)]
        self._write(self.result, lines)
        self.tested = True
        self.activate_button.config(state="normal")

    # -- activate -----------------------------------------------------------

    def activate(self):
        """Write the tested rule set. The only thing here that saves anything."""
        if not self.tested or self.proposed is None:
            messagebox.showinfo("Activate Rule",
                                "Press Save & Test first.", parent=self)
            return
        if not self._still_current():
            return

        rule = self.proposed[self.snapshot.section]["rules"][self.proposed_index]
        teaching.stamp(rule, self.snapshot, self.action())
        teaching.activate(self.proposed, self.save_path)
        self.rules = self.proposed
        self.activated = True
        logger.info("Scenario rule activated from a correction: %s", rule["name"])

        self.test_button.config(state="disabled")
        self.activate_button.config(state="disabled")
        self.scope_list.config(state="disabled")
        self._write(self.result, [
            "Rule activated successfully.", "",
            "    Priority:  #%d of the %s section"
            % (self.proposed_index + 1, self.snapshot.section),
            "    Condition: %s" % rule["name"],
            "    Decision:  %s" % scenarios.ACTION_LABELS[rule["action"]].upper(),
            "",
            "The scenario engine uses it from the next round on. It is an "
            "ordinary rule now: the Scenarios window can move, edit or remove "
            "it like any other.",
        ])
        if self.on_change:
            self.on_change()
