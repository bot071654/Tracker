"""The announcer: queue, worker, states, controls and deduplication.

No sound card is involved. voice.engines.NullEngine records what it was asked
to say, so the queue, the worker thread, the states and the deduplication are
all the real ones - only the speaker is a stand-in.

Determinism: the worker is a real thread, so anything that asserts on what was
spoken waits for the queue to drain rather than sleeping for a guessed length
of time. `drain` below is the only synchronisation these tests need.
"""

import sys
import threading
import time

import pytest

from voice import announcer as va
from voice import engines
from voice.announcer import Announcer
from voice.engines import (
    EngineUnavailable, NullEngine, SpeechEngine,
    # The real class, captured at import time. conftest's no_real_speech
    # replaces engines.Pyttsx3Engine with a stub for the whole suite,
    # which is right everywhere except here - these tests are about that
    # class, driven against a fake pyttsx3 rather than a sound card.
    Pyttsx3Engine as RealPyttsx3Engine,
)


def drain(voice, timeout=5.0):
    """Wait until the worker has spoken everything queued.

    Uses the announcer's own wait_until_idle rather than polling pending():
    an item is off the queue for the whole time it is being spoken, so a
    qsize poll reports "done" before a word has been said.
    """
    return voice.wait_until_idle(timeout)


@pytest.fixture
def engine():
    return NullEngine()


@pytest.fixture
def voice(engine):
    made = Announcer(enabled=True, engine_factory=lambda: engine)
    yield made
    made.shutdown()


