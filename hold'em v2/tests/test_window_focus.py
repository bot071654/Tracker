"""Pausing the tracker while the game window is not in front.

Nothing here needs Windows, a browser or a casino: the foreground window is a
function the gate is handed, so every test says what the screen looks like.

Three levels, because the feature has three parts:

    FocusGate           the state machine, on its own
    Tracker._run        the one gate, in front of _tick
    App                 the status line, and only the status line
"""

import queue
import subprocess
import sys
import time

import pytest

import window_focus as wf
from config.settings import DEFAULT_CONFIG
from tracker import Tracker

TARGET = "Casino Hold'em"
GAME = "Casino Hold'em Live - Google Chrome"
OTHER = "Slack | general"


def titles(*sequence):
    """A foreground-window reader that plays back a fixed list, then repeats."""
    seen = list(sequence)

    def read():
        return seen[0] if len(seen) == 1 else seen.pop(0)
    return read


# -- the state machine --------------------------------------------------------

def test_an_active_game_is_not_paused():
    gate = wf.FocusGate(TARGET, read_title=titles(GAME))
    assert gate.observe() == wf.GAME_ACTIVE
    assert gate.paused is False


def test_an_inactive_game_is_paused():
    """A single non-match is absorbed; committing it takes a second one."""
    gate = wf.FocusGate(TARGET, read_title=titles(OTHER))
    assert gate.observe() == wf.GAME_ACTIVE     # first non-match: absorbed
    assert gate.observe() == wf.GAME_INACTIVE   # second: committed
    assert gate.paused is True


def test_a_single_non_match_does_not_leave_active():
    """Item 1: exactly what the debounce exists to absorb."""
    gate = wf.FocusGate(TARGET, read_title=titles(OTHER, GAME))
    assert gate.observe() == wf.GAME_ACTIVE
    assert gate.paused is False


def test_two_consecutive_non_matches_commit_inactive():
    """Item 2, spelled out on its own."""
    gate = wf.FocusGate(TARGET, read_title=titles(OTHER))
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE
    assert gate.paused is True


def test_a_match_in_between_resets_the_streak():
    """Item 3's exact example: non-match, match, non-match, match.

    Two non-matches only count against each other if nothing matching
    happened in between - this sequence never has two IN A ROW, so it must
    stay GAME_ACTIVE for every single one of these four observations.
    """
    gate = wf.FocusGate(TARGET, read_title=titles(OTHER, GAME, OTHER, GAME))
    for _ in range(4):
        gate.observe()
        assert gate.paused is False, "two non-consecutive non-matches were counted together"


def test_the_match_is_partial_and_ignores_case():
    gate = wf.FocusGate("casino hold'em", read_title=titles(GAME))
    assert gate.observe() == wf.GAME_ACTIVE


def test_repeated_inactive_looks_report_once():
    """Item 4: a window left in the background must not churn events."""
    gate = wf.FocusGate(TARGET, read_title=titles(OTHER))
    gate.observe()                               # absorbed transient
    assert gate.observe() == wf.GAME_INACTIVE    # committed
    assert [gate.observe() for _ in range(50)] == [None] * 50
    assert gate.paused is True


def test_returning_reports_once():
    """Item 5: recovery is immediate and reports exactly once, then goes quiet."""
    gate = wf.FocusGate(
        TARGET, read_title=titles(OTHER, OTHER, OTHER, GAME, GAME))
    assert gate.observe() == wf.GAME_ACTIVE      # 1st non-match: absorbed
    assert gate.observe() == wf.GAME_INACTIVE    # 2nd non-match: committed
    assert gate.observe() is None                # still inactive: silent
    assert gate.observe() == wf.GAME_ACTIVE      # a match recovers at once
    assert gate.observe() is None                # still active: silent


def test_a_gate_with_no_target_never_pauses_and_says_nothing():
    """The shipped default. Without a target the tracker behaves as before.

    It reports nothing at all, so the window puts no claim on screen about a
    check that is not happening.
    """
    gate = wf.FocusGate("", read_title=titles(OTHER))
    assert gate.enabled is False
    assert [gate.observe() for _ in range(5)] == [None] * 5
    assert gate.paused is False


def test_a_platform_that_cannot_tell_never_pauses():
    """macOS and Linux: foreground_title() is None, which is not "inactive"."""
    gate = wf.FocusGate(TARGET, read_title=lambda: None)
    assert gate.observe() == wf.GAME_ACTIVE
    assert gate.paused is False


def test_a_reader_that_raises_does_not_stop_the_tracker(monkeypatch):
    def explode():
        raise OSError("user32 said no")

    monkeypatch.setattr(wf, "_user32", lambda: None)
    gate = wf.FocusGate(TARGET, read_title=wf.foreground_title)
    assert gate.observe() == wf.GAME_ACTIVE
    assert explode  # the real reader is guarded; see foreground_title


def test_foreground_title_is_none_off_windows(monkeypatch):
    monkeypatch.setattr(wf, "_user32", lambda: None)
    assert wf.foreground_title() is None


# -- item 6: the real startup-order regression --------------------------------
#
# Reproduced from the real diagnostic: creating the tracker's own Tk window
# is, on its own, enough for Windows to report it as the foreground window
# for one instant - even started with the real poker window already active.
# The real strings from the physical-machine probe, not placeholders.

