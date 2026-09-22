"""Speaking what the tracker has already worked out, without ever holding it up.

The tracker polls about five times a second and must never wait for a voice.
So announce() does one thing - put a short record on a queue and return - and a
single background thread does the speaking. The tracker thread never touches
the speech engine, never blocks on it, and never sees its exceptions.

    tracker -> app -> Announcer.announce()  -> queue -> worker -> speaker
                      (returns immediately)

WHAT THIS MODULE DOES NOT DO
    It does not look at the screen, evaluate a hand, decide a winner or
    identify a round. Everything it says was decided elsewhere and handed to
    it. It reads; it never plays. Nothing here clicks, bets or wagers.

PAUSE, HONESTLY
    pyttsx3's engine has no pause() and no resume() - checked, not assumed.
    So pause() does the documented alternative rather than pretending:

        * the phrase being spoken is stopped
        * the state becomes PAUSED
        * nothing new is spoken until resume()

    It is a stop-and-hold, not a freeze mid-word, and resume() does not finish
    the interrupted sentence. Saying otherwise would be a lie told by the
    status line.

WHY STALE SPEECH IS DROPPED
    Speech is far slower than the game. A round takes about 20 seconds and a
    phrase takes two, so a backlog is a voice describing a hand that finished
    minutes ago - worse than silence, because it is wrong. Two rules keep it
    honest:

        * TRANSIENT announcements (cards, hands) belong to the round that
          produced them. When that round is over they are dropped unspoken.
        * FINAL announcements (the winner) are kept. A result is worth hearing
          slightly late; that is the one thing a listener is waiting for.

    The queue is bounded as well. When it is full the oldest transient item is
    dropped to make room, so a slow voice loses the middle of a round rather
    than the end of it.
"""

import logging
import queue
import threading
import time

from voice import engines

logger = logging.getLogger(__name__)

# -- states -------------------------------------------------------------------
# Strings rather than an enum, matching the tracker's own status words
# (WAITING, CONFIRMED, HELD), so a state can be logged and put in the UI as it
# stands.
OFF = "OFF"                # voice_enabled is false; nothing was ever started
IDLE = "IDLE"              # running, nothing to say
SPEAKING = "SPEAKING"      # a phrase is being spoken now
PAUSED = "PAUSED"          # holding: no new speech until resume()
MUTED = "MUTED"            # running and silent
STOPPED = "STOPPED"        # shut down
ERROR = "ERROR"            # the engine could not be started

# -- announcement kinds -------------------------------------------------------
TRANSIENT = "transient"    # true only while its round is current
FINAL = "final"            # worth hearing even a little late

QUEUE_LIMIT = 32           # generous for one round; a backlog means trouble
SHUTDOWN = object()        # sentinel put on the queue to end the worker


class Announcement:
    """One thing to say, and enough about it to decide whether to still say it."""

    __slots__ = ("text", "key", "round_id", "kind")

    def __init__(self, text, key=None, round_id=None, kind=TRANSIENT):
        self.text = text
        self.key = key
        self.round_id = round_id
        self.kind = kind

    def __repr__(self):
        return "Announcement(%r, key=%r, round=%r, %s)" % (
            self.text, self.key, self.round_id, self.kind)