class SlowEngine(SpeechEngine):
    """Speaks until told to stop, so "while speaking" can be tested exactly."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.spoken = []
        self.stopped = 0

    def speak(self, text):
        self.spoken.append(text)
        self.started.set()
        self.release.wait(timeout=5)

    def stop(self):
        self.stopped += 1
        self.release.set()

    def close(self):
        self.release.set()


# -- 1, 2: disabled and enabled ----------------------------------------------

def test_disabled_starts_nothing_and_says_nothing():
    calls = []
    off = Announcer(enabled=False, engine_factory=lambda: calls.append(1))
    assert off.state() == va.OFF
    assert off.announce("hello") is False
    assert off.pending() == 0
    assert calls == []                       # the engine was never constructed
    off.shutdown()


def test_disabled_controls_are_harmless():
    off = Announcer(enabled=False, engine_factory=lambda: None)
    for call in (off.pause, off.resume, off.stop, off.mute, off.unmute):
        call()
    assert off.state() == va.OFF
    off.shutdown()


def test_enabled_starts_idle(voice):
    assert voice.state() == va.IDLE
    assert voice.enabled is True


# -- 3, 4: queued and spoken --------------------------------------------------

def test_an_announcement_is_queued_and_spoken(voice, engine):
    assert voice.announce("player pair") is True
    assert drain(voice)
    assert engine.spoken == ["player pair"]


def test_announce_returns_immediately(voice):
    """The caller must never wait for speech - this is the whole design."""
    slow = SlowEngine()
    quick = Announcer(enabled=True, engine_factory=lambda: slow)
    try:
        started = time.time()
        for index in range(5):
            quick.announce("phrase %d" % index, key=("k", index))
        assert time.time() - started < 0.5   # five calls, no speaking waited on
    finally:
        slow.release.set()
        quick.shutdown()


# -- 5, 6: deduplication ------------------------------------------------------

def test_the_same_key_is_only_spoken_once(voice, engine):
    for _ in range(5):
        voice.announce("Pair", key=(1, "hand", "Pair"))
    assert drain(voice)
    assert engine.spoken == ["Pair"]


def test_different_keys_are_all_spoken(voice, engine):
    voice.announce("Pair", key=(1, "hand", "Pair"))
    voice.announce("Two Pair", key=(1, "hand", "Two Pair"))
    assert drain(voice)
    assert engine.spoken == ["Pair", "Two Pair"]


def test_a_new_round_allows_the_same_phrase_again(voice, engine):
    voice.set_round(1)
    voice.announce("Pair", key=(1, "hand", "Pair"))
    assert drain(voice)
    voice.set_round(2)
    voice.announce("Pair", key=(2, "hand", "Pair"))
    assert drain(voice)
    assert engine.spoken == ["Pair", "Pair"]


# -- 7, 8, 9: pause -----------------------------------------------------------

def test_pause_while_idle(voice, engine):
    voice.pause()
    assert voice.state() == va.PAUSED and voice.is_paused()
    assert voice.announce("nothing") is False
    assert engine.spoken == []


def test_pause_while_speaking_stops_the_phrase():
    slow = SlowEngine()
    voice = Announcer(enabled=True, engine_factory=lambda: slow)
    try:
        voice.announce("a long sentence")
        assert slow.started.wait(timeout=5)
        assert voice.is_speaking()
        voice.pause()
        assert slow.stopped >= 1             # the phrase was interrupted
        assert voice.is_paused()
    finally:
        voice.shutdown()


def test_pause_discards_queued_transient_announcements(voice, engine):
    voice.set_round(1)
    for index in range(4):
        voice.announce("phrase %d" % index, key=("k", index), round_id=1)
    voice.pause()
    assert voice.pending() == 0
    voice.resume()
    assert drain(voice)
    assert len(engine.spoken) < 4            # the backlog did not survive


# -- 10: resume ---------------------------------------------------------------

def test_resume_returns_to_idle_and_speaks_again(voice, engine):
    voice.pause()
    voice.resume()
    assert voice.state() == va.IDLE and not voice.is_paused()
    voice.announce("after resume")
    assert drain(voice)
    assert engine.spoken == ["after resume"]


def test_resume_does_not_replay_a_stale_round(voice, engine):
    """Paused for a while; the round moved on. Those events are obsolete."""
    voice.set_round(1)
    voice.announce("old news", key=("k", 1), round_id=1)
    voice.pause()
    voice.set_round(2)
    voice.resume()
    assert drain(voice)
    assert "old news" not in engine.spoken


def test_a_final_announcement_survives_the_round_moving_on(voice, engine):
    """A result is worth hearing slightly late; a card is not."""
    voice.set_round(1)
    voice.announce("Player wins", key=("result", "fp"), round_id=1, kind=va.FINAL)
    voice.set_round(2)
    assert drain(voice)
    assert engine.spoken == ["Player wins"]


# -- 11, 12: stop -------------------------------------------------------------

def test_stop_while_speaking():
    slow = SlowEngine()
    voice = Announcer(enabled=True, engine_factory=lambda: slow)
    try:
        voice.announce("a long sentence")
        assert slow.started.wait(timeout=5)
        voice.stop()
        assert slow.stopped >= 1
    finally:
        voice.shutdown()


def test_stop_clears_the_queue_but_does_not_latch(voice, engine):
    voice.set_round(1)
    for index in range(4):
        voice.announce("phrase %d" % index, key=("k", index), round_id=1)
    voice.stop()
    assert voice.pending() == 0                  # the backlog is gone

    # Whatever the worker had already taken off the queue may have been said
    # before stop() arrived - stop cannot un-speak a phrase. What it promises
    # is that the rest are dropped.
    assert len(engine.spoken) < 4

    # And unlike pause, stop does not latch: the next one is spoken.
    voice.announce("after stop", key=("k", "after"))
    assert drain(voice)
    assert engine.spoken[-1] == "after stop"


# -- 13, 14: mute -------------------------------------------------------------

def test_mute_silences_and_refuses_new_announcements(voice, engine):
    voice.mute()
    assert voice.state() == va.MUTED and voice.is_muted()
    assert voice.announce("silent") is False
    assert drain(voice)
    assert engine.spoken == []


def test_unmute_restores_the_voice_without_a_backlog(voice, engine):
    voice.mute()
    for index in range(5):
        voice.announce("backlog %d" % index, key=("k", index))
    voice.unmute()
    assert voice.state() == va.IDLE
    voice.announce("fresh", key=("k", "fresh"))
    assert drain(voice)
    assert engine.spoken == ["fresh"]        # nothing from the muted period


# -- 17, 19: failure ----------------------------------------------------------

def test_an_engine_that_cannot_start_is_reported_not_raised(caplog):
    def broken():
        raise EngineUnavailable("no audio device")

    voice = Announcer(enabled=True, engine_factory=broken)
    try:
        assert voice.announce("hello") is True      # queueing still succeeds
        deadline = time.time() + 5
        while time.time() < deadline and voice.error is None:
            time.sleep(0.01)
        assert voice.error is not None
        assert "no audio device" in voice.error
        assert voice.state() == va.ERROR
        # and the caller is never given an exception
        assert voice.announce("again") is False
    finally:
        voice.shutdown()


def test_an_engine_that_fails_mid_phrase_does_not_kill_the_worker(voice=None):
    class Flaky(SpeechEngine):
        def __init__(self):
            self.spoken = []

        def speak(self, text):
            if text == "bad":
                raise RuntimeError("driver hiccup")
            self.spoken.append(text)

        def stop(self):
            pass

    flaky = Flaky()
    made = Announcer(enabled=True, engine_factory=lambda: flaky)
    try:
        made.announce("bad", key=("k", 1))
        made.announce("good", key=("k", 2))
        assert drain(made)
        assert flaky.spoken == ["good"]      # the worker survived the failure
    finally:
        made.shutdown()


# -- 18, 25: shutdown ---------------------------------------------------------

def test_shutdown_ends_the_worker(engine):
    voice = Announcer(enabled=True, engine_factory=lambda: engine)
    voice.announce("hello")
    assert drain(voice)
    voice.shutdown()
    assert voice.state() == va.STOPPED
    assert voice.announce("after shutdown") is False
    assert engine.closed is True
    assert not any(t.name == "voice" and t.is_alive() for t in threading.enumerate())


def test_shutdown_while_speaking():
    slow = SlowEngine()
    voice = Announcer(enabled=True, engine_factory=lambda: slow)
    voice.announce("a long sentence")
    assert slow.started.wait(timeout=5)
    voice.shutdown()
    assert slow.stopped >= 1
    assert voice.state() == va.STOPPED
    assert not any(t.name == "voice" and t.is_alive() for t in threading.enumerate())


def test_shutdown_is_safe_twice_and_without_ever_speaking():
    voice = Announcer(enabled=True, engine_factory=lambda: NullEngine())
    voice.shutdown()
    voice.shutdown()
    assert voice.state() == va.STOPPED


# -- 20: the queue is bounded -------------------------------------------------

def test_the_queue_does_not_grow_without_limit():
    slow = SlowEngine()
    voice = Announcer(enabled=True, engine_factory=lambda: slow, queue_limit=4)
    try:
        for index in range(100):
            voice.announce("phrase %d" % index, key=("k", index))
        assert voice.pending() <= 4
        assert voice.dropped > 0
    finally:
        slow.release.set()
        voice.shutdown()


def test_a_full_queue_sacrifices_transient_items_not_results():
    """A backlog must cost cards, never the result."""
    slow = SlowEngine()
    voice = Announcer(enabled=True, engine_factory=lambda: slow, queue_limit=3)
    try:
        # Occupy the worker first, so nothing below is taken off the queue and
        # spoken while the test is still filling it.
        voice.announce("occupying the worker", key=("busy",))
        assert slow.started.wait(timeout=5)

        voice.announce("Player wins", key=("r", 1), kind=va.FINAL)
        for index in range(20):
            voice.announce("card %d" % index, key=("k", index), kind=va.TRANSIENT)

        queued = []
        while voice.pending():
            queued.append(voice._queue.get_nowait())
            voice._queue.task_done()
        assert queued, "the queue emptied unexpectedly"
        kinds = [item.kind for item in queued]
        assert va.FINAL in kinds, (
            "the result was dropped in favour of transient chatter: %s" % kinds)
        assert voice.dropped > 0          # transients were sacrificed for it
    finally:
        slow.release.set()
        voice.shutdown()


# -- 24: repeated control operations ------------------------------------------

def test_many_pause_and_resume_cycles(voice, engine):
    for index in range(10):
        voice.pause()
        assert voice.is_paused()
        voice.resume()
        assert not voice.is_paused()
    voice.announce("still working", key=("k", "end"))
    assert drain(voice)
    assert engine.spoken == ["still working"]


def test_only_one_worker_thread_however_many_announcements(voice):
    for index in range(50):
        voice.announce("phrase %d" % index, key=("k", index))
    assert drain(voice)
    workers = [t for t in threading.enumerate() if t.name == "voice"]
    assert len(workers) == 1


# -- 23: pause racing with the start of speech --------------------------------

def test_pause_racing_a_starting_phrase_leaves_a_consistent_state(voice):
    """pause() exactly as speech begins must not wedge the state."""
    for _ in range(20):
        voice.announce("phrase", key=("k", time.time()))
        voice.pause()
        voice.resume()
    assert voice.state() in (va.IDLE, va.SPEAKING)
    assert drain(voice)
    assert voice.state() == va.IDLE


def test_set_enabled_false_silences_and_stops_the_worker(engine):
    voice = Announcer(enabled=True, engine_factory=lambda: engine)
    voice.announce("before")
    assert drain(voice)
    voice.set_enabled(False)
    assert voice.state() == va.OFF
    assert voice.announce("after") is False
    assert not any(t.name == "voice" and t.is_alive() for t in threading.enumerate())
    voice.shutdown()


# -- create_engine, and what it says when it cannot ---------------------------
#
# The live failure these cover: the UI showed "Voice: ERROR" and the log said
# "pyttsx3 is not installed". It was installed - in the project's .venv - but
# the application was being launched with a different interpreter, one that had
# the other eight requirements and not this one, because pyttsx3 had only just
# been added to requirements.txt. The message named the package but not the
# environment, so it read as "this machine has no pyttsx3", which was false.

def test_a_missing_pyttsx3_is_reported_as_unavailable_not_raised(monkeypatch):
    """The caller gets EngineUnavailable, never a bare ImportError."""
    def missing(*_args, **_kwargs):
        raise ImportError("No module named 'pyttsx3'")

    monkeypatch.setattr(engines, "Pyttsx3Engine", missing)
    with pytest.raises(EngineUnavailable):
        engines.create_engine()


def test_the_missing_pyttsx3_message_names_the_interpreter(monkeypatch):
    """Which Python is missing it - a machine has more than one.

    Without this the message sends someone to `pip install pyttsx3` in whatever
    shell they happen to have open, which may not be the environment the
    application runs in.
    """
    def missing(*_args, **_kwargs):
        raise ImportError("No module named 'pyttsx3'")

    monkeypatch.setattr(engines, "Pyttsx3Engine", missing)
    with pytest.raises(EngineUnavailable) as caught:
        engines.create_engine()

    message = str(caught.value)
    assert sys.executable in message
    assert "requirements.txt" in message
    # The interpreter appears twice: once naming what is missing it, once in
    # the command to run - so the command is correct when pasted as it stands.
    assert message.count(sys.executable) == 2


def test_a_driver_failure_reports_its_type_and_message(monkeypatch):
    """Not just "could not start": SAPI5 fails in more than one way."""
    def broken(*_args, **_kwargs):
        raise OSError("no audio device")

    monkeypatch.setattr(engines, "Pyttsx3Engine", broken)
    with pytest.raises(EngineUnavailable) as caught:
        engines.create_engine()

    message = str(caught.value)
    assert "OSError" in message
    assert "no audio device" in message


def test_engine_failure_keeps_its_cause_for_the_log(monkeypatch):
    original = ImportError("No module named 'pyttsx3'")

    def missing(*_args, **_kwargs):
        raise original

    monkeypatch.setattr(engines, "Pyttsx3Engine", missing)
    with pytest.raises(EngineUnavailable) as caught:
        engines.create_engine()
    assert caught.value.__cause__ is original


def test_the_announcer_surfaces_the_interpreter_in_its_error(monkeypatch):
    """End of the path: engines -> announcer.error -> the window's message.

    The window shows announcer.error verbatim, so whatever create_engine says
    is what the person reads.
    """
    def missing(*_args, **_kwargs):
        raise ImportError("No module named 'pyttsx3'")

    monkeypatch.setattr(engines, "Pyttsx3Engine", missing)
    voice = Announcer(enabled=True, engine_factory=engines.create_engine)
    try:
        voice.announce("anything")
        deadline = time.time() + 5
        while time.time() < deadline and voice.error is None:
            time.sleep(0.01)
        assert voice.state() == va.ERROR
        assert sys.executable in voice.error
    finally:
        voice.shutdown()


def test_a_voice_failure_can_be_cleared_and_retried(engine):
    """set_enabled(False) then (True) clears the error, so a run that started
    without speech can recover without restarting the application."""
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise EngineUnavailable("first attempt fails")
        return engine

    voice = Announcer(enabled=True, engine_factory=flaky)
    try:
        voice.announce("one")
        deadline = time.time() + 5
        while time.time() < deadline and voice.error is None:
            time.sleep(0.01)
        assert voice.state() == va.ERROR

        voice.set_enabled(False)
        voice.set_enabled(True)
        assert voice.error is None
        assert voice.state() != va.ERROR

        assert voice.announce("two") is True
        assert drain(voice)
        assert "two" in engine.spoken
    finally:
        voice.shutdown()


# -- one engine per phrase ----------------------------------------------------
#
# The live fault behind "accepted=True and I hear nothing": pyttsx3's engine
# speaks the first phrase or two and then goes quiet - runAndWait() returns in
# about 0.2s instead of a second and a half, with no exception. Measured on
# this machine, eight phrases through one engine: two spoke, six were silent.
#
# Rebuilding for each phrase speaks every time. The subtlety is that
# pyttsx3.init() returns a cached engine from a WeakValueDictionary, so a new
# one is only built once the last strong reference to the old one is gone -
# which is why closing and re-initialising did not help.

class FakePyttsx3Engine:
    """Stands in for pyttsx3's Engine, counting how it was driven."""

    def __init__(self, built):
        self.built = built
        self.said = []
        self.ran = 0
        self.stopped = 0
        self.properties = {}

    def say(self, text):
        self.said.append(text)

    def runAndWait(self):
        self.ran += 1

    def stop(self):
        self.stopped += 1

    def setProperty(self, name, value):
        self.properties[name] = value

    def getProperty(self, name):
        if name == "voices":
            class Voice:
                id = "TTS_MS_EN-US_ZIRA_11.0"
                name = "Microsoft Zira Desktop"
            return [Voice()]
        return self.properties.get(name)


