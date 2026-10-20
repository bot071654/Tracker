"""Diagnostics-only validation of the result-panel recognition and the round lifecycle. READ-ONLY.

    python tools/validate_rounds.py --check                 what is on screen, and would a run start? (writes nothing)
    python tools/validate_rounds.py --seconds 360           watch the authorized practice table for six minutes
    python tools/validate_rounds.py --replay "Screenshot 2026-09-02 152130.png"
                                                            self-check of this harness with no casino: one saved frame is
                                                            "the screen", then a cleared table
    python tools/validate_rounds.py --machine-diagnostics   (opt-in) also record read-only Windows machine-state clues for a still screen (idle time, power,
                                                            foreground window identity, frame size and brightness): machine_diagnostics.py. Clues, never a verdict.
    python tools/audit_evidence_images.py <evidence folder>  after any run: image sizes from file headers only; flags anything whole-screen-sized
    python tools/validate_rounds.py --panel-diagnostics     (opt-in) also measure the result-panel thumbnails poll to poll, for the cache
                                                            evidence: panel_diagnostics.py; thumbnail-sized pictures only, in panel_thumbnails/

What it does: runs the real Tracker (the source tree's, not the installed app) against the screen for a while and records, for
every round, what happened to the result panels and the result (round_validation.RoundRecorder), and what the next-round
alert did (round_validation.AlertProbe, the window's own alert logic with nothing to show or say).

What it never does:
  * click, type, bet or send anything to the game - it only captures the screen (the same capture the tracker always makes);
  * contact a backend or the database: saves are a stub that sleeps like a slow network, and nothing is submitted anywhere;
  * touch the installed app or its data: every output of the tracker goes to POKER_TRACKER_HOME inside the evidence folder,
    and the installed app's config.json is only READ, for the calibration;
  * run while the installed app's windows are on screen (they would cover the table) - it stops and says so;
  * change the confidence or refusal policy: it observes the readers as they are.

Everything it writes goes to one new timestamped folder: rounds.json, polls.jsonl, summary.json, report.md, run.log, frames/
and the tracker's own home (logs, the crops of any thumbnail it refused). It will not write into a folder that already holds
anything, so earlier evidence cannot be overwritten.

Dealer-box evidence (dealer_diagnostics.py): on every poll it records whether the tracker had a box for each of the dealer's
two table cards and what the layout finder made of that frame (the rows of cards it found, what sat above the board, the
bright blobs it threw away and why). When the dealer's row of the result panel is on screen and the tracker has had no dealer
box for 3 seconds - or the round is at the river or complete with a panel row up and no dealer box for 6 seconds - it saves
the raw frame, whole, to frames/ with a .json beside it holding the diagnostics at that moment (at most 12 a run, one per round
per trigger). A frame is the whole screen as captured, so it may show anything else that was on it: they stay in the evidence
folder and nowhere else. This is the evidence that tells apart "the dealer's cards were not on the table" from "they were
face up and the finder did not pair them".
"""

import argparse
import ctypes
import datetime
import json
import logging
import os
import queue
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = "casino - google chrome"
FELT_BOX = ((190, 300), (1730, 935))          # the part of the sample screenshot that holds the cards, panels and the call to bet


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="report what is on screen and exit; writes nothing")
    parser.add_argument("--seconds", type=int, default=360, help="how long to watch once the table is found (default 360)")
    parser.add_argument("--wait", type=int, default=240, help="how long to wait for the table with the game window in front")
    parser.add_argument("--out", help="evidence folder (default: a new C:\\Users\\<you>\\PHT-validation-<timestamp>)")
    parser.add_argument("--replay", metavar="SCREENSHOT", help="use one saved frame as the screen (no casino needed)")
    parser.add_argument("--save-frames", action="store_true", help="also keep the tracker's annotated pictures of changed moments")
    parser.add_argument("--max-frames", type=int, default=12, help="most raw frames to save for the dealer-box evidence (0 saves none)")
    parser.add_argument("--max-read-captures", type=int, default=40,
                        help="most crops to save of a raw card reading that disagrees with the card memory, 3 small files each "
                             "(0 saves none; the per-poll dealer lines are kept either way)")
    parser.add_argument("--max-seed-captures", type=int, default=40,
                        help="most crops to save of a card memory being seeded or switching value (the dealer's slots always, other "
                             "slots only below 0.80 confidence), a cap of its own (0 saves none; every event is still listed)")
    parser.add_argument("--max-exposure-captures", type=int, default=6,
                        help="most incidents to save in which a dealer card was read, the round did not complete and the table was lost "
                             "within about 2 s: two frames, the dealer crops and a timeline each, in <evidence>/table_loss (0 saves none; "
                             "the per-poll timing, load and crop-change records are kept either way)")
    parser.add_argument("--panel-diagnostics", action="store_true",
                        help="opt in: measure how much each result-panel thumbnail's pixels change from poll to poll and against the tracker's cache "
                             "(panel_diagnostics.py); measures only, changes nothing the tracker reads or decides")
    parser.add_argument("--max-panel-captures", type=int, default=80,
                        help="with --panel-diagnostics: most thumbnail samples to save (up to three small pictures each, never a whole frame), in "
                             "<evidence>/panel_thumbnails (0 saves none; the measurements are kept either way)")
    parser.add_argument("--machine-diagnostics", action="store_true",
                        help="opt in: record read-only Windows machine-state clues (idle time, AC/battery, foreground window identity without its title, "
                             "frame size and brightness) and, once at preflight, the display and sleep timeouts (machine_diagnostics.py); also shown by "
                             "--check; it keeps clues for 'display idle' and 'casino page stall' separate and never decides between them")
    parser.add_argument("--dealer-seconds", type=float, default=3.0,
                        help="how long the result panel may be up with no dealer box before a frame is saved (default 3)")
    parser.add_argument("--replay-phases", default="14,8,14,8",
                        help="with --replay: seconds of screen, cleared table, screen, cleared table (default 14,8,14,8)")
    return parser.parse_args(argv)


