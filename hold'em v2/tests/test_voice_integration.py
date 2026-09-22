"""The voice, wired into the window, and the promise that it changes nothing.

The requirement these exist for is the one that matters most:

    voice SPEAKING -> tracker RUNNING
    voice PAUSED   -> tracker RUNNING
    voice ERROR    -> tracker RUNNING

The design makes that structural rather than careful. The Announcer is fed
from the window's event loop, not from Tracker._run, so the recognition thread
never touches it - it cannot block on speech because it never calls it. These
check that the wiring is really that way round, and that a real tracker tick
is unaffected by whatever the voice is doing.
"""

import queue
import threading
import time

import pytest

from voice.announcer import Announcer
from voice.engines import NullEngine, SpeechEngine


class BlockingEngine(SpeechEngine):
    """Never finishes a phrase unless released. The worst case for a caller."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def speak(self, text):
        self.started.set()
        self.release.wait(timeout=10)

    def stop(self):
        self.release.set()

    def close(self):
        self.release.set()


# -- the tracker never calls the voice ----------------------------------------

def test_the_tracker_module_does_not_import_the_voice():
    """Structural: the recognition loop cannot wait for speech it cannot see."""
    import tracker

    source = open(tracker.__file__, encoding="utf-8").read()
    assert "voice" not in source.lower().replace("voice_", ""), \
        "tracker.py must not reach into the voice system"
    assert not hasattr(tracker.Tracker, "announce")


def test_the_tracker_exposes_the_round_id_it_already_had():
    """The one tracker change: an existing value published, nothing computed."""
    import tracker

    source = open(tracker.__file__, encoding="utf-8").read()
    assert '"round_id": self.memory.generation,' in source


# -- a real tracker tick, while the voice is stuck speaking -------------------

@pytest.fixture
def stub_tracker(monkeypatch):
    """A Tracker whose screen reading is stubbed, so ticks are cheap."""
    import tracker as tracker_module

    frame = __import__("numpy").zeros((40, 60, 3), dtype="uint8")
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: frame)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "read_table",
                        lambda config, images=None: ({slot: None for slot in
                                                      tracker_module.CARD_SLOTS},
                                                     {slot: {"present": False,
                                                             "card": None,
                                                             "confidence": 0.0,
                                                             "confident": False,
                                                             "ratio": 0.0}
                                                      for slot in tracker_module.CARD_SLOTS}))
    config = {"regions": {}, "poll_interval_seconds": 0.01,
              "scenario_engine": False, "dealer_debug": False,
              "live_diagnostics": False, "debug_save_failures": False}
    return tracker_module.Tracker(config, queue.Queue())


def test_the_tracker_keeps_ticking_while_the_voice_is_stuck(stub_tracker):
    """A phrase that never ends must not cost the recognition loop a thing."""
    blocking = BlockingEngine()
    voice = Announcer(enabled=True, engine_factory=lambda: blocking)
    try:
        voice.announce("a phrase that never finishes")
        assert blocking.started.wait(timeout=5)
        assert voice.is_speaking()

        started = time.time()
        for _ in range(20):
            stub_tracker._tick()
        elapsed = time.time() - started

        assert voice.is_speaking()               # still stuck, deliberately
        assert elapsed < 5.0, (
            "20 ticks took %.2fs while the voice was speaking" % elapsed)
    finally:
        blocking.release.set()
        voice.shutdown()


def test_the_tracker_keeps_ticking_while_the_voice_is_paused(stub_tracker):
    voice = Announcer(enabled=True, engine_factory=lambda: NullEngine())
    try:
        voice.pause()
        for _ in range(10):
            stub_tracker._tick()
        assert voice.is_paused()
        assert stub_tracker.events.qsize() == 10     # every tick still reported
    finally:
        voice.shutdown()


def test_the_tracker_keeps_ticking_when_the_voice_engine_is_broken(stub_tracker):
    def broken():
        raise RuntimeError("no audio device")

    voice = Announcer(enabled=True, engine_factory=broken)
    try:
        voice.announce("hello")
        deadline = time.time() + 5
        while time.time() < deadline and voice.error is None:
            time.sleep(0.01)
        assert voice.error is not None

        for _ in range(10):
            stub_tracker._tick()
        assert stub_tracker.events.qsize() == 10
    finally:
        voice.shutdown()


def test_a_tick_emits_the_round_id_the_voice_needs(stub_tracker):
    stub_tracker._tick()
    kind, payload = stub_tracker.events.get_nowait()
    assert kind == "update"
    assert "round_id" in payload
    assert payload["round_id"] == stub_tracker.memory.generation


# -- the window wires it up ---------------------------------------------------

def test_the_window_feeds_the_voice_from_its_own_event_loop():
    """Not from the tracker thread - that is what keeps the loop clean."""
    import app

    source = open(app.__file__, encoding="utf-8").read()
    assert "self.voice_events.observe(payload, progress)" in source
    assert "self.voice_events.announce_result(record)" in source
    assert "self.announcer.shutdown()" in source


def test_disabling_voice_in_the_config_starts_nothing(monkeypatch):
    """voice_enabled false must cost nothing at all."""
    created = []

    def factory():
        created.append(1)
        return NullEngine()

    voice = Announcer(enabled=False, engine_factory=factory)
    for index in range(50):
        voice.announce("phrase %d" % index, key=("k", index))
    assert created == []
    assert voice.pending() == 0
    assert not any(t.name == "voice" for t in threading.enumerate())
    voice.shutdown()


def test_the_config_carries_the_voice_settings():
    from config.settings import DEFAULT_CONFIG

    for key in ("voice_enabled", "voice_rate", "voice_volume", "voice_name"):
        assert key in DEFAULT_CONFIG
    assert isinstance(DEFAULT_CONFIG["voice_enabled"], bool)
    assert DEFAULT_CONFIG["voice_name"] == ""      # no hard-coded Windows voice


# -- 29: no game interaction --------------------------------------------------

def test_the_voice_package_cannot_touch_the_game():
    """Read-only by construction: no clicking, no betting, no wagering."""
    import os
    import re

    import voice

    folder = os.path.dirname(voice.__file__)
    # Whole words, so "ante" does not match "wanted" and "bet" does not
    # match "between". The first three are what would actually give this
    # package the ability to touch the game.
    forbidden = [r"pyautogui", r"mouse_controller", r"automation",
                 r"\bclick\w*", r"\bante\b",
                 r"\bwager\w*", r"\bbet\b"]

    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py"):
            continue
        source = open(os.path.join(folder, name), encoding="utf-8").read()
        # Strip comments and docstrings: the prose in this package
        # legitimately discusses not doing these things, and prose is not
        # code.
        lines = [line for line in source.splitlines()
                 if not line.strip().startswith("#")]
        code = "\n".join(lines)
        code = "".join(code.split('"""')[::2]).lower()
        for pattern in forbidden:
            found = re.search(pattern, code)
            assert not found, "%s has %r in code" % (name, found.group(0))