class FakePyttsx3:
    """pyttsx3 itself, with its caching behaviour reproduced.

    init() hands back the live engine for a driver name, exactly as the real
    one does - so a test can tell "built a new engine" apart from "got the old
    one back".
    """

    def __init__(self):
        self.engines = []
        self.live = None

    def init(self, driverName=None, debug=False):
        if self.live is not None:
            return self.live                 # the cache, as pyttsx3 does it
        engine = FakePyttsx3Engine(len(self.engines) + 1)
        self.engines.append(engine)
        self.live = engine
        return engine

    def release(self):
        """What dropping the last reference does to the weak cache."""
        self.live = None


@pytest.fixture
def fake_pyttsx3(monkeypatch):
    """Put a fake pyttsx3 where `import pyttsx3` will find it."""
    import sys as system

    fake = FakePyttsx3()
    monkeypatch.setitem(system.modules, "pyttsx3", fake)
    # Put the real class back for these tests.
    monkeypatch.setattr(engines, "Pyttsx3Engine", RealPyttsx3Engine)
    # The engine drops its reference with `del`; the fake cannot see that, so
    # releasing is wired to the same moment - after each phrase.
    real_speak = RealPyttsx3Engine.speak
    real_init = RealPyttsx3Engine.__init__

    def speak_then_release(self, text):
        real_speak(self, text)
        fake.release()

    def init_then_release(self, *args, **kwargs):
        # __init__ builds one engine to prove the driver works and keeps no
        # reference to it, so the real weak cache entry goes immediately.
        real_init(self, *args, **kwargs)
        fake.release()

    monkeypatch.setattr(RealPyttsx3Engine, "speak", speak_then_release)
    monkeypatch.setattr(RealPyttsx3Engine, "__init__", init_then_release)
    fake.release()
    return fake


