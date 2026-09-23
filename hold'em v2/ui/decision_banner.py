"""One banner, showing one decision: whichever one is current.

The window used to have an ANTE-only alert. This is that same banner,
generalised: one widget, in one place, whose text is replaced when the
decision changes. There is no second banner for PLAY and no third for WAIT -
showing two at once would be showing two answers to a question that has one.

WHERE THE DECISION COMES FROM

Nothing here decides anything. Every value it can display is a constant that
already existed, produced by logic this module does not touch:

    poker/scenarios.py          ANTE, SKIP      the pre-round rules
                                PLAY, FOLD      the flop rules
    poker/scenario_engine.py    PLAY, DONT_PLAY, WAIT
    BONUS                       a button name, not a decision (see below)

See DECISION_LABELS. A value that is not in that table is not displayed at
all, which is the right answer for "no decision yet" - better a blank banner
than a confident wrong one.

ABOUT BONUS

There is no BONUS decision in this project. `BONUS` is the name of a button on
the local test table, and mouse_controller says of it: "Nothing here ever
clicks it: it is not an action and validation refuses it." It is mapped here
so the banner is complete if a BONUS rule is ever added, but as the code
stands nothing can produce it and the label will never appear.

ONE LOOK

Every decision is drawn the same way - one green banner, one position, one
size, one font, one underline - so the only thing that changes between them
is the word. See BACKGROUND and colour_for.

ONLY WHILE THE TRACKER IS RUNNING

The banner is an always-on-top window with no title bar, so one left behind
sits over whatever the person does next. It therefore shows nothing at all
unless it has been told the tracker is running: start() before, stop() after.

That is not belt and braces, it is the fix for a real bug. Stopping the
tracker cleared the banner, and a moment later it came back. The tracker
queues its updates for the window to drain, and stopping the thread does not
empty that queue - so the payloads already in it were still delivered after
the stop, each one showing its decision again on a banner that had just been
cleared. Clearing was never going to be enough; the banner has to refuse.

WHY IT ONLY UPDATES ON A CHANGE

The tracker polls about five times a second and re-reports the same decision
every time. Redrawing (and re-sounding) on each poll would make a stable
decision flicker and beep. So show() compares against what is already on
screen and does nothing at all when they match - see `changed`.
"""

import tkinter as tk
import tkinter.font as tkfont

from poker import scenario_engine as se
from poker import scenarios as scenario_rules

# The BONUS button's name. Deliberately NOT imported from
# automation.mouse_controller, where the same string is defined: that module
# is the betting automation, and the banner is a read-only display that has no
# business importing it. A test asserts the two stay equal, so they cannot
# drift apart without something failing.
BONUS = "bonus"

# -- what each existing decision value is called on screen ---------------------
#
# The keys are the project's own constants, not strings typed again here, so a
# decision cannot be renamed in one place and missed in the other. Two of them
# deliberately share a label: scenario_rules.PLAY ("play", the flop rule) and
# se.PLAY ("PLAY", the Scenario Engine) are the same instruction to the person
# watching, and differ only in which half of the application said it.
DECISION_LABELS = {
    scenario_rules.ANTE: "ANTE NOW",
    scenario_rules.SKIP: "SKIP ROUND",
    scenario_rules.PLAY: "PLAY NOW",
    scenario_rules.FOLD: "FOLD",
    se.PLAY: "PLAY NOW",
    se.DONT_PLAY: "DON'T PLAY",
    se.WAIT: "WAIT",
    BONUS: "BONUS NOW",
}

# ONE look, for every decision. The banner is a single component and it does
# not change appearance with its contents: same green, same position, same
# size, same font, same weight, same underline, same spacing. Only the words
# change. An earlier version greyed the decisions that ask for nothing; that
# is gone, because two styles invite the reader to decode the colour instead
# of reading the word.
BACKGROUND = "#0b7a3b"
FOREGROUND = "#ffffff"


def colour_for(decision):                       # noqa: ARG001 - same for all
    """The banner's background: the same for every decision, deliberately."""
    return BACKGROUND


