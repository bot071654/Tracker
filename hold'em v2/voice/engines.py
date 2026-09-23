"""The speech engine, behind a two-method interface.

Kept separate from the announcer for two reasons. The tests need to drive the
announcer without a sound card, and swapping in a recording stub is the only
honest way to do that. And pyttsx3 has a constraint the announcer should not
have to know about: on Windows it is a COM object, so it must be created and
used on one thread - the announcer's worker creates it and nobody else touches
it.

What this engine can and cannot do was measured, not assumed:

    speaking from a worker thread   works; runAndWait blocks until done
    stop() from another thread      works; a 7-second phrase was cut at 1.1s
    pause() / resume()              DO NOT EXIST on pyttsx3's engine

That last line is why the announcer pauses the way it does. See announcer.py.
"""

import logging
import sys
import threading

logger = logging.getLogger(__name__)


class EngineUnavailable(RuntimeError):
    """Raised when no speech engine could be started."""


class SpeechEngine:
    """What the announcer needs from a voice.

    speak() blocks until the phrase is finished; stop() interrupts it from
    another thread; close() releases whatever the engine holds.
    """

    def speak(self, text):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def close(self):
        pass


class NullEngine(SpeechEngine):
    """Says nothing, successfully.

    Used when speech is unavailable and by the tests. The announcer runs
    exactly as it would otherwise - the queue, the worker, the states and the
    deduplication are all real - which is what makes it testable without a
    speaker.
    """

    def __init__(self):
        self.spoken = []
        self.stopped = 0
        self.closed = False

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        self.stopped += 1

    def close(self):
        self.closed = True


class Pyttsx3Engine(SpeechEngine):
    """Windows SAPI5 through pyttsx3. Offline, no account, no API key.

    Must be constructed on the thread that will speak through it.

    ONE ENGINE PER PHRASE, AND WHY

    pyttsx3's engine is not reusable for long. Driven the way the worker drives
    it - say(), runAndWait(), repeat - it speaks the first phrase or two and
    then goes quiet: runAndWait() returns in about 0.2s instead of the second
    and a half the phrase takes, with no exception and nothing in the log.
    Measured here, eight phrases through one engine:

        Wait         1.69s  spoke
        Ante         0.46s  spoke
        Skip round   0.22s  SILENT
        Play now     0.24s  SILENT
        ... and silent from then on

    That is what "accepted=True and I hear nothing" was. The application was
    doing everything right and handing the phrase to a driver that had quietly
    stopped producing audio.

    Building a new engine for each phrase speaks every time, and costs about
    0.08s after the first - measured, not assumed:

        Wait         build 0.98s  speak 1.76s  spoke
        Ante         build 0.08s  speak 1.23s  spoke
        Skip round   build 0.09s  speak 1.57s  spoke
        ... all eight spoke

    There is one catch. pyttsx3.init() does not always build an engine: it
    keeps a WeakValueDictionary of live ones and hands back the existing entry
    for a driver name. Holding a strong reference to the old engine therefore
    guarantees the next init() returns that same dead one - which is why
    closing and re-initialising did not help either. The reference is dropped
    as soon as the phrase ends, so the cache entry goes with it.
    """

    def __init__(self, rate=None, volume=None, voice=None):
        import pyttsx3                      # imported here: never at module load

        self._pyttsx3 = pyttsx3
        self._rate, self._volume, self._voice = rate, volume, voice
        # The engine currently speaking, so stop() can interrupt it from
        # another thread. Guarded because stop() is called from the window's
        # thread while the worker is inside speak().
        self._lock = threading.Lock()
        self._speaking = None
        self._closed = False
        # Build one now and let it go: this is what turns "pyttsx3 is missing"
        # or "SAPI will not start" into EngineUnavailable at startup rather
        # than into silence at the first announcement.
        self._build()

    def _build(self):
        """A new engine, configured. The caller owns the only reference."""
        engine = self._pyttsx3.init()
        if self._rate is not None:
            engine.setProperty("rate", int(self._rate))
        if self._volume is not None:
            engine.setProperty(
                "volume", max(0.0, min(1.0, float(self._volume))))
        if self._voice:
            self._select_voice(engine, self._voice)
        return engine

    def _select_voice(self, engine, wanted):
        """Pick the configured voice, or keep the system default and say so.

        `wanted` is matched against the voice's name and its id, case
        insensitively and as a substring, so "zira" finds "Microsoft Zira
        Desktop - English (United States)" without anyone having to paste a
        registry path into the configuration.
        """
        try:
            voices = engine.getProperty("voices") or []
        except Exception as exc:            # noqa: BLE001
            logger.warning("Could not list the installed voices: %s", exc)
            return

        needle = str(wanted).strip().lower()
        for voice in voices:
            name = (getattr(voice, "name", "") or "").lower()
            identifier = (getattr(voice, "id", "") or "").lower()
            if needle in name or needle in identifier:
                engine.setProperty("voice", voice.id)
                return

        logger.warning(
            "No installed voice matches %r - using the system default. "
            "Installed: %s", wanted,
            ", ".join(getattr(v, "name", "?") for v in voices) or "none")

    def speak(self, text):
        """Say one phrase, on a driver built for it and released afterwards."""
        with self._lock:
            if self._closed:
                return
        engine = self._build()
        with self._lock:
            self._speaking = engine
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            with self._lock:
                self._speaking = None
            # Dropping the last reference here is not tidiness: it is what
            # lets the next init() build a new engine instead of returning
            # this one from pyttsx3's cache.
            del engine

    def stop(self):
        """Cut the phrase in progress short. Safe from another thread."""
        with self._lock:
            engine = self._speaking
        if engine is None:
            return
        try:
            engine.stop()
        except Exception as exc:            # noqa: BLE001 - it may have ended
            logger.debug("Could not stop the phrase in progress: %s", exc)

    def close(self):
        with self._lock:
            self._closed = True
        self.stop()


def create_engine(rate=None, volume=None, voice=None):
    """A speech engine, or EngineUnavailable with the reason.

    Call this on the thread that will do the speaking.
    """
    try:
        return Pyttsx3Engine(rate=rate, volume=volume, voice=voice)
    except ImportError as exc:
        # Name the interpreter. "pyttsx3 is not installed" on its own sent
        # somebody hunting: pyttsx3 *was* installed - in the project's .venv -
        # while the application was being launched with a different Python that
        # had the other eight requirements but not this one, which had only
        # just been added to requirements.txt. The package alone does not
        # identify which environment is missing it, and a machine generally has
        # more than one.
        raise EngineUnavailable(
            "pyttsx3 is not installed for %s - run: "
            "\"%s\" -m pip install -r requirements.txt"
            % (sys.executable, sys.executable)
        ) from exc
    except Exception as exc:                # noqa: BLE001 - drivers fail variously
        raise EngineUnavailable(
            "Could not start the speech engine: %s: %s" % (type(exc).__name__, exc)
        ) from exc
