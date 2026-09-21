"""Action Controller: Scenario Engine result -> one mouse click on the LOCAL TEST table.

    Card Recognition -> Scenario Engine -> Action Controller -> Test Poker Table

This module decides nothing about poker. It receives a result that is already
worked out - PLAY, DON'T_PLAY, WAIT, or ANTE_REQUIRED at the start of a hand -
and maps it to at most one click per action per hand:

    ANTE_REQUIRED -> TEST_ANTE
    PLAY          -> TEST_PLAY   (only after TEST_ANTE was clicked this hand)
    DON'T_PLAY    -> nothing
    WAIT          -> nothing
    (BONUS        -> never: the test table shows it, nothing here clicks it)

TEST ONLY. There is one mode, "TEST", and no other is accepted. A click is only
made when all of these hold at that moment:

    1. automation_enabled is true in config/mouse_controller.json (default false)
    2. the local TEST POKER TABLE (automation/test_poker_ui.py) is running and
       has published a fresh heartbeat
    3. the window it names has that exact title and belongs to that process
    4. the click point lies inside that table's button, and Windows reports the
       TEST POKER TABLE window as the window under that point - if another
       always-on-top window (such as the tracker) covers it, the test table is
       raised without taking focus and checked again
    5. the action is valid for this hand, has not been requested before, and
       the hand has not changed by the time the button is pressed

and after the click the table must report that it received it. Anything else is
NO ACTION, with the reason logged. Logged blocks: [TEST HAND START], [TEST HAND]
(round, player, flop, scenario), [TEST ACTION] (ANTE / PLAY / NONE / CANCELLED)
and [TEST HAND SUMMARY] (ante=N, play=N). The mouse moves on a single worker thread,
so the tracker's polling loop is never held up; close() cancels anything still
queued; and PyAutoGUI's fail-safe (throw the mouse into a screen corner) halts
the controller for the session.

Button positions: when ante_button / play_button are null (the default), the
positions the running test table publishes are used, so moving the table does
not break anything. Configured [x, y] points are still checked against the
table's real button rectangles.

Hands, not round ids
--------------------
CardMemory's round id changes every few seconds while the table is empty (it
clears again after every `clear_frames` empty polls), so it cannot say when a
hand starts. HandCycle does: a hand starts when the table has been empty for
`ante_empty_polls` polls in a row after it last showed cards. The first hand
starts at the first such empty table, so a tracker started mid-hand does
nothing until that hand is over. The tracker's round id at the start of a hand
is remembered, and a scenario from an earlier round id is never acted on.
"""

import ctypes
import json
import logging
import os
import queue
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, fields

from poker.scenario_engine import DONT_PLAY, PLAY, WAIT

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "mouse_controller.json")

TEST = "TEST"
MODES = (TEST,)                      # deliberately nothing else

ANTE_REQUIRED = "ANTE_REQUIRED"
DECISIONS = (PLAY, DONT_PLAY, WAIT, ANTE_REQUIRED)

ANTE, PLAY_BUTTON = "ante", "play"
# The test table also shows a BONUS button so it looks like the real layout.
# Nothing here ever clicks it: it is not an action and validation refuses it.
BONUS = "bonus"
CLICKABLE = (ANTE, PLAY_BUTTON)
ACTION_NAMES = {ANTE: "TEST_ANTE", PLAY_BUTTON: "TEST_PLAY"}
LOG_NAMES = {ANTE: "ANTE", PLAY_BUTTON: "PLAY"}

# The local test betting screen (automation/test_betting_screen.py) publishes its
# phase. A button is only clicked in the phase it is open in; a table that
# publishes no phase (automation/test_poker_ui.py) is always open.
OPEN_PHASE = {ANTE: "BETTING", PLAY_BUTTON: "DECISION"}

# What the pre-round rules (poker/scenarios.py) may say with ANTE_REQUIRED.
PREROUND_ANTE, PREROUND_SKIP = "ante", "skip"
NONE = "NONE"

# How a requested action stands within a hand.
REQUESTED, CLICKED, FAILED = "REQUESTED", "CLICKED", "FAILED"

TEST_WINDOW_TITLE = "TEST POKER TABLE"
DEFAULT_STATUS_FILE = os.path.join(tempfile.gettempdir(), "poker_test_table_status.json")


