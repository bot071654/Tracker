"""End-to-end TEST: the app plays the local betting screen by itself, with real clicks.

    python tools/test_betting_screen_demo.py              7 rounds, session expires in round 5
    python tools/test_betting_screen_demo.py --rounds 10 --seed 3

Starts automation/test_betting_screen.py (LOCAL TEST SCREEN - no casino, no
money) and runs the real Tracker thread against it:

    betting screen deals -> card readings -> CardMemory -> Scenario Engine
        -> pre-round rules (config/scenarios.json) -> Action Controller -> click

The cards are taken from the screen's own published deal instead of from pixels
(the screen draws its cards with a font, not the casino card faces the
recognition templates were taught). Everything after that is the real code.

Each round is then checked against what should have happened, worked out
independently from the screen's own record:

    ANTE clicked   exactly when your pre-round rules say ANTE for that round
    PLAY clicked   exactly when ANTE was placed and the Scenario Engine says PLAY
    no click       while the session is expired, and never on a closed button

The controller is enabled for this process only; config/mouse_controller.json
is not changed. Move the mouse into a screen corner to abort. Exit code 0 when
every check passes.
"""

import argparse
import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from calibration.calibrator import make_dpi_aware  # noqa: E402

make_dpi_aware()

import numpy  # noqa: E402

import tracker as tracker_module  # noqa: E402
from automation import mouse_controller as mc  # noqa: E402
from config.settings import CARD_SLOTS  # noqa: E402
from poker import scenario_engine as se  # noqa: E402
from poker import scenarios as scenario_rules  # noqa: E402
from poker.hand_record import build_hand_record  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("automation.mouse_controller").setLevel(logging.INFO)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--seed", type=int, default=8)
    parser.add_argument("--expire-round", type=int, default=5)
    args = parser.parse_args(argv)

    config = mc.load_mouse_config()
    assert config.mode == mc.TEST
    config.automation_enabled = True                    # this process only
    config.move_duration = 0.5                          # slow enough to watch
    config.phase_wait = 25.0
    # Its own heartbeat file, so another test table left open cannot mix in.
    config.status_file = os.path.join(tempfile.gettempdir(),
                                      "poker_test_betting_screen_%d.json" % os.getpid())
    mc.load_mouse_config = lambda path=None: config
    target = mc.TestTableTarget(config)
    try:
        os.remove(config.status_file)
    except OSError:
        pass

    screen = subprocess.Popen([
        sys.executable, os.path.join("automation", "test_betting_screen.py"),
        "--seed", str(args.seed), "--next-game", "2", "--betting", "8", "--dealing", "2",
        "--decision", "8", "--reveal", "3", "--result", "3",
        "--expire-round", str(args.expire_round), "--expire-seconds", "6",
        "--status-file", config.status_file])
    for _ in range(150):
        if target.button_centre(mc.ANTE):
            break
        time.sleep(0.1)
    else:
        print("FAIL: the test betting screen did not start")
        screen.terminate()
        return 1

    # Cards come from the screen's published deal; the rest is the real pipeline.
    frames = {"n": 0}

    def grab(monitor=1):
        frames["n"] += 1
        return numpy.full((40, 60, 3), frames["n"] % 250, "uint8")

    def read_from_screen(cfg, images=None):
        shown = ((target.read() or {}).get("cards") or {})
        reads, seen = {}, {}
        for slot in CARD_SLOTS:
            card = shown.get(slot)
            reads[slot] = {"card": card, "present": bool(card), "confident": bool(card),
                           "confidence": 0.95 if card else 0.0, "suit_margin": 0.3}
            seen[slot] = card
        return seen, reads

    tracker_module.grab_full_screen = grab
    tracker_module.monitor_origin = lambda monitor=1: (0, 0)
    tracker_module.read_table = read_from_screen
    tracker_module.db.insert_hand = lambda record: (True, {"id": 0})
    tracker_module.excel_export.append_hand = lambda row: None
    tracker_module.Tracker._verify_against_panels = lambda self, record: ("unchecked", "demo")
    se.load_engine_config = lambda: se.EngineConfig()

    rules = scenario_rules.load()
    tr = tracker_module.Tracker({"monitor": 1, "clear_frames": 3, "poll_interval_seconds": 0.2,
                                 "scenario_engine": True}, queue.Queue())
    tr.preround_scenarios = rules
    tr.start()

    print("[DEMO] local TEST betting screen running; the app is playing %d rounds by itself"
          % args.rounds, flush=True)
    last_round = 0
    try:
        while True:
            status = target.read() or {}
            done = [r for r in status.get("results") or [] if r["round"] <= args.rounds]
            if done and done[-1]["round"] != last_round:
                last_round = done[-1]["round"]
                print("[DEMO] round %d finished: %s" % (last_round, status.get("message")), flush=True)
            if len(done) >= args.rounds or screen.poll() is not None:
                break
            time.sleep(0.5)
    finally:
        tr.stop()
        final = target.read() or {}
        screen.terminate()

    return report(final, rules, args)


