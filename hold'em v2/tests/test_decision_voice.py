"""The decision on the banner, said aloud.

The voice used to announce the cards, the hand and the winner, but never the
decision - the one thing the banner exists to show. These tests drive the real
App, the real DecisionBanner and the real Announcer, with voice.engines'
NullEngine recording what was said instead of a sound card.

What they are really checking is that there is one source of truth: the voice
says what the banner shows, when the banner changes, and at no other time.
"""

import threading

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenario_engine as se                     # noqa: E402
from poker import scenarios as scenario_rules               # noqa: E402
from ui.decision_banner import (                            # noqa: E402
    DECISION_LABELS, SPOKEN_DECISIONS, spoken,
)
from voice import engines                                   # noqa: E402


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
def engine(monkeypatch):
    """A recording engine in place of SAPI5.

    conftest's no_real_speech makes Pyttsx3Engine raise, which is right for the
    suite at large; these tests are about what gets said, so they put a
    recorder there instead. A per-test patch wins over the autouse one.
    """
    recorder = engines.NullEngine()
    monkeypatch.setattr(engines, "Pyttsx3Engine",
                        lambda *_args, **_kwargs: recorder)
    return recorder


def stub_everything_but_the_voice(monkeypatch):
    """Silence the database and the screen reading; leave the voice real.

    App.start() asks the database whether it is reachable and, when it is not,
    opens a modal asking whether to start anyway. Neither belongs in a test
    about the voice: the modal has nobody to click it and hangs the run, and an
    unreachable database makes start() return before the banner is ever
    enabled - which is exactly how these tests failed the first time they were
    run, while Docker happened to be down.
    """
    import app as app_module
    import tracker as tracker_module

    monkeypatch.setattr(app_module.App, "_startup_checks", lambda self: None)
    monkeypatch.setattr(app_module.App, "_refresh_statistics", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "start", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "stop", lambda self: None)
    monkeypatch.setattr(tracker_module.Tracker, "is_running",
                        lambda self: getattr(self, "_fake", False))
    monkeypatch.setattr(app_module.db, "check_connection",
                        lambda *_a, **_k: (True, "stubbed"))
    monkeypatch.setattr(app_module.messagebox, "askyesno",
                        lambda *_a, **_k: pytest.fail(
                            "a modal dialog was opened during a test"))
    return app_module


@pytest.fixture
def application(root, engine, monkeypatch):
    """The real window, with the database and the screen reading stubbed out."""
    app_module = stub_everything_but_the_voice(monkeypatch)
    made = app_module.App(root)
    root.update()
    try:
        yield made
    finally:
        made.tracker._fake = False
        made.on_close()


class BlockingEngine(engines.SpeechEngine):
    """Speaks only once released, so the queue can be inspected mid-phrase.

    The announcer refuses new announcements while paused rather than holding
    them (see Announcer.announce), so "something is queued but not yet said"
    has to be arranged by blocking the worker instead.
    """

    def __init__(self):
        self.spoken = []
        self.started = threading.Event()
        self.gate = threading.Event()
        self.interrupted = False

    def speak(self, text):
        self.started.set()
        self.gate.wait(5)
        if self.interrupted:
            self.interrupted = False
            return
        self.spoken.append(text)

    def stop(self):
        self.interrupted = True
        self.gate.set()

    def close(self):
        self.gate.set()


@pytest.fixture
def blocking_engine(monkeypatch):
    held = BlockingEngine()
    monkeypatch.setattr(engines, "Pyttsx3Engine",
                        lambda *_a, **_k: held)
    return held


@pytest.fixture
def held_application(root, blocking_engine, monkeypatch):
    app_module = stub_everything_but_the_voice(monkeypatch)
    made = app_module.App(root)
    root.update()
    try:
        yield made
    finally:
        blocking_engine.gate.set()
        made.tracker._fake = False
        made.on_close()


def payload(decision, round_id=1, reason="because"):
    """A tracker update carrying a scenario decision, as the tracker emits it."""
    return {"state": "FLOP", "round_id": round_id, "seen": {}, "cards": {},
            "statuses": {},
            "scenario": {"decision": decision, "reason": reason,
                         "round_id": round_id}}


def said(application, engine, timeout=5.0):
    """Everything spoken so far, once the worker has caught up."""
    application.announcer.wait_until_idle(timeout=timeout)
    return list(engine.spoken)



DECISION_PHRASES = {spoken(d) for d in DECISION_LABELS}


def decisions_said(application, engine, timeout=5.0):
    """Only the decision announcements.

    VoiceEvents legitimately announces the cards and the made hand from the
    same payload, so a test about decisions filters to the decision phrases
    rather than asserting on everything that was said.
    """
    return [phrase for phrase in said(application, engine, timeout)
            if phrase in DECISION_PHRASES]

def run(application, *decisions, **kwargs):
    """Feed decisions in as polls, the way the tracker does."""
    round_id = kwargs.get("round_id", 1)
    application.start()
    application.tracker._fake = True
    for decision in decisions:
        application._update_ante_alert(payload(decision, round_id))
        application.root.update()
    return application


# -- the words themselves ------------------------------------------------------

def test_every_displayed_decision_has_a_spoken_form():
    """The two tables are keyed on the same constants and cannot drift.

    A decision added to DECISION_LABELS without a SPOKEN_DECISIONS entry would
    show on the banner and be silent, which is the disagreement this whole
    arrangement exists to prevent.
    """
    assert set(SPOKEN_DECISIONS) == set(DECISION_LABELS)
    for decision in DECISION_LABELS:
        assert spoken(decision) == SPOKEN_DECISIONS[decision]


def test_every_decision_the_banner_can_show_can_also_be_said():
    for decision in DECISION_LABELS:
        assert spoken(decision)


def test_an_unknown_decision_is_not_said():
    assert spoken(None) is None
    assert spoken("something the project does not have") is None


@pytest.mark.parametrize("decision,expected", [
    (se.WAIT, "Wait"),
    (se.PLAY, "Play now"),
    (se.DONT_PLAY, "Don't play"),
    (scenario_rules.ANTE, "Ante"),
    (scenario_rules.SKIP, "Skip round"),
    (scenario_rules.FOLD, "Fold"),
])
def test_the_spoken_form_of_each_decision(decision, expected):
    """Said, not shouted: the banner is glanced at, a voice is listened to."""
    assert spoken(decision) == expected


# -- 1-4: each decision is announced ------------------------------------------

@pytest.mark.parametrize("decision,expected", [
    (se.WAIT, "Wait"),
    (se.PLAY, "Play now"),
    (se.DONT_PLAY, "Don't play"),
])
def test_a_decision_on_the_banner_is_announced_once(application, engine,
                                                    decision, expected):
    run(application, decision)
    assert said(application, engine) == [expected]
    assert application.decision_banner.text == DECISION_LABELS[decision]


def test_the_preround_ante_is_announced(application, engine):
    """The pre-round path reaches the banner through the ANTE alert, not the
    scenario payload, so it is wired separately and checked separately."""
    application.start()
    application.tracker._fake = True
    application._apply_ante_event((
        __import__("ui.ante_alert", fromlist=["SHOW"]).SHOW,
        scenario_rules.ANTE, "the rules say ante"))
    application.root.update()
    assert said(application, engine) == ["Ante"]


# -- 5, 6: on change, and only on change --------------------------------------

def test_the_same_decision_on_every_poll_is_said_once(application, engine):
    """The tracker polls about five times a second."""
    run(application, *([se.WAIT] * 12))
    assert said(application, engine) == ["Wait"]


def test_a_change_is_announced(application, engine):
    run(application, se.WAIT, se.WAIT, se.PLAY, se.PLAY, se.PLAY, se.WAIT)
    assert said(application, engine) == ["Wait", "Play now", "Wait"]


def test_returning_to_an_earlier_decision_is_announced_again(application, engine):
    """Not deduplicated by value: WAIT after PLAY is news, not a repeat."""
    run(application, se.WAIT, se.PLAY, se.WAIT, se.PLAY)
    assert said(application, engine) == ["Wait", "Play now", "Wait", "Play now"]


def test_the_banner_and_the_voice_never_disagree(application, engine):
    for decision in (se.WAIT, se.PLAY, se.DONT_PLAY):
        run(application, decision)
        assert application.decision_banner.text == DECISION_LABELS[decision]
    assert said(application, engine) == ["Wait", "Play now", "Don't play"]


# -- 7, 8, 9: rounds and sessions ---------------------------------------------

def test_the_same_decision_in_a_new_round_is_not_repeated(application, engine):
    """The live defect: "Wait, Wait, Wait, Wait" in six seconds.

    round_id is CardMemory's generation, which is bumped on every clear rather
    than once per hand - it ticked 7, 8, 9, 10 between two real rounds. The
    banner correctly drops its decision on each tick, so it reported a change
    each time; the voice must not treat that as news, because the decision
    itself never moved.
    """
    run(application, se.PLAY)
    for round_id in (2, 3, 4):
        application._update_ante_alert(payload(se.PLAY, round_id=round_id))
        application.root.update()
    assert said(application, engine) == ["Play now"]


def test_a_new_round_with_a_different_decision_is_announced(application, engine):
    """Suppressing the repeat must not suppress a genuine change."""
    run(application, se.PLAY)
    application._update_ante_alert(payload(se.WAIT, round_id=2))
    application.root.update()
    assert said(application, engine) == ["Play now", "Wait"]


def test_a_decision_returning_after_another_is_announced_again(application,
                                                               engine):
    """Only consecutive repeats are dropped, not a decision that comes back."""
    run(application, se.PLAY)
    application._update_ante_alert(payload(se.WAIT, round_id=2))
    application.root.update()
    application._update_ante_alert(payload(se.PLAY, round_id=3))
    application.root.update()
    assert said(application, engine) == ["Play now", "Wait", "Play now"]


def test_a_queued_decision_survives_the_generation_ticking(
        held_application, blocking_engine):
    """The bug behind "accepted=True but I hear nothing".

    A decision used to be queued against round_id - CardMemory's generation -
    and thrown away unspoken the moment the generation ticked. It ticks several
    times between real hands, and a phrase takes about a second, so most
    decisions were accepted and then silently discarded before the worker
    reached them.
    """
    application = held_application
    application.start()
    application.tracker._fake = True

    # The first decision seizes the worker and blocks in speak().
    application._update_ante_alert(payload(se.WAIT, round_id=1))
    application.root.update()
    assert blocking_engine.started.wait(5)

    # The second queues behind it.
    application._update_ante_alert(payload(se.PLAY, round_id=1))
    application.root.update()

    application.announcer.set_round(2)          # the generation ticks
    blocking_engine.gate.set()                  # let the worker run
    application.announcer.wait_until_idle(timeout=5)

    assert blocking_engine.spoken == ["Wait", "Play now"]
    assert application.announcer.dropped == 0


def test_a_newer_decision_replaces_one_still_waiting(held_application,
                                                     blocking_engine):
    """What keeps it honest instead of the round id.

    There is only ever one current decision, so a newer one takes the place of
    an older one that has not been said yet - the voice says where the banner
    is now, not every step it took getting there.
    """
    application = held_application
    application.start()
    application.tracker._fake = True

    application._update_ante_alert(payload(se.WAIT, round_id=1))
    application.root.update()
    assert blocking_engine.started.wait(5)      # "Wait" is being spoken

    application._update_ante_alert(payload(se.PLAY, round_id=1))
    application.root.update()
    application._update_ante_alert(payload(se.DONT_PLAY, round_id=1))
    application.root.update()
    assert application.announcer.pending() == 1  # not two

    blocking_engine.gate.set()
    application.announcer.wait_until_idle(timeout=5)
    assert blocking_engine.spoken == ["Wait", "Don't play"]


def test_stopping_the_tracker_clears_what_was_queued(held_application,
                                                    blocking_engine):
    """The announcer's queue outlives the tracker, so stopping must empty it."""
    application = held_application
    application.start()
    application.tracker._fake = True

    application._update_ante_alert(payload(se.WAIT))
    application.root.update()
    assert blocking_engine.started.wait(5)
    application._update_ante_alert(payload(se.PLAY))
    application.root.update()
    assert application.announcer.pending() == 1

    application.tracker._fake = False
    application.stop()                          # App.stop clears the queue
    application.root.update()
    assert application.announcer.pending() == 0

    blocking_engine.gate.set()
    application.announcer.wait_until_idle(timeout=5)
    assert "Play now" not in blocking_engine.spoken


