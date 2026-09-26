"""Is the game window the one in front? Pause the tracker when it is not.

The tracker grabs the whole monitor and finds the table in the picture. When
the game window is not in front, that picture is of something else, and every
box either reads another application's pixels or falls back to the last layout
that worked - tracker._locate says as much in its own comment. Reading on is
not neutral: it spends confidence on cards that are not there.

So one gate, in the tracker's own loop, in front of the one method that does
all the work.

WHAT THIS CAN AND CANNOT SEE

It asks Windows for the foreground window and reads its title. That is a
WINDOW, not a browser tab. Nothing here enumerates tabs and nothing talks to
Chrome.

In practice it still notices a tab change, because Chrome puts the active
tab's page title in its window title - switch tabs and the title read here
changes with it. That is a consequence of how Chrome names its window, not tab
detection, and it inherits the page's own title: two tabs on one site can read
alike, and a page that rewrites its title (an unread count, say) changes it
without the tab changing. Read it as "the window in front is showing something
whose title matches", because that is exactly what it is.

PLATFORMS

Windows reaches user32 through ctypes, the same way automation/mouse_controller
does - no pywin32, and nothing imported at module load that does not exist
elsewhere. Anywhere else foreground_title() returns None, which FocusGate reads
as "cannot tell" and treats as ACTIVE, so the tracker behaves exactly as it
always has.
"""

import ctypes
import logging
import os
import time

logger = logging.getLogger(__name__)

# The two states. Named rather than boolean so a log line says which.
GAME_ACTIVE = "GAME_ACTIVE"
GAME_INACTIVE = "GAME_INACTIVE"

# app.py's root.title(...) - kept here, not re-typed there, so the one place
# that has to recognise this title and the one place that sets it cannot
# drift apart. See FocusGate's own-window grace period below.
APP_WINDOW_TITLE = "Poker Hand Tracker"

# How long after a FocusGate is created its own window is allowed to be the
# foreground one without that being read as "the person left the game" - see
# the docstring on FocusGate.look().
STARTUP_GRACE_SECONDS = 3.0


def _user32():
    """user32 on Windows, None anywhere else."""
    return ctypes.WinDLL("user32") if os.name == "nt" else None


def foreground_title():
    """The title of the window in front, or None when it cannot be told.

    None is not "no window": it means this platform cannot answer, and the
    caller must not read it as "the game is not in front".
    """
    user32 = _user32()
    if user32 is None:
        return None
    try:
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        handle = user32.GetForegroundWindow()
        if not handle:
            return ""                   # a real answer: nothing is in front
        length = user32.GetWindowTextLengthW(ctypes.c_void_p(handle))
        buffer = ctypes.create_unicode_buffer(int(length) + 1)
        user32.GetWindowTextW(ctypes.c_void_p(handle), buffer, int(length) + 1)
        return buffer.value
    except Exception:                   # noqa: BLE001 - never stop the tracker
        logger.exception("Could not read the foreground window; "
                         "treating the game as active")
        return None


