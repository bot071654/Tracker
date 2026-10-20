"""The harness's read-only machine-state clues (machine_diagnostics.py), the saver status report and the harness wiring.

Why: a 600-second practice run (PHT-validation-20261019-230713) had 1,697 consecutive polls with no screen change while the casino window stayed in front.
"The display went to sleep" and "the casino page stalled" both fit what was kept, and a future run should keep the read-only clues that could tell them apart
WITHOUT deciding. These tests pin what is recorded (idle time, power, foreground identity without its title, frame size and brightness, one record per still
episode), that every probe may be unavailable without harm, that the two hypotheses are kept apart and never concluded, that nothing here can change a setting,
touch the casino or save a picture, and that a harness run given `--max-frames 0 --max-exposure-captures 0` keeps no whole-screen image. Everything runs on fake
probes or in temporary folders; no tracker, screen, casino or real debug folder is touched.
"""

import ast
import copy
import importlib.util
import json
import os

import numpy as np
import pytest

import machine_diagnostics as mdg                                  # noqa: E402
import round_validation as rv                                      # noqa: E402

cv2 = pytest.importorskip("cv2")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POWERCFG_TEXT = """
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
  GUID Alias: SCHEME_BALANCED
  Subgroup GUID: 7516b95f-f776-4464-8c53-06167f40cc99  (Display)
    Power Setting GUID: 3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e  (Turn off display after)
      Minimum Possible Setting: 0x00000000
      Maximum Possible Setting: 0xffffffff
      Possible Settings increment: 0x00000001
      Possible Settings units: Seconds
    Current AC Power Setting Index: 0x0000012c
    Current DC Power Setting Index: 0x000000b4
"""