def expected_actions(results, rules):
    """Per round: what the rules and the Scenario Engine say, from the screen's own record."""
    expected, history = [], []
    for result in results:
        previous = history[0] if history else None
        preround, rule = scenario_rules.decide_preround(rules, previous, history)
        cards = result["cards"]
        slots = {slot: {"card": cards[slot], "status": "CONFIRMED", "readings": 3}
                 for slot in se.PLAYER_SLOTS + se.FLOP_SLOTS}
        scenario = se.evaluate_round(slots, round_id=result["round"])["decision"]
        expected.append({"round": result["round"], "preround": preround,
                         "rule": rule["name"] if rule else "default rule",
                         "scenario": scenario})
        history.insert(0, build_hand_record(cards))
    return expected


def report(final, rules, args):
    results = [r for r in final.get("results") or [] if r["round"] <= args.rounds]
    expected = expected_actions(results, rules)
    periods = final.get("expired_periods") or []
    presses = final.get("presses") or []
    failures = []

    print("\n%-5s %-8s %-11s %-13s %-10s %-5s %-5s %-6s %s" % (
        "ROUND", "PLAYER", "FLOP", "RULES SAY", "SCENARIO", "ANTE", "PLAY", "NET", "CHECK"))
    for result, want in zip(results, expected):
        cards = result["cards"]
        want_ante = want["preround"] == scenario_rules.ANTE
        want_play = want_ante and want["scenario"] == se.PLAY
        problems = []
        if result["ante"] != want_ante:
            problems.append("ANTE %s, rules say %s" % ("clicked" if result["ante"] else "missed",
                                                       want["preround"].upper()))
        if result["play"] != want_play:
            problems.append("PLAY %s, expected %s" % ("clicked" if result["play"] else "not clicked",
                                                      "PLAY" if want_play else "none"))
        if result["bonus"]:
            problems.append("BONUS was clicked")
        ok = not problems
        if not ok:
            failures.append("round %d: %s" % (result["round"], "; ".join(problems)))
        print("%-5d %-8s %-11s %-13s %-10s %-5s %-5s %+-6d %s" % (
            result["round"], "%s %s" % (cards["player_1"], cards["player_2"]),
            " ".join(cards[s] for s in ("flop_1", "flop_2", "flop_3")),
            want["preround"].upper(), want["scenario"], "yes" if result["ante"] else "-",
            "yes" if result["play"] else "-", result["net"],
            "PASS" if ok else "FAIL: " + "; ".join(problems)))

    during = [p for p in presses for start, end in periods
              if start <= p[0] <= (end or float("inf"))]
    refused = [p for p in presses if not p[3]]
    print("\nsession expired periods: %s" % periods)
    print("[%s] no click while the session was expired (%d)" % ("PASS" if not during else "FAIL", len(during)))
    print("[%s] no click on a closed button (%d refused)" % ("PASS" if not refused else "FAIL", len(refused)))
    if not periods and args.expire_round <= args.rounds:
        failures.append("the session never expired")
    if during:
        failures.append("clicked while expired")
    if refused:
        failures.append("clicked a closed button: %s" % refused)
    if len(results) < args.rounds:
        failures.append("only %d of %d rounds finished" % (len(results), args.rounds))
    print("TEST credits: %s" % final.get("credits"))
    print("RESULT: %s" % ("ALL PASS" if not failures else "FAILED: %s" % failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
