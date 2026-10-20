"""Read-only Windows machine-state clues for the validation harness. DIAGNOSTICS ONLY: it looks, it never touches, and it never concludes.

WHY

A 600-second practice run (PHT-validation-20261019-230713) reported 1,697 consecutive polls in which the captured picture did not change at all
(from t=207.9 s to t=504.7 s) while the casino window stayed in front. Two explanations fit what was kept, and the evidence cannot separate them:

  * DISPLAY IDLE - the display went to sleep after the battery timeout (180 s) and the capture stopped updating;
  * CASINO PAGE STALL - the page or stream stopped redrawing while the display stayed on.

(A locked session, a secure-desktop prompt or a capture failure are further possibilities.) This module records, for a future run only, the
read-only clues that could tell them apart. It never says which one happened: every output is a clue, labelled as one, kept under the hypothesis
it bears on, and `conclusion` is always "not determined".

WHAT IT RECORDS (all read-only, none of it changes a setting, touches the casino or waits on anything)

  * every poll: the captured frame's size and its mean brightness (a coarse sample of the pixels the tracker already captured), with the poll's
    capture duration and screen-change measure, which the tracker already reports;
  * about once a second: Windows idle time since the last keyboard or mouse input (`GetLastInputInfo`), whether the machine is on AC or battery and
    the battery level (`GetSystemPowerStatus`), which window is in the foreground WITHOUT recording its title (executable name, window class, whether
    the title matches the game's, whether it is minimised), and whether the input desktop can be opened (`OpenInputDesktop`; refused on a lock or
    secure-desktop screen);
  * once, at preflight: the display and sleep timeouts for AC and battery, read from `powercfg /query` text the harness hands in, and the same
    probes as above.

For every run of at least `still_polls` polls with no screen change ("a still episode") it keeps one record: when, how long, the capture time before,
during and after, the brightness range, the idle time at the start against the display timeout for the power source in use, whether input arrived
during the episode, whether the foreground window or power source changed, and how often the input desktop was not accessible.

WHAT IT CANNOT KNOW

Windows offers no read-only polling call for the display's power state, so display-off is never observed directly: after a run the Kernel-Power
events for its time window are the other evidence. A dark frame and an unchanged frame are clues, not proof: a sleeping display may be captured
as the last picture it showed, and a stalled page can also be dark. An input during an episode with the picture still unchanged argues against a
sleeping display (it would wake) but does not by itself prove a stalled page. Idle time and the timeouts are the user's machine settings as
Windows reports them, and the idle-at-start figure can be up to one sampling interval late.

PRIVACY AND SAFETY

No window title is recorded (only whether it matches the game's), no picture is saved, nothing is written to disk by this module, and the probes
run on the harness's own thread (never the tracker's polling thread), a few microseconds each, at most once a second apart.
"""

import collections
import ctypes
import os
import re

DEFAULT_GAME_TITLE = "casino - google chrome"
SAMPLE_INTERVAL_S = 1.0                  # the system probes run at most this often (seconds of the run's own clock)
STILL_POLLS = 25                         # consecutive polls without any screen change that make a "still episode"
DARK_MEAN = 10.0                         # a frame whose coarse mean brightness (0-255) is at or below this is called dark - a clue only
IDLE_NEAR_TIMEOUT_S = 15.0               # idle time this close to the display timeout when the picture stopped is a clue for a sleeping display
EPISODE_LIMIT = 50
AFTER_POLLS = 20                         # polls after an episode over which the capture time is summarised
BEFORE_WINDOW_S = 30.0                   # seconds before an episode over which the capture time is summarised
SERIES_LIMIT = 20000
FRAME_STEP = 16                          # the brightness sample takes every 16th row and column
DISPLAY_STATE_NOTE = ("not readable by this harness: Windows has no read-only polling call for the display's power state; after a run, read the "
                      "Kernel-Power events for its time window")
CONCLUSION = "not determined: these are clues from read-only probes, not proof of either hypothesis"

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DESKTOP_READOBJECTS = 0x0001


