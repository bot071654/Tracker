"""Deterministic TEST-mode demonstration of the whole downstream chain, with real clicks.

    python tools/test_automation_demo.py

Starts the LOCAL TEST POKER TABLE (ANTE / BONUS / PLAY), then drives the real
Tracker poll loop with a scripted table (no screen capture) through five hands:

    HAND 1  new hand -> mouse to ANTE, click -> WAIT -> scenario becomes PLAY
            -> mouse to PLAY, click -> 30 more identical polls, no second click
    HAND 2  new hand -> ANTE once -> DON'T_PLAY -> no PLAY click
    HAND 3  new hand -> ANTE once -> cards never confirmed (WAIT) -> no PLAY click
    HAND 4  new hand -> ANTE once -> PLAY -> the same PLAY payload re-sent 25
            more times -> exactly one PLAY click
    HAND 5  new hand -> ANTE once -> an invalid scenario -> no PLAY click
    STOP    a click queued at the moment the tracker stops is cancelled

Every decision comes from the real Scenario Engine and CardMemory. The
controller is enabled for this process only (config/mouse_controller.json is
not changed), and PyAutoGUI only clicks the local test table after all of the
controller's checks pass. BONUS is never clicked. Move the mouse into a screen
corner to abort (PyAutoGUI fail-safe). Exit code 0 when every check passes.
"""

import logging
import os
import queue
import subprocess
import sys
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

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("automation.mouse_controller").setLevel(logging.INFO)

VISIBLE_MOVE = 0.6            # seconds per mouse move, so it can be followed on screen
PAUSE = 0.8                   # between demo steps

table = {}
invalid = {"on": False}
moves = []                    # (button under the point, (x, y)) for every real move


def scripted_read_table(config, images=None):
    reads, seen = {}, {}
    for slot in CARD_SLOTS:
        card = table.get(slot)
        reads[slot] = {"card": card, "present": bool(card), "confident": bool(card),
                       "confidence": 0.9 if card else 0.0, "suit_margin": 0.2}
        seen[slot] = card
    return seen, reads


def say(text):
    print("\n[DEMO] %s" % text, flush=True)