# Which decisions are worth a chime. This is not styling - the banner looks
# identical either way - it is about noise. The Scenario Engine returns to
# WAIT constantly while a hand is in progress, and a sound each time would be
# unusable. A chime marks the decisions that ask the watcher to do something.
CHIMES = (scenario_rules.ANTE, scenario_rules.PLAY, se.PLAY, BONUS)


def label_for(decision):
    """The words for a decision, or None when there is nothing to show."""
    return DECISION_LABELS.get(decision)


# What each decision is SAID as. Keyed on exactly the same constants as
# DECISION_LABELS above, because the banner and the voice must be answering
# the same question about the same value - the banner is the single source of
# truth and this is only its translation into speech.
#
# Written out rather than derived from the labels. Lowercasing "ANTE NOW"
# gives "Ante now", and the ante is called for by name: "Ante" is the word.
# The others keep the banner's own wording, so nobody has to learn two
# vocabularies. A test asserts every label has an entry here, so a decision
# added to one table cannot be missed in the other.
#
# This lives here rather than in voice/phrasing.py because the decision values
# are the game's own words - one of them is the ante - and voice/ is checked by
# test_the_voice_package_cannot_touch_the_game for exactly those words. That
# guard is worth more than the convenience of putting this beside the other
# phrasing, and the banner already owns the labels.
SPOKEN_DECISIONS = {
    scenario_rules.ANTE: "Ante",
    scenario_rules.SKIP: "Skip round",
    scenario_rules.PLAY: "Play now",
    scenario_rules.FOLD: "Fold",
    se.PLAY: "Play now",
    se.DONT_PLAY: "Don't play",
    se.WAIT: "Wait",
    # Unreachable, like its label: nothing in the project produces BONUS.
    BONUS: "Bonus now",
}


def spoken(decision):
    """The words for a decision, said aloud. None when there is nothing.

    The voice decides nothing. It is handed the value the banner was handed,
    and this turns that one value into one phrase.
    """
    return SPOKEN_DECISIONS.get(decision)


class CurrentDecision:
    """Which decision is current, and whether it just changed.

    Plain Python, no Tk, so the rules about replacing and resetting can be
    tested without a display.

    `round_id` is the tracker's own round identity (CardMemory's generation,
    already in every update payload). Nothing here invents one. When it
    changes the decision is dropped, so a PLAY NOW from the round that just
    ended cannot still be on screen during the next one.
    """

    def __init__(self):
        self._decision = None
        self._round_id = None

    @property
    def decision(self):
        return self._decision

    @property
    def round_id(self):
        return self._round_id

    def observe(self, decision, round_id=None):
        """Fold in the current decision. Returns True when it changed.

        A decision this module has no label for is treated as no decision:
        the banner clears rather than keeping the previous one.
        """
        if round_id != self._round_id:
            self._round_id = round_id
            changed = self._decision is not None
            self._decision = None
            if decision is None or label_for(decision) is None:
                return changed
            self._decision = decision
            return True

        if decision is not None and label_for(decision) is None:
            decision = None
        if decision == self._decision:
            return False
        self._decision = decision
        return True

    def reset(self):
        """Forget the decision; the next one is a change whatever it is."""
        changed = self._decision is not None
        self._decision = None
        self._round_id = None
        return changed