# -- the numbers, pure and testable ------------------------------------------------------------------------------------------------------

def parse_powercfg_indexes(text):
    """(AC seconds, battery seconds) from `powercfg /query SCHEME_CURRENT <group> <setting>` text, or (None, None).

    The last two hexadecimal values in that output are the current AC and DC setting indexes (the labels are localised; the order is not).
    """
    values = re.findall(r"0x([0-9a-fA-F]{8})", text or "")
    if len(values) < 2:
        return None, None
    return int(values[-2], 16), int(values[-1], 16)


def frame_stats(frame):
    """{"width", "height", "mean_brightness"} of a captured frame (a coarse sample of its pixels), or None for no usable frame."""
    try:
        height, width = int(frame.shape[0]), int(frame.shape[1])
        sample = frame[::FRAME_STEP, ::FRAME_STEP]
        if sample.size == 0:
            return None
        return {"width": width, "height": height, "mean_brightness": round(float(sample.mean()), 1)}
    except Exception:  # noqa: BLE001 - not a frame
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _median(values):
    ordered = sorted(values)
    return None if not ordered else ordered[len(ordered) // 2]


def _round(value, places=1):
    return None if value is None else round(float(value), places)


# -- the Windows probes ------------------------------------------------------------------------------------------------------------------

class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("dwTime", ctypes.c_uint32)]


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_uint32), ("BatteryFullLifeTime", ctypes.c_uint32)]