def test_restarting_does_not_replay_the_previous_session(application, engine):
    run(application, se.PLAY)
    assert said(application, engine) == ["Play now"]

    application.tracker._fake = False
    application.stop()
    application.root.update()

    application.start()
    application.tracker._fake = True
    application.root.update()
    assert said(application, engine) == ["Play now"]        # nothing replayed


def test_the_same_decision_after_a_restart_is_announced_again(application, engine):
    """A new session is a new statement of the decision, not a repeat."""
    run(application, se.PLAY)
    application.tracker._fake = False
    application.stop()
    application.root.update()
    run(application, se.PLAY)
    assert said(application, engine) == ["Play now", "Play now"]


def test_nothing_is_announced_before_the_tracker_starts(application, engine):
    """The banner refuses to show anything when not running, so nothing is
    said either - they are the same decision."""
    application._update_ante_alert(payload(se.PLAY))
    application.root.update()
    assert said(application, engine) == []
    assert application.decision_banner.visible is False


# -- 10, 11, 12: the tracker survives whatever the voice is doing -------------

def test_the_tracker_runs_with_the_voice_disabled(application, engine):
    application.announcer.set_enabled(False)
    run(application, se.WAIT, se.PLAY)
    assert application.status_var.get() == "Status: RUNNING"
    assert application.decision_banner.text == DECISION_LABELS[se.PLAY]
    assert engine.spoken == []


