"""One banner, one decision, replaced when the decision actually changes.

The rules about *which* decision is current are plain Python (CurrentDecision)
and are tested without a display. The rules about what ends up on screen need
Tk, and those tests skip on a machine that cannot open a window.

Nothing here decides anything about poker. Every value used is a constant the
project already had; this checks only that the right one is displayed.
"""

import tkinter as tk

import pytest

from poker import scenario_engine as se
from poker import scenarios as scenario_rules
from ui import decision_banner as db
from ui.decision_banner import CurrentDecision, DECISION_LABELS, label_for


# -- the labels ---------------------------------------------------------------

def test_the_five_requested_labels_are_exactly_these_words():
    assert label_for(scenario_rules.ANTE) == "ANTE NOW"
    assert label_for(scenario_rules.PLAY) == "PLAY NOW"
    assert label_for(se.PLAY) == "PLAY NOW"
    assert label_for(db.BONUS) == "BONUS NOW"
    assert label_for(se.WAIT) == "WAIT"
    assert label_for(scenario_rules.SKIP) == "SKIP ROUND"


def test_the_other_two_real_decisions_are_also_labelled():
    """DON'T_PLAY and FOLD are decisions the engine and the rules really make."""
    assert label_for(se.DONT_PLAY) == "DON'T PLAY"
    assert label_for(scenario_rules.FOLD) == "FOLD"


def test_every_label_key_is_a_constant_the_project_already_had():
    """No invented decision values - each key comes from existing code."""
    known = {scenario_rules.ANTE, scenario_rules.SKIP, scenario_rules.PLAY,
             scenario_rules.FOLD, se.PLAY, se.DONT_PLAY, se.WAIT, db.BONUS}
    assert set(DECISION_LABELS) == known


def test_the_banner_never_imports_the_betting_automation():
    """A read-only display has no business reaching into the clicker.

    Comments and docstrings are stripped first: this module talks about
    mouse_controller at length, explaining why it does not import it, and
    prose is not an import.
    """
    import inspect

    source = inspect.getsource(db)
    lines = [line for line in source.splitlines()
             if not line.strip().startswith("#")]
    code = "".join(chr(10).join(lines).split('"""')[::2]).lower()
    for forbidden in ("pyautogui", "mouse_controller", "import automation"):
        assert forbidden not in code, "decision_banner has %r in code" % forbidden


# -- one look for every decision ----------------------------------------------

def test_every_decision_uses_the_same_green_background():
    """One style. The word changes; nothing else does."""
    backgrounds = {db.colour_for(decision) for decision in DECISION_LABELS}
    assert backgrounds == {db.BACKGROUND}
    assert db.BACKGROUND == "#0b7a3b"


def test_there_is_no_second_banner_colour():
    """An earlier version greyed the decisions that ask for nothing."""
    assert not hasattr(db, "HOLD")
    palette = [value for name, value in vars(db).items()
               if isinstance(value, str) and value.startswith("#")]
    assert set(palette) == {db.BACKGROUND, db.FOREGROUND}


def test_the_chime_list_is_about_sound_not_style():
    """CHIMES must not be used to pick a colour."""
    import inspect

    for decision in db.CHIMES:
        assert db.colour_for(decision) == db.BACKGROUND
    source = inspect.getsource(db.colour_for)
    assert "CHIMES" not in source


# -- 1-5, 7, 8: which decision is current -------------------------------------

def test_a_first_decision_is_a_change():
    current = CurrentDecision()
    assert current.observe(scenario_rules.ANTE, 1) is True
    assert current.decision == scenario_rules.ANTE


def test_the_same_decision_repeated_is_not_a_change():
    """ANTE ANTE ANTE ANTE ANTE = one banner, drawn once."""
    current = CurrentDecision()
    assert current.observe(scenario_rules.ANTE, 1) is True
    for _ in range(20):
        assert current.observe(scenario_rules.ANTE, 1) is False
    assert current.decision == scenario_rules.ANTE


@pytest.mark.parametrize("first,second", [
    (scenario_rules.ANTE, se.PLAY),
    (se.PLAY, db.BONUS),
    (db.BONUS, se.WAIT),
    (se.WAIT, scenario_rules.SKIP),
])
def test_a_different_decision_replaces_the_previous_one(first, second):
    current = CurrentDecision()
    current.observe(first, 1)
    assert current.observe(second, 1) is True
    assert current.decision == second          # and only this one


def test_the_whole_sequence_replaces_one_at_a_time():
    """ANTE -> PLAY -> BONUS -> WAIT -> SKIP, one current decision throughout."""
    current = CurrentDecision()
    order = [scenario_rules.ANTE, se.PLAY, db.BONUS, se.WAIT, scenario_rules.SKIP]
    seen = []
    for decision in order:
        for _ in range(3):                     # polled repeatedly, as it really is
            current.observe(decision, 1)
        seen.append(current.decision)
    assert seen == order


# -- 9: a new round --------------------------------------------------------------

