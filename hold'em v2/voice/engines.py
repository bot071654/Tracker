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
    """

    def __init__(self, rate=None, volume=None, voice=None):
        import pyttsx3                      # imported here: never at module load

        self._engine = pyttsx3.init()
        if rate is not None:
            self._engine.setProperty("rate", int(rate))
        if volume is not None:
            self._engine.setProperty("volume", max(0.0, min(1.0, float(volume))))
        if voice:
            self._select_voice(voice)

    def _select_voice(self, wanted):
        """Pick the configured voice, or keep the system default and say so.

        `wanted` is matched against the voice's name and its id, case
        insensitively and as a substring, so "zira" finds "Microsoft Zira
        Desktop - English (United States)" without anyone having to paste a
        registry path into the configuration.
        """
        try:
            voices = self._engine.getProperty("voices") or []
        except Exception as exc:            # noqa: BLE001
            logger.warning("Could not list the installed voices: %s", exc)
            return

        needle = str(wanted).strip().lower()
        for voice in voices:
            name = (getattr(voice, "name", "") or "").lower()
            identifier = (getattr(voice, "id", "") or "").lower()
            if needle in name or needle in identifier:
                self._engine.setProperty("voice", voice.id)
                logger.info("Voice: %s", getattr(voice, "name", voice.id))
                return

        logger.warning(
            "No installed voice matches %r - using the system default. "
            "Installed: %s", wanted,
            ", ".join(getattr(v, "name", "?") for v in voices) or "none")

    def speak(self, text):
        self._engine.say(text)
        self._engine.runAndWait()

    def stop(self):
        self._engine.stop()

    def close(self):
        try:
            self._engine.stop()
        except Exception:                   # noqa: BLE001 - shutting down anyway
            pass


def create_engine(rate=None, volume=None, voice=None):
    """A speech engine, or EngineUnavailable with the reason.

    Call this on the thread that will do the speaking.
    """
    try:
        return Pyttsx3Engine(rate=rate, volume=volume, voice=voice)
    except ImportError as exc:
        raise EngineUnavailable(
            "pyttsx3 is not installed - run: python -m pip install pyttsx3"
        ) from exc
    except Exception as exc:                # noqa: BLE001 - drivers fail variously
        raise EngineUnavailable(
            "Could not start the speech engine: %s: %s" % (type(exc).__name__, exc)
        ) from exc