def test_the_tracker_runs_with_the_voice_unavailable(root, monkeypatch):
    """No engine at all - the state the missing pyttsx3 produced."""
    monkeypatch.setattr(engines, "Pyttsx3Engine", None)     # not callable
    app_module = stub_everything_but_the_voice(monkeypatch)
    made = app_module.App(root)
    try:
        root.update()
        run(made, se.WAIT, se.PLAY)
        assert made.status_var.get() == "Status: RUNNING"
        assert made.decision_banner.text == DECISION_LABELS[se.PLAY]
    finally:
        made.tracker._fake = False
        made.on_close()


def test_muting_silences_the_decision_but_not_the_banner(application, engine):
    application.start()
    application.tracker._fake = True
    application.toggle_mute()
    application._update_ante_alert(payload(se.PLAY))
    application.root.update()
    assert said(application, engine) == []
    assert application.decision_banner.text == DECISION_LABELS[se.PLAY]

    application.toggle_mute()
    application._update_ante_alert(payload(se.DONT_PLAY))
    application.root.update()
    assert said(application, engine) == ["Don't play"]


def test_pause_and_resume_still_work_around_a_decision(application, engine):
    application.start()
    application.tracker._fake = True
    application.pause_voice()
    application._update_ante_alert(payload(se.PLAY))
    application.root.update()
    # Paused refuses rather than holds - Announcer.announce says so - so the
    # decision made while paused is simply not spoken.
    assert said(application, engine) == []

    application.resume_voice()
    application._update_ante_alert(payload(se.DONT_PLAY))
    application.root.update()
    assert said(application, engine) == ["Don't play"]