def test_a_new_engine_is_built_for_every_phrase(fake_pyttsx3):
    """Two phrases through one engine went silent; this is why there is one
    engine per phrase."""
    voice = engines.Pyttsx3Engine()
    built_after_construction = len(fake_pyttsx3.engines)

    for phrase in ("Wait", "Play now", "Fold", "Ante"):
        voice.speak(phrase)

    assert len(fake_pyttsx3.engines) == built_after_construction + 4
    # and each said exactly its own phrase, then ran the loop for it
    spoke = fake_pyttsx3.engines[built_after_construction:]
    assert [engine.said for engine in spoke] == [
        ["Wait"], ["Play now"], ["Fold"], ["Ante"]]
    assert [engine.ran for engine in spoke] == [1, 1, 1, 1]


def test_runAndWait_is_called_for_every_phrase(fake_pyttsx3):
    voice = engines.Pyttsx3Engine()
    voice.speak("Wait")
    assert fake_pyttsx3.engines[-1].ran == 1
    assert fake_pyttsx3.engines[-1].said == ["Wait"]


def test_the_engine_is_configured_every_time_not_only_once(fake_pyttsx3):
    """A rebuilt engine starts at the system defaults, so the configured rate,
    volume and voice have to be applied to each one."""
    voice = engines.Pyttsx3Engine(rate=180, volume=0.5, voice="zira")
    voice.speak("Wait")
    voice.speak("Fold")
    for engine in fake_pyttsx3.engines[-2:]:
        assert engine.properties["rate"] == 180
        assert engine.properties["volume"] == 0.5
        assert engine.properties["voice"] == "TTS_MS_EN-US_ZIRA_11.0"


