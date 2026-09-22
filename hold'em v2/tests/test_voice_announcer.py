"""The announcer: queue, worker, states, controls and deduplication.

No sound card is involved. voice.engines.NullEngine records what it was asked
to say, so the queue, the worker thread, the states and the deduplication are
all the real ones - only the speaker is a stand-in.

Determinism: the worker is a real thread, so anything that asserts on what was
spoken waits for the queue to drain rather than sleeping for a guessed length
of time. `drain` below is the only synchronisation these tests need.
"""

import threading
import time

import pytest

from voice import announcer as va
from voice.announcer import Announcer
from voice.engines import EngineUnavailable, NullEngine, SpeechEngine


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
