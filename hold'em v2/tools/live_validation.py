"""LIVE READ-ONLY validation of the recognition pipeline, hand by hand.

    python tools/live_validation.py                    record: opens the normal tracker
                                                       window; press Start Tracker and
                                                       play the hands yourself
    python tools/live_validation.py --report SESSION   hand-by-hand report (+ evidence sheets)

    LIVE TABLE -> MSS capture -> card recognition -> CardMemory -> Scenario Engine
               -> PLAY / DON'T_PLAY / WAIT -> UI display

READ ONLY. The Action Controller is forced OFF in this process whatever
config/mouse_controller.json says, and the run fails loudly if PyAutoGUI was
ever imported. Nothing is clicked, typed or sent to the game. The tracker,
recognition, CardMemory and Scenario Engine run unmodified; this tool only
watches what they emit.

What is recorded, in logs/live_validation/<SESSION>/:

    polls.jsonl      every poll: round id (CardMemory generation), state, per-slot
                     presence / status / confidence / card, the scenario payload
    display.jsonl    what the window actually showed for that poll
    frames/*.jpg     the table area whenever anything changes (and every 2 s while
                     cards are showing) - the evidence the true cards are read from

The report groups polls into hands by round id and works out, per slot,
confirmation latency (first poll the card is present -> first CONFIRMED),
revisions after confirmation (false confirmations), refusals (AMBIGUOUS /
UNKNOWN) and previous-hand leakage (a card from the last hand displayed in a
slot that is empty on screen). It writes one evidence sheet per hand WITHOUT
the tracker's readings on it, so the true cards can be read independently.

Ground truth goes in <SESSION>/ground_truth.json:

    {"12": {"player_1": "AS", "player_2": "UNCLEAR", "flop_1": "7D", ...,
            "dealer_2": null}}

a card, "UNCLEAR" (not counted - never guessed), or null (not dealt / never
visible). Then run --report again for the accuracy figures.
"""

import argparse
import json
import os
import queue
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SESSIONS = os.path.join(ROOT, "logs", "live_validation")
SLOTS = ["player_1", "player_2", "flop_1", "flop_2", "flop_3", "turn", "river",
         "dealer_1", "dealer_2"]
GROUPS = [("Player", ["player_1", "player_2"]), ("Flop", ["flop_1", "flop_2", "flop_3"]),
          ("Turn", ["turn"]), ("River", ["river"]), ("Dealer", ["dealer_1", "dealer_2"])]
SHOWN = ("CONFIRMED", "HELD")
UNCLEAR = "UNCLEAR"
FRAME_EVERY = 2.0             # seconds between routine frames while cards show
FRAME_PAD = 40                # pixels around the card boxes
MIN_FRAME_GAP = 0.25          # seconds; a change inside the gap is saved on the next poll
READABLE = 0.62               # a reading this good means the card face was visible


# -- recording ----------------------------------------------------------------------------

class Recorder:
    """Watches the tracker's own updates. Writes on a background thread."""

    def __init__(self, folder):
        self.folder = folder
        os.makedirs(os.path.join(folder, "frames"), exist_ok=True)
        self.polls = open(os.path.join(folder, "polls.jsonl"), "a", encoding="utf-8")
        self.display = open(os.path.join(folder, "display.jsonl"), "a", encoding="utf-8")
        self.jobs = queue.Queue()
        self.last_key = None
        self.pending = False
        self.last_frame_at = 0.0
        self.followup_at = None
        self.frames = 0
        self.writer = threading.Thread(target=self._write, name="validation-writer", daemon=True)
        self.writer.start()

    def on_poll(self, tracker, payload):
        """Tracker thread: one update, plus the frame it was read from."""
        now = time.time()
        reads = payload.get("reads") or {}
        statuses = payload.get("statuses") or {}
        cards = payload.get("cards") or {}
        scenario = payload.get("scenario") or {}
        generation = tracker.memory.generation
        row = {
            "t": round(now, 3), "emitted_at": payload.get("emitted_at"),
            "round": generation, "state": payload.get("state"),
            "cards": {s: cards.get(s) for s in SLOTS},
            "seen": {s: (payload.get("seen") or {}).get(s) for s in SLOTS},
            "present": {s: bool((reads.get(s) or {}).get("present")) for s in SLOTS},
            "conf": {s: round(float((reads.get(s) or {}).get("confidence") or 0.0), 3)
                     for s in SLOTS},
            "read_as": {s: (reads.get(s) or {}).get("card") for s in SLOTS},
            "status": {s: statuses.get(s) for s in SLOTS},
            "scenario": {k: scenario.get(k) for k in
                         ("decision", "round_id", "player_cards", "flop_cards",
                          "primary_scenario", "matched_scenarios", "reason")},
            "action": payload.get("action"),
        }
        frame_name = None
        key = (generation, row["state"], tuple(row["cards"].values()),
               scenario.get("decision"), tuple(row["present"].values()))
        if key != self.last_key:
            self.pending = True                  # kept until a frame is actually saved
        any_present = any(row["present"].values())
        due = (self.pending
               or (self.followup_at is not None and now >= self.followup_at)
               or (any_present and now - self.last_frame_at >= FRAME_EVERY))
        stash = getattr(tracker, "_validation_frame", None)
        if due and stash is not None and now - self.last_frame_at >= MIN_FRAME_GAP:
            frame, origin, regions = stash
            frame_name = "%06d_r%s_%s.jpg" % (self.frames, generation, row["state"])
            self.frames += 1
            self.last_frame_at = now
            # One more a moment after a change, when the card has settled.
            self.followup_at = now + 0.8 if self.pending else None
            self.pending = False
            self.jobs.put(("frame", frame_name, frame, origin, regions))
        self.last_key = key
        row["frame"] = frame_name
        self.jobs.put(("poll", row))

    def on_display(self, app, payload):
        """UI thread: what the window shows after handling the update."""
        panel = getattr(app, "scenario_panel", None)
        row = {"emitted_at": payload.get("emitted_at"), "shown_at": round(time.time(), 3),
               "decision": panel.decision_var.get() if panel is not None else None,
               "cards": {s: var.get() for s, var in (getattr(app, "card_vars", {}) or {}).items()}}
        self.jobs.put(("display", row))

    def _write(self):
        import cv2

        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                if job[0] == "poll":
                    self.polls.write(json.dumps(job[1]) + "\n")
                    self.polls.flush()
                elif job[0] == "display":
                    self.display.write(json.dumps(job[1]) + "\n")
                    self.display.flush()
                else:
                    _, name, frame, origin, regions = job
                    image = table_area(frame, origin, regions)
                    if image is not None:
                        cv2.imwrite(os.path.join(self.folder, "frames", name), image,
                                    [cv2.IMWRITE_JPEG_QUALITY, 92])
            except Exception as exc:  # noqa: BLE001 - recording must never stop tracking
                print("validation writer: %s" % exc, flush=True)

    def close(self):
        self.jobs.put(None)
        self.writer.join(timeout=10)
        self.polls.close()
        self.display.close()


def table_area(frame, origin, regions):
    """The part of the frame holding the nine card boxes, padded."""
    boxes = [r for r in (regions or {}).values() if r]
    if frame is None or not boxes:
        return frame
    left = min(int(r["left"]) for r in boxes) - int(origin[0]) - FRAME_PAD
    top = min(int(r["top"]) for r in boxes) - int(origin[1]) - FRAME_PAD
    right = max(int(r["left"]) + int(r["width"]) for r in boxes) - int(origin[0]) + FRAME_PAD
    bottom = max(int(r["top"]) + int(r["height"]) for r in boxes) - int(origin[1]) + FRAME_PAD
    height, width = frame.shape[:2]
    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    if right <= left or bottom <= top:
        return frame
    return frame[top:bottom, left:right].copy()


def force_read_only():
    """The Action Controller cannot be enabled in this process."""
    from automation import mouse_controller as mc

    def disabled(path=None):
        return mc.MouseControllerConfig(automation_enabled=False).validate()

    mc.load_mouse_config = disabled
    assert disabled().automation_enabled is False


def install_tracker_hooks(recorder):
    """Let the recorder see every update and its frame. Returns an undo function."""
    import tracker as tracker_module

    Tracker = tracker_module.Tracker
    original_read, original_emit = Tracker._read_table, Tracker._emit

    def read_table(self):
        result = original_read(self)
        frame = self._frame
        if frame is not None:
            self._validation_frame = (frame[0], frame[1], dict(self._last_regions or {}))
        return result

    def emit(self, kind, payload):
        if kind == "update":
            try:
                recorder.on_poll(self, payload)
            except Exception as exc:  # noqa: BLE001
                print("validation recorder: %s" % exc, flush=True)
        original_emit(self, kind, payload)

    Tracker._read_table, Tracker._emit = read_table, emit

    def undo():
        Tracker._read_table, Tracker._emit = original_read, original_emit

    return undo


def record(session):
    folder = os.path.join(SESSIONS, session)
    force_read_only()

    import tkinter as tk

    import app as app_module
    from calibration.calibrator import make_dpi_aware
    from config.settings import setup_logging

    recorder = Recorder(folder)
    install_tracker_hooks(recorder)

    original_handle = app_module.App._handle_event

    def handle_event(self, kind, payload):
        original_handle(self, kind, payload)
        if kind == "update":
            try:
                recorder.on_display(self, payload)
            except Exception as exc:  # noqa: BLE001
                print("validation display: %s" % exc, flush=True)

    app_module.App._handle_event = handle_event

    with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as handle:
        json.dump({"session": session, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "read_only": True, "automation_enabled": False}, handle, indent=2)

    setup_logging()
    make_dpi_aware()
    root = tk.Tk()
    application = app_module.App(root)
    root.title("Poker Hand Tracker - LIVE READ-ONLY VALIDATION")
    print("LIVE READ-ONLY VALIDATION  session %s" % session, flush=True)
    print("Mouse automation is OFF in this process. Press Start Tracker, play the hands,", flush=True)
    print("then close the window. Recording to %s" % folder, flush=True)
    try:
        root.mainloop()
    finally:
        try:
            if application.tracker.is_running():
                application.tracker.stop()
        except Exception:  # noqa: BLE001
            pass
        recorder.close()
    if "pyautogui" in sys.modules:
        print("FAIL: PyAutoGUI was loaded during a read-only run", flush=True)
        return 2
    print("Recorded %d frames. Report:\n    python tools/live_validation.py --report %s"
          % (recorder.frames, session), flush=True)
    return 0


# -- report -----------------------------------------------------------------------------------

def load_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    return rows


def split_hands(polls):
    """[(round id, [polls])] for every round in which a player card was present."""
    hands, current, rows = [], None, []
    for row in polls:
        if row["round"] != current:
            if rows and any(r["present"]["player_1"] or r["present"]["player_2"] for r in rows):
                hands.append((current, rows))
            current, rows = row["round"], []
        rows.append(row)
    if rows and any(r["present"]["player_1"] or r["present"]["player_2"] for r in rows):
        hands.append((current, rows))
    return hands


def analyse_hand(rows, previous_final=None):
    """Everything the tracker's own record says about one hand."""
    slots = {}
    for slot in SLOTS:
        first_present = first_confirmed = None
        confirmed_card = None
        revisions, refusals, values = [], {"AMBIGUOUS": 0, "UNKNOWN": 0}, []
        own_reads = set()
        leaks = 0
        for row in rows:
            status = row["status"].get(slot)
            card = row["cards"].get(slot)
            # Latency runs from the first poll the face could be read, not the
            # first poll anything was there: a dealer card lies face down for
            # most of the hand and a card is "present" while still sliding in.
            if first_present is None and row["present"].get(slot) \
                    and (row.get("conf") or {}).get(slot, 1.0) >= READABLE:
                first_present = row["t"]
            if row["present"].get(slot) and (row.get("read_as") or {}).get(slot):
                own_reads.add(row["read_as"][slot])
            # Leakage: the previous hand's card shown here before this hand ever
            # read it. The same card turning up in the same place in two hands in
            # a row happens (twice in the first live session) and is not a leak.
            if (previous_final and card and card == previous_final.get(slot)
                    and not row["present"].get(slot) and card not in own_reads):
                leaks += 1
            if status in refusals:
                refusals[status] += 1
            if status == "CONFIRMED" and first_confirmed is None:
                first_confirmed, confirmed_card = row["t"], card
            if card and (not values or values[-1] != card):
                values.append(card)
            if confirmed_card and card and card != confirmed_card and status in SHOWN:
                if not revisions or revisions[-1] != card:
                    revisions.append(card)
        final = next((row["cards"].get(slot) for row in reversed(rows) if row["cards"].get(slot)), None)
        slots[slot] = {
            "final": final, "values": values, "present": first_present is not None,
            "latency_ms": (round((first_confirmed - first_present) * 1000)
                           if first_present is not None and first_confirmed is not None else None),
            "revisions": revisions, "refusals": refusals, "leaks": leaks,
        }
    decisions = []
    for row in rows:
        decision = (row.get("scenario") or {}).get("decision")
        if decision and (not decisions or decisions[-1] != decision):
            decisions.append(decision)
    final_decision = next((d for d in reversed(decisions) if d != "WAIT"), decisions[-1] if decisions else None)
    return {"slots": slots, "decisions": decisions, "decision": final_decision,
            "start": rows[0]["t"], "end": rows[-1]["t"],
            "final": {slot: slots[slot]["final"] for slot in SLOTS}}


def classify(truth, info):
    """correct / wrong / unread / ambiguous / unclear / not_dealt / phantom for one slot."""
    got = info["final"]
    if truth == UNCLEAR:
        return "unclear"
    if truth is None:
        return "phantom" if got else "not_dealt"
    if got == truth:
        return "correct"
    if got:
        return "wrong"
    return "ambiguous" if info["refusals"]["AMBIGUOUS"] else "unread"


def display_agreement(rows, display):
    """How many polls' scenario decision was shown as sent (joined on emitted_at)."""
    shown = {round(d["emitted_at"], 3): d for d in display if d.get("emitted_at")}
    agree = total = 0
    for row in rows:
        entry = shown.get(round(row["emitted_at"], 3)) if row.get("emitted_at") else None
        decision = (row.get("scenario") or {}).get("decision")
        if entry is None or not decision:
            continue
        total += 1
        # ui/scenario_panel.py shows "Decision: DON'T PLAY" for DON'T_PLAY.
        if entry.get("decision") == "Decision: %s" % decision.replace("_", " "):
            agree += 1
    return agree, total


def evidence_sheet(folder, round_id, rows):
    """One image per hand, labelled with state and time only (never the tracker's reading).

    For each state: the frame with the most cards present (earliest of those)
    and the last frame. The live video zooms and pans, so one frame per state
    can hide a card that another shows clearly.
    """
    import cv2
    import numpy

    picks = []
    for state in ("PLAYER_CARDS", "FLOP", "TURN", "RIVER", "COMPLETE"):
        framed = [row for row in rows if row.get("frame") and row["state"] == state]
        if not framed:
            continue
        best = max(framed, key=lambda row: sum(row["present"].values()))
        for row in (best, framed[-1]):
            if row not in picks:
                picks.append(row)
    if not picks:
        return None
    tiles = []
    start = rows[0]["t"]
    for row in picks:
        image = cv2.imread(os.path.join(folder, "frames", row["frame"]))
        if image is None:
            continue
        strip = numpy.full((26, image.shape[1], 3), 255, "uint8")
        cv2.putText(strip, "%s  +%.1fs  %s" % (row["state"], row["t"] - start, row["frame"]),
                    (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        tiles.append(numpy.vstack([strip, image]))
    if not tiles:
        return None
    width = max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, 6, 0, width - t.shape[1], cv2.BORDER_CONSTANT,
                                value=(255, 255, 255)) for t in tiles]
    per_row = 3
    height = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, height - t.shape[0], 0, 6, cv2.BORDER_CONSTANT,
                                value=(255, 255, 255)) for t in tiles]
    while len(tiles) % per_row:
        tiles.append(numpy.full_like(tiles[0], 255))
    sheet = numpy.vstack([numpy.hstack(tiles[i:i + per_row])
                          for i in range(0, len(tiles), per_row)])
    path = os.path.join(folder, "evidence", "hand_r%s.jpg" % round_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, sheet, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return path


def report(session, sheets=True):
    folder = os.path.join(SESSIONS, session)
    polls = load_jsonl(os.path.join(folder, "polls.jsonl"))
    display = load_jsonl(os.path.join(folder, "display.jsonl"))
    truth_path = os.path.join(folder, "ground_truth.json")
    truth = {}
    if os.path.exists(truth_path):
        with open(truth_path, encoding="utf-8") as handle:
            truth = json.load(handle)
    hands = split_hands(polls)
    print("Session %s: %d polls, %d hands with player cards\n" % (session, len(polls), len(hands)))

    totals = {}
    latencies = []
    previous = None
    summary = []
    for round_id, rows in hands:
        info = analyse_hand(rows, previous)
        previous_final, previous = previous, info["final"]
        agree, shown = display_agreement(rows, display)
        sheet = evidence_sheet(folder, round_id, rows) if sheets else None
        hand_truth = truth.get(str(round_id), {})
        print("=" * 78)
        print("ROUND %s   %s   %.1fs   evidence: %s" % (
            round_id, time.strftime("%H:%M:%S", time.localtime(info["start"])),
            info["end"] - info["start"], os.path.relpath(sheet, ROOT) if sheet else "-"))
        for label, group in GROUPS:
            got = " ".join(info["slots"][s]["final"] or "--" for s in group)
            real = " ".join(str(hand_truth.get(s, "?")) if hand_truth else "?" for s in group)
            print("  %-7s tracker: %-12s truth: %s" % (label + ":", got, real))
        issues = []
        for slot in SLOTS:
            s = info["slots"][slot]
            if s["latency_ms"] is not None:
                latencies.append(s["latency_ms"])
            if s["revisions"]:
                issues.append("%s revised after confirmation -> %s" % (slot, " ".join(s["revisions"])))
            if s["refusals"]["AMBIGUOUS"] or s["refusals"]["UNKNOWN"]:
                issues.append("%s refusals AMBIGUOUS=%d UNKNOWN=%d" % (
                    slot, s["refusals"]["AMBIGUOUS"], s["refusals"]["UNKNOWN"]))
            if s["leaks"]:
                issues.append("LEAK %s showed the previous hand's %s on %d poll(s) while empty"
                              % (slot, previous_final.get(slot), s["leaks"]))
            if s["present"] and not s["final"]:
                issues.append("%s present on screen but never displayed" % slot)
        print("  Scenario: %s   (sequence %s)" % (info["decision"], " -> ".join(info["decisions"])))
        print("  UI showed the sent decision on %d/%d polls" % (agree, shown))
        print("  Confirmation latency ms: %s" % ", ".join(
            "%s=%s" % (s, info["slots"][s]["latency_ms"]) for s in SLOTS
            if info["slots"][s]["latency_ms"] is not None))
        for issue in issues:
            print("  ! %s" % issue)
        verdicts = {}
        if hand_truth:
            for slot in SLOTS:
                verdict = classify(hand_truth.get(slot), info["slots"][slot])
                verdicts[slot] = verdict
                totals[verdict] = totals.get(verdict, 0) + 1
            print("  Recognition: %s" % ", ".join("%s=%s" % kv for kv in verdicts.items()
                                                  if kv[1] != "not_dealt"))
        summary.append((round_id, info, verdicts, agree, shown))

    print("\n" + "=" * 78)
    print("OVERALL")
    if latencies:
        values = sorted(latencies)
        print("  confirmation latency ms  median %d  p95 %d  worst %d  (n=%d)" % (
            values[len(values) // 2], values[min(len(values) - 1, int(0.95 * (len(values) - 1) + 0.5))],
            values[-1], len(values)))
    if totals:
        scored = sum(totals.get(k, 0) for k in ("correct", "wrong", "unread", "ambiguous", "phantom"))
        print("  slots: %s" % ", ".join("%s=%d" % kv for kv in sorted(totals.items())))
        if scored:
            print("  accuracy (correct / visible, unclear excluded): %d/%d = %.1f%%" % (
                totals.get("correct", 0), scored, 100.0 * totals.get("correct", 0) / scored))
            read = totals.get("correct", 0) + totals.get("wrong", 0) + totals.get("phantom", 0)
            if read:
                print("  precision (correct / displayed): %d/%d = %.1f%%" % (
                    totals.get("correct", 0), read, 100.0 * totals.get("correct", 0) / read))
    else:
        print("  no ground_truth.json yet - accuracy not scored")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Live read-only recognition validation")
    parser.add_argument("--report", metavar="SESSION")
    parser.add_argument("--session", default=time.strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--no-sheets", action="store_true")
    args = parser.parse_args(argv)
    if args.report:
        report(args.report, sheets=not args.no_sheets)
        return 0
    return record(args.session)


if __name__ == "__main__":
    sys.exit(main())