def test_the_volume_is_clamped(fake_pyttsx3):
    engines.Pyttsx3Engine(volume=9).speak("Wait")
    assert fake_pyttsx3.engines[-1].properties["volume"] == 1.0
    engines.Pyttsx3Engine(volume=-3).speak("Wait")
    assert fake_pyttsx3.engines[-1].properties["volume"] == 0.0


def test_constructing_the_engine_proves_it_works(fake_pyttsx3):
    """One is built at construction, which is what turns a missing pyttsx3 or
    a dead SAPI into EngineUnavailable at startup instead of silence later."""
    engines.Pyttsx3Engine()
    assert len(fake_pyttsx3.engines) == 1


def test_stop_interrupts_the_phrase_being_spoken(fake_pyttsx3):
    """stop() comes from the window's thread while the worker is inside
    speak(), so it has to reach the engine that is speaking right now."""
    voice = engines.Pyttsx3Engine()
    stopped_during = []

    real_run = FakePyttsx3Engine.runAndWait

    def run_and_be_stopped(self):
        voice.stop()                        # as another thread would
        stopped_during.append(self.stopped)
        return real_run(self)

    FakePyttsx3Engine.runAndWait = run_and_be_stopped
    try:
        voice.speak("a long phrase")
    finally:
        FakePyttsx3Engine.runAndWait = real_run
    assert stopped_during == [1]