def test_a_new_round_drops_the_previous_decision():
    """previous round PLAY NOW, new round WAIT - never PLAY NOW still showing."""
    current = CurrentDecision()
    current.observe(se.PLAY, 1)
    assert current.observe(se.WAIT, 2) is True
    assert current.decision == se.WAIT


def test_a_new_round_with_no_decision_clears_the_old_one():
    current = CurrentDecision()
    current.observe(se.PLAY, 1)
    assert current.observe(None, 2) is True
    assert current.decision is None


def test_the_round_id_is_the_trackers_own():
    """Whatever the tracker calls the round is what this follows."""
    current = CurrentDecision()
    current.observe(se.PLAY, 41)
    assert current.round_id == 41
    current.observe(se.PLAY, 42)
    assert current.round_id == 42


# -- 10: nothing misleading -------------------------------------------------------

def test_no_decision_shows_nothing():
    current = CurrentDecision()
    assert current.observe(None, 1) is False
    assert current.decision is None


def test_an_unknown_decision_is_treated_as_none_not_kept():
    current = CurrentDecision()
    current.observe(scenario_rules.ANTE, 1)
    assert current.observe("SOMETHING_NEW", 1) is True
    assert current.decision is None            # not a stale ANTE


def test_reset_forgets_everything():
    current = CurrentDecision()
    current.observe(se.PLAY, 1)
    assert current.reset() is True
    assert current.decision is None and current.round_id is None


# -- the widget --------------------------------------------------------------

@pytest.fixture
def root():
    tk = pytest.importorskip("tkinter")
    try:
        window = tk.Tk()
    except tk.TclError as exc:                 # pragma: no cover - headless
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def banner(root):
    """A banner with the tracker running - which is when decisions appear.

    start() is required now: a banner that has not been told the tracker is
    running shows nothing, which is the whole point of the lifecycle. The
    tests below that are about the stopped state call stop() themselves.
    """
    made = db.DecisionBanner(root, sound=False)
    made.start()
    yield made
    made.destroy()


# -- 6, 11: one banner, one style, always underlined -----------------------------

def test_there_is_one_widget_not_one_per_decision(banner, root):
    """Every decision goes through the same label, so two cannot show at once."""
    shown = []
    for decision in (scenario_rules.ANTE, se.PLAY, db.BONUS, se.WAIT,
                     scenario_rules.SKIP):
        banner.show(decision, "", 1)
        root.update()
        shown.append((banner.title_label, banner.text))
    labels = {id(widget) for widget, _ in shown}
    assert len(labels) == 1                    # one widget throughout
    assert [text for _, text in shown] == [
        "ANTE NOW", "PLAY NOW", "BONUS NOW", "WAIT", "SKIP ROUND"]


@pytest.mark.parametrize("decision,expected", [
    (scenario_rules.ANTE, "ANTE NOW"),
    (se.PLAY, "PLAY NOW"),
    (db.BONUS, "BONUS NOW"),
    (se.WAIT, "WAIT"),
    (scenario_rules.SKIP, "SKIP ROUND"),
])
def test_every_decision_is_underlined_in_the_same_font(banner, root, decision,
                                                       expected):
    banner.show(decision, "", 1)
    root.update()
    assert banner.text == expected
    font = banner.title_label.cget("font")
    # The underline belongs to the font, so it cannot go missing for a longer
    # or shorter word, and it is the same font object every time.
    assert banner.title_font.cget("underline") == 1
    assert font is banner.title_font or str(font) == str(banner.title_font)
    assert banner.title_font.cget("size") == 30
    assert banner.title_font.cget("weight") == "bold"


def test_the_geometry_font_and_colour_do_not_change_between_decisions(banner, root):
    """Every real decision, drawn identically - only the words differ."""
    measured = []
    for decision in DECISION_LABELS:
        banner.show(decision, "", 1)
        root.update()
        measured.append((banner.window.geometry(),
                         str(banner.title_label.cget("font")),
                         banner.title_label.pack_info()["pady"],
                         str(banner.title_label.cget("background")),
                         str(banner.frame.cget("background")),
                         str(banner.title_label.cget("foreground"))))
    assert len(set(measured)) == 1             # size, font, spacing and colour
    assert measured[0][3] == db.BACKGROUND


# -- 7, 8 on the widget ----------------------------------------------------------

def test_showing_the_same_decision_again_does_nothing(banner, root):
    assert banner.show(scenario_rules.ANTE, "", 1) is True
    for _ in range(10):
        assert banner.show(scenario_rules.ANTE, "", 1) is False
    root.update()
    assert banner.text == "ANTE NOW" and banner.visible


def test_a_change_replaces_the_text_in_place(banner, root):
    banner.show(scenario_rules.ANTE, "", 1)
    root.update()
    before = banner.title_label
    assert banner.show(se.PLAY, "", 1) is True
    root.update()
    assert banner.title_label is before        # same widget, new words
    assert banner.text == "PLAY NOW"


def test_a_new_round_resets_what_is_displayed(banner, root):
    banner.show(se.PLAY, "", 1)
    root.update()
    assert banner.text == "PLAY NOW"
    banner.show(se.WAIT, "", 2)
    root.update()
    assert banner.text == "WAIT"