POKER_REAL = "Casino - Google Chrome"
CHATGPT_REAL = "Compare Playwright PyAutoGUI - Google Chrome"
OTHER_TAB_REAL = "Gmail - Google Chrome"


def test_the_startup_order_regression_settles_active_without_a_click():
    """Poker active -> the tracker's own window steals focus once -> poker again.

    Must settle at GAME_ACTIVE without the person doing anything at all.
    """
    gate = wf.FocusGate(
        POKER_REAL, read_title=titles("Poker Hand Tracker", POKER_REAL))
    gate.observe()                               # sees its own window once
    gate.observe()                               # sees the real game
    assert gate.state == wf.GAME_ACTIVE
    assert gate.paused is False


def test_a_genuine_pause_still_reaches_inactive_with_real_titles():
    """Poker active -> ChatGPT -> ChatGPT again must genuinely pause."""
    gate = wf.FocusGate(
        POKER_REAL, read_title=titles(POKER_REAL, CHATGPT_REAL, CHATGPT_REAL))
    gate.observe()                               # active
    gate.observe()                               # first non-match: absorbed
    assert gate.observe() == wf.GAME_INACTIVE    # second: committed
    assert gate.paused is True


def test_another_real_chrome_tab_also_pauses():
    gate = wf.FocusGate(
        POKER_REAL, read_title=titles(POKER_REAL, OTHER_TAB_REAL, OTHER_TAB_REAL))
    gate.observe()
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE


# -- the real bug: the tracker's own window holding the foreground longer
# than one poll, because the game was already open before it was started ----
#
# test_the_startup_order_regression_settles_active_without_a_click (above)
# only covers a steal lasting exactly one poll - the two-in-a-row debounce
# was already enough for that. The reported bug is the ordinary case: Start
# Tracker is a click, the window it is clicked in genuinely holds the
# foreground until the person's attention (and the foreground window) moves
# back to the game, and that can easily take longer than one 0.2s poll. The
# fix is FocusGate's own-window grace period, not the older two-poll one.

def fake_clock(*ticks):
    """A clock that returns each value in turn, then repeats the last one."""
    values = list(ticks)

    def read():
        return values[0] if len(values) == 1 else values.pop(0)
    return read


def test_the_tracker_window_can_hold_the_foreground_for_several_polls():
    """Game first, tracker second: several polls before the click back."""
    gate = wf.FocusGate(POKER_REAL, read_title=titles(
        "Poker Hand Tracker", "Poker Hand Tracker", "Poker Hand Tracker",
        "Poker Hand Tracker", "Poker Hand Tracker", POKER_REAL))
    for _ in range(5):
        gate.observe()
        assert gate.paused is False, "paused on the tracker's own window"
    gate.observe()                              # the click back to the game
    assert gate.state == wf.GAME_ACTIVE
    assert gate.paused is False


def test_the_grace_period_expires_if_the_game_is_never_returned_to():
    """The own-window allowance is not permission to ignore it forever.

    If the tracker's own window is still in front once the grace period has
    passed, that is exactly what leaving the game to do something else looks
    like - own window or not - and it must still pause.
    """
    clock = fake_clock(0.0, 0.0, 10.0, 10.2, 10.4)
    gate = wf.FocusGate(
        POKER_REAL, read_title=titles("Poker Hand Tracker"), clock=clock,
        grace_seconds=3.0)
    gate.observe()                              # t=0.0: within the grace period
    assert gate.paused is False
    gate.observe()                              # t=10.0: grace period long over
    assert gate.observe() == wf.GAME_INACTIVE   # t=10.4: second real non-match


def test_a_genuinely_different_application_still_pauses_within_the_grace_period():
    """The grace period is specific to the tracker's own window, not a
    blanket pass on every non-match for the first few seconds.
    """
    gate = wf.FocusGate(
        POKER_REAL, read_title=titles(POKER_REAL, CHATGPT_REAL, CHATGPT_REAL),
        grace_seconds=30.0)
    gate.observe()
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE


def test_tracker_started_before_the_game_still_settles_paused():
    """Tracker first, game later: the own-window grace period must not make
    a tracker that has never seen the game claim it is active.
    """
    clock = fake_clock(100.0, 100.2, 100.4)
    gate = wf.FocusGate(
        POKER_REAL, read_title=titles(OTHER_TAB_REAL, OTHER_TAB_REAL), clock=clock)
    gate.observe()
    assert gate.observe() == wf.GAME_INACTIVE
    assert gate.paused is True


def test_the_game_is_recognised_the_instant_it_is_returned_to_during_the_grace_period():
    gate = wf.FocusGate(POKER_REAL, read_title=titles(
        "Poker Hand Tracker", POKER_REAL, "Poker Hand Tracker"))
    gate.observe()
    assert gate.observe() == wf.GAME_ACTIVE
    gate.observe()                              # back to the tracker's own window
    assert gate.paused is False, "one more look at our own window must not pause"


# -- the gate in the tracker's loop -------------------------------------------