def test_the_voice_never_announces_a_betting_action(application, engine):
    """Read-only: the announcement is words, and nothing else happens.

    The decisions themselves are the game's words - ANTE is one - but saying
    one must not reach the automation. The banner is display-only and so is
    this; the guard that proves it is test_decision_banner's
    test_the_banner_never_imports_the_betting_automation.
    """
    import ui.decision_banner as banner_module

    source = open(banner_module.__file__, encoding="utf-8").read()
    code = "".join("\n".join(
        line for line in source.splitlines()
        if not line.strip().startswith("#")).split('"""')[::2]).lower()
    for forbidden in ("pyautogui", "mouse_controller", "import automation"):
        assert forbidden not in code


# -- the delivery path: one bad event must not silence everything -------------
#
# _poll_events is the whole user-interface loop: the banner, the card table,
# the saved-hand line and every voice announcement are fed from it. Its
# reschedule used to sit after a bare `except queue.Empty`, so an exception
# raised while handling one event escaped before the reschedule ran and the
# loop ended. Tk printed the traceback to stderr - invisible when the program
# is started from a shortcut - and the window carried on looking alive while it
# silently stopped showing anything the tracker said.
#
# That is exactly the shape of "the voice stopped working": nothing is wrong
# with the voice, nothing is logged, and no decision ever reaches it again.