def saver_status(args, debug_save_failures=None):
    """Every image saver the harness can run, whether it is ON for these arguments, and what kind of picture it can keep. Reporting only.

    `debug_save_failures` is the copied configuration's setting (None when not known): the refused-card crops follow it and no option controls them.
    """
    panel_on = bool(args.panel_diagnostics) and args.max_panel_captures > 0
    return [
        {"saver": "dealer-box frames", "folder": "frames/", "option": "--max-frames", "value": args.max_frames, "on": args.max_frames > 0,
         "kind": "WHOLE-SCREEN frames", "whole_screen": True},
        {"saver": "table-loss incidents", "folder": "table_loss/", "option": "--max-exposure-captures", "value": args.max_exposure_captures,
         "on": args.max_exposure_captures > 0, "kind": "WHOLE-SCREEN frames (two an incident) and dealer crops", "whole_screen": True},
        {"saver": "annotated table views", "folder": "home/debug/live/ and home/debug/card_crops/", "option": "--save-frames",
         "value": bool(args.save_frames), "on": bool(args.save_frames), "kind": "large table-area pictures and card crops (not the whole screen)",
         "whole_screen": False},
        {"saver": "card-reading crops", "folder": "read_mismatches/", "option": "--max-read-captures", "value": args.max_read_captures,
         "on": args.max_read_captures > 0, "kind": "card crops and wider context crops", "whole_screen": False},
        {"saver": "card-memory seed crops", "folder": "read_mismatches/", "option": "--max-seed-captures", "value": args.max_seed_captures,
         "on": args.max_seed_captures > 0, "kind": "card crops and wider context crops", "whole_screen": False},
        {"saver": "panel thumbnails", "folder": "panel_thumbnails/", "option": "--max-panel-captures",
         "value": args.max_panel_captures if args.panel_diagnostics else None, "on": panel_on, "kind": "thumbnails (at most 120 px a side)", "whole_screen": False},
        {"saver": "refused-card crops (the tracker's)", "folder": "home/logs/failures/", "option": "(no option: debug_save_failures)",
         "value": debug_save_failures, "on": debug_save_failures, "kind": "card crops and panel thumbnails", "whole_screen": False},
    ]


def whole_screen_savers_on(args):
    """The names of the savers that can keep a whole-screen frame and are ON for these arguments (an empty list when none is)."""
    return [row["saver"] for row in saver_status(args) if row["whole_screen"] and row["on"]]


def saver_lines(rows):
    """The saver status as lines for a person; says plainly whether any whole-screen saver is on."""
    lines = ["image savers for these arguments:"]
    for row in rows:
        state = "unknown" if row["on"] is None else ("ON " if row["on"] else "off")
        lines.append("  %-7s %-38s %-34s %s -> %s" % (state, row["saver"], "%s %s" % (row["option"], row["value"]), row["kind"], row["folder"]))
    whole = [row["saver"] for row in rows if row["whole_screen"] and row["on"]]
    lines.append("WHOLE-SCREEN SAVERS ON: %s" % ", ".join(whole) if whole else "no whole-screen saver is on")
    return lines


def read_powercfg(group, setting):
    """`powercfg /query SCHEME_CURRENT <group> <setting>` output (a read-only query), or None."""
    import subprocess
    try:
        out = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", group, setting], capture_output=True, text=True, timeout=15)
        return out.stdout if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 - the timeouts are a refinement
        return None


def installed_debug_save_failures():
    """The installed configuration's debug_save_failures (read only), or None when it cannot be read."""
    try:
        with open(os.path.join(os.environ.get("LOCALAPPDATA", ""), "PokerHandTracker", "config", "config.json"), encoding="utf-8") as handle:
            return bool(json.load(handle).get("debug_save_failures"))
    except Exception:  # noqa: BLE001
        return None