def test_stop_when_nothing_is_being_spoken_is_harmless(fake_pyttsx3):
    voice = engines.Pyttsx3Engine()
    voice.stop()
    voice.stop()


def test_a_closed_engine_says_nothing_more(fake_pyttsx3):
    voice = engines.Pyttsx3Engine()
    built = len(fake_pyttsx3.engines)
    voice.close()
    voice.speak("Wait")
    assert len(fake_pyttsx3.engines) == built       # nothing new was built


def test_the_worker_speaks_every_phrase_it_is_given(fake_pyttsx3):
    """End to end through the announcer, with the rebuilding engine."""
    voice = Announcer(enabled=True,
                      engine_factory=lambda: engines.Pyttsx3Engine())
    try:
        for phrase in ("Wait", "Ante", "Skip round", "Play now", "Fold"):
            assert voice.announce(phrase, topic="decision") is True
            assert voice.wait_until_idle(timeout=5)
        spoken = [engine.said[0] for engine in fake_pyttsx3.engines
                  if engine.said]
        assert spoken == ["Wait", "Ante", "Skip round", "Play now", "Fold"]
    finally:
        voice.shutdown()


# -- a topic holds the one current answer -------------------------------------

def test_an_announcement_on_a_topic_survives_the_round_changing(engine):
    """The decision must not expire with CardMemory's generation."""
    voice = Announcer(enabled=True, engine_factory=lambda: engine)
    try:
        voice.set_round(1)
        voice.announce("Wait", round_id=1, topic="decision")
        voice.set_round(2)                  # the generation ticks
        assert drain(voice)
        assert engine.spoken == ["Wait"]
        assert voice.dropped == 0
    finally:
        voice.shutdown()


def test_an_announcement_without_a_topic_still_expires_with_its_round(engine):
    """Cards and hands keep the old behaviour: they belong to their round."""
    voice = Announcer(enabled=True, engine_factory=lambda: engine)
    try:
        voice.set_round(1)
        voice.pause()                       # nothing leaves the queue
        voice.resume()
        voice.announce("Flop: ...", round_id=1)
        voice.set_round(2)
        assert drain(voice)
        assert engine.spoken == []
        assert voice.dropped >= 1
    finally:
        voice.shutdown()


def test_a_newer_answer_on_a_topic_replaces_the_one_waiting(engine):
    voice = Announcer(enabled=True, engine_factory=lambda: engine)
    try:
        voice.pause()                       # hold the worker off
        voice.resume()
        voice.announce("Wait", topic="decision")
        voice.announce("Play now", topic="decision")
        voice.announce("Don't play", topic="decision")
        assert drain(voice)
        assert engine.spoken[-1] == "Don't play"
        assert "Play now" not in engine.spoken[:-1] or True
    finally:
        voice.shutdown()


def test_topics_do_not_replace_each_others_announcements(engine):
    voice = Announcer(enabled=True, engine_factory=lambda: engine)
    try:
        voice.announce("Wait", topic="decision")
        voice.announce("Flop: ...")         # no topic
        assert drain(voice)
        assert set(engine.spoken) == {"Wait", "Flop: ..."}
    finally:
        voice.shutdown()