@pytest.fixture
def tracker():
    config = dict(DEFAULT_CONFIG)
    config["poll_interval_seconds"] = 0
    config["game_window_title"] = TARGET
    made = Tracker(config, queue.Queue())
    made._check_resolution = lambda: None
    return made


def run_polls(tracker, count):
    """Run Tracker._run for exactly `count` polls, without sleeping."""
    left = {"n": count}

    def wait(_timeout=None):
        left["n"] -= 1
        if left["n"] <= 0:
            tracker._stop.set()
        return False

    tracker._stop.wait = wait
    tracker._run()


def recording_tick(tracker, calls):
    """A _tick that does what the real one does to the state these tests watch."""
    def tick():
        calls.append(time.time())
        tracker.memory.generation += 1
        tracker._emit("update", {"round_id": tracker.memory.generation})
        tracker._emit("saved", {"id": len(calls)})
    return tick


def drain(tracker):
    out = []
    while not tracker.events.empty():
        out.append(tracker.events.get_nowait())
    return out


def test_recognition_runs_while_the_game_is_active(tracker):
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(GAME))
    calls = []
    tracker._tick = recording_tick(tracker, calls)
    run_polls(tracker, 5)
    assert len(calls) == 5


def reach_confirmed_inactive(tracker):
    """Run exactly the polls needed to commit GAME_INACTIVE, then stop there.

    The debounce means the FIRST poll of any active->inactive transition
    still runs _tick() once - that one grace poll is item 7's own words,
    "remain ACTIVE temporarily", and is covered on its own below. Everything
    past this point in a test is the STEADY, confirmed pause, which is what
    the tests after this helper are actually about.
    """
    run_polls(tracker, 2)
    tracker._stop.clear()


def test_the_one_grace_poll_still_runs_recognition_once(tracker):
    """The debounce's one approved cost: item 7, "remain ACTIVE temporarily"."""
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(OTHER))
    calls = []
    tracker._tick = recording_tick(tracker, calls)
    run_polls(tracker, 1)
    assert len(calls) == 1, "the one absorbed poll did not run recognition"
    assert tracker.focus.paused is False


def test_recognition_is_skipped_once_the_pause_is_confirmed(tracker):
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(OTHER))
    calls = []
    tracker._tick = recording_tick(tracker, calls)
    reach_confirmed_inactive(tracker)
    after_confirmed = len(calls)
    run_polls(tracker, 5)
    assert len(calls) == after_confirmed, (
        "the screen was read again after the pause was confirmed")


def test_the_pause_does_not_advance_card_memory_once_confirmed(tracker):
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(OTHER))
    tracker._tick = recording_tick(tracker, [])
    reach_confirmed_inactive(tracker)
    before = tracker.memory.generation
    run_polls(tracker, 10)
    assert tracker.memory.generation == before


def test_the_pause_emits_no_update_so_nothing_can_decide(tracker):
    """No update means no scenario decision, no banner and no voice."""
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(OTHER))
    tracker._tick = recording_tick(tracker, [])
    reach_confirmed_inactive(tracker)
    drain(tracker)                               # discard the grace poll's own update
    run_polls(tracker, 10)
    kinds = [kind for kind, _ in drain(tracker)]
    assert "update" not in kinds


def test_the_pause_writes_no_hand(tracker):
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(OTHER))
    tracker._tick = recording_tick(tracker, [])
    reach_confirmed_inactive(tracker)
    drain(tracker)
    run_polls(tracker, 10)
    kinds = [kind for kind, _ in drain(tracker)]
    assert "saved" not in kinds


def test_repeated_inactive_polls_emit_one_focus_event(tracker):
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(OTHER))
    tracker._tick = recording_tick(tracker, [])
    run_polls(tracker, 20)
    focus = [payload for kind, payload in drain(tracker) if kind == "focus"]
    # Two events, not one, and this is the debounce working as intended: the
    # gate starts optimistic (GAME_ACTIVE) and nothing has been reported yet,
    # so the FIRST poll's absorbed non-match still reports that starting
    # state once - the same mechanism that lets the startup regression settle
    # on ACTIVE without ever announcing PAUSED. The SECOND poll is the real
    # commit. Eighteen more polls after that report nothing further.
    assert focus == [wf.GAME_ACTIVE, wf.GAME_INACTIVE]


def test_leaving_and_returning_emits_one_event_each_way(tracker):
    tracker.focus = wf.FocusGate(
        TARGET, read_title=titles(GAME, GAME, OTHER, OTHER, OTHER, GAME, GAME))
    calls = []
    tracker._tick = recording_tick(tracker, calls)
    run_polls(tracker, 7)
    focus = [payload for kind, payload in drain(tracker) if kind == "focus"]
    assert focus == [wf.GAME_ACTIVE, wf.GAME_INACTIVE, wf.GAME_ACTIVE]
    # Five active polls read the screen, not four: the first of the three
    # non-matching polls is the debounce's one absorbed poll, still counted
    # as active. Only the next two are the confirmed, genuine pause.
    assert len(calls) == 5