def foreign_tracker_windows(user32):
    """(pids, visible windows) of any running PokerHandTracker.exe - the installed app, not this process."""
    import subprocess
    from ctypes import wintypes
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-Process PokerHandTracker -ErrorAction SilentlyContinue).Id -join ','"],
                         capture_output=True, text=True).stdout.strip()
    pids = {int(x) for x in out.split(",") if x.strip().isdigit()}
    found = []
    old = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:  # noqa: BLE001
        pass

    def visit(handle, _):
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(handle) and not user32.IsIconic(handle):
            rect = wintypes.RECT()
            user32.GetWindowRect(handle, ctypes.byref(rect))
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            if rect.right > rect.left and buffer.value:
                found.append((buffer.value, rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
    if old:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    return sorted(pids), found


def fresh_evidence_dir(path):
    """`path`, created if needed - and refused if it already holds anything: earlier evidence is never overwritten."""
    if os.path.isdir(path) and os.listdir(path):
        raise FileExistsError("%s already holds files; evidence is never overwritten - choose a new folder" % path)
    os.makedirs(path, exist_ok=True)
    return path


class FrameTap:
    """Wraps the tracker's screen capture so the harness can see the very frame each poll read. Reads nothing extra."""

    def __init__(self, grab):
        self._grab, self.latest = grab, None

    def __call__(self, monitor=1):
        self.latest = self._grab(monitor)
        return self.latest


class FrameSaver:
    """Saves a raw frame and its diagnostics into <evidence>/frames, never over a file that is already there."""

    def __init__(self, folder):
        self.folder = os.path.join(folder, "frames")

    def __call__(self, frame, name, info):
        import cv2
        os.makedirs(self.folder, exist_ok=True)
        stem, number = name, 1
        while os.path.exists(os.path.join(self.folder, stem + ".png")) or os.path.exists(os.path.join(self.folder, stem + ".json")):
            number += 1
            stem = "%s_%d" % (name, number)
        cv2.imwrite(os.path.join(self.folder, stem + ".png"), frame)
        with open(os.path.join(self.folder, stem + ".json"), "w", encoding="utf-8") as handle:
            json.dump(info, handle, indent=1, default=str)
        return stem + ".png"


class ExposureSaver:
    """Saves a table-loss incident's frames, dealer crops and JSON into <evidence>/table_loss, never over a file that is already there.

    The same rule as ReadSaver (a name taken by any of its files gets a number added), in a folder of its own, made on the first save.
    """

    def __init__(self, folder):
        self._inner = ReadSaver(folder)
        self._inner.folder = os.path.join(folder, "table_loss")

    def __call__(self, name, images, info):
        return self._inner(name, images, info)


class PanelSaver:
    """Saves a panel-thumbnail sample's small pictures and JSON into <evidence>/panel_thumbnails, never over a file that is already there.

    Thumbnail-sized pictures only: anything larger than MAX_SIDE pixels on a side is refused before anything is written, so a whole frame
    (which may show private browser content) can never be saved through it. Names are reduced to safe characters, so a name cannot leave the
    folder. Otherwise the same rule as ReadSaver, in a folder of its own, made on the first save.
    """

    MAX_SIDE = 120

    def __init__(self, folder):
        self._inner = ReadSaver(folder)
        self._inner.folder = os.path.join(folder, "panel_thumbnails")

    @staticmethod
    def _safe(text):
        import re
        return re.sub(r"\.{2,}", "_", re.sub(r"[^A-Za-z0-9_.-]", "_", str(text))).lstrip(".") or "sample"

    def __call__(self, name, images, info):
        for label, image in images.items():
            if image is None or getattr(image, "ndim", 0) != 3 or max(image.shape[:2]) > self.MAX_SIDE:
                raise ValueError("%s is not a thumbnail-sized picture: nothing was saved" % label)
        return self._inner(self._safe(name), {self._safe(label): image for label, image in images.items()}, info)


class ReadSaver:
    """Saves the crop, the wider crop and the JSON of a reading that disagreed with the card memory into <evidence>/read_mismatches.

    The folder is made on the first save, and a name already taken - by any of its three files - gets a number added: nothing
    that is already there is ever overwritten.
    """

    def __init__(self, folder):
        self.folder = os.path.join(folder, "read_mismatches")

    def __call__(self, name, images, info):
        import cv2
        os.makedirs(self.folder, exist_ok=True)
        suffixes = [label + ".png" for label in images] + ["json"]
        stem, number = name, 1
        while any(os.path.exists(os.path.join(self.folder, "%s_%s" % (stem, suffix) if suffix != "json" else stem + ".json"))
                  for suffix in suffixes):
            number += 1
            stem = "%s_%d" % (name, number)
        info = dict(info, files=[stem + "_" + label + ".png" for label in images])
        for label, image in images.items():
            cv2.imwrite(os.path.join(self.folder, "%s_%s.png" % (stem, label)), image)
        with open(os.path.join(self.folder, stem + ".json"), "w", encoding="utf-8") as handle:
            json.dump(info, handle, indent=1, default=str)
        return stem


def clear_the_table(frame):
    """The sample frame with the cards, the panels and the call to bet painted over with the felt: an empty table."""
    import numpy as np
    (x0, y0), (x1, y1) = FELT_BOX
    felt = np.median(frame[620:640, 1380:1400].reshape(-1, 3), axis=0).astype(frame.dtype)
    out = frame.copy()
    out[y0:y1, x0:x1] = felt
    return out


def main(argv=None):
    args = arguments(argv)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    evidence = args.out or os.path.join(os.path.expanduser("~"), "PHT-validation-%s" % stamp)
    if not args.check:
        try:
            fresh_evidence_dir(evidence)
        except FileExistsError as exc:
            print(str(exc), flush=True)
            return 2
        # Every output of the tracker lands here, never in the installed app's folders.
        os.environ["POKER_TRACKER_HOME"] = os.path.join(evidence, "home")
        handler = logging.FileHandler(os.path.join(evidence, "run.log"), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
    os.environ.pop("POKER_TRACKER_API_URL", None)
    sys.path.insert(0, ROOT)

    import numpy as np  # noqa: F401,E402
    import cv2  # noqa: E402
    import round_validation as rv  # noqa: E402
    import tracker as T  # noqa: E402
    import window_focus  # noqa: E402
    from capture.screen_capture import grab_full_screen, monitor_origin  # noqa: E402
    from recognition import card_recognizer, result_panel, table_layout  # noqa: E402

    def say(text):
        print(text, flush=True)

    run = {"mode": "check" if args.check else ("replay" if args.replay else "live practice table"), "evidence_dir": evidence,
           "started": stamp, "source_tree": ROOT}

    # -- 0. the screen, and who else is on it ------------------------------------------------------------------------------
    replay = None
    if args.replay:
        replay = cv2.imread(args.replay)
        if replay is None:
            say("cannot read %s" % args.replay)
            return 2
        frame, origin = replay, (0, 0)
        pids, tracker_windows = [], []
    else:
        frame, origin = grab_full_screen(1), monitor_origin(1)
        user32 = ctypes.WinDLL("user32")
        pids, tracker_windows = foreign_tracker_windows(user32)
    run["capture"] = {"width": int(frame.shape[1]), "height": int(frame.shape[0]), "origin": list(origin)}
    run["installed_app_processes"] = pids
    run["installed_app_windows"] = [{"title": t, "rect": [l, tp, r, b]} for t, l, tp, r, b in tracker_windows]
    say("capture: %dx%d origin %s" % (frame.shape[1], frame.shape[0], origin))
    say("installed tracker app running: %s; its visible windows: %s" % (bool(pids), [w[0] for w in tracker_windows] or "none"))
    if replay is None:
        say("foreground window: %r" % window_focus.foreground_title())

    run["savers"] = saver_status(args, installed_debug_save_failures() if replay is None else None)
    for line in saver_lines(run["savers"]):
        say(line)
    machine_snapshot = machine_probe = mdg = None
    if args.machine_diagnostics:
        import machine_diagnostics as mdg  # noqa: E402
        machine_probe = mdg.WindowsProbe(GAME)
        machine_snapshot = mdg.preflight_snapshot(machine_probe, read_powercfg("SUB_VIDEO", "VIDEOIDLE"), read_powercfg("SUB_SLEEP", "STANDBYIDLE"))
        run["machine_at_start"] = machine_snapshot
        say("machine at start: power %s, idle %s s, input desktop %s, display timeout (AC / battery) %s / %s s, sleep timeout %s / %s s" % (
            machine_snapshot["power"], machine_snapshot["idle_s"], machine_snapshot["desktop_accessible"], machine_snapshot["display_timeout_s"]["ac"], machine_snapshot["display_timeout_s"]["battery"],
            machine_snapshot["sleep_timeout_s"]["ac"], machine_snapshot["sleep_timeout_s"]["battery"]))
        say("display power state: %s" % machine_snapshot["display_power_state"])

    if tracker_windows:
        say("NOT RUNNING: the installed app's windows are on screen and would cover the table. Close or minimise them.")
        run["aborted"] = "installed app windows visible"
        if not args.check:
            json.dump(run, open(os.path.join(evidence, "summary.json"), "w"), indent=2)
        return 1

    # -- 1. is the practice table on screen with the game window in front? --------------------------------------------------
    layout = None
    if replay is None:
        deadline = time.time() + (0 if args.check else args.wait)
        while True:
            if GAME in (window_focus.foreground_title() or "").lower():
                found = table_layout.locate(grab_full_screen(1), origin=origin)
                if table_layout.looks_plausible(found):
                    layout = found
                    break
            if time.time() >= deadline:
                break
            time.sleep(1.0)
        run["table_found"] = layout is not None
        say("table found with the game window in front: %s%s" % (layout is not None, "" if layout is None else
                                                                   " (slots: %s)" % ", ".join(sorted(layout["regions"]))))
        if layout is None:
            run["aborted"] = "the practice table is not on screen with the game window in front"
            say("Nothing to watch: put the authorized practice table in front (Casino - Google Chrome) and run again.")
            if not args.check:
                json.dump(run, open(os.path.join(evidence, "summary.json"), "w"), indent=2)
            return 1
        if args.check:
            say("CHECK OK: a run would start now.")
            return 0

    # -- 2. the real tracker, saves stubbed, nothing contacting a backend ---------------------------------------------------------
    saves = []

    class DbError(Exception):
        pass

    def slow_insert(record):
        time.sleep(2.5 if replay is None else 0.1)                  # what a slow network would cost; nothing is sent anywhere
        saves.append(record["hand_fingerprint"])
        return True, {"id": len(saves), "hand_fingerprint": record["hand_fingerprint"]}

    T.db = types.SimpleNamespace(insert_hand=slow_insert, DatabaseError=DbError)
    T.excel_export = types.SimpleNamespace(append_hand=lambda row: None, ExcelError=Exception)

    if replay is not None:
        shown = {"frame": replay}
        T.grab_full_screen = lambda monitor=1: shown["frame"]
        T.monitor_origin = lambda monitor=1: (0, 0)
        cfg = {"monitor": 1, "regions": {}, "game_window_title": "", "clear_frames": 3, "stable_frames": 2,
               "poll_interval_seconds": 0.1, "async_save": True}
    else:
        config_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "PokerHandTracker", "config", "config.json")
        with open(config_path, encoding="utf-8") as handle:        # read only: the installed app's calibration
            cfg = json.load(handle)
        cfg.update({"monitor": 1, "game_window_title": "Casino - Google Chrome", "dealer_debug": False, "async_save": True})
    cfg["live_diagnostics"] = bool(args.save_frames)

    tracker = T.Tracker(cfg, queue.Queue())
    if replay is not None:
        tracker._own_rects = lambda: []

    import dealer_diagnostics as dd  # noqa: E402
    watch = dd.DealerWatch(saver=FrameSaver(evidence) if args.max_frames > 0 else None, seconds=args.dealer_seconds,
                           max_frames=args.max_frames)
    import read_diagnostics as rd  # noqa: E402
    read_watch = rd.ReadWatch(saver=ReadSaver(evidence) if args.max_read_captures > 0 else None, max_captures=args.max_read_captures,
                              thresholds=(cfg.get("confidence_threshold", 0.62), cfg.get("presence_threshold", 0.35)),
                              seed_saver=ReadSaver(evidence) if args.max_seed_captures > 0 else None,
                              max_seed_captures=args.max_seed_captures)
    import poll_diagnostics as pd  # noqa: E402
    poll_watch = pd.PollWatch(exposure_saver=ExposureSaver(evidence) if args.max_exposure_captures > 0 else None,
                              max_exposure=args.max_exposure_captures)
    panel_watch = None
    if args.panel_diagnostics:
        import panel_diagnostics as pnl  # noqa: E402
        panel_watch = pnl.PanelWatch(saver=PanelSaver(evidence) if args.max_panel_captures > 0 else None, max_captures=args.max_panel_captures,
                                     tolerance=result_panel.PanelTracker.CACHE_TOLERANCE, pool_size=result_panel.PanelTracker.CACHE_SIZE)
    machine_watch = None
    if args.machine_diagnostics:
        machine_watch = mdg.MachineWatch(probe=machine_probe, timeouts=dict(machine_snapshot["display_timeout_s"]), preflight=machine_snapshot)
    recorder = rv.RoundRecorder(dealer_watch=watch, read_watch=read_watch, poll_watch=poll_watch, panel_watch=panel_watch, machine_watch=machine_watch)
    tap = FrameTap(T.grab_full_screen)
    T.grab_full_screen = tap
    emit = tracker._emit

    # The frame the tracker READ this tick, its origin and the slots it read afresh, as they stand the moment the read returns:
    # the tracker clears its own copy of the frame later in the tick, and `tap.latest` can be a later grab (the panel check).
    # Only references are kept; the tracker's read is called unchanged and its result handed back untouched.
    tick = {}
    read_table = tracker._read_table

    def read_table_noted():
        result = read_table()
        try:
            captured = tracker._frame
            tick.update(frame=captured[0] if captured else None, origin=captured[1] if captured else (0, 0),
                        fresh=sorted(tracker._timing.get("fresh") or ()))
        except Exception:  # noqa: BLE001 - noting a frame must never stop a poll
            tick.clear()
        return result

    tracker._read_table = read_table_noted

    def emit_with_snapshot(kind, payload):
        """The tracker's own `_emit`, with the state of that poll added for the harness (same thread, so nothing has moved on)."""
        if kind == "update":
            payload["_harness"] = {"frame": tap.latest, "layout_source": tracker._layout_source,
                                   "regions": sorted(tracker._last_regions or {}),
                                   "region_sources": dict(tracker._region_sources or {}),
                                   "read_frame": tick.get("frame"), "origin": tick.get("origin", (0, 0)),
                                   "fresh": tick.get("fresh")}
        emit(kind, payload)

    tracker._emit = emit_with_snapshot
    probe = rv.AlertProbe(session=1)
    run.update({"panel_reader": "result_panel.read_thumbnail", "cache_tolerance": result_panel.PanelTracker.CACHE_TOLERANCE,
                "reads_per_poll": result_panel.READS_PER_POLL, "suit_margin": card_recognizer.SUIT_MARGIN,
                "rank_margin": card_recognizer.RANK_MARGIN, "corroborated_suit_margin": card_recognizer.CORROBORATED_SUIT_MARGIN,
                "confirm_frames": result_panel.CONFIRM_FRAMES})

    stop_at = {"t": None}
    if replay is not None:
        cleared = clear_the_table(replay)

        def script():                                              # the screen, then a cleared table, then the screen again
            seconds_each = [float(v) for v in args.replay_phases.split(",")]
            for picture, seconds in zip((replay, cleared, replay, cleared), seconds_each):
                shown["frame"] = picture
                time.sleep(seconds)
            stop_at["t"] = 0
        threading.Thread(target=script, daemon=True).start()

    say("watching for %s (read-only; saves stubbed) ..." % ("the scripted replay" if replay is not None else "%d s" % args.seconds))
    began = time.perf_counter()
    last_flush = began
    tracker.start()
    try:
        while stop_at["t"] is None and (replay is not None or time.perf_counter() - began < args.seconds):
            try:
                kind, payload = tracker.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind != "update":
                continue
            recorder.observe(payload, payload.pop("_harness", None))
            recorder.observe_alert(probe.observe(payload), payload)
            if time.perf_counter() - last_flush > 20:
                rv.write_evidence(evidence, recorder, run)
                last_flush = time.perf_counter()
    except KeyboardInterrupt:
        say("interrupted - writing what was seen")
    finally:
        tracker.stop()
    logging.shutdown()

    run["seconds_watched"] = round(time.perf_counter() - began, 1)
    run["updates"] = len(recorder.polls)
    run["hands_the_stub_was_asked_to_save"] = len(saves)
    rounds, summary = rv.write_evidence(evidence, recorder, run)
    with open(os.path.join(evidence, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(report(rounds, summary, run))
    say("DONE. evidence folder: %s" % evidence)
    say(json.dumps(summary, indent=1))
    return 0


def report(rounds, summary, run):
    lines = ["# Round validation (%s)" % run["mode"], "",
             "Evidence folder: `%s`  |  watched %ss, %s updates" % (run["evidence_dir"], run.get("seconds_watched"), run.get("updates")),
             "", "Readers as run: panel reader `%s`, cache tolerance %s, %s reads per poll, suit margin %.2f, rank margin %.2f, "
                 "corroborated floor %.2f (unchanged policy)." % (run["panel_reader"], run["cache_tolerance"], run["reads_per_poll"],
                                                                  run["suit_margin"], run["rank_margin"],
                                                                  run["corroborated_suit_margin"]), "",
             "| round | completed | table result | panel result | status | complete->confirmed | complete->panels gone | player row | "
             "dealer row | ANTE/SKIP | pending | unknown |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rounds:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s/5 (vis %s) | %s/5 (vis %s) | %s | %s | %s |" % (
            r["round_id"], r["completed"], r["table_result"], r["panel_winner"], r["result_status"],
            r["seconds_complete_to_confirmed"], r["seconds_complete_to_panel_gone"],
            r["panel"]["player"]["max_confirmed_cards"], r["panel"]["player"]["polls_visible"],
            r["panel"]["dealer"]["max_confirmed_cards"], r["panel"]["dealer"]["polls_visible"],
            r["ante_skip_alerts"], r["pending_notices"], r["unknown_notices"]))
    watched = [r for r in rounds if r.get("dealer_boxes")]
    if watched:
        lines += ["", "## Dealer boxes (the tracker's boxes for the dealer's two table cards)", "",
                  "| round | completed | polls | polls with a box | polls with both | first box (s) | fresh dealer slots (polls) | "
                  "rows above the board | dealer-zone cards (max) | rejected blobs (max) | longest no-box while dealer panel up (s) | frames |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in watched:
            d = r["dealer_boxes"]
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["round_id"], r["completed"], d["polls"], d["polls_with_any_box"], d["polls_with_both_boxes"], d["first_box_s"],
                d["polls_with_fresh_dealer_slots"], d["above_row_lengths_seen"], d["zone_cards_max"], d["zone_rejected_max"],
                d["longest_absence_with_dealer_row_visible_s"], ", ".join(f["name"] for f in d["frames"]) or "-"))
    reads = summary.get("read_evidence")
    if reads:
        lines += ["", "## Raw readings that disagreed with the card memory", "",
                  "%s distinct, %s polls; %s capture(s) saved in `read_mismatches/` (limit %s, %s skipped by the limit); "
                  "%s diagnostic error(s)." % (reads["distinct_mismatches"], reads["mismatch_polls"], reads["captures_saved"],
                                               reads["capture_limit"], reads["skipped_by_limit"], reads["diagnostic_errors"]), ""]
        if reads["entries"]:
            lines += ["| round | slot | raw | memory | first t (s) | polls | saved |", "|---|---|---|---|---|---|---|"]
            lines += ["| %s | %s | %s | %s | %s | %s | %s |" % (e["round"], e["slot"], e["raw"], e["memory"], e["first_t"],
                                                              e["occurrences"], ", ".join(e["captures"]) or "-")
                      for e in reads["entries"]]
        memory = reads.get("memory_events")
        if memory:
            lines += ["", "## Card memory seeded or switched (first value per slot, and each later change)", "",
                      "%s event(s): %s seed(s), %s switch(es); %s capture(s) saved (limit %s, %s skipped by the limit, %s events not kept); "
                      "%s seed(s) whose value was not the round's final one." % (
                          memory["events"], memory["seeds"], memory["switches"], memory["captures_saved"], memory["capture_limit"],
                          memory["skipped_by_limit"], memory["events_dropped"], memory["seeds_whose_final_value_differs"]), ""]
            if memory["entries"]:
                lines += ["| round | slot | kind | value | from | t (s) | confidence | box source | final value | differs | saved / why not |",
                          "|---|---|---|---|---|---|---|---|---|---|---|"]
                lines += ["| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                    e["round"], e["slot"], e["kind"], e["value"], e["previous"] or "-", e["t"], e["confidence"],
                    e["initial"].get("box_source"), e["final_memory"], e["final_differs"], e["capture"] or e["skipped"] or "-")
                    for e in memory["entries"]]
    perf = summary.get("poll_diagnostics")
    if perf:
        def row(stats):
            return "-" if not stats else "%s / %s / %s" % (stats["median"], stats["p90"], stats["max"])
        lines += ["", "## Poll timing, machine load and crop stability (diagnostic record)", "",
                  "%s polls; stage times in ms, median / p90 / max; gaps between updates: median %s s, max %s s, %s over 1 s, %s over 2 s." % (
                      perf["polls"], perf["gaps_s"]["median"], perf["gaps_s"]["max"], perf["gaps_s"]["over_1s"], perf["gaps_s"]["over_2s"]), "",
                  "| stage | in a round | idle |", "|---|---|---|"]
        for name in ("total", "capture", "locate", "read", "panel", "diagnostics"):
            lines.append("| %s | %s | %s |" % (name, row(perf["timing_ms"]["in_round"].get(name)), row(perf["timing_ms"]["idle"].get(name))))
        cpu = perf["cpu_percent"]
        lines += ["", "Machine CPU (percent of all logical processors): whole machine %s, this process %s (%s samples). In-round tick ms with the machine "
                      "at 85%% or more: %s; below 85%%: %s." % (row(cpu["system"]), row(cpu["process"]), cpu["samples"],
                                                                  row(cpu["in_round_tick_ms_when_system_busy_85_plus"]),
                                                                  row(cpu["in_round_tick_ms_when_system_below_85"])), ""]
        if perf["crop_change_grey_levels"]:
            lines += ["Mean grey-level change of each slot's crop against the previous poll's (the tracker's own dealer 'still' tolerance is 2.0):", "",
                      "| slot | comparisons | median | p90 | identical | <=1 | <=2 | <=4 | <=8 |", "|---|---|---|---|---|---|---|---|---|"]
            for slot, c in perf["crop_change_grey_levels"].items():
                lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (slot, c["comparisons"], c["median"], c["p90"], c["share_identical"],
                                                                                 c["share_le_1.0"], c["share_le_2.0"], c["share_le_4.0"], c["share_le_8.0"]))
            lines.append("")
        loss = perf["table_loss_after_dealer_read"]
        lines += ["Dealer card read, round not complete, table lost within about 2 s: %s incident(s) over %s dealer-read poll(s); %s capture(s) saved in "
                  "`table_loss/` (limit %s, %s skipped by the limit)." % (len(loss["incidents"]), loss["dealer_read_polls_watched"], loss["captures_saved"],
                                                                         loss["capture_limit"], loss["skipped_by_limit"]), ""]
        if loss["incidents"]:
            lines += ["| round | dealer read at (s) | table lost at (s) | between (s) | dealer reads | saved / why not |", "|---|---|---|---|---|---|"]
            lines += ["| %s | %s | %s | %s | %s | %s |" % (i["round"], i["seen_t"], i["lost_t"], i["seconds_between"], json.dumps(i["dealer_reads_at_seen"]),
                                                          i["capture"] or i.get("skipped") or "-") for i in loss["incidents"]]
    panels = summary.get("panel_diagnostics")
    if panels:
        def dist_row(label, colour, d):
            bins = " ".join("%s:%s" % (name, count) for name, count in d["bins"].items() if count)
            return "| %s | %s | %s | %s | %s | %s | %s | %s |" % (label, colour, d["n"], d["min"], d["median"], d["p90"], d["max"], bins)
        cap, mirror, labelled = panels["capture"], panels["mirrored_cache"], panels["labelled"]
        lines += ["", "## Result-panel thumbnails: how much the pictures change, and what the cache would do (diagnostic record)", "",
                  "Measures only: nothing the tracker reads or decides was changed. Differences are the cache's own statistic - the largest per-pixel, "
                  "per-channel difference between two pictures of the same size (0-255). The mirrored cache uses the tracker's tolerance %s and size %s." % (
                      mirror["tolerance"], mirror["size"]), "",
                  "%s polls, %s thumbnail samples; the reader accepted %s, refused %s, queued %s. %s round(s) evaluated (%s confirmed), %s still open and "
                  "evaluated provisionally. %s diagnostic error(s)." % (panels["polls"], panels["thumbnail_samples"], panels["readings"]["accepted"],
                                                                      panels["readings"]["refused"], panels["readings"]["queued"],
                                                                      panels["rounds"]["evaluated"], panels["rounds"]["confirmed"],
                                                                      panels["rounds"]["still_open_evaluated_provisionally"], panels["diagnostic_errors"]), "",
                  "Thumbnail samples saved: %s of at most %s (%s); skipped by the limit: %s; failed: %s; not thumbnail-sized, not measured or saved: %s. "
                  "Pictures are at most %s pixels a side, in `panel_thumbnails/`; no whole frame is ever saved." % (
                      cap["saved"], cap["limit"], json.dumps(cap["saved_by_kind"]), json.dumps(cap["skipped_by_limit"]), cap["capture_failed"],
                      cap["not_thumbnail_sized_skipped"], cap["max_picture_side"]), "",
                  "**Labels.** %s Labelled samples: %s. Unlabelled: %s (%s)." % (
                      panels["label_rule"], panels["labelled_samples"], panels["unlabelled_samples"], json.dumps(panels["unlabelled_reasons"])), "",
                  "### Labelled samples", "",
                  "| measure | colour of the label | n | min | median | p90 | max | counts by bin |", "|---|---|---|---|---|---|---|---|"]
        for key, label in (("consecutive_polls_same_card", "same card, consecutive polls"),
                           ("consecutive_polls_different_card", "different card, consecutive polls"),
                           ("initial_picture", "same card vs the first picture at the position"),
                           ("nearest_cached_picture_of_the_same_card", "nearest cached picture of the SAME card"),
                           ("nearest_cached_picture_of_a_different_card", "nearest cached picture of a DIFFERENT card")):
            for colour, d in labelled[key].items():
                lines.append(dist_row(label, colour, d))
        lines += ["", "With the mirrored cache: it would reuse the right read %s time(s) and **a different card's read %s time(s)**; it read a picture "
                      "again although the same card was cached %s time(s)." % (labelled["cache_would_reuse_the_right_read"],
                                                                            labelled["cache_would_reuse_a_different_cards_read"],
                                                                            labelled["read_again_although_the_same_card_was_cached"]), ""]
        if labelled["wrong_reuse_examples"]:
            lines += ["| sample | round | side | position | label | would reuse the read of | difference |", "|---|---|---|---|---|---|---|"]
            lines += ["| %s | %s | %s | %s | %s | %s | %s |" % (e["sample"], e["round"], e["side"], e["position"], e["label"],
                                                              e["would_reuse_the_read_of"], e["difference"]) for e in labelled["wrong_reuse_examples"]]
            lines.append("")
        lines += ["### Unlabelled observations (no claim about which card the picture shows)", "",
                  "| measure | colour of the PROPOSED card | n | min | median | p90 | max | counts by bin |", "|---|---|---|---|---|---|---|---|"]
        for colour, d in panels["unlabelled"]["consecutive_polls_by_colour_of_the_proposed_card"].items():
            lines.append(dist_row("consecutive polls", colour, d))
        if panels["saved"]:
            lines += ["", "### Saved samples (label decided at the end of the round)", "",
                      "| file | kind | round | side | position | t (s) | card the reader proposed | accepted | label | why |", "|---|---|---|---|---|---|---|---|---|---|"]
            lines += ["| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (e["name"], e["kind"], e["round"], e["side"], e["position"], e["t"],
                                                                              e["read_card"], e["accepted"], e["label"] or "-", e["label_basis"])
                      for e in panels["saved"]]
        lines.append("")
    machine = summary.get("machine_diagnostics")
    if machine:
        lines += ["", "## Machine-state clues for a still screen (diagnostic record; clues, never a verdict)", "",
                  "Read-only probes of Windows idle time, power source, the foreground window (identity, not its title) and the captured frame's size and "
                  "brightness. They separate two hypotheses and decide neither: **display idle** (the display went to sleep) and **casino page stall** (the page "
                  "stopped redrawing). %s. Conclusion: %s." % (machine["limits"], machine["conclusion"]), "",
                  "%s polls, %s system samples (at most one every %s s), %s diagnostic error(s); probes with no answer: %s. Frame sizes: %s; mean brightness %s. "
                  "Foreground changes: %s; power-source changes: %s; samples in which the input desktop could not be opened: %s; longest idle time seen: %s s." % (
                      machine["polls"], machine["system_samples"], machine["sampling"]["system_probe_interval_s"], machine["diagnostic_errors"],
                      json.dumps(machine["probe_had_no_answer_in_samples"]), json.dumps(machine["frame_sizes"]), json.dumps(machine["mean_brightness"]),
                      machine["foreground_changes"], machine["power_source_changes"], machine["input_desktop_refused_samples"], machine["idle_s_max"]), ""]
        start = machine.get("preflight")
        if start:
            lines += ["At preflight: power %s, display timeout (AC / battery) %s / %s s, sleep timeout %s / %s s; display power state: %s" % (
                json.dumps(start["power"]), start["display_timeout_s"]["ac"], start["display_timeout_s"]["battery"], start["sleep_timeout_s"]["ac"],
                start["sleep_timeout_s"]["battery"], start["display_power_state"]), ""]
        episodes = machine["still_episodes"]
        lines += ["%s still episode(s) (at least %s consecutive polls with no screen change)." % (len(episodes), machine["sampling"]["still_episode_polls"]), ""]
        if episodes:
            lines += ["| start (s) | seconds | polls | capture ms before / during / after | brightness min-max | idle at start (s) | minus display timeout (s) | "
                      "input during | foreground changed | input desktop refused |", "|---|---|---|---|---|---|---|---|---|---|"]
            for e in episodes:
                cm, br, idle = e["capture_ms"], e["mean_brightness"], e["idle_s"]
                lines.append("| %s | %s%s | %s | %s / %s / %s | %s-%s | %s | %s | %s | %s | %s |" % (
                    e["start_t"], e["seconds"], " (still going at the end)" if e["still_at_end_of_run"] else "", e["polls"], cm["before_median"], cm["during_median"],
                    cm["after_median"], br["min"], br["max"], idle["at_start"], e["idle_at_start_minus_timeout_s"], idle["input_during_episode"],
                    e["foreground_changed"], e["input_desktop_refused_samples"]))
            for number, e in enumerate(episodes, 1):
                lines += ["", "Episode %d clues (not proof):" % number]
                for name, label in (("display_idle", "display idle"), ("casino_page_stall", "casino page stall")):
                    clues = e["hypotheses"][name]
                    lines.append("- %s: for %s; against %s" % (label, json.dumps(clues["for"]) if clues["for"] else "none",
                                                               json.dumps(clues["against"]) if clues["against"] else "none"))
                if e["hypotheses"]["neutral"]:
                    lines.append("- neither way: %s" % json.dumps(e["hypotheses"]["neutral"]))
        lines.append("")
    refused = summary.get("dealer_boxes", {}).get("fusion_refused_polls")
    if refused:
        d = summary["dealer_boxes"]
        line_at = next((e["threshold"] for r in rounds for e in (r.get("dealer_boxes") or {}).get("refusals", [])), None)
        lines += ["", "## Fusion candidates the paper-white guard refused (diagnostic record)", "",
                  "%s refused poll(s), reasons %s; share of the face-test pixels that are paper white: min %s, max %s (the line is %s; the "
                  "full distinct records, with boxes, are in rounds.json `dealer_boxes.refusals`; %s further distinct refusal(s) were counted, "
                  "not kept)." % (refused, json.dumps(d.get("fusion_refusal_reasons")), d["fusion_refused_paper_white_min"],
                                  d["fusion_refused_paper_white_max"], line_at, d.get("fusion_refusals_dropped", 0)), ""]
    recovered = summary.get("dealer_boxes", {}).get("recovered_polls")
    if recovered:
        d = summary["dealer_boxes"]
        lines += ["", "## Fusion-recovered dealer pairs: paper-white share of the halves (diagnostic only)", "",
                  "%s recovered poll(s); share of the face-test pixels that are paper white: min %s, max %s. Face-down backs measured "
                  "0.000-0.018 and white faces 0.6-0.9 (hand-covered 0.39 at worst); see polls.jsonl `dealer_table.diag.halves`." % (
                      recovered, d["recovered_paper_white_min"], d["recovered_paper_white_max"]), ""]
    lines += ["", "## Summary", "```", json.dumps(summary, indent=1), "```", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