class WindowsProbe:
    """The four read-only probes. Every method returns None when Windows, or the answer, is not available; none raises."""

    def __init__(self, game_title=DEFAULT_GAME_TITLE):
        self.game_title = (game_title or "").lower()
        self._tried = False
        self._apis = None

    def _libraries(self):
        if not self._tried:
            self._tried = True
            if os.name == "nt":
                try:
                    user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
                    c = ctypes
                    user32.GetLastInputInfo.argtypes, user32.GetLastInputInfo.restype = [c.POINTER(_LastInputInfo)], c.c_int
                    kernel32.GetTickCount.argtypes, kernel32.GetTickCount.restype = [], c.c_uint32
                    kernel32.GetSystemPowerStatus.argtypes, kernel32.GetSystemPowerStatus.restype = [c.POINTER(_SystemPowerStatus)], c.c_int
                    user32.GetForegroundWindow.argtypes, user32.GetForegroundWindow.restype = [], c.c_void_p
                    user32.GetWindowTextLengthW.argtypes, user32.GetWindowTextLengthW.restype = [c.c_void_p], c.c_int
                    user32.GetWindowTextW.argtypes, user32.GetWindowTextW.restype = [c.c_void_p, c.c_wchar_p, c.c_int], c.c_int
                    user32.GetClassNameW.argtypes, user32.GetClassNameW.restype = [c.c_void_p, c.c_wchar_p, c.c_int], c.c_int
                    user32.GetWindowThreadProcessId.argtypes, user32.GetWindowThreadProcessId.restype = [c.c_void_p, c.POINTER(c.c_uint32)], c.c_uint32
                    user32.IsIconic.argtypes, user32.IsIconic.restype = [c.c_void_p], c.c_int
                    kernel32.OpenProcess.argtypes, kernel32.OpenProcess.restype = [c.c_uint32, c.c_int, c.c_uint32], c.c_void_p
                    kernel32.QueryFullProcessImageNameW.argtypes = [c.c_void_p, c.c_uint32, c.c_wchar_p, c.POINTER(c.c_uint32)]
                    kernel32.QueryFullProcessImageNameW.restype = c.c_int
                    kernel32.CloseHandle.argtypes, kernel32.CloseHandle.restype = [c.c_void_p], c.c_int
                    user32.OpenInputDesktop.argtypes, user32.OpenInputDesktop.restype = [c.c_uint32, c.c_int, c.c_uint32], c.c_void_p
                    user32.CloseDesktop.argtypes, user32.CloseDesktop.restype = [c.c_void_p], c.c_int
                    self._apis = (user32, kernel32)
                except Exception:  # noqa: BLE001 - no Windows API: every probe answers None
                    self._apis = None
        return self._apis

    def idle_seconds(self):
        """Seconds since the last keyboard or mouse input (`GetLastInputInfo`), or None."""
        try:
            apis = self._libraries()
            if apis is None:
                return None
            info = _LastInputInfo()
            info.cbSize = ctypes.sizeof(info)
            if not apis[0].GetLastInputInfo(ctypes.byref(info)):
                return None
            return ((apis[1].GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
        except Exception:  # noqa: BLE001
            return None

    def power(self):
        """{"ac": True | False | None, "battery_percent": int | None, "battery_saver": bool | None}, or None."""
        try:
            apis = self._libraries()
            if apis is None:
                return None
            status = _SystemPowerStatus()
            if not apis[1].GetSystemPowerStatus(ctypes.byref(status)):
                return None
            return {"ac": {0: False, 1: True}.get(status.ACLineStatus),
                    "battery_percent": None if status.BatteryLifePercent == 255 else int(status.BatteryLifePercent),
                    "battery_saver": bool(status.SystemStatusFlag) if status.SystemStatusFlag in (0, 1) else None}
        except Exception:  # noqa: BLE001
            return None

    def foreground(self):
        """The foreground window without its title: {"present", "exe", "class", "pid", "title_matches_game", "minimized"}, or None."""
        try:
            apis = self._libraries()
            if apis is None:
                return None
            user32, kernel32 = apis
            handle = user32.GetForegroundWindow()
            if not handle:
                return {"present": False}
            length = user32.GetWindowTextLengthW(handle)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, title, length + 1)
            matches = bool(self.game_title) and self.game_title in title.value.lower()       # the title is compared here and never kept
            klass = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(handle, klass, 256)
            pid = ctypes.c_uint32(0)
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            exe = None
            process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid.value) if pid.value else None
            if process:
                try:
                    path, size = ctypes.create_unicode_buffer(1024), ctypes.c_uint32(1024)
                    if kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                        exe = os.path.basename(path.value)
                finally:
                    kernel32.CloseHandle(process)
            return {"present": True, "exe": exe, "class": klass.value, "pid": int(pid.value), "title_matches_game": matches,
                    "minimized": bool(user32.IsIconic(handle))}
        except Exception:  # noqa: BLE001
            return None

    def desktop_accessible(self):
        """{"accessible": bool, "error": Windows error code or None}: can the input desktop be opened (refused on a lock or secure-desktop screen)?"""
        try:
            apis = self._libraries()
            if apis is None:
                return None
            ctypes.set_last_error(0)
            desktop = apis[0].OpenInputDesktop(0, 0, DESKTOP_READOBJECTS)
            if desktop:
                apis[0].CloseDesktop(desktop)
                return {"accessible": True, "error": None}
            return {"accessible": False, "error": ctypes.get_last_error()}
        except Exception:  # noqa: BLE001
            return None


def preflight_snapshot(probe, display_timeout_text=None, sleep_timeout_text=None):
    """The machine as it stands before a run, once. `*_text` is `powercfg /query` output the harness read; None leaves the timeout unknown."""
    display_ac, display_dc = parse_powercfg_indexes(display_timeout_text)
    sleep_ac, sleep_dc = parse_powercfg_indexes(sleep_timeout_text)

    def ask(name):
        try:
            return getattr(probe, name)()
        except Exception:  # noqa: BLE001
            return None
    return {"power": ask("power"), "idle_s": _round(ask("idle_seconds")), "desktop_accessible": ask("desktop_accessible"), "foreground": ask("foreground"),
            "display_timeout_s": {"ac": display_ac, "battery": display_dc}, "sleep_timeout_s": {"ac": sleep_ac, "battery": sleep_dc},
            "display_power_state": DISPLAY_STATE_NOTE}


# -- the per-poll watch ------------------------------------------------------------------------------------------------------------------