def test_returning_to_the_game_does_not_invent_a_round(tracker):
    """The pause is not a new deal: memory picks up where it left off.

    Staged around the debounce rather than through it: one poll to
    establish a baseline while active, two to reach a CONFIRMED pause (the
    first of those two still runs - see test_the_one_grace_poll_... above -
    so the baseline is taken after both, not after the first alone), one
    more confirmed-inactive poll to show nothing further moves, then one
    poll back to prove the resume advances it exactly once.
    """
    tracker.focus = wf.FocusGate(
        TARGET, read_title=titles(GAME, OTHER, OTHER, OTHER, GAME))
    tracker._tick = recording_tick(tracker, [])
    run_polls(tracker, 3)                       # active, then confirmed inactive
    tracker._stop.clear()
    baseline = tracker.memory.generation
    run_polls(tracker, 1)                       # still away, already confirmed
    assert tracker.memory.generation == baseline
    tracker._stop.clear()
    run_polls(tracker, 1)                       # back
    assert tracker.memory.generation == baseline + 1


def test_a_tracker_started_while_the_game_is_inactive_settles_paused(tracker):
    """Item 10: Start with the game behind something else ends up paused.

    Not "starts paused" - the first poll is the debounce's one approved
    grace poll (see test_the_one_grace_poll_... above) - but by the third
    poll, with the game never actually there, it has settled into it.
    """
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(OTHER))
    calls = []
    tracker._tick = recording_tick(tracker, calls)
    run_polls(tracker, 3)
    assert len(calls) == 1, "recognition ran past the one approved grace poll"
    assert tracker.focus.paused is True
    assert [p for k, p in drain(tracker) if k == "focus"] == [
        wf.GAME_ACTIVE, wf.GAME_INACTIVE]


def test_a_tracker_started_while_the_game_is_active_starts_active(tracker):
    tracker.focus = wf.FocusGate(TARGET, read_title=titles(GAME))
    calls = []
    tracker._tick = recording_tick(tracker, calls)
    run_polls(tracker, 3)
    assert len(calls) == 3
    assert [p for k, p in drain(tracker) if k == "focus"] == [wf.GAME_ACTIVE]


def test_without_a_target_the_loop_is_unchanged(tracker):
    """The shipped default must not alter the tracker at all."""
    tracker.focus = wf.FocusGate("", read_title=titles(OTHER))
    calls = []
    tracker._tick = recording_tick(tracker, calls)
    run_polls(tracker, 4)
    assert len(calls) == 4


# -- the status line, and only the status line --------------------------------

tk = pytest.importorskip("tkinter")


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:                  # pragma: no cover - headless
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def application(root, monkeypatch):
    """The real window, with the database, screen reading and voice stubbed."""
    import app as app_module
    import tracker as tracker_module
    from voice import engines

    monkeypatch.setattr(engines, "Pyttsx3Engine",
                        lambda *_a, **_k: engines.NullEngine())
    monkeypatch.setattr(app_module.App, "_startup_checks", lambda self: None)
    monkeypatch.setattr(app_module.App, "_refresh_statistics", lambda self: None)
    monkeypatch.setattr(app_module.db, "check_connection",
                        lambda *_a, **_k: (True, "stubbed"))
    monkeypatch.setattr(app_module.messagebox, "askyesno",
                        lambda *_a, **_k: pytest.fail("a modal was opened"))
    monkeypatch.setattr(tracker_module.Tracker, "start", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "stop", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "is_running",
                        lambda self: getattr(self, "_fake", False))

    made = app_module.App(root)
    root.update()
    try:
        yield made
    finally:
        made.tracker._fake = False
        made.on_close()


def begin(application):
    application.start()
    application.tracker._fake = True
    application.root.update()


def focus(application, state):
    """Deliver a focus event the way the tracker thread does."""
    application.events.put(("focus", state))
    application._poll_events()
    application.root.update()


SETTLED = ["player_1", "player_2", "flop_1", "flop_2", "flop_3"]
CARDS = {"player_1": "AS", "player_2": "7D",
         "flop_1": "KC", "flop_2": "7C", "flop_3": "2H"}


def deliver(application, decision, round_id=1):
    """A tracker update carrying a decision, drained by the real poll loop."""
    from poker import scenario_engine as se

    application.events.put(("update", {
        "state": "FLOP", "round_id": round_id, "cards": CARDS,
        "seen": dict(CARDS), "reads": {}, "held": {}, "panels": {},
        "diagnostics": {}, "dealer": {}, "action": None,
        "statuses": {slot: "CONFIRMED" for slot in SETTLED},
        "timing": {}, "emitted_at": time.time(), "uncertain": [],
        "scenario": {"decision": decision, "reason": "FLOP_PAIR",
                     "round_id": round_id}}))
    application._poll_events()
    application.root.update()
    return se


# -- the banner must not be left over another application ---------------------

def test_going_inactive_takes_the_decision_banner_down(application):
    from poker import scenario_engine as se

    begin(application)
    deliver(application, se.PLAY)
    banner = application.decision_banner
    assert banner.visible and banner.text == "PLAY NOW"

    focus(application, wf.GAME_INACTIVE)
    assert not banner.visible, "a decision was left over another application"
    assert not banner.window.winfo_viewable()
    assert banner.text == ""
    assert banner.window.state() == "withdrawn"


