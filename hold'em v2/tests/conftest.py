"""Makes the project importable from the tests, and keeps test output tidy."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def no_real_speech(monkeypatch):
    """No test opens the sound card.

    Six test files build the real App, which builds a real Announcer with
    whatever config/config.json says - and it says voice_enabled true. While
    pyttsx3 was not installed that cost nothing: the worker raised ImportError
    and stopped. Once it was installed those tests began starting a real SAPI5
    engine, and loading COM on a background thread while pytest was tearing
    down Tk crashed the interpreter outright.

    So the suite passing depended on a package being absent, which is not a
    property a suite should have. Tests should not produce audio in any case.

    Only Pyttsx3Engine is replaced - the one class that touches COM. Everything
    around it stays real, including create_engine's own error handling, which
    several tests are specifically about; a test that wants a different
    Pyttsx3Engine just patches it again and its patch wins.

    It raises rather than returning a NullEngine, which is the other obvious
    choice, because a working engine keeps the announcer's worker thread alive
    for the rest of the test. Six App tests never shut their announcer down,
    and a worker still blocked on the queue when pytest finalises Tk is how
    tkinter.__del__ ends up running off the main thread. Raising reproduces
    what the whole suite was stable under before pyttsx3 was installed - the
    worker records the error and ends - without that stability depending on a
    package being missing. No test asserts anything about the App's voice
    state; the voice has its own 70 tests, which inject their own engine.
    """
    from voice import engines

    def no_engine(*_args, **_kwargs):
        raise ImportError("No module named 'pyttsx3' (disabled for tests)")

    monkeypatch.setattr(engines, "Pyttsx3Engine", no_engine)