class MachineWatch:
    """What RoundRecorder calls once per update. Returns a small entry for the poll's line; keeps one record per still episode."""

    def __init__(self, probe=None, interval=SAMPLE_INTERVAL_S, still_polls=STILL_POLLS, timeouts=None, preflight=None, dark_mean=DARK_MEAN):
        self.probe = probe if probe is not None else WindowsProbe()
        self.interval, self.still_polls, self.dark_mean = float(interval), int(still_polls), float(dark_mean)
        self.timeouts = dict(timeouts or {})                          # {"ac": seconds, "battery": seconds}, from the preflight
        self.preflight = preflight
        self.polls = self.samples = self.errors = 0
        self.first_error = None
        self.failed = collections.Counter()                           # probe name -> samples in which it had no answer
        self.sizes = collections.Counter()                            # (width, height) -> polls
        self.brightness = []                                          # the first SERIES_LIMIT per-poll values
        self.foreground_seen = collections.Counter()                  # (exe, class, title matches, minimised) -> samples
        self.foreground_changes = self.power_changes = self.desktop_refused = 0
        self.idle_max = None
        self._last_t = None
        self._last_sample = None                                      # (t, sample dict) of the newest sample
        self._previous_foreground = self._previous_power = None
        self._recent = collections.deque()                            # (t, capture ms) over the last BEFORE_WINDOW_S
        self._open = None                                             # the still episode in progress
        self._episodes = []
        self._after = []                                              # [episode, capture ms] lists still collecting

    # -- one poll -------------------------------------------------------------------------------------------------------------

    def observe(self, t, round_id, payload, extras):
        """The compact per-poll entry. Never raises: a failure is counted and the entry says so."""
        self.polls += 1
        try:
            return self._observe(t, payload or {}, extras or {})
        except Exception as exc:  # noqa: BLE001 - a diagnostic must never stop a poll
            self.errors += 1
            if self.first_error is None:
                self.first_error = "%s: %s" % (type(exc).__name__, exc)
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

    def _observe(self, t, payload, extras):
        timing = payload.get("timing") or {}
        capture_ms = timing.get("capture") if _number(timing.get("capture")) else None
        screen_diff = timing.get("screen_diff") if _number(timing.get("screen_diff")) else None
        frame = extras.get("read_frame")
        frame = extras.get("frame") if frame is None else frame
        stats = frame_stats(frame) if frame is not None else None
        entry = {}
        if stats is not None:
            entry["frame"] = [stats["width"], stats["height"], stats["mean_brightness"]]
            self.sizes[(stats["width"], stats["height"])] += 1
            if len(self.brightness) < SERIES_LIMIT:
                self.brightness.append(stats["mean_brightness"])
        if self._last_t is None or t - self._last_t >= self.interval:
            self._last_t = t
            entry["sys"] = self._sample(t)
        self._track(t, stats, capture_ms, screen_diff)
        return entry

    def _ask(self, name):
        try:
            value = getattr(self.probe, name)()
        except Exception:  # noqa: BLE001 - a probe that fails is an unavailable probe
            value = None
        if value is None:
            self.failed[name] += 1
        return value

    def _sample(self, t):
        self.samples += 1
        idle, power, foreground, desktop = (self._ask("idle_seconds"), self._ask("power"), self._ask("foreground"), self._ask("desktop_accessible"))
        sample = {"idle_s": _round(idle) if _number(idle) else None,
                  "ac": (power or {}).get("ac"), "battery_pct": (power or {}).get("battery_percent"),
                  "fg": None if not foreground else {"exe": foreground.get("exe"), "class": foreground.get("class"),
                                                     "match": foreground.get("title_matches_game"), "min": foreground.get("minimized")},
                  "desktop": None if not desktop else desktop.get("accessible")}
        if sample["idle_s"] is not None:
            self.idle_max = sample["idle_s"] if self.idle_max is None else max(self.idle_max, sample["idle_s"])
        if sample["fg"] is not None:
            identity = (sample["fg"]["exe"], sample["fg"]["class"], sample["fg"]["match"], sample["fg"]["min"])
            self.foreground_seen[identity] += 1
            if self._previous_foreground is not None and identity != self._previous_foreground:
                self.foreground_changes += 1
            self._previous_foreground = identity
        if sample["ac"] is not None:
            if self._previous_power is not None and sample["ac"] != self._previous_power:
                self.power_changes += 1
            self._previous_power = sample["ac"]
        if sample["desktop"] is False:
            self.desktop_refused += 1
        self._last_sample = (t, sample)
        if self._open is not None:
            self._open["samples"].append((t, sample))
        return sample

    # -- still episodes -----------------------------------------------------------------------------------------------------------

    def _track(self, t, stats, capture_ms, screen_diff):
        for item in list(self._after):
            if capture_ms is not None:
                item[1].append(capture_ms)
            if len(item[1]) >= AFTER_POLLS:
                self._after.remove(item)
        still = screen_diff is not None and screen_diff == 0
        if still:
            if self._open is None:
                before = [ms for when, ms in self._recent if ms is not None]
                self._open = {"start": t, "last": t, "polls": 0, "capture": [], "bright": [], "samples": [], "before": _median(before)}
                if self._last_sample is not None:
                    self._open["samples"].append(self._last_sample)          # the newest sample taken before the episode began
            ep = self._open
            ep["polls"] += 1
            ep["last"] = t
            if capture_ms is not None:
                ep["capture"].append(capture_ms)
            if stats is not None:
                ep["bright"].append(stats["mean_brightness"])
        elif self._open is not None:
            self._close()
        if capture_ms is not None:
            self._recent.append((t, capture_ms))
        while self._recent and t - self._recent[0][0] > BEFORE_WINDOW_S:
            self._recent.popleft()

    def _close(self):
        ep, self._open = self._open, None
        if ep["polls"] >= self.still_polls and len(self._episodes) < EPISODE_LIMIT:
            record = {"ep": ep, "after": []}
            self._episodes.append(record)
            self._after.append((record, record["after"]))

    def _render(self, ep, after, still_open):
        samples = ep["samples"]
        idles = [(when, s["idle_s"]) for when, s in samples if s["idle_s"] is not None]
        at_start = idles[0][1] if idles else None
        resets = any(b[1] < a[1] - 1.0 for a, b in zip(idles, idles[1:]))
        identities = []
        for _when, s in samples:
            if s["fg"] is not None:
                item = {"exe": s["fg"]["exe"], "class": s["fg"]["class"], "title_matches_game": s["fg"]["match"], "minimized": s["fg"]["min"]}
                if item not in identities:
                    identities.append(item)
        acs = [s["ac"] for _when, s in samples if s["ac"] is not None]
        battery = [s["battery_pct"] for _when, s in samples if s["battery_pct"] is not None]
        timeout = self.timeouts.get("ac" if acs and acs[0] else "battery") if acs else None
        bright = ep["bright"]
        dark = bool(bright) and max(bright) <= self.dark_mean
        gap = None if at_start is None or not _number(timeout) else round(at_start - timeout, 1)
        refused = sum(1 for _when, s in samples if s["desktop"] is False)
        clues = {"display_idle": {"for": [], "against": []}, "casino_page_stall": {"for": [], "against": []}, "neutral": []}
        if gap is not None:
            if abs(gap) <= IDLE_NEAR_TIMEOUT_S:
                clues["display_idle"]["for"].append("idle time at the start (%.0f s) was within %.0f s of the display timeout for the power source in use (%s s)"
                                                    % (at_start, IDLE_NEAR_TIMEOUT_S, timeout))
            elif gap < -IDLE_NEAR_TIMEOUT_S:
                clues["display_idle"]["against"].append("idle time at the start (%.0f s) was %.0f s below the display timeout (%s s)" % (at_start, -gap, timeout))
                clues["casino_page_stall"]["for"].append("the picture stopped changing while the machine had not been idle long enough to blank the display")
            else:
                clues["neutral"].append("idle time at the start was %.0f s past the display timeout: a sleeping display and a stall are both possible" % gap)
        if dark:
            clues["display_idle"]["for"].append("the frame was dark throughout (mean brightness at most %.0f of 255)" % self.dark_mean)
            clues["casino_page_stall"]["against"].append("a dark frame is more usual for a blank display than for a stalled page (not proof)")
        elif bright:
            clues["neutral"].append("the frame was not dark (mean brightness %.0f-%.0f): a sleeping display can be captured as the last picture it showed" % (min(bright), max(bright)))
        if resets:
            clues["display_idle"]["against"].append("keyboard or mouse input arrived during the episode and the picture still did not change (a sleeping display would wake)")
            clues["casino_page_stall"]["for"].append("input arrived during the episode and the picture still did not change")
        if len(identities) > 1:
            clues["neutral"].append("the foreground window changed during the episode (a lock screen or another window is possible)")
        if refused:
            clues["neutral"].append("the input desktop could not be opened in %d sample(s): a lock or secure-desktop screen is possible" % refused)
        return {"start_t": _round(ep["start"], 3), "end_t": _round(ep["last"], 3), "seconds": _round(ep["last"] - ep["start"], 1), "polls": ep["polls"],
                "still_at_end_of_run": still_open,
                "capture_ms": {"before_median": _round(ep["before"]), "during_median": _round(_median(ep["capture"])), "after_median": _round(_median(after))},
                "mean_brightness": {"min": min(bright) if bright else None, "max": max(bright) if bright else None, "dark_throughout": dark},
                "idle_s": {"at_start": at_start, "at_end": idles[-1][1] if idles else None, "input_during_episode": resets, "samples": len(idles)},
                "display_timeout_s_for_power_source": timeout, "idle_at_start_minus_timeout_s": gap,
                "power": {"ac_values": sorted(set(acs), key=str), "battery_percent_first": battery[0] if battery else None,
                          "battery_percent_last": battery[-1] if battery else None},
                "foreground_windows_seen": identities, "foreground_changed": len(identities) > 1, "input_desktop_refused_samples": refused,
                "hypotheses": clues, "conclusion": CONCLUSION}

    # -- the summary --------------------------------------------------------------------------------------------------------------

    def summary(self):
        """Everything so far; an episode still in progress is included, flagged. Safe to call at any time, any number of times."""
        episodes = [self._render(item["ep"], item["after"], False) for item in self._episodes]
        if self._open is not None and self._open["polls"] >= self.still_polls and len(episodes) < EPISODE_LIMIT:
            episodes.append(self._render(self._open, [], True))
        bright = self.brightness
        return {
            "polls": self.polls, "system_samples": self.samples, "diagnostic_errors": self.errors, "first_error": self.first_error,
            "probe_had_no_answer_in_samples": dict(self.failed),
            "sampling": {"system_probe_interval_s": self.interval, "still_episode_polls": self.still_polls, "dark_means_mean_brightness_at_most": self.dark_mean,
                         "idle_near_timeout_within_s": IDLE_NEAR_TIMEOUT_S},
            "preflight": self.preflight, "display_timeouts_used_s": self.timeouts or None,
            "frame_sizes": {"%dx%d" % size: n for size, n in self.sizes.items()},
            "mean_brightness": None if not bright else {"min": min(bright), "median": _median(bright), "max": max(bright)},
            "idle_s_max": self.idle_max, "foreground_windows": [{"exe": k[0], "class": k[1], "title_matches_game": k[2], "minimized": k[3], "samples": n}
                                                               for k, n in self.foreground_seen.most_common()],
            "foreground_changes": self.foreground_changes, "power_source_changes": self.power_changes, "input_desktop_refused_samples": self.desktop_refused,
            "still_episodes": episodes,
            "limits": DISPLAY_STATE_NOTE, "conclusion": CONCLUSION,
        }