@dataclass
class MouseControllerConfig:
    automation_enabled: bool = False
    mode: str = TEST
    test_window_title: str = TEST_WINDOW_TITLE
    test_window_position: list = field(default_factory=lambda: [10, 40])
    status_file: str = ""                      # empty -> DEFAULT_STATUS_FILE
    ante_button: list = None                   # [x, y] screen pixels, or null = from the table
    play_button: list = None
    move_duration: float = 0.1
    click_duration: float = 0.05
    verify_timeout: float = 1.5                # seconds to wait for the table to confirm
    status_max_age: float = 3.0                # a heartbeat older than this means no table
    ante_empty_polls: int = 3                  # empty polls after cards that start a hand
    require_ante_before_play: bool = True
    phase_wait: float = 15.0                   # seconds to wait for a test screen's betting to open

    def validate(self):
        if self.mode not in MODES:
            raise ValueError("mode must be TEST; %r is not supported" % (self.mode,))
        self.automation_enabled = bool(self.automation_enabled)
        for name in ("ante_button", "play_button", "test_window_position"):
            value = getattr(self, name)
            if value is None:
                continue
            if len(value) != 2:
                raise ValueError("%s must be [x, y] or null" % name)
            setattr(self, name, [int(value[0]), int(value[1])])
        self.move_duration = max(0.0, min(1.0, float(self.move_duration)))
        self.click_duration = max(0.0, min(0.5, float(self.click_duration)))
        self.verify_timeout = max(0.1, float(self.verify_timeout))
        self.status_max_age = max(0.5, float(self.status_max_age))
        self.ante_empty_polls = max(1, int(self.ante_empty_polls))
        self.require_ante_before_play = bool(self.require_ante_before_play)
        self.phase_wait = max(0.0, min(60.0, float(self.phase_wait)))
        self.status_file = self.status_file or DEFAULT_STATUS_FILE
        return self

    def button(self, name):
        return self.ante_button if name == ANTE else self.play_button


def load_mouse_config(path=CONFIG_PATH):
    """The controller's config. Any problem reading it leaves automation OFF."""
    config = MouseControllerConfig()
    if not os.path.exists(path):
        return config.validate()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle) or {}
        known = {item.name for item in fields(MouseControllerConfig)}
        for key, value in stored.items():
            if key in known:
                setattr(config, key, value)
        return config.validate()
    except (OSError, ValueError, TypeError) as exc:
        logger.error("Could not use %s - automation stays disabled: %s", path, exc)
        return MouseControllerConfig().validate()


def save_mouse_config(config, path=CONFIG_PATH):
    data = asdict(config.validate())
    if data["status_file"] == DEFAULT_STATUS_FILE:
        data["status_file"] = ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4)


# -- when a hand starts --------------------------------------------------------------

class HandCycle:
    """Numbers the hands from what the table shows. See the module docstring."""

    def __init__(self, empty_polls=3):
        self.empty_polls = max(1, int(empty_polls))
        self.hand = None
        self._empty = 0
        self._had_cards = False

    def observe(self, table_empty):
        """Fold one poll in. Returns (hand number or None, True if it just started)."""
        if not table_empty:
            self._empty = 0
            self._had_cards = True
            return self.hand, False
        self._empty += 1
        if self._empty >= self.empty_polls and (self.hand is None or self._had_cards):
            self.hand = 1 if self.hand is None else self.hand + 1
            self._had_cards = False
            return self.hand, True
        return self.hand, False


# -- the local test table, seen from outside ---------------------------------------------