def load_harness():
    spec = importlib.util.spec_from_file_location("validate_rounds_machine", os.path.join(ROOT, "tools", "validate_rounds.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- the numbers ---------------------------------------------------------------------------------------------------------------------------------

def test_the_display_timeouts_are_the_last_two_values_whatever_language_the_labels_are_in():
    assert mdg.parse_powercfg_indexes(POWERCFG_TEXT) == (300, 180)
    localised = POWERCFG_TEXT.replace("Current AC Power Setting Index", "Aktueller Netzbetrieb-Index").replace("Current DC Power Setting Index", "Akku")
    assert mdg.parse_powercfg_indexes(localised) == (300, 180)
    assert mdg.parse_powercfg_indexes("") == (None, None) and mdg.parse_powercfg_indexes(None) == (None, None)
    assert mdg.parse_powercfg_indexes("only one value: 0x0000000a") == (None, None)


def test_frame_size_and_mean_brightness_are_taken_from_the_captured_frame():
    frame = np.full((1080, 1920, 3), 100, np.uint8)
    assert mdg.frame_stats(frame) == {"width": 1920, "height": 1080, "mean_brightness": 100.0}
    assert mdg.frame_stats(np.zeros((1080, 1920, 3), np.uint8))["mean_brightness"] == 0.0
    assert mdg.frame_stats(np.full((5, 7, 3), 40, np.uint8)) == {"width": 7, "height": 5, "mean_brightness": 40.0}       # smaller than one sampling step
    assert mdg.frame_stats(None) is None and mdg.frame_stats("not a frame") is None and mdg.frame_stats(np.zeros((0, 0, 3), np.uint8)) is None


def test_the_brightness_is_the_mean_of_the_sample_not_its_maximum_or_its_first_pixel():
    frame = np.zeros((160, 160, 3), np.uint8)
    frame[:, :80] = 200                                                  # half bright, half black
    assert mdg.frame_stats(frame)["mean_brightness"] == pytest.approx(100.0, abs=0.1)
    ramp = np.tile(np.arange(160, dtype=np.uint8)[:, None, None], (1, 160, 3))
    assert mdg.frame_stats(ramp)["mean_brightness"] == round(float(ramp[::mdg.FRAME_STEP, ::mdg.FRAME_STEP].mean()), 1)


def test_the_brightness_is_a_sample_so_it_is_cheap_on_a_full_screen_frame():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[::mdg.FRAME_STEP, ::mdg.FRAME_STEP] = 200                       # exactly the pixels the sample looks at
    assert mdg.frame_stats(frame)["mean_brightness"] == 200.0


# -- the Windows probes: unavailable is an answer, never an error ---------------------------------------------------------------------------------

def test_every_probe_answers_none_when_windows_cannot_be_asked(monkeypatch):
    probe = mdg.WindowsProbe("casino")
    monkeypatch.setattr(probe, "_libraries", lambda: None)
    assert (probe.idle_seconds(), probe.power(), probe.foreground(), probe.desktop_accessible()) == (None, None, None, None)


def test_a_probe_whose_api_raises_answers_none(monkeypatch):
    class Exploding:
        def __getattr__(self, name):
            raise OSError("no such function")
    probe = mdg.WindowsProbe("casino")
    monkeypatch.setattr(probe, "_libraries", lambda: (Exploding(), Exploding()))
    assert (probe.idle_seconds(), probe.power(), probe.foreground(), probe.desktop_accessible()) == (None, None, None, None)


@pytest.mark.skipif(os.name != "nt", reason="the Windows probes")
def test_the_real_probes_answer_plausibly_and_never_record_a_title():
    probe = mdg.WindowsProbe("casino - google chrome")
    idle, power, foreground, desktop = probe.idle_seconds(), probe.power(), probe.foreground(), probe.desktop_accessible()
    assert idle is None or (isinstance(idle, float) and 0.0 <= idle < 10 ** 7)
    assert power is None or set(power) == {"ac", "battery_percent", "battery_saver"}
    assert foreground is None or (set(foreground) <= {"present", "exe", "class", "pid", "title_matches_game", "minimized"} and "title" not in foreground)
    assert desktop is None or (set(desktop) == {"accessible", "error"} and isinstance(desktop["accessible"], bool))


def test_a_game_title_is_compared_and_never_kept():
    source = open(os.path.join(ROOT, "machine_diagnostics.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    returned_keys = {key.value for node in ast.walk(tree) if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
                     for key in node.value.keys if isinstance(key, ast.Constant)}
    assert "title" not in returned_keys and "title_matches_game" in returned_keys


# -- a scripted machine -------------------------------------------------------------------------------------------------------------------------

class FakeProbe:
    """Each answer is a value, or a list that is consumed one per sample (the last one repeating), or an exception to raise."""

    def __init__(self, idle=None, power=None, foreground=None, desktop=None):
        self.script = {"idle_seconds": idle, "power": power, "foreground": foreground, "desktop_accessible": desktop}
        self.calls = {name: 0 for name in self.script}

    def _answer(self, name):
        self.calls[name] += 1
        value = self.script[name]
        if isinstance(value, list):
            value = value[min(self.calls[name] - 1, len(value) - 1)]
        if isinstance(value, Exception):
            raise value
        return value

    def idle_seconds(self):
        return self._answer("idle_seconds")

    def power(self):
        return self._answer("power")

    def foreground(self):
        return self._answer("foreground")

    def desktop_accessible(self):
        return self._answer("desktop_accessible")


GAME = {"present": True, "exe": "chrome.exe", "class": "Chrome_WidgetWin_1", "pid": 1234, "title_matches_game": True, "minimized": False}
AC_OFF = {"ac": False, "battery_percent": 79, "battery_saver": False}
AC_ON = {"ac": True, "battery_percent": 80, "battery_saver": False}


def poll(watch, t, diff=1.0, capture=50.0, frame=None):
    payload = {"timing": {"capture": capture, "screen_diff": diff}}
    extras = {"read_frame": frame if frame is not None else np.full((64, 96, 3), 120, np.uint8)}
    return watch.observe(t, 1, payload, extras)


def watch_of(probe, **kwargs):
    kwargs.setdefault("timeouts", {"ac": 300, "battery": 180})
    return mdg.MachineWatch(probe=probe, **kwargs)


# -- per poll ---------------------------------------------------------------------------------------------------------------------------------

def test_every_poll_carries_the_frame_and_the_system_probes_run_at_most_once_an_interval():
    probe = FakeProbe(idle=[5.0, 6.0, 7.0], power=AC_OFF, foreground=GAME, desktop={"accessible": True, "error": None})
    watch = watch_of(probe, interval=1.0)
    entries = [poll(watch, t) for t in (0.0, 0.25, 0.5, 0.99, 1.0, 1.5, 2.1)]
    assert all(entry["frame"] == [96, 64, 120.0] for entry in entries)
    assert [("sys" in e) for e in entries] == [True, False, False, False, True, False, True]
    assert entries[0]["sys"] == {"idle_s": 5.0, "ac": False, "battery_pct": 79, "fg": {"exe": "chrome.exe", "class": "Chrome_WidgetWin_1", "match": True, "min": False}, "desktop": True}
    assert probe.calls == {"idle_seconds": 3, "power": 3, "foreground": 3, "desktop_accessible": 3}


def test_an_unavailable_probe_leaves_a_blank_and_is_counted_while_the_rest_still_answer():
    probe = FakeProbe(idle=None, power=RuntimeError("boom"), foreground=GAME, desktop=None)
    watch = watch_of(probe)
    entry = poll(watch, 0.0)
    assert entry["sys"]["idle_s"] is None and entry["sys"]["ac"] is None and entry["sys"]["battery_pct"] is None and entry["sys"]["desktop"] is None
    assert entry["sys"]["fg"]["exe"] == "chrome.exe"
    assert watch.summary()["probe_had_no_answer_in_samples"] == {"idle_seconds": 1, "power": 1, "desktop_accessible": 1} and watch.summary()["diagnostic_errors"] == 0


def test_a_frame_that_is_missing_or_not_a_frame_gives_an_entry_without_one():
    watch = watch_of(FakeProbe())
    for frame in (None, "nope"):
        entry = watch.observe(0.0, 1, {"timing": {"capture": 1.0, "screen_diff": 1.0}}, {"read_frame": frame})
        assert "frame" not in entry
    assert watch.summary()["frame_sizes"] == {}


def test_changes_of_foreground_power_source_and_desktop_access_are_counted():
    other = dict(GAME, exe="notepad.exe", title_matches_game=False)
    probe = FakeProbe(idle=1.0, power=[AC_OFF, AC_OFF, AC_ON], foreground=[GAME, other, other], desktop=[{"accessible": True, "error": None}, {"accessible": False, "error": 5}])
    watch = watch_of(probe, interval=1.0)
    for t in (0.0, 1.0, 2.0):
        poll(watch, t)
    summary = watch.summary()
    assert summary["foreground_changes"] == 1 and summary["power_source_changes"] == 1 and summary["input_desktop_refused_samples"] == 2
    assert [w["exe"] for w in summary["foreground_windows"]] == ["notepad.exe", "chrome.exe"] and summary["foreground_windows"][0]["samples"] == 2


def test_the_window_title_is_never_in_the_record():
    probe = FakeProbe(foreground={"present": True, "exe": "chrome.exe", "class": "C", "pid": 1, "title_matches_game": True, "minimized": False})
    watch = watch_of(probe)
    poll(watch, 0.0)
    assert "title" not in json.dumps(watch.summary()).replace("title_matches_game", "")


def test_malformed_updates_never_raise_and_an_internal_failure_is_counted(monkeypatch):
    watch = watch_of(FakeProbe())
    for payload, extras in (({}, {}), (None, None), ({"timing": None}, {"read_frame": None}), ({"timing": {"capture": "x", "screen_diff": "y"}}, {"read_frame": object()})):
        assert isinstance(watch.observe(0.0, 1, payload, extras), dict)
    def boom(frame):
        raise RuntimeError("the frame statistics failed")
    monkeypatch.setattr(mdg, "frame_stats", boom)
    entry = poll(watch, 5.0)
    assert entry["error"].startswith("RuntimeError") and watch.summary()["diagnostic_errors"] == 1 and watch.summary()["first_error"].startswith("RuntimeError")


def test_the_update_and_the_frame_are_left_exactly_as_they_were():
    frame = np.full((64, 96, 3), 77, np.uint8)
    frame.flags.writeable = False
    payload = {"timing": {"capture": 50.0, "screen_diff": 0.0}, "state": "WAITING", "lifecycle": {"state": "WAITING"}}
    extras = {"read_frame": frame, "origin": (0, 0), "layout_source": "cached"}
    before = (copy.deepcopy(payload), frame.tobytes(), {k: v for k, v in extras.items() if k != "read_frame"})
    watch = watch_of(FakeProbe(idle=3.0, power=AC_ON, foreground=GAME, desktop={"accessible": True, "error": None}))
    for t in (0.0, 0.5, 1.2):
        watch.observe(t, 1, payload, extras)
    watch.summary()
    assert payload == before[0] and frame.tobytes() == before[1] and {k: v for k, v in extras.items() if k != "read_frame"} == before[2]


# -- still episodes ------------------------------------------------------------------------------------------------------------------------------

def run_episode(watch, still_polls=40, before_polls=10, after_polls=25, step=0.2, frame_value=120, capture_during=45.0):
    t = 0.0
    for _ in range(before_polls):
        poll(watch, t, diff=1.0, capture=50.0, frame=np.full((64, 96, 3), frame_value, np.uint8))
        t += step
    start = t
    for _ in range(still_polls):
        poll(watch, t, diff=0.0, capture=capture_during, frame=np.full((64, 96, 3), frame_value, np.uint8))
        t += step
    for _ in range(after_polls):
        poll(watch, t, diff=0.5, capture=90.0, frame=np.full((64, 96, 3), frame_value, np.uint8))
        t += step
    return start


def test_a_run_of_unchanged_polls_is_one_episode_with_capture_times_before_during_and_after():
    watch = watch_of(FakeProbe(idle=[100.0 + n for n in range(100)], power=AC_OFF, foreground=GAME, desktop={"accessible": True, "error": None}))
    start = run_episode(watch)
    episode = watch.summary()["still_episodes"]
    assert len(episode) == 1
    e = episode[0]
    assert e["start_t"] == round(start, 3) and e["polls"] == 40 and e["seconds"] == pytest.approx(7.8, abs=0.1) and e["still_at_end_of_run"] is False
    assert e["capture_ms"] == {"before_median": 50.0, "during_median": 45.0, "after_median": 90.0}
    assert e["mean_brightness"] == {"min": 120.0, "max": 120.0, "dark_throughout": False}
    assert e["foreground_changed"] is False and e["input_desktop_refused_samples"] == 0 and e["power"]["ac_values"] == [False]


def test_a_short_still_spell_is_not_an_episode_and_the_threshold_is_the_callers():
    watch = watch_of(FakeProbe(idle=1.0), still_polls=25)
    run_episode(watch, still_polls=24)
    assert watch.summary()["still_episodes"] == []
    watch = watch_of(FakeProbe(idle=1.0), still_polls=25)
    run_episode(watch, still_polls=25)                                          # exactly the threshold is an episode
    assert [e["polls"] for e in watch.summary()["still_episodes"]] == [25]
    watch = watch_of(FakeProbe(idle=1.0), still_polls=10)
    run_episode(watch, still_polls=24)
    assert len(watch.summary()["still_episodes"]) == 1


def test_an_unknown_screen_change_is_not_still():
    watch = watch_of(FakeProbe(idle=1.0), still_polls=3)
    for t in range(10):
        watch.observe(float(t), 1, {"timing": {"capture": 5.0}}, {"read_frame": np.zeros((8, 8, 3), np.uint8)})
    assert watch.summary()["still_episodes"] == []


def test_an_episode_still_going_at_the_end_is_reported_and_marked_and_the_summary_can_be_asked_again():
    watch = watch_of(FakeProbe(idle=1.0), still_polls=5)
    for n in range(12):
        poll(watch, n * 0.2, diff=0.0)
    first, second = watch.summary(), watch.summary()
    assert first == second and len(first["still_episodes"]) == 1 and first["still_episodes"][0]["still_at_end_of_run"] is True
    json.dumps(first)                                                             # and it can be written to summary.json


def test_two_episodes_are_two_records():
    watch = watch_of(FakeProbe(idle=1.0), still_polls=5)
    t = 0.0
    for diff in ([0.0] * 6 + [1.0] * 3 + [0.0] * 7 + [2.0]):
        poll(watch, t, diff=diff)
        t += 0.2
    assert [e["polls"] for e in watch.summary()["still_episodes"]] == [6, 7]


def test_the_episodes_kept_are_bounded():
    watch = watch_of(FakeProbe(idle=1.0), still_polls=2)
    t = 0.0
    for _ in range(mdg.EPISODE_LIMIT + 10):
        for diff in (0.0, 0.0, 0.0, 1.0):
            poll(watch, t, diff=diff)
            t += 0.2
    assert len(watch.summary()["still_episodes"]) == mdg.EPISODE_LIMIT


# -- two hypotheses, kept apart and never decided -------------------------------------------------------------------------------------------------

def test_idle_near_the_display_timeout_and_a_dark_frame_are_clues_for_display_idle_and_against_a_stall():
    watch = watch_of(FakeProbe(idle=[170.0 + n for n in range(100)], power=AC_OFF, foreground=GAME, desktop={"accessible": True, "error": None}))
    run_episode(watch, frame_value=0)
    e = watch.summary()["still_episodes"][0]
    assert e["display_timeout_s_for_power_source"] == 180 and e["idle_s"]["at_start"] == pytest.approx(172.0, abs=3) and abs(e["idle_at_start_minus_timeout_s"]) < 15
    clues = e["hypotheses"]
    assert len(clues["display_idle"]["for"]) == 2 and clues["display_idle"]["against"] == []
    assert clues["casino_page_stall"]["for"] == [] and len(clues["casino_page_stall"]["against"]) == 1
    assert e["conclusion"].startswith("not determined") and e["mean_brightness"]["dark_throughout"] is True


def test_a_machine_that_was_not_idle_long_enough_and_input_during_the_episode_are_clues_for_a_stall_and_against_display_idle():
    idle = [30.0 + n for n in range(5)] + [1.0 + n for n in range(100)]                  # input arrives (idle falls back) during the episode
    watch = watch_of(FakeProbe(idle=idle, power=AC_OFF, foreground=GAME, desktop={"accessible": True, "error": None}))
    run_episode(watch, still_polls=60)
    e = watch.summary()["still_episodes"][0]
    clues = e["hypotheses"]
    assert e["idle_s"]["input_during_episode"] is True and e["idle_at_start_minus_timeout_s"] < -100
    assert any("not been idle long enough" in text for text in clues["casino_page_stall"]["for"]) and any("input arrived" in text for text in clues["casino_page_stall"]["for"])
    assert any("below the display timeout" in text for text in clues["display_idle"]["against"]) and any("would wake" in text for text in clues["display_idle"]["against"])
    assert clues["display_idle"]["for"] == []


def test_a_bright_frame_and_an_unknown_timeout_say_nothing_either_way():
    watch = watch_of(FakeProbe(idle=[100.0 + n for n in range(100)], power=AC_OFF, foreground=GAME), timeouts={})
    run_episode(watch)
    e = watch.summary()["still_episodes"][0]
    assert e["idle_at_start_minus_timeout_s"] is None and e["display_timeout_s_for_power_source"] is None
    assert e["hypotheses"]["display_idle"] == {"for": [], "against": []} and e["hypotheses"]["casino_page_stall"] == {"for": [], "against": []}
    assert any("not dark" in text for text in e["hypotheses"]["neutral"])


def test_the_display_timeout_follows_the_power_source_in_use():
    for power, expected in ((AC_ON, 300), (AC_OFF, 180)):
        watch = watch_of(FakeProbe(idle=[100.0 + n for n in range(100)], power=power, foreground=GAME))
        run_episode(watch)
        assert watch.summary()["still_episodes"][0]["display_timeout_s_for_power_source"] == expected


def test_a_foreground_change_and_a_refused_desktop_are_mentioned_as_other_possibilities():
    other = dict(GAME, exe="LockApp.exe", title_matches_game=False)
    watch = watch_of(FakeProbe(idle=[100.0 + n for n in range(100)], power=AC_OFF, foreground=[GAME] * 4 + [other], desktop=[{"accessible": True, "error": None}] * 4 + [{"accessible": False, "error": 5}]))
    run_episode(watch)
    e = watch.summary()["still_episodes"][0]
    assert e["foreground_changed"] is True and e["input_desktop_refused_samples"] >= 1
    assert any("foreground window changed" in text for text in e["hypotheses"]["neutral"]) and any("lock or secure-desktop" in text for text in e["hypotheses"]["neutral"])


def test_nothing_in_the_summary_claims_a_cause():
    watch = watch_of(FakeProbe(idle=[170.0 + n for n in range(100)], power=AC_OFF, foreground=GAME), timeouts={"ac": 300, "battery": 180})
    run_episode(watch, frame_value=0)
    text = json.dumps(watch.summary()).lower()
    assert watch.summary()["conclusion"].startswith("not determined") and "not proof" in text
    for claim in ("proves", "the display was off", "the page stalled", "caused by"):
        assert claim not in text.replace("not proof", ""), claim
    assert set(watch.summary()["still_episodes"][0]["hypotheses"]) == {"display_idle", "casino_page_stall", "neutral"}


# -- preflight ---------------------------------------------------------------------------------------------------------------------------------

def test_the_preflight_records_the_timeouts_and_says_the_display_state_cannot_be_read():
    probe = FakeProbe(idle=12.3, power=AC_OFF, foreground=GAME, desktop={"accessible": True, "error": None})
    snap = mdg.preflight_snapshot(probe, POWERCFG_TEXT, POWERCFG_TEXT.replace("0x0000012c", "0x00000384").replace("0x000000b4", "0x00000258"))
    assert snap["display_timeout_s"] == {"ac": 300, "battery": 180} and snap["sleep_timeout_s"] == {"ac": 900, "battery": 600}
    assert snap["power"] == AC_OFF and snap["idle_s"] == 12.3 and snap["desktop_accessible"]["accessible"] is True and snap["foreground"]["exe"] == "chrome.exe"
    assert "not readable" in snap["display_power_state"] and "Kernel-Power" in snap["display_power_state"]


def test_a_preflight_with_nothing_available_still_answers():
    snap = mdg.preflight_snapshot(FakeProbe(idle=RuntimeError("x"), power=None, foreground=OSError("y"), desktop=None), None, None)
    assert snap["power"] is None and snap["idle_s"] is None and snap["foreground"] is None and snap["display_timeout_s"] == {"ac": None, "battery": None}


# -- it can only look ---------------------------------------------------------------------------------------------------------------------------

def test_the_module_imports_and_names_nothing_that_changes_a_setting_acts_on_a_window_saves_a_picture_or_waits():
    tree = ast.parse(open(os.path.join(ROOT, "machine_diagnostics.py"), encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"collections", "ctypes", "os", "re"}, imported
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for banned in ("sleep", "imwrite", "open", "write", "SendInput", "mouse_event", "keybd_event", "SetCursorPos", "SetForegroundWindow", "ShowWindow", "PostMessageW",
                   "SendMessageW", "SetThreadExecutionState", "PowerSetActiveScheme", "PowerWriteACValueIndex", "SystemParametersInfoW", "LockWorkStation", "ExitWindowsEx",
                   "subprocess", "Popen", "system", "environ", "connect", "setNumThreads", "SetSystemPowerState", "SetSuspendState"):
        assert banned not in names, banned


# -- the harness: flag, wiring, savers -------------------------------------------------------------------------------------------------------------------

def test_the_machine_flag_is_opt_in():
    harness = load_harness()
    assert harness.arguments([]).machine_diagnostics is False
    assert harness.arguments(["--machine-diagnostics"]).machine_diagnostics is True


def test_the_machine_watch_is_built_only_when_asked_and_handed_to_the_recorder():
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    assert "    if args.machine_diagnostics:\n        machine_watch = mdg.MachineWatch(" in source
    assert "machine_watch=machine_watch" in source and "mdg.preflight_snapshot(machine_probe, read_powercfg(\"SUB_VIDEO\", \"VIDEOIDLE\"), read_powercfg(\"SUB_SLEEP\", \"STANDBYIDLE\"))" in source


def test_the_harness_reads_the_timeouts_with_a_read_only_powercfg_query_and_changes_no_setting():
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    assert '["powercfg", "/query", "SCHEME_CURRENT", group, setting]' in source
    for forbidden in ("powercfg\", \"/change", "powercfg\", \"/setacvalueindex", "powercfg\", \"/setdcvalueindex", "powercfg\", \"/s\"", "/hibernate", "SetThreadExecutionState"):
        assert forbidden not in source, forbidden


def frame_payload(round_id=1, state="IN_PROGRESS", status="none", seen=None):
    return {"state": "RIVER", "lifecycle": {"state": state, "round_id": round_id, "result_status": status}, "cards": {}, "seen": seen or {},
            "timing": {"capture": 40.0, "screen_diff": 0.0}, "panels": {"sides": {}}}


def test_through_the_recorder_every_poll_line_carries_the_machine_entry_and_the_summary_and_report_say_so(tmp_path):
    harness = load_harness()
    recorder = rv.RoundRecorder(machine_watch=watch_of(FakeProbe(idle=[100.0 + n for n in range(100)], power=AC_OFF, foreground=GAME), still_polls=5))
    for number in range(12):
        payload = frame_payload()
        payload["emitted_mono"] = 50.0 + number * 0.5
        recorder.observe(payload, {"read_frame": np.full((64, 96, 3), 90, np.uint8)})
    assert all("machine" in line and "frame" in line["machine"] for line in recorder.polls) and "sys" in recorder.polls[0]["machine"]
    rounds, summary = rv.write_evidence(str(tmp_path / "e"), recorder, {"mode": "test"})
    assert summary["machine_diagnostics"]["polls"] == 12 and len(summary["machine_diagnostics"]["still_episodes"]) == 1
    assert all("machine" in json.loads(line) for line in open(tmp_path / "e" / "polls.jsonl"))
    text = harness.report(rounds, summary, {"mode": "test", "evidence_dir": "x", "panel_reader": "p", "cache_tolerance": 1, "reads_per_poll": 2,
                                           "suit_margin": 0.05, "rank_margin": 0.1, "corroborated_suit_margin": 0.02})
    for expected in ("## Machine-state clues for a still screen", "clues, never a verdict", "display idle", "casino page stall", "Episode 1 clues (not proof)", "not readable"):
        assert expected in text, expected


def test_a_recorder_without_the_machine_watch_writes_what_it_wrote_before(tmp_path):
    recorder = rv.RoundRecorder()
    payload = frame_payload()
    payload["emitted_mono"] = 1.0
    recorder.observe(payload, {"read_frame": np.zeros((8, 8, 3), np.uint8)})
    rv.write_evidence(str(tmp_path / "e"), recorder)
    assert "machine_diagnostics" not in json.load(open(tmp_path / "e" / "summary.json")) and "machine" not in json.loads(open(tmp_path / "e" / "polls.jsonl").readline())


def test_a_machine_watch_that_blows_up_does_not_stop_the_recorder():
    class Exploding:
        def observe(self, *a, **k):
            raise RuntimeError("boom")

        def summary(self):
            return {}
    recorder = rv.RoundRecorder(machine_watch=Exploding())
    payload = frame_payload()
    payload["emitted_mono"] = 1.0
    line = recorder.observe(payload, {"read_frame": None})
    assert line["machine"]["error"].startswith("RuntimeError") and recorder.polls == [line]


# -- the saver status, and the whole-screen savers -------------------------------------------------------------------------------------------------------

SAFE = ["--seconds", "600", "--panel-diagnostics", "--max-panel-captures", "80", "--max-frames", "0", "--max-exposure-captures", "0"]


def test_with_the_defaults_two_savers_can_keep_whole_screen_frames():
    harness = load_harness()
    args = harness.arguments([])
    assert args.max_frames == 12 and args.max_exposure_captures == 6 and args.save_frames is False
    assert harness.whole_screen_savers_on(args) == ["dealer-box frames", "table-loss incidents"]
    assert any(line.startswith("WHOLE-SCREEN SAVERS ON: dealer-box frames, table-loss incidents") for line in harness.saver_lines(harness.saver_status(args)))


def test_the_safe_arguments_turn_every_whole_screen_saver_off_and_say_so():
    harness = load_harness()
    args = harness.arguments(SAFE)
    assert args.max_frames == 0 and args.max_exposure_captures == 0 and args.save_frames is False
    assert harness.whole_screen_savers_on(args) == []
    lines = harness.saver_lines(harness.saver_status(args, debug_save_failures=True))
    assert lines[-1] == "no whole-screen saver is on" and not any("WHOLE-SCREEN SAVERS ON" in line for line in lines)
    status = {row["saver"]: row for row in harness.saver_status(args, debug_save_failures=True)}
    assert status["dealer-box frames"]["on"] is False and status["table-loss incidents"]["on"] is False and status["annotated table views"]["on"] is False
    assert status["panel thumbnails"]["on"] is True and status["card-reading crops"]["on"] is True


def test_each_whole_screen_saver_is_switched_off_by_its_own_option_alone():
    harness = load_harness()
    assert harness.whole_screen_savers_on(harness.arguments(["--max-frames", "0"])) == ["table-loss incidents"]
    assert harness.whole_screen_savers_on(harness.arguments(["--max-exposure-captures", "0"])) == ["dealer-box frames"]


def test_the_annotated_view_saver_follows_save_frames_and_is_not_a_whole_screen_saver():
    harness = load_harness()
    row = [r for r in harness.saver_status(harness.arguments(SAFE + ["--save-frames"])) if r["saver"] == "annotated table views"][0]
    assert row["on"] is True and row["whole_screen"] is False and "not the whole screen" in row["kind"]


def test_the_refused_card_crops_are_reported_as_outside_every_option():
    harness = load_harness()
    for known, expected in ((True, True), (False, False), (None, None)):
        row = [r for r in harness.saver_status(harness.arguments(SAFE), known) if r["saver"].startswith("refused-card crops")][0]
        assert row["on"] is expected and "no option" in row["option"] and row["whole_screen"] is False
    assert any("unknown" in line for line in harness.saver_lines(harness.saver_status(harness.arguments(SAFE), None)))


def test_the_panel_saver_status_needs_the_flag_and_a_cap():
    harness = load_harness()
    rows = lambda argv: {r["saver"]: r for r in harness.saver_status(harness.arguments(argv))}        # noqa: E731
    assert rows(["--max-panel-captures", "80"])["panel thumbnails"]["on"] is False
    assert rows(["--panel-diagnostics", "--max-panel-captures", "0"])["panel thumbnails"]["on"] is False
    assert rows(["--panel-diagnostics"])["panel thumbnails"]["on"] is True


def calls_in_main(name):
    tree = ast.parse(open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read())
    main = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "main"][0]
    return [node for node in ast.walk(main) if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == name]


def test_the_harness_builds_every_whole_screen_saver_only_under_its_own_cap_above_zero():
    tree = ast.parse(open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for saver, option in (("FrameSaver", "max_frames"), ("ExposureSaver", "max_exposure_captures"), ("PanelSaver", "max_panel_captures")):
        made = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == saver]
        assert made, saver
        for call in made:
            parent = parents[call]
            assert isinstance(parent, ast.IfExp) and parent.body is call and ast.unparse(parent.test) == "args.%s > 0" % option and ast.unparse(parent.orelse) == "None", ast.unparse(parent)


def test_the_only_picture_writers_in_the_harness_file_are_the_two_known_savers():
    tree = ast.parse(open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read())
    writers = []
    for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        for node in ast.walk(cls):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "cv2.imwrite":
                writers.append(cls.name)
    assert sorted(writers) == ["FrameSaver", "ReadSaver"]
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    assert 'cfg["live_diagnostics"] = bool(args.save_frames)' in source and '"dealer_debug": False' in source


def build_watches(harness, args, evidence):
    """The two watches exactly as main() builds them (the expressions are checked against main's source above)."""
    import dealer_diagnostics as dd
    import poll_diagnostics as pd
    watch = dd.DealerWatch(saver=harness.FrameSaver(evidence) if args.max_frames > 0 else None, seconds=args.dealer_seconds, max_frames=args.max_frames)
    poll_watch = pd.PollWatch(exposure_saver=harness.ExposureSaver(evidence) if args.max_exposure_captures > 0 else None, max_exposure=args.max_exposure_captures)
    return watch, poll_watch


def trigger_both(watch, poll_watch, frame):
    record = {}
    for number in range(10):                                               # the dealer's panel up and no dealer box for 4.5 s
        t = number * 0.5
        watch.observe(t, 1, "RIVER", {"player": True, "dealer": True}, {"layout_source": "dynamic", "regions": [], "region_sources": {}, "seen": [None, None], "present": [0, 0]}, frame, record)
    seen = {"state": "RIVER", "lifecycle": {"state": "IN_PROGRESS", "round_id": 1}, "seen": {"dealer_1": "7S"}, "reads": {}, "diagnostics": {"slots": []}, "timing": {}}
    poll_watch.observe(10.0, 1, seen, {"read_frame": frame, "origin": (0, 0), "layout_source": "dynamic", "fresh": []}, {})
    lost = {"state": "RIVER", "lifecycle": {"state": "IN_PROGRESS", "round_id": 1}, "seen": {}, "reads": {}, "diagnostics": {"slots": []}, "timing": {}}
    poll_watch.observe(10.5, 1, lost, {"read_frame": frame, "origin": (0, 0), "layout_source": "cached", "fresh": []}, {})


def test_by_default_those_two_triggers_do_save_whole_frames__the_control_that_shows_the_scenario_is_real(tmp_path):
    harness = load_harness()
    watch, poll_watch = build_watches(harness, harness.arguments([]), str(tmp_path))
    trigger_both(watch, poll_watch, np.full((216, 384, 3), 90, np.uint8))
    pictures = sorted(os.path.relpath(os.path.join(r, f), tmp_path) for r, _d, fs in os.walk(tmp_path) for f in fs if f.endswith(".png"))
    assert any(p.startswith("frames") for p in pictures) and any(p.startswith("table_loss") and "frame" in p for p in pictures), pictures
    assert len(watch.frames) >= 1


def test_with_max_frames_0_and_max_exposure_captures_0_the_same_triggers_keep_no_picture_at_all(tmp_path):
    harness = load_harness()
    watch, poll_watch = build_watches(harness, harness.arguments(SAFE), str(tmp_path))
    trigger_both(watch, poll_watch, np.full((216, 384, 3), 90, np.uint8))
    assert os.listdir(tmp_path) == []                                       # nothing was created: no frames/, no table_loss/, no file of any kind
    assert watch.frames == []
    assert poll_watch.summary()["table_loss_after_dealer_read"]["incidents"][0]["capture"] is None and poll_watch.summary()["table_loss_after_dealer_read"]["capturing"] is False