class Announcer:
    """The voice. Thread-safe; every public method may be called from any thread.

    `engine_factory` is called once, on the worker thread, and must return a
    voice.engines.SpeechEngine. The default starts pyttsx3; the tests pass a
    stub, which is how all of this is tested without a sound card.
    """

    def __init__(self, enabled=True, rate=None, volume=None, voice=None,
                 engine_factory=None, queue_limit=QUEUE_LIMIT):
        self._enabled = bool(enabled)
        self._rate, self._volume, self._voice = rate, volume, voice
        self._engine_factory = engine_factory or (
            lambda: engines.create_engine(rate=rate, volume=volume, voice=voice))

        self._queue = queue.Queue(maxsize=max(1, int(queue_limit)))
        self._lock = threading.RLock()          # guards the flags and _spoken
        self._worker = None
        self._engine = None                     # worker thread only
        self._shutdown = threading.Event()

        self._paused = False
        self._muted = False
        self._speaking = False
        self._error = None
        self._round_id = None                   # the round considered current
        self._spoken = set()                    # keys already said this round
        self._dropped = 0                       # for the log, and for tests

    # -- state ----------------------------------------------------------------

    @property
    def enabled(self):
        return self._enabled

    def state(self):
        """One of the module's state words. Cheap; safe from any thread."""
        with self._lock:
            if not self._enabled:
                return OFF
            if self._error:
                return ERROR
            if self._shutdown.is_set():
                return STOPPED
            if self._paused:
                return PAUSED
            if self._muted:
                return MUTED
            return SPEAKING if self._speaking else IDLE

    def is_speaking(self):
        with self._lock:
            return self._speaking

    def is_paused(self):
        with self._lock:
            return self._paused

    def is_muted(self):
        with self._lock:
            return self._muted

    @property
    def error(self):
        with self._lock:
            return self._error

    @property
    def dropped(self):
        with self._lock:
            return self._dropped

    def pending(self):
        """How many announcements are waiting. For the tests and the log."""
        return self._queue.qsize()

    def wait_until_idle(self, timeout=5.0):
        """Block until everything queued has been spoken or discarded.

        True when the voice went quiet, False on timeout. Built on the queue's
        own task_done bookkeeping rather than on qsize, because an item is off
        the queue for the whole time it is being spoken - polling qsize would
        report "finished" the instant the worker picked a phrase up, which is
        before a word of it has been said.

        For the tests, and for anyone who wants to let the voice finish.
        """
        deadline = time.monotonic() + float(timeout)
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
        return True

    # -- the round the voice believes is current -------------------------------

    def set_round(self, round_id):
        """A new round has started: forget what was said, drop what was unsaid.

        The round id is the tracker's own (CardMemory's generation). Nothing
        here invents one.
        """
        with self._lock:
            if round_id == self._round_id:
                return
            self._round_id = round_id
            self._spoken.clear()
        self._discard_stale()

    # -- announcing ------------------------------------------------------------

    def announce(self, text, key=None, round_id=None, kind=TRANSIENT):
        """Queue something to say. Returns whether it was accepted.

        Never blocks and never raises. Refused when voice is off, shut down,
        paused, muted, already said this round, or the queue is full of things
        that matter more.
        """
        if not text:
            return False
        with self._lock:
            if not self._enabled or self._shutdown.is_set() or self._error:
                return False
            if self._paused or self._muted:
                return False
            if key is not None:
                if key in self._spoken:
                    return False           # the poll loop saw the same thing again
                self._spoken.add(key)
            if round_id is None:
                round_id = self._round_id

        item = Announcement(text, key=key, round_id=round_id, kind=kind)
        if not self._put(item):
            with self._lock:
                self._spoken.discard(key)  # it was not said, so do not remember it
            return False
        self._ensure_worker()
        return True

    def _put(self, item):
        """Queue an announcement, making room by dropping a transient one."""
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            pass

        # Full. Sacrifice the oldest transient item rather than the newest, so
        # what is lost is the middle of a round and not its result.
        kept, sacrificed = [], False
        try:
            while True:
                queued = self._queue.get_nowait()
                # Every get must be matched by a task_done, or the queue's own
                # accounting never reaches zero and wait_until_idle hangs. The
                # put below puts the item back on the books.
                self._queue.task_done()
                if not sacrificed and getattr(queued, "kind", FINAL) == TRANSIENT:
                    sacrificed = True
                    with self._lock:
                        self._dropped += 1
                    continue
                kept.append(queued)
        except queue.Empty:
            pass
        for queued in kept:
            try:
                self._queue.put_nowait(queued)
            except queue.Full:              # pragma: no cover - cannot happen
                break
        if not sacrificed:
            with self._lock:
                self._dropped += 1
            return False                    # everything queued is FINAL; keep it
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:                  # pragma: no cover
            return False

    # -- controls --------------------------------------------------------------

    def pause(self):
        """Hold the voice. The tracker is not affected in any way.

        Stops the phrase in progress, because pyttsx3 cannot suspend one.
        """
        with self._lock:
            if not self._enabled:
                return
            self._paused = True
        self._interrupt()
        self._discard(lambda item: item.kind == TRANSIENT)
        logger.info("[VOICE] paused")

    def resume(self):
        """Let the voice speak again, without replaying a stale backlog."""
        with self._lock:
            if not self._enabled:
                return
            self._paused = False
        self._discard_stale()
        logger.info("[VOICE] resumed")

    def stop(self):
        """Stop speaking now and drop what was waiting. The tracker continues.

        Unlike pause(), this does not latch: the next announcement is spoken.
        It is the "be quiet about this round" control.
        """
        with self._lock:
            if not self._enabled:
                return
        self._interrupt()
        self._discard(lambda item: True)
        logger.info("[VOICE] stopped speaking; queue cleared")

    def mute(self):
        """Silence. Nothing is queued while muted, so unmuting says nothing old."""
        with self._lock:
            if not self._enabled:
                return
            self._muted = True
        self._interrupt()
        self._discard(lambda item: item.kind == TRANSIENT)
        logger.info("[VOICE] muted")

    def unmute(self):
        with self._lock:
            if not self._enabled:
                return
            self._muted = False
        self._discard_stale()
        logger.info("[VOICE] unmuted")

    def set_enabled(self, enabled):
        """Turn the voice on or off at runtime.

        Turning it off silences it and stops the worker; turning it on does
        not start anything until there is something to say.
        """
        with self._lock:
            if bool(enabled) == self._enabled:
                return
            self._enabled = bool(enabled)
            turning_off = not self._enabled
        if turning_off:
            self._interrupt()
            self._discard(lambda item: True)
            self._stop_worker()
            logger.info("[VOICE] disabled")
        else:
            with self._lock:
                self._error = None
                self._shutdown.clear()
            logger.info("[VOICE] enabled")

    def shutdown(self, timeout=3.0):
        """Stop accepting work, end the worker, release the engine.

        Safe to call more than once, and safe to call when nothing was ever
        started. Leaves no thread behind to keep the process alive.
        """
        with self._lock:
            already = self._shutdown.is_set()
            self._shutdown.set()
        if already:
            return
        self._interrupt()
        self._discard(lambda item: True)
        self._stop_worker(timeout)
        logger.info("[VOICE] shut down")

    # -- queue housekeeping ----------------------------------------------------

    def _discard(self, predicate):
        """Remove every queued announcement the predicate accepts."""
        kept = []
        removed = 0
        try:
            while True:
                item = self._queue.get_nowait()
                # A discarded announcement is finished with, as far as the
                # queue is concerned; one task_done per get, and the ones put
                # back are counted again by the put.
                self._queue.task_done()
                if item is SHUTDOWN:
                    kept.append(item)
                elif predicate(item):
                    removed += 1
                else:
                    kept.append(item)
        except queue.Empty:
            pass
        for item in kept:
            try:
                self._queue.put_nowait(item)
            except queue.Full:              # pragma: no cover
                break
        if removed:
            with self._lock:
                self._dropped += removed
        return removed

    def _discard_stale(self):
        """Drop transient announcements from a round that has moved on."""
        with self._lock:
            current = self._round_id
        return self._discard(
            lambda item: item.kind == TRANSIENT
            and item.round_id is not None
            and item.round_id != current)

    def _interrupt(self):
        """Cut off the phrase being spoken, if any. Never raises."""
        engine = self._engine
        if engine is None:
            return
        try:
            engine.stop()
        except Exception as exc:            # noqa: BLE001 - a voice must not throw
            logger.warning("[VOICE] could not stop the engine: %s", exc)

    # -- the worker ------------------------------------------------------------

    def _ensure_worker(self):
        """Start the one worker thread, if it is not already running."""
        with self._lock:
            if self._shutdown.is_set() or not self._enabled:
                return
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run, name="voice", daemon=True)
            self._worker.start()

    def _stop_worker(self, timeout=3.0):
        with self._lock:
            worker = self._worker
            self._worker = None
        if worker is None:
            return
        try:
            self._queue.put_nowait(SHUTDOWN)
        except queue.Full:
            self._discard(lambda item: True)
            try:
                self._queue.put_nowait(SHUTDOWN)
            except queue.Full:              # pragma: no cover
                pass
        worker.join(timeout=timeout)
        if worker.is_alive():               # pragma: no cover - a wedged driver
            logger.warning("[VOICE] the worker did not stop within %.1fs", timeout)

    def _run(self):
        """The one speaking thread. Owns the engine; nothing else touches it."""
        try:
            self._engine = self._engine_factory()
        except Exception as exc:            # noqa: BLE001 - includes EngineUnavailable
            with self._lock:
                self._error = str(exc)
            logger.error("[VOICE] unavailable, continuing without it: %s", exc)
            self._discard(lambda item: True)
            return

        while True:
            item = self._queue.get()
            try:
                if item is SHUTDOWN:
                    break
                if not self._should_speak(item):
                    continue
                with self._lock:
                    self._speaking = True
                try:
                    self._engine.speak(item.text)
                except Exception as exc:    # noqa: BLE001 - one bad phrase only
                    logger.warning("[VOICE] could not speak %r: %s", item.text, exc)
                finally:
                    with self._lock:
                        self._speaking = False
            finally:
                self._queue.task_done()

        try:
            self._engine.close()
        except Exception:                   # noqa: BLE001 - shutting down anyway
            pass
        self._engine = None

    def _should_speak(self, item):
        """Decided at the last moment, because the world moves while we queue."""
        with self._lock:
            if self._shutdown.is_set() or not self._enabled:
                return False
            if self._paused or self._muted:
                return False
            if (item.kind == TRANSIENT and item.round_id is not None
                    and self._round_id is not None
                    and item.round_id != self._round_id):
                self._dropped += 1
                return False
        return True