class FocusGate:
    """Whether the tracker may read the screen this poll.

    One object holds the state, so no recognition code has to ask the
    question. `observe()` folds in one look and returns the transition - and
    only the transition, so a window left in the background for a minute
    produces one event, not three hundred.

    Off unless `target` names something. An empty target is the shipped
    default and means every poll is GAME_ACTIVE, which is how the tracker
    behaved before this existed.
    """

    def __init__(self, target, read_title=foreground_title,
                own_title=APP_WINDOW_TITLE, grace_seconds=STARTUP_GRACE_SECONDS,
                clock=time.time):
        self.target = (target or "").strip()
        self._read_title = read_title
        self._own_title = (own_title or "").strip()
        self._grace_seconds = float(grace_seconds)
        self._clock = clock
        self._created_at = clock()
        # Optimistic: until a look says otherwise the tracker runs. A gate
        # that starts paused would stall a tracker on a machine that cannot
        # answer the question at all.
        self.state = GAME_ACTIVE
        self._reported = None           # the last state handed to a caller
        self.title = None               # what the last look actually saw
        # How many consecutive raw looks in a row have not matched. Only ever
        # used to decide whether to COMMIT a move into GAME_INACTIVE - see
        # observe(). Recovering to GAME_ACTIVE is never held back by this.
        self._inactive_streak = 0

    @property
    def enabled(self):
        """Whether the gate has anything to look for."""
        return bool(self.target)

    @property
    def paused(self):
        return self.state == GAME_INACTIVE

    def look(self):
        """The state the world is in right now, without recording it.

        None is a third answer, distinct from both states: inconclusive,
        because this poll's foreground window was the tracker's OWN, and
        within the grace period from when this gate was created (a fresh one
        every time Start Tracker is pressed - see Tracker.__init__). Pressing
        that button, or the window it opens, legitimately holds the
        foreground for a moment before the person's attention (and the
        window in front) returns to the game - that is not the same claim as
        "the person switched to something else", and observe() must not act
        on it as though it were. Once the grace period has elapsed, the
        tracker's own window is judged exactly like any other: still in
        front of the game after several seconds is exactly what leaving the
        game to look at something else looks like, own window or not.
        """
        if not self.enabled:
            return GAME_ACTIVE
        title = self._read_title()
        self.title = title
        if title is None:
            # This platform cannot say. Not a reason to stop tracking.
            return GAME_ACTIVE
        if self.target.lower() in title.lower():
            return GAME_ACTIVE
        if (self._own_title and self._own_title.lower() in title.lower()
                and self._clock() - self._created_at < self._grace_seconds):
            return None
        return GAME_INACTIVE

    def observe(self):
        """Fold one look in. The transition, or None when nothing changed.

        A gate with no target reports nothing at all, ever. Saying "the game
        is active" when nothing is being watched would put a claim on screen
        about a check that is not happening.

        look() returning None - the startup grace period, our own window -
        is folded in as a complete no-op: neither state nor the debounce
        streak moves. It is not a look that says "still active" (state may
        already be paused, from before this gate existed, and this poll must
        not un-pause it) and not one that says "gone inactive" either; it is
        simply not a look that answers the question at all, so nothing here
        is updated on the strength of it.

        A single non-matching real look does not commit the state to
        GAME_INACTIVE - it takes two of them in a row. This is a second,
        narrower safety net behind the grace period above: even a look this
        application cannot explain at all - own window or not - is given one
        poll's benefit of the doubt before it is acted on, so a one-poll
        fluke never takes the banner down or pauses recognition on its own.
        A second consecutive non-match is a different claim, and is honoured
        immediately.

        Recovering the other way is never delayed. A matching look is
        accepted the instant it is seen, whether that is the first look ever
        made or the middle of a long paused stretch - there is nothing here
        it needs to be sure about a match resolves any ambiguity outright,
        and it is also what resets the streak, so two non-matches only ever
        count if nothing matching happened in between.
        """
        if not self.enabled:
            self.state = GAME_ACTIVE
            return None
        raw = self.look()
        if raw is None:
            return None
        if raw == GAME_ACTIVE:
            self._inactive_streak = 0
            self.state = GAME_ACTIVE
        else:
            self._inactive_streak += 1
            if self._inactive_streak >= 2:
                self.state = GAME_INACTIVE
            # else: exactly one non-match so far - the state is left exactly
            # as it was. If that was GAME_ACTIVE, it stays GAME_ACTIVE
            # (the transient is absorbed); if it was already GAME_INACTIVE,
            # it stays GAME_INACTIVE (nothing to recover from a non-match).
        if self.state == self._reported:
            return None
        self._reported = self.state
        # One block per TRANSITION only - never once per poll. A tracker
        # polling five times a second would otherwise fill the log with
        # nothing new to say; this line only appears when the STATE actually
        # changes, which is the whole point of storing _reported.
        #
        # "matched" is the raw look this poll actually made, not self.state -
        # they can now differ. The one poll that gets absorbed by the
        # debounce has matched=False and state=GAME_ACTIVE at the same time,
        # and the log has to say which is which or it is worse than useless
        # for exactly the kind of question this line exists to answer.
        logger.info(
            "[FOCUS] foreground_title=%r configured_game_window_title=%r "
            "matched=%s state=%s",
            self.title, self.target, raw == GAME_ACTIVE, self.state)
        return self.state


# -- one-time startup focus restoration ---------------------------------------
#
# A separate concern from FocusGate on purpose. FocusGate observes what is in
# front, on every poll, for as long as the tracker runs. These two functions
# do one thing, once, at startup: creating the tracker's own Tk window is, on
# Windows, enough on its own for the OS to grant it the foreground - even
# when some other application was genuinely active a moment before and the
# person never asked for the tracker to be in front. This is not -topmost,
# and does not touch it: topmost only decides stacking order; this decides
# which window currently holds input focus, a different thing entirely.
#
# Confirmed directly, in a fresh process each time so repeated window
# creation could not be mistaken for the fix itself: SetWindowPos with
# SWP_NOACTIVATE, applied before the window is ever mapped, does not stop
# this - Tk's own startup requests activation before any Python-level code
# gets a chance to intervene. The only technique that reliably worked:
# capture what was in front before the tracker's window existed, and hand it
# straight back immediately afterward. A process asking for the foreground on
# behalf of some OTHER, unrelated window is refused by Windows' own
# anti-focus-stealing rules; this process just legitimately held the
# foreground itself, which is one of the few conditions under which the
# request is allowed to succeed.

def capture_foreground_window():
    """The window in front right now, before this application creates its own.

    Call this as early as possible - before tk.Tk() - because the steal
    restore_foreground_window() corrects happens inside that call itself.
    None on any platform this cannot be asked on, or when nothing answers.
    """
    user32 = _user32()
    if user32 is None:
        return None
    try:
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = user32.GetForegroundWindow()
        return hwnd if hwnd else None
    except Exception:                   # noqa: BLE001 - never block startup
        logger.exception("Could not read the foreground window before startup")
        return None


def restore_foreground_window(hwnd):
    """Hand the foreground back to `hwnd`, once, right after tk.Tk().

    Safe everywhere and never raises. Does nothing if `hwnd` is falsy - no
    previous window was captured, or this platform could not say - and does
    nothing where there is no user32 to ask. A failed restore is logged, not
    raised: it leaves the tracker's own window in front, which is exactly
    what would have happened anyway without calling this at all.
    """
    if not hwnd:
        return False
    user32 = _user32()
    if user32 is None:
        return False
    try:
        user32.SetForegroundWindow.restype = ctypes.c_int
        ok = bool(user32.SetForegroundWindow(ctypes.c_void_p(hwnd)))
        if not ok:
            logger.debug(
                "Could not hand the foreground back to the previous window "
                "(%r); the tracker's own window stays in front", hwnd)
        return ok
    except Exception:                   # noqa: BLE001 - never block startup
        logger.exception("Restoring the previous foreground window failed")
        return False