def main():
    tracker_module.grab_full_screen = lambda monitor=1: numpy.zeros((40, 60, 3), "uint8")
    tracker_module.monitor_origin = lambda monitor=1: (0, 0)
    tracker_module.read_table = scripted_read_table
    tracker_module.db.insert_hand = lambda record: (False, None)
    se.load_engine_config = lambda: se.EngineConfig()

    config = mc.load_mouse_config()
    assert config.mode == mc.TEST
    config.automation_enabled = True                   # this process only
    config.move_duration = VISIBLE_MOVE
    config.ante_button = config.play_button = None     # use the table's own geometry
    mc.load_mouse_config = lambda path=None: config
    target = mc.TestTableTarget(config)

    def button_at(x, y):
        rects = ((target.read() or {}).get("buttons") or {})
        return next((name for name, (l, t, r, b) in rects.items()
                     if l <= x < r and t <= y < b), None)

    class RecordingMouse(mc.PyAutoGuiMouse):
        def move(self, x, y, duration):
            super().move(x, y, duration)
            moves.append((button_at(x, y), (x, y)))

    mc.PyAutoGuiMouse = RecordingMouse

    real_engine = tracker_module.Tracker._run_scenario_engine

    def engine(self, cards, reads):
        result = real_engine(self, cards, reads)
        if invalid["on"] and result:
            result = dict(result, decision="NOT_A_DECISION")
        return result

    tracker_module.Tracker._run_scenario_engine = engine

    ui = subprocess.Popen([sys.executable, os.path.join("automation", "test_poker_ui.py")])
    for _ in range(150):
        if target.button_centre(mc.ANTE):
            break
        time.sleep(0.1)
    else:
        print("FAIL: the test table did not start")
        ui.terminate()
        return 1
    time.sleep(PAUSE)

    events = queue.Queue()
    tr = tracker_module.Tracker({"monitor": 1, "clear_frames": 3}, events)
    failures = []
    last_decision = {"value": None}
    last_scenario = {"value": None}

    def poll(n):
        for _ in range(n):
            tr._tick()
            while not events.empty():
                kind, payload = events.get_nowait()
                scenario = (payload or {}).get("scenario") if kind == "update" else None
                if scenario:
                    last_scenario["value"] = scenario
                decision = (scenario or {}).get("decision")
                if decision and decision != last_decision["value"]:
                    last_decision["value"] = decision
                    print("[DEMO] scenario is now %s" % decision, flush=True)
            time.sleep(0.05)

    def settle():
        end = time.time() + 3 * (VISIBLE_MOVE + config.verify_timeout)
        while time.time() < end:
            status = tr.action_controller.status() if tr.action_controller else {}
            if mc.REQUESTED not in (status.get("ante"), status.get("play")):
                break
            time.sleep(0.05)
        time.sleep(PAUSE)

    def counts():
        return dict((target.read() or {}).get("counts") or {})

    def check(label, expected_counts, expected_moves=None):
        got = counts()
        ok = got == expected_counts
        detail = "table clicks %s" % got
        if expected_moves is not None:
            got_moves = [name for name, _ in moves]
            ok = ok and got_moves == expected_moves
            detail += "  mouse moved to %s" % got_moves
        print("[%s] %-58s %s" % ("PASS" if ok else "FAIL", label, detail), flush=True)
        if not ok:
            failures.append(label)

    def click_counts(ante, play):
        return {mc.ANTE: ante, mc.BONUS: 0, mc.PLAY_BUTTON: play}

    def new_hand(n, what):
        say("HAND %d: table cleared -> new hand (%s)" % (n, what))
        table.clear()
        poll(12)
        last_decision["value"] = None                   # ignore the old hand's held cards
        settle()

    seat = lambda p, f: dict(zip(["player_1", "player_2", "flop_1", "flop_2", "flop_3"], p + f))

    # HAND 1 -------------------------------------------------------------------------------
    new_hand(1, "expect: mouse moves to ANTE and clicks it")
    check("HAND 1 mouse moved to ANTE and clicked it once", click_counts(1, 0), [mc.ANTE])
    say("HAND 1: player cards only -> WAIT, no PLAY yet")
    table.update({"player_1": "7S", "player_2": "7D"})
    poll(10); settle()
    check("HAND 1 WAIT -> no PLAY click", click_counts(1, 0), [mc.ANTE])
    say("HAND 1: flop 7H KC 3S -> trips -> expect scenario PLAY, mouse to PLAY, click")
    table.update(seat(["7S", "7D"], ["7H", "KC", "3S"]))
    poll(20); settle()
    check("HAND 1 scenario PLAY -> mouse moved to PLAY, clicked once",
          click_counts(1, 1), [mc.ANTE, mc.PLAY_BUTTON])
    say("HAND 1: 30 more polls with the same PLAY scenario -> expect no more clicks")
    poll(30); settle()
    check("HAND 1 repeated PLAY updates -> no duplicate click",
          click_counts(1, 1), [mc.ANTE, mc.PLAY_BUTTON])

    # HAND 2 -------------------------------------------------------------------------------
    new_hand(2, "expect ANTE, then DON'T_PLAY")
    table.update(seat(["JS", "9D"], ["7C", "5H", "2S"]))
    poll(25); settle()
    check("HAND 2 DON'T_PLAY -> ANTE only, no PLAY click", click_counts(2, 1),
          [mc.ANTE, mc.PLAY_BUTTON, mc.ANTE])

    # HAND 3 -------------------------------------------------------------------------------
    new_hand(3, "expect ANTE, then WAIT for the whole hand")
    table.update({"player_1": "AS"})
    poll(25); settle()
    check("HAND 3 WAIT -> ANTE only, no PLAY click", click_counts(3, 1))

    # HAND 4 -------------------------------------------------------------------------------
    new_hand(4, "expect ANTE, PLAY, then duplicate payloads ignored")
    table.update(seat(["AH", "AD"], ["AC", "9S", "4D"]))
    poll(20); settle()
    controller = tr.action_controller
    scenario = last_scenario["value"]                   # the engine's own PLAY payload
    say("HAND 4: re-sending the identical %s payload 25 times -> expect no more clicks"
        % scenario["decision"])
    for _ in range(25):
        controller.handle_scenario_result(dict(scenario), action_round=controller.round)
    settle()
    check("HAND 4 PLAY once despite 25 duplicate payloads", click_counts(4, 2))

    # HAND 5 -------------------------------------------------------------------------------
    new_hand(5, "expect ANTE, then an invalid scenario is refused")
    invalid["on"] = True
    table.update(seat(["KS", "KD"], ["KH", "2C", "2D"]))
    poll(20); settle()
    check("HAND 5 invalid scenario -> no PLAY click", click_counts(5, 2))

    # STOP ---------------------------------------------------------------------------------
    say("STOP: a new hand starts and the tracker is stopped while the ANTE click is queued")
    table.clear()
    for _ in range(12):
        tr._tick()                                      # no pause: the click is still in flight
    invalid["on"] = False
    tr.stop()
    time.sleep(VISIBLE_MOVE + PAUSE)
    check("STOP queued ANTE cancelled -> no click after stop", click_counts(5, 2))

    ok_bonus = counts().get(mc.BONUS) == 0
    print("[%s] %-58s" % ("PASS" if ok_bonus else "FAIL", "BONUS was never clicked"), flush=True)
    if not ok_bonus:
        failures.append("BONUS clicked")

    time.sleep(PAUSE)
    ui.terminate()
    print("\nRESULT: %s" % ("ALL PASS" if not failures else "FAILED: %s" % failures), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