def full_payload(decision, round_id=1, cards=None):
    """Every key tracker._emit('update', ...) sends."""
    return {"state": "FLOP" if cards else "WAITING", "round_id": round_id,
            "cards": cards or {}, "seen": dict(cards or {}),
            "reads": {}, "held": {}, "panels": {}, "diagnostics": {},
            "dealer": {}, "action": None, "statuses": {}, "timing": {},
            "emitted_at": 0.0, "uncertain": [],
            "scenario": {"decision": decision, "reason": "r",
                         "round_id": round_id}}


def test_a_malformed_event_does_not_stop_the_event_loop(application, engine):
    """The next event is still handled."""
    application.start()
    application.tracker._fake = True

    application.events.put(("update", {"state": "FLOP"}))       # missing keys
    application.events.put(("update", full_payload(
        se.PLAY, cards={"player_1": "AS", "player_2": "7D"})))
    application._poll_events()
    application.root.update()

    assert application.decision_banner.text == DECISION_LABELS[se.PLAY]
    assert said(application, engine) == ["Play now"]


def test_the_loop_reschedules_itself_after_a_bad_event(application):
    """Without the reschedule nothing is ever drained again."""
    scheduled = []
    real_after = application.root.after

    def watched(delay, *args):
        if args and getattr(args[0], "__name__", "") == "_poll_events":
            scheduled.append(delay)
        return real_after(delay, *args)

    application.root.after = watched
    try:
        application.events.put(("update", {"state": "FLOP"}))
        application._poll_events()
    finally:
        application.root.after = real_after
    assert scheduled, "the poll loop did not reschedule itself"


def test_a_bad_event_is_logged_rather_than_swallowed(application, caplog):
    """Dropped, but not quietly - the log has to say a payload was refused."""
    import logging

    # The session has to be open for the payload to be handled at all: an
    # update that arrives while nothing is being tracked is now dropped before
    # anything looks at it, so a malformed one would never reach the handler
    # and there would be nothing to log. Started here exactly as the test
    # below it does.
    application.start()
    application.tracker._fake = True
    with caplog.at_level(logging.ERROR, logger="app"):
        application.events.put(("update", {"state": "FLOP"}))
        application._poll_events()
    assert any("Could not handle" in record.message for record in caplog.records)


def test_decisions_keep_being_announced_after_a_bad_event(application, engine):
    """The symptom this is really about: the voice going silent for good."""
    application.start()
    application.tracker._fake = True

    # Cards on the table throughout, not an empty one: the ANTE alert now
    # fires on the very first empty poll it sees (see AnteAlertLogic and
    # ui/ante_alert.py), and firing it would legitimately take the banner
    # over - a different decision arriving, not the bug under test.
    dealt = {"player_1": "AS", "player_2": "7D",
             "flop_1": "KC", "flop_2": "7C", "flop_3": "2H"}
    application.events.put(("update", full_payload(se.WAIT, cards=dealt)))
    application._poll_events()
    application.root.update()
    assert decisions_said(application, engine) == ["Wait"]

    application.events.put(("update", {"nonsense": True}))
    application.events.put(("update", full_payload(se.PLAY, cards=dealt)))
    application.events.put(("update", full_payload(se.DONT_PLAY, round_id=2,
                                                   cards=dealt)))
    application._poll_events()
    application.root.update()
    assert decisions_said(application, engine) == [
        "Wait", "Play now", "Don't play"]


def test_an_unknown_event_kind_is_ignored_without_stopping_the_loop(
        application, engine):
    application.start()
    application.tracker._fake = True
    application.events.put(("something_new", {"whatever": 1}))
    application.events.put(("update", full_payload(
        se.PLAY, cards={"player_1": "AS", "player_2": "7D"})))
    application._poll_events()
    application.root.update()
    assert said(application, engine) == ["Play now"]


# -- the banner is the single source of truth ---------------------------------
#
# The acceptance criterion, stated as tests: whatever the green banner is
# displaying, the voice says the matching phrase for that same canonical value.
# If the banner says PLAY and the voice says WAIT, these fail.