class DecisionBanner:
    """The one banner. A borderless, always-on-top strip near the top of the screen.

    Same geometry, font and behaviour as the ANTE alert it grew out of, so it
    appears where that did. What is new is that it takes any decision, that
    the text is underlined, and that showing the same decision twice does
    nothing.
    """

    def __init__(self, root, width=520, height=110, top=70, sound=True,
                 active=False):
        self.root = root
        self.sound = sound
        # Inactive until the tracker starts. The safe default: a banner that
        # has not been told anything shows nothing, rather than showing a
        # decision nobody is tracking.
        self._active = bool(active)
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)          # no title bar, no taskbar button
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

        # The underline is a property of the font, not a line drawn under a
        # guess at the text's width. One font object, shared by every
        # decision, so the rule is exactly as long as the words above it for
        # ANTE NOW, SKIP ROUND, WAIT and anything added later - without
        # anybody measuring a string.
        self.title_font = tkfont.Font(family="Segoe UI", size=30,
                                      weight="bold", underline=1)
        self.title_label = tk.Label(self.frame, textvariable=self.title_var,
                                    font=self.title_font)
        self.title_label.pack(pady=(10, 0))
        self.reason_label = tk.Label(self.frame, textvariable=self.reason_var,
                                     font=("Segoe UI", 10), wraplength=width - 20)
        self.reason_label.pack()

        self.visible = False
        self.on_dismiss = None
        self.current = CurrentDecision()
        for widget in (self.window, self.frame, self.title_label, self.reason_label):
            widget.bind("<Button-1>", self._clicked)

    # -- the tracker's lifecycle -----------------------------------------------

    @property
    def active(self):
        """Whether the banner is allowed to show anything at all."""
        return self._active

    def start(self):
        """The tracker is running. Starts hidden, waiting for a decision.

        Whatever the last session ended on is forgotten, so restarting never
        puts the previous session's decision back on screen.
        """
        self._active = True
        self.current.reset()
        self.hide()

    def stop(self):
        """The tracker has stopped. Nothing shown, nothing remembered.

        Withdraws the window, so no always-on-top frame is left over other
        applications, and refuses anything that arrives afterwards - which
        things do, from the queue the tracker filled before it was stopped.
        Safe to call repeatedly and safe to call having never started.
        """
        self._active = False
        self.current.reset()
        self.hide()

    # -- what is on screen -----------------------------------------------------

    @property
    def decision(self):
        """The decision currently displayed, or None."""
        return self.current.decision if self.visible else None

    @property
    def text(self):
        """The words currently displayed. Empty when nothing is shown."""
        return self.title_var.get() if self.visible else ""

    # -- showing ---------------------------------------------------------------

    def show(self, decision, reason="", round_id=None):
        """Display `decision`, replacing whatever was there. Returns True if it changed.

        Called on every poll. When the decision has not changed this does
        nothing at all: no redraw, no sound, no flicker. An unknown decision
        hides the banner rather than leaving a stale one up.

        Refused outright while the tracker is not running - see stop().
        """
        if not self._active:
            if self.visible:
                self.hide()
            return False
        changed = self.current.observe(decision, round_id)
        if not changed:
            if self.visible:
                self.reason_var.set(self._reason_text(reason))
            return False

        settled = self.current.decision
        if settled is None:
            self.hide()
            return True

        self.title_var.set(label_for(settled))
        self.reason_var.set(self._reason_text(reason))
        background = colour_for(settled)
        for widget in (self.window, self.frame, self.title_label, self.reason_label):
            widget.configure(background=background)
        for widget in (self.title_label, self.reason_label):
            widget.configure(foreground=FOREGROUND)
        self.window.deiconify()
        self.window.lift()
        self.visible = True
        if self.sound and settled in CHIMES:
            from ui.ante_alert import play_sound
            play_sound(settled)
        return True

    @staticmethod
    def _reason_text(reason):
        return "%s   (click to dismiss)" % reason if reason else "(click to dismiss)"

    def hide(self):
        self.window.withdraw()
        self.visible = False

    def reset(self):
        """A new round, or the tracker stopped: show nothing."""
        self.current.reset()
        self.hide()

    def _clicked(self, _event=None):
        self.hide()
        if self.on_dismiss:
            self.on_dismiss()

    def destroy(self):
        try:
            self.window.destroy()
        except tk.TclError:
            pass