def test_an_old_decision_cannot_come_back_while_inactive(application):
    """Frames queued before the pause must not put it up again."""
    from poker import scenario_engine as se

    begin(application)
    deliver(application, se.PLAY)
    focus(application, wf.GAME_INACTIVE)
    deliver(application, se.PLAY)                   # a straggler from before
    assert not application.decision_banner.visible


def test_returning_restores_the_lifecycle_without_inventing_a_decision(application):
    """The banner comes back empty and waits: coming back is not a decision."""
    from poker import scenario_engine as se

    begin(application)
    deliver(application, se.PLAY)
    focus(application, wf.GAME_INACTIVE)
    focus(application, wf.GAME_ACTIVE)

    banner = application.decision_banner
    assert banner.active is True, "the banner would refuse the next decision"
    assert not banner.visible, "returning to the game invented a decision"
    assert banner.text == ""

    deliver(application, se.DONT_PLAY, round_id=2)   # the session's next one
    assert banner.visible and banner.text == "DON'T PLAY"


# -- the two status lines -----------------------------------------------------

def test_the_paused_line_persists_for_the_whole_inactive_period(application):
    begin(application)
    focus(application, wf.GAME_INACTIVE)
    for _ in range(10):                             # the poll loop keeps running
        application._poll_events()
        application.root.update()
    assert application.focus_var.get() == "TRACKING PAUSED — GAME NOT ACTIVE"
    assert str(application.focus_label.cget("foreground")) == "#777777"
    assert application.focus_label.winfo_ismapped()


def test_the_active_line_persists_through_normal_frames(application):
    """The point of a second line: the running status does not overwrite it."""
    from poker import scenario_engine as se

    begin(application)
    focus(application, wf.GAME_ACTIVE)
    deliver(application, se.PLAY)
    deliver(application, se.PLAY, round_id=2)

    assert application.focus_var.get() == "TRACKING ACTIVE — GAME ACTIVE"
    # ...and the existing status line still carries the round information.
    assert application.status_var.get() == "Status: RUNNING - FLOP"


def test_the_frozen_status_line_is_greyed_while_paused_and_restored_after(application):
    from poker import scenario_engine as se

    begin(application)
    deliver(application, se.PLAY)
    focus(application, wf.GAME_INACTIVE)
    assert str(application.status_label.cget("foreground")) == "#777777"
    focus(application, wf.GAME_ACTIVE)
    assert str(application.status_label.cget("foreground")) == ""


def test_no_line_is_shown_when_nothing_is_being_watched(application):
    """With no game_window_title the tracker never reports, so nothing shows."""
    begin(application)
    assert application.focus_var.get() == ""
    assert not application.focus_label.winfo_ismapped()


# -- Stop is authoritative ----------------------------------------------------

def test_stopping_while_paused_leaves_the_window_reading_stopped(application):
    import app as app_module

    begin(application)
    focus(application, wf.GAME_INACTIVE)
    application.tracker._fake = False
    application.stop()
    application.root.update()
    assert application.status_var.get() == "Status: STOPPED"
    # Not stuck PAUSED_GREY from the pause just before stop() - STOPPED gets
    # its own colour now (app_module.STOPPED_RED), never the paused one.
    assert str(application.status_label.cget("foreground")) == app_module.STOPPED_RED
    assert application.focus_var.get() == ""
    assert not application.focus_label.winfo_ismapped()


def test_a_focus_event_after_stop_cannot_revive_the_tracker(application):
    """Stopped + game active = still stopped."""
    begin(application)
    application.tracker._fake = False
    application.stop()
    application.root.update()
    focus(application, wf.GAME_ACTIVE)
    assert application.status_var.get() == "Status: STOPPED"
    assert application.tracking is False
    assert application.focus_var.get() == ""


def test_a_focus_event_after_stop_cannot_put_the_banner_back(application):
    from poker import scenario_engine as se

    begin(application)
    deliver(application, se.PLAY)
    application.tracker._fake = False
    application.stop()
    application.root.update()
    focus(application, wf.GAME_ACTIVE)
    assert not application.decision_banner.visible
    assert application.decision_banner.active is False


def test_the_pause_says_nothing(application):
    """Focus changes are not announced."""
    begin(application)
    focus(application, wf.GAME_INACTIVE)
    focus(application, wf.GAME_ACTIVE)
    assert application.announcer.pending() == 0
    assert application._spoken_decision is None


# -- diagnosability: the exact root cause of the real-world failure -----------
#
# The code path that takes the banner down was correct the whole time. What
# was missing was that config["game_window_title"] is "" in the shipped
# config.json, so FocusGate was never enabled, and the fix nobody could see
# was: self.config is loaded once at App.__init__ and never re-read, so even
# setting the title and pressing Stop then Start would not have picked it up
# without relaunching the whole application.