def test_no_decision_hides_the_banner(banner, root):
    banner.show(scenario_rules.ANTE, "", 1)
    root.update()
    assert banner.visible
    banner.show(None, "", 1)
    root.update()
    assert not banner.visible and banner.text == ""


def test_reset_clears_the_banner(banner, root):
    banner.show(scenario_rules.ANTE, "", 1)
    root.update()
    banner.reset()
    root.update()
    assert not banner.visible
    assert banner.show(scenario_rules.ANTE, "", 1) is True   # a change again


def test_the_banner_never_takes_focus(banner, root):
    banner.show(scenario_rules.ANTE, "", 1)
    root.update()
    assert bool(banner.window.attributes("-topmost")) is True
    assert root.grab_current() is None


# -- the lifecycle: only while the tracker runs -------------------------------
#
# The bug these exist for: stopping the tracker cleared the banner, and then a
# payload the tracker had already queued was drained and put it straight back
# - an always-on-top green banner reading DON'T PLAY, sitting over whatever
# the person did next, with the window saying STOPPED.

def test_a_new_banner_is_inactive_and_shows_nothing(root):
    """Hidden before the tracker starts."""
    made = db.DecisionBanner(root, sound=False)
    try:
        assert made.active is False
        assert made.show(se.PLAY, "", 1) is False
        root.update()
        assert not made.visible and made.text == ""
    finally:
        made.destroy()


def test_starting_leaves_it_hidden_until_a_decision_arrives(root):
    made = db.DecisionBanner(root, sound=False)
    try:
        made.start()
        root.update()
        assert made.active is True
        assert not made.visible                # running, but nothing to say yet
        made.show(se.PLAY, "", 1)
        root.update()
        assert made.visible and made.text == "PLAY NOW"
    finally:
        made.destroy()


def test_stopping_hides_the_banner_and_clears_the_decision(banner, root):
    banner.show(se.DONT_PLAY, "no pair", 7)
    root.update()
    assert banner.visible and banner.text == "DON'T PLAY"

    banner.stop()
    root.update()
    assert not banner.visible
    assert banner.text == ""
    assert banner.decision is None
    assert banner.current.decision is None
    assert banner.active is False


def test_a_payload_queued_before_the_stop_cannot_bring_it_back(banner, root):
    """The actual bug. The tracker's queue outlives the tracker thread."""
    banner.show(se.DONT_PLAY, "no pair", 7)
    root.update()
    banner.stop()
    root.update()

    for _ in range(5):                         # the backlog draining
        assert banner.show(se.DONT_PLAY, "no pair", 7) is False
    root.update()
    assert not banner.visible and banner.text == ""


def test_a_stopped_banner_leaves_no_window_on_screen(banner, root):
    """Withdrawn, not merely blanked - it is an always-on-top frame."""
    banner.show(se.PLAY, "", 1)
    root.update()
    assert banner.window.winfo_viewable()

    banner.stop()
    root.update()
    assert banner.window.state() == "withdrawn"
    assert not banner.window.winfo_viewable()


def test_restarting_does_not_restore_the_previous_decision(banner, root):
    banner.show(se.PLAY, "", 7)
    root.update()
    banner.stop()
    root.update()

    banner.start()                             # a new tracking session
    root.update()
    assert not banner.visible and banner.text == ""
    assert banner.decision is None


def test_a_new_decision_after_restarting_displays_normally(banner, root):
    banner.show(se.PLAY, "", 7)
    banner.stop()
    banner.start()
    assert banner.show(se.WAIT, "", 8) is True
    root.update()
    assert banner.visible and banner.text == "WAIT"


def test_stopping_twice_is_safe(banner, root):
    banner.show(se.PLAY, "", 1)
    banner.stop()
    banner.stop()
    banner.stop()
    root.update()
    assert not banner.visible and banner.active is False


def test_stopping_without_ever_starting_is_safe(root):
    made = db.DecisionBanner(root, sound=False)
    try:
        made.stop()
        made.stop()
        root.update()
        assert not made.visible
    finally:
        made.destroy()


def test_many_start_stop_cycles_leave_one_window_and_no_orphans(root):
    made = db.DecisionBanner(root, sound=False)
    try:
        window = made.window
        for index in range(10):
            made.start()
            made.show(se.PLAY, "", index)
            root.update()
            assert made.visible
            made.stop()
            root.update()
            assert not made.visible
            assert made.window is window       # the same one window throughout
        toplevels = [child for child in root.winfo_children()
                     if isinstance(child, tk.Toplevel)]
        assert len(toplevels) == 1
    finally:
        made.destroy()


def test_only_one_toplevel_exists_for_one_banner(root):
    before = [c for c in root.winfo_children() if isinstance(c, tk.Toplevel)]
    made = db.DecisionBanner(root, sound=False)
    try:
        after = [c for c in root.winfo_children() if isinstance(c, tk.Toplevel)]
        assert len(after) == len(before) + 1
    finally:
        made.destroy()
    root.update()
    final = [c for c in root.winfo_children() if isinstance(c, tk.Toplevel)]
    assert len(final) == len(before)           # destroy leaves nothing behind