EVERY_DECISION = [
    (se.WAIT, "WAIT", "Wait"),
    (se.PLAY, "PLAY NOW", "Play now"),
    (se.DONT_PLAY, "DON'T PLAY", "Don't play"),
    (scenario_rules.ANTE, "ANTE NOW", "Ante"),
    (scenario_rules.SKIP, "SKIP ROUND", "Skip round"),
    (scenario_rules.PLAY, "PLAY NOW", "Play now"),
    (scenario_rules.FOLD, "FOLD", "Fold"),
]


@pytest.mark.parametrize("decision,banner_text,phrase", EVERY_DECISION)
def test_the_voice_mirrors_the_banner(application, engine, decision,
                                      banner_text, phrase):
    """One canonical value in; the same one displayed and said."""
    application.start()
    application.tracker._fake = True

    application._publish_decision(decision, "reason", 77)
    application.root.update()

    # 1. the banner received and is displaying that canonical decision
    assert application.decision_banner.decision == decision
    assert application.decision_banner.text == banner_text
    # 2. the voice was given the same canonical decision, and said its phrase
    assert application._spoken_decision == decision
    assert said(application, engine) == [phrase]
    # 3. nothing else was invented
    assert spoken(decision) == phrase


def test_the_banner_and_the_voice_are_handed_the_identical_object(application,
                                                                  monkeypatch):
    """Not equal values - the same argument, from the one publish path."""
    seen = {}
    real_show = application.decision_banner.show
    real_announce = application._announce_decision

    def watched_show(decision, reason="", round_id=None):
        seen["banner"] = decision
        return real_show(decision, reason, round_id)

    def watched_announce(decision, changed, round_id):
        seen["voice"] = decision
        return real_announce(decision, changed, round_id)

    monkeypatch.setattr(application.decision_banner, "show", watched_show)
    monkeypatch.setattr(application, "_announce_decision", watched_announce)

    application.start()
    application.tracker._fake = True
    application._publish_decision(se.DONT_PLAY, "r", 5)
    assert seen["banner"] is seen["voice"] is se.DONT_PLAY


def test_both_live_paths_go_through_the_one_publish_method(application,
                                                           monkeypatch):
    """The engine path and the pre-round path, so neither can drift."""
    import inspect

    published = []
    monkeypatch.setattr(application, "_publish_decision",
                        lambda d, r="", i=None: published.append(d))
    application.start()
    application.tracker._fake = True

    application._show_engine_decision(payload(se.PLAY, round_id=3))
    application._apply_ante_event(
        (__import__("ui.ante_alert", fromlist=["SHOW"]).SHOW,
         scenario_rules.ANTE, "betting open"))
    assert published == [se.PLAY, scenario_rules.ANTE]

    # And neither path speaks on its own.
    for method in (application._show_engine_decision,
                   application._apply_ante_event):
        source = inspect.getsource(method)
        assert "announcer.announce" not in source


def test_the_voice_layer_never_evaluates_a_scenario():
    """It is handed a decision; it does not work one out."""
    import inspect

    import app as app_module

    for name in ("_publish_decision", "_announce_decision"):
        source = inspect.getsource(getattr(app_module.App, name))
        for forbidden in ("decide_flop", "decide_preround", "evaluate_round",
                          "detect_player_decision", "describe_flop"):
            assert forbidden not in source


# -- the exact sequence from the brief ----------------------------------------

def test_the_full_decision_sequence_is_announced_once_each(application, engine):
    """Every genuine change announced exactly once; every repeat silent."""
    application.start()
    application.tracker._fake = True

    sequence = [se.WAIT, se.WAIT, se.WAIT,
                scenario_rules.ANTE, scenario_rules.ANTE,
                se.PLAY, se.PLAY, se.PLAY,
                se.DONT_PLAY, se.DONT_PLAY,
                se.WAIT,
                scenario_rules.SKIP, scenario_rules.SKIP,
                scenario_rules.FOLD, scenario_rules.FOLD]
    for index, decision in enumerate(sequence):
        # A different round id every time, so round churn cannot be what makes
        # this pass or fail - only the decision value may.
        application._publish_decision(decision, "", index)
        application.root.update()

    assert said(application, engine) == [
        "Wait", "Ante", "Play now", "Don't play", "Wait", "Skip round", "Fold"]


def test_the_same_sequence_leaves_the_banner_on_the_last_decision(application,
                                                                  engine):
    for decision in (se.WAIT, scenario_rules.ANTE, se.PLAY, scenario_rules.FOLD):
        application.start()
        application.tracker._fake = True
        application._publish_decision(decision, "", 1)
        application.root.update()
        assert application.decision_banner.text == DECISION_LABELS[decision]