def test_starting_reloads_the_game_window_title_from_disk(application, monkeypatch):
    """Editing config.json and pressing Start must not need a relaunch.

    Not asserting anything about what App.__init__ happened to load - the
    shipped config.json now genuinely has a title in it, so that value is not
    a fixed thing this test can assume. What is being proved is the RELOAD:
    whatever was there before, a fresh call to start() picks up whatever
    load_config() returns right now, not whatever it returned earlier.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "load_config",
                        lambda: {"game_window_title": ""})
    application.config["game_window_title"] = ""     # simulate the old value
    monkeypatch.setattr(app_module, "load_config",
                        lambda: {"game_window_title": TARGET})
    application.start()
    application.root.update()
    assert application.config["game_window_title"] == TARGET
    assert application.tracker.focus.target == TARGET


def test_starting_picks_up_the_real_shipped_config_file(root, monkeypatch):
    """End to end, with no mocking of load_config at all: the actual file.

    This is the one test that answers "does config/config.json's real,
    on-disk value actually reach a real App instance" - every other test
    proves the mechanism with a value it supplies itself.
    """
    import app as app_module
    import tracker as tracker_module
    from voice import engines

    monkeypatch.setattr(engines, "Pyttsx3Engine",
                        lambda *_a, **_k: engines.NullEngine())
    monkeypatch.setattr(app_module.App, "_startup_checks", lambda self: None)
    monkeypatch.setattr(app_module.App, "_refresh_statistics", lambda self: None)
    monkeypatch.setattr(app_module.db, "check_connection",
                        lambda *_a, **_k: (True, "stubbed"))
    monkeypatch.setattr(app_module.messagebox, "askyesno",
                        lambda *_a, **_k: pytest.fail("a modal was opened"))
    monkeypatch.setattr(tracker_module.Tracker, "start", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "stop", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "is_running",
                        lambda self: getattr(self, "_fake", False))

    made = app_module.App(root)
    try:
        made.start()
        root.update()
        real_config = app_module.load_config()
        assert made.config["game_window_title"] == real_config["game_window_title"]
        assert made.tracker.focus.target == real_config["game_window_title"]
    finally:
        made.tracker._fake = False
        made.on_close()


def test_starting_with_no_configured_title_behaves_as_before(application,
                                                              monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "load_config",
                        lambda: {"game_window_title": ""})
    application.start()
    application.root.update()
    assert application.tracker.focus.enabled is False


def test_the_focus_log_line_has_every_required_field(caplog):
    """Checked on the SECOND poll - the confirmed commit - not the first.

    The first poll's "matched" is legitimately False while its "state" is
    legitimately GAME_ACTIVE (the absorbed transient); this line's whole
    diagnostic purpose is to show that distinction accurately rather than
    obscure it, so it is checked against the poll where they actually agree.
    """
    import logging

    gate = wf.FocusGate(TARGET, read_title=titles(OTHER))
    gate.observe()                                # absorbed transient
    with caplog.at_level(logging.INFO, logger="window_focus"):
        gate.observe()                            # the confirmed commit
    lines = [r.message for r in caplog.records if "[FOCUS]" in r.getMessage()]
    assert len(lines) == 1
    message = caplog.records[0].getMessage()
    assert "foreground_title=" in message
    assert "configured_game_window_title=" in message
    assert "matched=" in message
    assert "state=" in message
    assert repr(TARGET) in message
    assert repr(OTHER) in message
    assert "matched=False" in message
    assert "state=GAME_INACTIVE" in message


def test_the_first_polls_log_line_shows_the_raw_look_honestly(caplog):
    """The absorbed poll: matched=False and state=GAME_ACTIVE, at once.

    Logging state alone here would say "matched=True", which is a lie about
    what was actually seen - see the fix in observe()'s log call.
    """
    import logging

    gate = wf.FocusGate(TARGET, read_title=titles(OTHER))
    with caplog.at_level(logging.INFO, logger="window_focus"):
        gate.observe()
    message = caplog.records[0].getMessage()
    assert "matched=False" in message
    assert "state=GAME_ACTIVE" in message


def test_repeated_polling_produces_no_extra_focus_log_lines(caplog):
    """The one thing item 4 and the diagnostic requirement both forbid.

    Two real events are expected here, not zero: the absorbed transient
    (reported once, since nothing had been reported yet) and the confirmed
    commit. What must not happen is a THIRD line from any of the 48 polls
    after that, with nothing left to change.
    """
    import logging

    gate = wf.FocusGate(TARGET, read_title=titles(OTHER))
    with caplog.at_level(logging.INFO, logger="window_focus"):
        gate.observe()                            # absorbed transient: 1 line
        gate.observe()                            # confirmed commit: 1 line
        for _ in range(48):
            gate.observe()
    lines = [r for r in caplog.records if "[FOCUS]" in r.getMessage()]
    assert len(lines) == 2, "a settled state logged again with nothing new to say"


def test_a_disabled_gate_logs_nothing(caplog):
    """No target configured -> nothing is being checked -> nothing is logged."""
    import logging

    gate = wf.FocusGate("", read_title=titles(OTHER))
    with caplog.at_level(logging.INFO, logger="window_focus"):
        for _ in range(10):
            gate.observe()
    assert not [r for r in caplog.records if "[FOCUS]" in r.getMessage()]


# -- one-time startup focus restoration ---------------------------------------
#
# capture_foreground_window() / restore_foreground_window() are deliberately
# separate from FocusGate: FocusGate observes, on every poll, for as long as
# the tracker runs; these two do one thing, once, at startup, and correct a
# problem FocusGate cannot see coming - the tracker's own window becoming
# the foreground window the instant Tk actually maps it, which happens even
# with nothing else in this application's own code involved.

def test_a_valid_previous_hwnd_is_handed_the_foreground(monkeypatch):
    calls = []

    class FakeUser32:
        def __init__(self):
            def SetForegroundWindow(hwnd):
                calls.append(hwnd)
                return 1
            self.SetForegroundWindow = SetForegroundWindow

    monkeypatch.setattr(wf, "_user32", lambda: FakeUser32())
    ok = wf.restore_foreground_window(12345)
    assert ok is True
    # Passed through ctypes.c_void_p, exactly as the real user32 call needs.
    assert [getattr(c, "value", c) for c in calls] == [12345]


def test_no_previous_hwnd_does_nothing_safely(monkeypatch):
    class FakeUser32:
        def __init__(self):
            def SetForegroundWindow(hwnd):           # pragma: no cover
                raise AssertionError("should never be called with no target")
            self.SetForegroundWindow = SetForegroundWindow

    monkeypatch.setattr(wf, "_user32", lambda: FakeUser32())
    assert wf.restore_foreground_window(None) is False
    assert wf.restore_foreground_window(0) is False


def test_a_failed_restore_does_not_raise(monkeypatch):
    class FakeUser32:
        def __init__(self):
            def SetForegroundWindow(hwnd):
                return 0                             # Windows refused it
            self.SetForegroundWindow = SetForegroundWindow

    monkeypatch.setattr(wf, "_user32", lambda: FakeUser32())
    assert wf.restore_foreground_window(999) is False   # no exception


def test_a_raising_user32_does_not_crash_the_application(monkeypatch):
    class FakeUser32:
        def __init__(self):
            def SetForegroundWindow(hwnd):
                raise OSError("user32 said no")
            self.SetForegroundWindow = SetForegroundWindow

    monkeypatch.setattr(wf, "_user32", lambda: FakeUser32())
    assert wf.restore_foreground_window(999) is False   # no exception escapes


def test_non_windows_capture_and_restore_are_both_no_ops(monkeypatch):
    monkeypatch.setattr(wf, "_user32", lambda: None)
    assert wf.capture_foreground_window() is None
    assert wf.restore_foreground_window(12345) is False     # never touches user32
    assert wf.restore_foreground_window(None) is False


def test_capture_reads_the_real_foreground_window(monkeypatch):
    monkeypatch.setattr(wf, "foreground_title", lambda: "irrelevant")

    class FakeUser32:
        def __init__(self):
            def GetForegroundWindow():
                return 424242
            self.GetForegroundWindow = GetForegroundWindow

    fake = FakeUser32()
    monkeypatch.setattr(wf, "_user32", lambda: fake)
    assert wf.capture_foreground_window() == 424242


def test_capture_returns_none_when_nothing_is_in_front(monkeypatch):
    class FakeUser32:
        def __init__(self):
            def GetForegroundWindow():
                return 0
            self.GetForegroundWindow = GetForegroundWindow

    monkeypatch.setattr(wf, "_user32", lambda: FakeUser32())
    assert wf.capture_foreground_window() is None


# -- the real startup-order regression, end to end -----------------------------
#
# A real, separate OS window, a real ctypes/user32 check, and the ACTUAL
# app.main() function - not a copy of its lines, which is exactly the kind of
# drift that let an earlier attempt at this fix look correct while it
# silently did nothing.
#
# app.main() itself runs in a genuinely FRESH, SEPARATE subprocess, not
# inside this test process. Confirmed directly: run in-process, this test
# passes even against the ORIGINAL, BROKEN placement of the fix, because by
# the time it runs, dozens of earlier tests in this same file have already
# created their own real tk.Tk() windows - and Windows only ever grants a
# brand-new PROCESS's very first window automatic activation. Once that
# process has already made one, it does not happen again, fix or no fix, so
# an in-process test of this specific bug cannot tell the two apart. A fresh
# `python -c ...` subprocess is the only way to genuinely be that first
# window, matching what a real `python app.py` launch actually is.

DUMMY_WINDOW_SOURCE = '''
import sys, tkinter as tk
root = tk.Tk()
root.title(sys.argv[1])
root.geometry("300x120+40+40")
tk.Label(root, text=sys.argv[1], wraplength=280).pack(padx=10, pady=10, expand=True)
root.after(int(float(sys.argv[2]) * 1000), root.destroy)
root.mainloop()
'''

# Run in its own fresh interpreter by run_app_main_once below - this is what
# makes app.main() genuinely that process's first-ever Tk window.
RUN_APP_MAIN_ONCE_SOURCE = '''
import sys
sys.path.insert(0, %(root)r)
import tkinter as tk
import window_focus as wf
import app as app_module

captured = {}
def stop_immediately(self, *_a, **_k):
    captured["root"] = self
    self.update()
    for after_id in self.tk.call("after", "info"):
        self.after_cancel(after_id)

tk.Tk.mainloop = stop_immediately
app_module.main()
print(wf.foreground_title())
root = captured.get("root")
if root is not None:
    try:
        root.destroy()
    except tk.TclError:
        pass
'''

POKER_TITLE = "Casino - Google Chrome"


@pytest.fixture
def a_real_other_window(tmp_path):
    """A genuinely separate, real OS top-level window with this exact title.

    Skips rather than fails where there is no real desktop to test against -
    this needs actual OS-level foreground behaviour, not a headless display.
    """
    if wf._user32() is None:
        pytest.skip("no Windows foreground-window API on this platform")
    script = tmp_path / "dummy_window.py"
    script.write_text(DUMMY_WINDOW_SOURCE, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), POKER_TITLE, "15"])
    time.sleep(1.0)
    if wf.foreground_title() != POKER_TITLE:
        proc.terminate()
        pytest.skip("could not get the dummy window into the foreground here")
    try:
        yield POKER_TITLE
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:                            # noqa: BLE001
            pass


def test_the_startup_order_regression_is_fixed_end_to_end(a_real_other_window,
                                                           tmp_path):
    """Poker already active -> launch the tracker, in a FRESH process ->
    poker stays foreground.

    Must be a fresh subprocess, not a call to app.main() from inside this
    test process - see the note above this section for why an in-process
    call cannot actually exercise this bug.
    """
    import os

    # window_focus.py lives at the repo root itself, one dirname away.
    repo_root = os.path.dirname(os.path.abspath(wf.__file__))
    script = tmp_path / "run_app_main_once.py"
    script.write_text(RUN_APP_MAIN_ONCE_SOURCE % {"root": repo_root},
                      encoding="utf-8")
    result = subprocess.run([sys.executable, str(script)],
                            cwd=repo_root, capture_output=True, text=True,
                            timeout=30)
    reported = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert reported == a_real_other_window, (
        "the tracker's own window was left as the foreground window "
        "(reported=%r, stderr=%s)" % (reported, result.stderr[-2000:]))


def test_starting_with_no_meaningful_previous_window_still_starts(monkeypatch):
    """Item 6: nothing worth restoring to must not stop the tracker starting."""
    monkeypatch.setattr(wf, "capture_foreground_window", lambda: None)
    restored = []
    monkeypatch.setattr(wf, "restore_foreground_window",
                        lambda hwnd: restored.append(hwnd))

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("no display available: %s" % exc)
    try:
        # The same two lines main() runs, with nothing to restore to.
        root.update_idletasks()
        wf.restore_foreground_window(wf.capture_foreground_window())
        assert restored == [None]
    finally:
        root.destroy()


# -- the stale-decision race: a queued update outlasting its own pause --------
#
# Real physical-machine evidence: a decision genuinely computed while active
# sat in the queue long enough that, by the time _handle_event actually
# processed it, the tracker's own live FocusGate had already moved to
# GAME_INACTIVE. The fix reads that live state at consumption time, not a
# copy carried by the event itself or by a "focus" event that could be
# sitting further back in the very same queue.

def queue_update(application, decision, round_id=1):
    """Put an "update" on the queue WITHOUT draining it - the race needs
    control over exactly when self.tracker.focus changes relative to when
    the event is actually consumed."""
    from poker import scenario_engine as se

    application.events.put(("update", {
        "state": "FLOP", "round_id": round_id, "cards": CARDS,
        "seen": dict(CARDS), "reads": {}, "held": {}, "panels": {},
        "diagnostics": {}, "dealer": {}, "action": None,
        "statuses": {slot: "CONFIRMED" for slot in SETTLED},
        "timing": {}, "emitted_at": time.time(), "uncertain": [],
        "scenario": {"decision": decision, "reason": "FLOP_PAIR",
                     "round_id": round_id}}))
    return se


def test_a_stale_queued_update_is_dropped_once_the_live_gate_is_paused(
        application):
    """The exact real-world race: queued while active, paused before draining."""
    begin(application)
    se = queue_update(application, "PLAY")            # 1. queued while active

    # 2. the live gate moves to inactive BEFORE this queued update is ever
    # consumed - exactly what happened on the real machine, where the
    # tracker's own thread had already committed GAME_INACTIVE while this
    # older update was still sitting, undrained, in the same queue.
    application.tracker.focus.state = wf.GAME_INACTIVE

    application._poll_events()                        # 3. now it is consumed
    application.root.update()

    assert not application.decision_banner.visible, (
        "a stale decision was displayed after the game had gone inactive")
    assert application.announcer.pending() == 0, (
        "a stale decision was announced after the game had gone inactive")
    assert application._spoken_decision is None


def test_the_normal_active_case_still_publishes_a_decision(application):
    """Nothing about the fix should affect an ordinary, active-game decision."""
    begin(application)
    se = queue_update(application, "PLAY")
    assert application.tracker.focus.paused is False   # the ordinary case

    application._poll_events()
    application.root.update()

    assert application.decision_banner.visible
    assert application.decision_banner.text == "PLAY NOW"


def test_manual_stop_is_unaffected_by_the_focus_check(application):
    """self.tracking is still checked first; Stop remains authoritative."""
    begin(application)
    queue_update(application, "PLAY")
    application.tracker._fake = False
    application.stop()
    # Even with the live gate reporting active, a stopped session must not
    # publish anything queued before Stop was pressed.
    application.tracker.focus.state = wf.GAME_ACTIVE
    application._poll_events()
    application.root.update()

    assert not application.decision_banner.visible
    assert application.status_var.get() == "Status: STOPPED"