class TestTableTarget:
    """Validates clicks against the running TEST POKER TABLE and confirms receipt.

    The table publishes a small JSON heartbeat (its window handle, title, pid,
    button rectangles in screen pixels and click counts). Windows itself is
    asked which window is under the point, so a moved or hidden table fails.
    """

    __test__ = False                      # not a pytest test class

    def __init__(self, config):
        self.config = config

    def read(self):
        try:
            with open(self.config.status_file, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _live_status(self):
        """(status, None) for a running, genuine test table, else (None, reason)."""
        status = self.read()
        if not status:
            return None, "TEST poker table is not running (no status file)"
        if time.time() - float(status.get("heartbeat", 0)) > self.config.status_max_age:
            return None, "TEST poker table is not running (stale heartbeat)"
        if status.get("title") != self.config.test_window_title:
            return None, "status file is not from the TEST poker table"
        hwnd = int(status.get("hwnd") or 0)
        if not _window_is_shown(hwnd, self.config.test_window_title):
            return None, "TEST poker table window is not visible"
        if _window_pid(hwnd) != int(status.get("pid") or -1):
            return None, "window does not belong to the TEST poker table process"
        return status, None

    def unavailable_reason(self):
        """Why the test table cannot be used right now, or None if it can."""
        return self._live_status()[1]

    def button_centre(self, button):
        """The centre of a button as the running table reports it, or None."""
        status, _ = self._live_status()
        rect = ((status or {}).get("buttons") or {}).get(button)
        if not rect:
            return None
        left, top, right, bottom = rect
        return [(left + right) // 2, (top + bottom) // 2]

    def validate(self, button, point):
        """(ok, reason). Checks 2-4 of the module docstring, at this instant."""
        if button not in CLICKABLE:
            return False, "%r is not a TEST action button" % (button,)
        status, reason = self._live_status()
        if status is None:
            return False, reason
        hwnd = int(status["hwnd"])
        rect = (status.get("buttons") or {}).get(button)
        if not rect:
            return False, "TEST %s button is not visible" % button.upper()
        phase = status.get("phase")
        if phase is not None and phase != OPEN_PHASE[button]:
            return False, "TEST %s is closed (phase %s)" % (button.upper(), phase)
        x, y = point
        left, top, right, bottom = rect
        if not (left <= x < right and top <= y < bottom):
            return False, ("click point %s is outside the TEST %s button %s"
                           % (list(point), button.upper(), rect))
        if _window_at(x, y) != hwnd:
            # Another always-on-top window (usually the tracker itself) is over
            # the test table. Bring the verified test table up - without taking
            # focus - and look again.
            _raise_without_activating(hwnd)
            if _window_at(x, y) != hwnd:
                return False, "another window covers the TEST %s button" % button.upper()
        return True, "ok"

    def wait_until_open(self, button, timeout, keep_waiting):
        """(True, None) once the screen's phase opens `button`, else (False, why).

        Returns at once for a table that publishes no phase. `keep_waiting()`
        is asked every poll, so a new hand or a stop ends the wait.
        """
        end = time.time() + timeout
        phase = None
        while True:
            status = self.read() or {}
            phase = status.get("phase")
            if phase is None or phase == OPEN_PHASE[button]:
                return True, None
            if not keep_waiting():
                return False, None
            if time.time() >= end:
                return False, "TEST %s did not open within %.0fs (phase %s)" % (
                    button.upper(), timeout, phase)
            time.sleep(0.05)

    def count(self, button):
        status = self.read() or {}
        return int((status.get("counts") or {}).get(button, 0))

    def wait_for_click(self, button, before, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if self.count(button) > before:
                return True
            time.sleep(0.02)
        return False


def _user32():
    return ctypes.WinDLL("user32") if os.name == "nt" else None


def _window_is_shown(hwnd, title):
    user32 = _user32()
    if user32 is None or not hwnd:
        return False
    handle = ctypes.c_void_p(hwnd)
    if not user32.IsWindow(handle) or not user32.IsWindowVisible(handle) or user32.IsIconic(handle):
        return False
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(handle, buffer, 256)
    return buffer.value == title


def _window_pid(hwnd):
    user32 = _user32()
    if user32 is None or not hwnd:
        return None
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return pid.value


def _window_at(x, y):
    user32 = _user32()
    if user32 is None:
        return None

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32.WindowFromPoint.restype = ctypes.c_void_p
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    child = user32.WindowFromPoint(POINT(int(x), int(y)))
    return user32.GetAncestor(child, 2) if child else None          # GA_ROOT


def _raise_without_activating(hwnd):
    """Put a window at the top of the always-on-top band without focusing it."""
    user32 = _user32()
    if user32 is None or not hwnd:
        return
    HWND_TOPMOST = ctypes.c_void_p(-1)
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos(ctypes.c_void_p(hwnd), HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)


# -- the mouse -------------------------------------------------------------------------

class PyAutoGuiMouse:
    """The only place PyAutoGUI is used. Imported lazily, so a tracker with
    automation disabled never loads it."""

    def __init__(self):
        import pyautogui

        pyautogui.FAILSAFE = True          # mouse into a screen corner aborts
        pyautogui.PAUSE = 0.0              # timing is set explicitly below
        self.pyautogui = pyautogui
        self.FailSafeException = pyautogui.FailSafeException

    def move(self, x, y, duration):
        self.pyautogui.moveTo(x, y, duration=duration)

    def press(self, hold):
        self.pyautogui.mouseDown()
        if hold:
            time.sleep(hold)
        self.pyautogui.mouseUp()


# -- the controller ----------------------------------------------------------------------

def _cards(cards):
    return " ".join(card or "--" for card in cards or []) or "--"


class ActionController:
    """One click per action per hand, from Scenario Engine results. Thread-safe."""

    def __init__(self, config, target=None, mouse=None, log=None, synchronous=False):
        self.config = config
        self.target = target or TestTableTarget(config)
        self._mouse = mouse
        self.log = log or logger
        self.synchronous = synchronous
        self.halted = False
        self.closed = False
        self.round = None
        self.round_floor = None                # tracker round id when this hand began
        self.actions = {}                      # button -> REQUESTED / CLICKED / FAILED
        self.last = {"round": None, "action": NONE, "result": None, "reason": None}
        self.clicks = 0
        self._hand = None                      # what this hand saw, for the summary log
        self._logged = set()
        self._lock = threading.Lock()
        self._jobs = queue.Queue()
        self._worker = None

    # -- public ----------------------------------------------------------------------

    def handle_scenario_result(self, result, action_round=None):
        """Act on one result. Returns the button requested ("ante"/"play") or None.

        `result` carries "decision" (PLAY, DON'T_PLAY, WAIT or ANTE_REQUIRED)
        and, from the tracker, "round_id". `action_round` is the hand it belongs
        to; without it result["round_id"] is used. Never blocks: a click is
        queued for the worker thread.
        """
        if not self.config.automation_enabled or self.halted or self.closed:
            return None
        if not isinstance(result, dict):
            result = {}                          # a malformed payload is just invalid
        decision = result.get("decision")
        hand = action_round if action_round is not None else result.get("round_id")
        source_round = result.get("round_id")
        with self._lock:
            if decision not in DECISIONS or hand is None:
                self._note(hand, decision, "Invalid state for the action controller")
                return None
            if hand != self.round:
                self._new_hand(hand, source_round)
            elif self.round_floor is None and source_round is not None:
                self.round_floor = source_round
            self._record_scenario(hand, decision, result)

            if decision == WAIT:
                self._note(hand, decision, "Waiting for confirmed cards")
                return None
            if decision == DONT_PLAY:
                self._note(hand, decision, "Scenario did not qualify")
                return None
            if decision == ANTE_REQUIRED:
                preround = result.get("preround_action", PREROUND_ANTE)
                if preround not in (PREROUND_ANTE, PREROUND_SKIP):
                    self._note(hand, decision, "Invalid pre-round action %r" % (preround,))
                    return None
                if preround == PREROUND_SKIP:
                    if self._hand is not None and not self._hand.get("skipped"):
                        self._hand["skipped"] = result.get("preround_rule") or "rule"
                    self._note(hand, decision, "Pre-round rule says SKIP: %s"
                               % (result.get("preround_rule") or "rule"))
                    return None
            button = ANTE if decision == ANTE_REQUIRED else PLAY_BUTTON
            if button == PLAY_BUTTON and source_round is not None and \
                    self.round_floor is not None and source_round < self.round_floor:
                self._note(hand, decision, "Scenario belongs to an earlier hand (round %s < %s)"
                           % (source_round, self.round_floor))
                return None
            if button in self.actions:
                self._note(hand, decision, "%s for round %s" % (
                    {REQUESTED: "Already queued", CLICKED: "Already executed",
                     FAILED: "Already attempted and failed"}[self.actions[button]], hand))
                return None
            if button == PLAY_BUTTON and self.config.require_ante_before_play \
                    and self.actions.get(ANTE) != CLICKED:
                self._note(hand, decision, "ANTE has not been clicked for round %s" % hand)
                return None
            if button == ANTE and PLAY_BUTTON in self.actions:
                self._note(hand, decision, "ANTE is not valid after PLAY in round %s" % hand)
                return None
            self.actions[button] = REQUESTED
            self.last = {"round": hand, "action": ACTION_NAMES[button],
                         "result": REQUESTED, "reason": None}

        job = (hand, decision, button)
        if self.synchronous:
            self._execute(job)
        else:
            self._ensure_worker()
            self._jobs.put(job)
        return button

    def status(self):
        """For the UI: the hand, each action's state, and the last action."""
        with self._lock:
            return {"round": self.round, "ante": self.actions.get(ANTE),
                    "play": self.actions.get(PLAY_BUTTON), "last": dict(self.last),
                    "halted": self.halted, "clicks": self.clicks}

    def close(self):
        """Stop for good: nothing queued is clicked, and the worker exits.

        A click already on its way re-checks `closed` before pressing, so it is
        cancelled too. The worker is given a moment to finish so the last
        hand's summary is complete.
        """
        with self._lock:
            already = self.closed
            self.closed = True
        dropped = 0
        try:
            while True:
                if self._jobs.get_nowait() is not None:
                    dropped += 1
        except queue.Empty:
            pass
        if dropped:
            self.log.info("[TEST ACTION]\nCANCELLED\nqueued=%d\nreason=controller stopped", dropped)
        worker = self._worker
        if worker is not None:
            self._jobs.put(None)
            if worker is not threading.current_thread():
                worker.join(timeout=self.config.verify_timeout + 1.0)
        if not already:
            with self._lock:
                self._summarise_hand()
                self._hand = None

    # -- internals --------------------------------------------------------------------

    def _new_hand(self, hand, source_round):
        self._summarise_hand()
        self.round = hand
        self.round_floor = source_round
        self.actions = {}
        self._logged = set()
        self._hand = {"round": hand, "source_round": source_round, "scenario": None,
                      "clicked": {ANTE: 0, PLAY_BUTTON: 0}, "failed": []}
        self.log.info("[TEST HAND START]\nround=%s\ntracker_round=%s", hand, source_round)

    def _record_scenario(self, hand, decision, result):
        """Log what the Scenario Engine sent, each time it changes within the hand."""
        if decision not in (PLAY, DONT_PLAY) or self._hand is None:
            return
        seen = (decision, tuple(result.get("player_cards") or ()),
                tuple(result.get("flop_cards") or ()),
                tuple(result.get("matched_scenarios") or ()))
        if seen == self._hand["scenario"]:
            return
        self._hand["scenario"] = seen
        self.log.info("[TEST HAND]\nround=%s\ntracker_round=%s\nplayer=%s\nflop=%s\n"
                      "scenario=%s\nprimary=%s\nmatched=%s", hand, result.get("round_id"),
                      _cards(result.get("player_cards")), _cards(result.get("flop_cards")),
                      decision, result.get("primary_scenario"),
                      ", ".join(result.get("matched_scenarios") or []) or "none")

    def _summarise_hand(self):
        hand = self._hand
        if not hand:
            return
        scenario = hand["scenario"]
        self.log.info("[TEST HAND SUMMARY]\nround=%s\nplayer=%s\nflop=%s\nscenario=%s\n"
                      "ante=%d\nplay=%d%s%s", hand["round"],
                      _cards(scenario[1]) if scenario else "--",
                      _cards(scenario[2]) if scenario else "--",
                      scenario[0] if scenario else WAIT,
                      hand["clicked"][ANTE], hand["clicked"][PLAY_BUTTON],
                      "\nskipped=%s" % hand["skipped"] if hand.get("skipped") else "",
                      "".join("\nfailed=%s" % item for item in hand["failed"]))

    def _note(self, hand, decision, reason):
        """Log a NONE once per hand for each (decision, reason): this runs every poll."""
        key = (hand, decision, reason)
        if key in self._logged:
            return
        self._logged.add(key)
        self.log.info("[TEST ACTION]\nNONE\nround=%s\ndecision=%s\nreason=%s",
                      hand, decision, reason)

    def _finish(self, hand, decision, button, outcome, reason):
        """Record and log an action's outcome. Call with the lock held."""
        self.last = {"round": hand, "action": ACTION_NAMES[button], "result": outcome,
                     "reason": reason}
        if self._hand is not None and self._hand["round"] == hand:
            if outcome == CLICKED:
                self._hand["clicked"][button] += 1
            else:
                self._hand["failed"].append("%s (%s)" % (LOG_NAMES[button], reason))
        if outcome == CLICKED:
            self.log.info("[TEST ACTION]\n%s\nround=%s\ndecision=%s\nresult=CLICKED",
                          LOG_NAMES[button], hand, decision)
        else:
            self.log.info("[TEST ACTION]\n%s\nround=%s\ndecision=%s\nresult=NONE\nreason=%s",
                          LOG_NAMES[button], hand, decision, reason)

    def _fail(self, hand, decision, button, reason):
        with self._lock:
            if hand == self.round:
                self.actions[button] = FAILED
            self._finish(hand, decision, button, FAILED, reason)

    def _still_current(self, hand):
        """(True, None) while this hand may still be clicked, else (False, why)."""
        with self._lock:
            if self.closed:
                return False, "controller was stopped before the click could be made"
            if self.halted:
                return False, "controller is halted"
            if hand != self.round:
                return False, "round changed before the click could be made"
        return True, None

    def _ensure_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="action-controller",
                                            daemon=True)
            self._worker.start()

    def _work(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                self._execute(job)
            except Exception as exc:  # noqa: BLE001 - one failed click must not end the worker
                hand, decision, button = job
                self._fail(hand, decision, button, "error: %s" % exc)

    def _execute(self, job):
        hand, decision, button = job
        current, why = self._still_current(hand)
        if not current:
            self._fail(hand, decision, button, why)
            return

        wait = getattr(self.target, "wait_until_open", None)
        if wait is not None:
            opened, why = wait(button, self.config.phase_wait,
                               lambda: self._still_current(hand)[0])
            if not opened:
                self._fail(hand, decision, button, why or self._still_current(hand)[1])
                return

        point = self.config.button(button) or self.target.button_centre(button)
        if not point:
            unavailable = getattr(self.target, "unavailable_reason", lambda: None)()
            self._fail(hand, decision, button, unavailable or
                       "TEST %s button is not visible (no position from the table)" % button.upper())
            return
        ok, reason = self.target.validate(button, point)
        if not ok:
            self._fail(hand, decision, button, reason)
            return

        mouse = self._mouse or PyAutoGuiMouse()
        self._mouse = mouse
        before = self.target.count(button)
        # Validation reads files and asks Windows; the hand may have moved on
        # meanwhile. An old hand's action must not even move the mouse.
        current, why = self._still_current(hand)
        if not current:
            self._fail(hand, decision, button, why)
            return
        self.log.info("[TEST ACTION]\n%s\nround=%s\nmoving to (%d, %d)",
                      LOG_NAMES[button], hand, point[0], point[1])
        try:
            mouse.move(point[0], point[1], self.config.move_duration)
            # The mouse took time to get there: the hand may have moved on, or
            # something may now cover the button. Check both right before pressing.
            current, why = self._still_current(hand)
            if not current:
                self._fail(hand, decision, button, why)
                return
            ok, reason = self.target.validate(button, point)
            if not ok:
                self._fail(hand, decision, button, "after moving: " + reason)
                return
            mouse.press(self.config.click_duration)
        except Exception as exc:  # noqa: BLE001
            failsafe = getattr(mouse, "FailSafeException", None)
            if failsafe is not None and isinstance(exc, failsafe):
                with self._lock:
                    self.halted = True
                self._fail(hand, decision, button,
                           "PyAutoGUI fail-safe triggered - controller halted")
            else:
                self._fail(hand, decision, button, "mouse error: %s" % exc)
            return

        received = self.target.wait_for_click(button, before, self.config.verify_timeout)
        with self._lock:
            self.clicks += 1
            if received:
                if hand == self.round:
                    self.actions[button] = CLICKED
                self._finish(hand, decision, button, CLICKED, "received by the TEST table")
            else:
                if hand == self.round:
                    self.actions[button] = FAILED
                self._finish(hand, decision, button, FAILED,
                             "clicked, but the TEST table did not register it")