def test_a_decision_the_banner_cannot_show_is_not_spoken(application, engine):
    """An unknown value clears the banner, so there is nothing to say."""
    application.start()
    application.tracker._fake = True
    application._publish_decision(se.PLAY, "", 1)
    application.root.update()
    assert said(application, engine) == ["Play now"]

    application._publish_decision("NOT_A_DECISION", "", 1)
    application.root.update()
    assert application.decision_banner.text == ""
    assert said(application, engine) == ["Play now"]        # nothing added


# -- the decision, and nothing else -------------------------------------------
#
# The voice reads the banner. It used to read the table as well - your cards,
# the flop, the turn, the river and the hand - which talks over the one thing
# it is there to say. That reading is now off by default in the window.

def test_the_window_does_not_read_the_table_out(application, engine):
    """config["voice_announce_cards"] is false, so only decisions are said."""
    assert application.voice_events.announce_cards is False


def test_only_the_decision_is_spoken_for_a_full_payload(application, engine):
    """A payload with every card settled: still just the decision."""
    application.start()
    application.tracker._fake = True

    dealt = {"player_1": "AS", "player_2": "7D", "flop_1": "KC",
             "flop_2": "7C", "flop_3": "2H", "turn": "9S", "river": "4D"}
    settled = {slot: "CONFIRMED" for slot in dealt}
    application.events.put(("update", {
        "state": "RIVER", "round_id": 1, "cards": dealt, "seen": dict(dealt),
        "reads": {}, "held": {}, "panels": {}, "diagnostics": {}, "dealer": {},
        "action": None, "statuses": settled, "timing": {}, "emitted_at": 0.0,
        "uncertain": [],
        "scenario": {"decision": se.DONT_PLAY, "reason": "r", "round_id": 1}}))
    application._poll_events()
    application.root.update()

    assert said(application, engine) == ["Don't play"]


def test_no_card_flop_turn_river_or_hand_phrase_is_ever_said(application,
                                                             engine):
    dealt = {"player_1": "AS", "player_2": "7D", "flop_1": "KC",
             "flop_2": "7C", "flop_3": "2H", "turn": "9S", "river": "4D"}
    settled = {slot: "CONFIRMED" for slot in dealt}
    application.start()
    application.tracker._fake = True
    for round_id, decision in enumerate((se.WAIT, se.PLAY, se.DONT_PLAY), 1):
        application.events.put(("update", {
            "state": "RIVER", "round_id": round_id, "cards": dealt,
            "seen": dict(dealt), "reads": {}, "held": {}, "panels": {},
            "diagnostics": {}, "dealer": {}, "action": None,
            "statuses": settled, "timing": {}, "emitted_at": 0.0,
            "uncertain": [],
            "scenario": {"decision": decision, "reason": "r",
                         "round_id": round_id}}))
        application._poll_events()
        application.root.update()

    for phrase in said(application, engine):
        assert phrase in DECISION_PHRASES, "%r is not a decision" % phrase
        for leak in ("Your cards", "Flop:", "Turn:", "River:", "of Spades",
                     "of Clubs", "pair", "wins"):
            assert leak not in phrase


def test_the_result_is_not_announced_either(application, engine):
    """"Player wins with pair" is a reading of the hand, not the decision."""
    application.start()
    application.tracker._fake = True
    application.voice_events.announce_result({
        "winner": "Player", "player_hand": "Pair", "dealer_hand": "High Card",
        "dealer_qualified": True, "hand_fingerprint": "abc"})
    application.root.update()
    assert said(application, engine) == []


def test_the_reading_can_be_turned_back_on(root, engine, monkeypatch):
    """One setting, so nothing had to be deleted to go quiet."""
    app_module = stub_everything_but_the_voice(monkeypatch)
    real_load = app_module.load_config

    def with_cards():
        config = dict(real_load())
        config["voice_announce_cards"] = True
        return config

    monkeypatch.setattr(app_module, "load_config", with_cards)
    made = app_module.App(root)
    try:
        root.update()
        assert made.voice_events.announce_cards is True
    finally:
        made.tracker._fake = False
        made.on_close()
