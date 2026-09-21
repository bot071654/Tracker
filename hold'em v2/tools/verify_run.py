"""Score a live test run, from the logs it left behind.

Written for the check after a restart: play some rounds, then run this. It
reads only what was logged since the mark, across every rotated log file, so
the old process's output cannot flatter or spoil the result.

    python tools/verify_run.py --mark          (run this the moment you start)
    python tools/verify_run.py                 (and this after the rounds)
    python tools/verify_run.py --all           (every log file, from the start)
    python tools/verify_run.py --from 19:03    (from a time today)
    python tools/verify_run.py --from "2026-09-13 19:03:39"

--mark notes the current time and moves any crops still lying about into
logs/failures_before_fix. Run it after the old tracker has stopped and before
the new one starts.

The mark is a time, not a line number. A line number stopped meaning anything
the first time the log rotated: tracker.log starts again at 2MB, and a mark at
line 12,827 of a file that is now 2,204 lines long silently selected nothing.

What it answers:

    does "--" still appear            slots still empty when a round is sealed
    is anything read as a WRONG card  panel disagreements, one card in two slots
    are completed hands stored        every sealed round reaching the database
    did a round get mixed up          the board at COMPLETE against what was
                                      stored, which is how a lost round shows
    does the result panel agree       each stored hand against the casino's
                                      best-five panel
    how long do the dealer's cards    per-stage time each poll at the river,
    take, and where does it go        boxed -> read -> memory -> shown for each
                                      dealer card, the gate's refusals, and the
                                      rounds lost waiting for the dealer

Nothing here judges a card by looking at it again - it reports what the
tracker itself said at the time. A clean report means the run was clean, not
that the reader is right.
"""

import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "tracker.log")
WATERMARK = os.path.join(ROOT, "logs", ".watermark_before_fix")
FAILURES = os.path.join(ROOT, "logs", "failures")

SLOTS = ["dealer_1", "dealer_2", "flop_1", "flop_2", "flop_3",
         "turn", "river", "player_1", "player_2"]

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
STATE = re.compile(r"State (\w+) -> (\w+) \| (.*?)(?: \| player has|$)")
COMPLETED = re.compile(r"Completed hand ([\w-]+)")
STORED = re.compile(r"Hand #(\d+) stored: ([\w-]+)")
STATUS = re.compile(r"tracker\s+(.+?) (AMBIGUOUS|UNKNOWN): ")
# Result-panel lines, kept apart from the table's: "Result panel dealer card 3
# UNKNOWN:" would otherwise match STATUS and be counted as a table refusal.
PANEL_FAILURE = re.compile(r"Result panel (player|dealer) card (\d) (AMBIGUOUS|UNKNOWN):")
PANEL_UNCHECKED = re.compile(r"Result panels could not check this hand: (.*)")
DEALER_TIMING = re.compile(
    r"DEALER_TIMING capture=([\d.]+)ms roi=([\d.]+)ms crop=([\d.]+)ms recognition=([\d.]+)ms "
    r"\(dealer_1 ([\d.]+), dealer_2 ([\d.]+)\) memory=([\d.]+)ms gate=([\d.]+)ms "
    r"panel=([\d.]+)ms diagnostics=([\d.]+)ms total=([\d.]+)ms "
    r"\| dealer_1 (\w+) (.*?) \| dealer_2 (\w+) (.*)")
TIMING_STAGES = ("capture", "roi", "crop", "recognition", "dealer_1 recognition",
                 "dealer_2 recognition", "memory", "gate", "panel", "diagnostics", "total")
DEALER_LATENCY = re.compile(
    r"DEALER_LATENCY (dealer_\d)=(\w+) boxed->read=(\w+) read->memory=(\w+) "
    r"memory->shown=(\w+) boxed->shown=(\w+)")
DEALER_GUI = re.compile(r"DEALER_GUI (dealer_\d)=(\w+) displayed (-?[\d.]+)ms")
REJECTED = re.compile(r"(dealer_\d): rejected (\w+) confidence=([\d.]+)")


def board_of(text):
    """{slot: card} out of a state line's "dealer_1=8D dealer_2=--" part."""
    return dict(re.findall(r"(\w+)=([\w-]+)", text))


def log_files():
    """Every log file, oldest first."""
    return [path for path in (LOG + ".3", LOG + ".2", LOG + ".1", LOG)
            if os.path.exists(path)]


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.readlines()


def _since(lines, moment):
    return [line for line in lines if STAMP.match(line) and line[:19] >= moment]


def read_lines():
    """(lines to score, a description of where they start)."""
    every = []
    for path in log_files():
        every += _read(path)
    if "--all" in sys.argv:
        return every, "the start of every log file"
    if "--from" in sys.argv:
        value = sys.argv[sys.argv.index("--from") + 1].strip()
        if value.isdigit():
            return _read(LOG)[int(value):], "line %s of tracker.log" % value
        if "-" not in value:
            value = time.strftime("%Y-%m-%d ") + value
        return _since(every, value), value
    if os.path.exists(WATERMARK):
        with open(WATERMARK) as handle:
            text = handle.read().strip()
        if text.isdigit():
            current = _read(LOG)
            if int(text) <= len(current):
                return current[int(text):], "line %s of tracker.log (an old-style mark)" % text
            print("The mark is at line %s, but tracker.log has only %d lines: the log has\n"
                  "rotated since. Reading every log file instead - run --mark again, or\n"
                  "use --from with a time.\n" % (text, len(current)))
            return every, "the start of every log file"
        return _since(every, text), "the mark at %s" % text
    return every, "the start of every log file"


def mark():
    """Draw the line between the old process's output and the new run's."""
    moment = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(WATERMARK), exist_ok=True)
    with open(WATERMARK, "w") as handle:
        handle.write(moment)

    moved = 0
    if os.path.isdir(FAILURES):
        older = os.path.join(ROOT, "logs", "failures_before_fix")
        os.makedirs(older, exist_ok=True)
        for name in os.listdir(FAILURES):
            if name.endswith(".png"):
                try:
                    os.replace(os.path.join(FAILURES, name), os.path.join(older, name))
                    moved += 1
                except OSError:
                    pass

    print("Marked at %s." % moment)
    print("Moved %d leftover crop(s) into logs/failures_before_fix." % moved)
    print("\nStart the tracker now. When the rounds are done:")
    print("    python tools/verify_run.py")
    return 0


def spread(values):
    """'median X  p95 Y  worst Z' for a list of numbers."""
    values = sorted(values)
    if not values:
        return "no data"
    p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
    return "median %6.1f  p95 %6.1f  worst %6.1f  (n=%d)" % (
        values[len(values) // 2], p95, values[-1], len(values))


def main():
    if "--mark" in sys.argv:
        return mark()

    if not log_files():
        print("No log at %s" % LOG)
        return 1

    lines, where = read_lines()
    print("Reading %d lines of log from %s.\n" % (len(lines), where))
    if not lines:
        print("Nothing logged since then - has the tracker been restarted and run?")
        return 0

    completes, completed, stored = [], [], []
    refusals = {"AMBIGUOUS": {}, "UNKNOWN": {}}
    conflicts, panel_conflicts, panel_ok = [], [], 0
    # Each sealed round paired with the hand that was actually written for it:
    # the one that follows it in the log, before the next round is sealed.
    pairs, pending = [], None
    unchecked, source_conflicts, panel_failures = {}, [], {}
    stale_panels = retired_panels = 0
    stages = {stage: [] for stage in TIMING_STAGES}
    statuses = {"dealer_1": {}, "dealer_2": {}}
    latency = {"boxed->read": [], "read->memory": [], "memory->shown": [], "boxed->shown": []}
    gui_delays, rejections = [], []
    at_river = False
    river_rounds = dealer_shown = dealer_lost = 0

    for line in lines:
        match = STATE.search(line)
        if match:
            new = match.group(2)
            if new == "RIVER" and not at_river:
                at_river, river_rounds = True, river_rounds + 1
            elif new == "COMPLETE":
                if at_river:
                    dealer_shown += 1
                at_river = False
            elif new in ("WAITING", "PLAYER_CARDS", "FLOP", "TURN") and at_river:
                dealer_lost += 1
                at_river = False
        if match and match.group(2) == "COMPLETE":
            board = board_of(match.group(3))
            completes.append(board)
            pending = board
        match = COMPLETED.search(line)
        if match:
            completed.append(match.group(1))
            if pending is not None:
                pairs.append((pending, match.group(1)))
                pending = None
        match = STORED.search(line)
        if match:
            stored.append(match.group(2))
        match = DEALER_TIMING.search(line)
        if match:
            for stage, value in zip(TIMING_STAGES, match.groups()[:11]):
                stages[stage].append(float(value))
            for slot, status in (("dealer_1", match.group(12)), ("dealer_2", match.group(14))):
                statuses[slot][status] = statuses[slot].get(status, 0) + 1
            continue
        match = DEALER_LATENCY.search(line)
        if match:
            for key, value in zip(latency, match.groups()[2:]):
                if value.lstrip("-").isdigit():
                    latency[key].append(float(value))
            continue
        match = DEALER_GUI.search(line)
        if match:
            gui_delays.append(float(match.group(3)))
            continue
        match = REJECTED.search(line)
        if match:
            rejections.append(float(match.group(3)))
        match = PANEL_FAILURE.search(line)
        if match:
            key = "%s card %s %s" % match.groups()
            panel_failures[key] = panel_failures.get(key, 0) + 1
        else:
            match = STATUS.search(line)
            if match:
                kind = match.group(2)
                refusals[kind][match.group(1)] = refusals[kind].get(match.group(1), 0) + 1
        match = PANEL_UNCHECKED.search(line)
        if match:
            reason = match.group(1).strip()[:80]
            unchecked[reason] = unchecked.get(reason, 0) + 1
        if "SOURCE CONFLICT" in line:
            source_conflicts.append(line.strip()[-110:])
        if "(previous round, ignored)" in line:
            stale_panels += 1
        if "Result panel evidence retired" in line:
            retired_panels += 1
        if "Same card read in two places" in line:
            conflicts.append(line.strip()[-90:])
        if "ROUND CONFLICT" in line:
            panel_conflicts.append(line.strip()[-90:])
        if "ROUND VERIFIED" in line:
            panel_ok += 1

    print("=" * 66)
    print("ROUNDS")
    print("=" * 66)
    print("  reached COMPLETE       %d" % len(completes))
    print("  hands completed        %d" % len(completed))
    print("  hands stored           %d" % len(stored))
    lost = len(completes) - len(stored)
    if lost > 0:
        print("  -> %d round(s) reached COMPLETE but were never stored" % lost)

    print("\n" + "=" * 66)
    print("DEALER CARDS (read-only diagnosis of where the time goes)")
    print("=" * 66)
    print("  rounds that reached the river  %d" % river_rounds)
    print("     dealer shown                %d" % dealer_shown)
    print("     round ended, dealer never   %d" % dealer_lost)
    print("  gate refusals                  %d   best refused confidence: %s" % (
        len(rejections), spread(rejections).split("  (")[0] if rejections else "-"))
    if any(stages.values()):
        print("  per poll at the river, ms:")
        for stage in TIMING_STAGES:
            print("     %-22s %s" % (stage, spread(stages[stage])))
        for slot in ("dealer_1", "dealer_2"):
            total = sum(statuses[slot].values())
            shares = "  ".join("%s %d%%" % (status, round(100.0 * n / total))
                               for status, n in sorted(statuses[slot].items(), key=lambda kv: -kv[1]))
            print("     %-9s polls by status: %s" % (slot, shares))
    else:
        print("  no DEALER_TIMING lines - is dealer_debug on in this run?")
    print("  from first boxed to shown, ms:")
    for key, values in latency.items():
        print("     %-15s %s" % (key, spread(values)))
    print("  tracker sent -> window showed  %s" % spread(gui_delays))

    print("\n" + "=" * 66)
    print('DOES "--" STILL APPEAR (slots empty when a round was sealed)')
    print("=" * 66)
    gaps = {}
    for board in completes:
        for slot in SLOTS:
            if board.get(slot, "--") == "--":
                gaps[slot] = gaps.get(slot, 0) + 1
    if not completes:
        print("  no completed rounds yet")
    elif not gaps:
        print("  none - every sealed round had all nine cards")
    else:
        for slot, n in sorted(gaps.items(), key=lambda kv: -kv[1]):
            print("  %-10s %d of %d rounds (%.0f%%)"
                  % (slot, n, len(completes), 100.0 * n / len(completes)))

    print("\n" + "=" * 66)
    print("WAS ANYTHING READ AS THE WRONG CARD")
    print("=" * 66)
    print("  panel agreed with the round    %d" % panel_ok)
    print("  panel disagreed                %d" % len(panel_conflicts))
    for note in panel_conflicts[:5]:
        print("     %s" % note)
    print("  same card read in two slots    %d" % len(conflicts))
    for note in conflicts[:5]:
        print("     %s" % note)
    if not panel_conflicts and not conflicts:
        print("  -> nothing contradicted itself")

    print("\n" + "=" * 66)
    print("DID A ROUND GET MIXED UP (the board sealed vs the hand stored)")
    print("=" * 66)
    mixed = 0
    for board, fingerprint in pairs:
        # As sets: the stored fingerprint lists the same nine cards in its own
        # order, so comparing slot by slot would call every round a mismatch.
        sealed = set(c for c in (board.get(s) for s in SLOTS) if c and c != "--")
        written = set(fingerprint.split("-"))
        if sealed and sealed != written:
            mixed += 1
            print("  sealed  %s" % " ".join(sorted(sealed)))
            print("  stored  %s" % " ".join(sorted(written)))
            print("  cards that appeared from nowhere: %s"
                  % (" ".join(sorted(written - sealed)) or "none"))
    if not mixed:
        print("  none - every stored hand matches the board it was sealed from")
    else:
        print("\n  -> %d round(s) stored cards other than the ones sealed." % mixed)
        print("     This is the round-boundary fault, not a recognition one.")

    print("\n" + "=" * 66)
    print("RESULT PANEL (the casino's best-five panel against the table)")
    print("=" * 66)
    checked = panel_ok + len(panel_conflicts) + sum(unchecked.values())
    print("  stored hands checked           %d" % checked)
    print("     panel agreed                %d" % panel_ok)
    print("     panel disagreed             %d" % len(panel_conflicts))
    print("     could not check             %d" % sum(unchecked.values()))
    for reason, n in sorted(unchecked.items(), key=lambda kv: -kv[1])[:4]:
        print("        %3d  %s" % (n, reason))
    print("  source conflicts during rounds %d" % len(source_conflicts))
    for note in source_conflicts[:5]:
        print("     %s" % note)
    print("  old panel shown and ignored    %d" % stale_panels)
    print("  panel evidence retired         %d   (round change or new deal)" % retired_panels)
    total = sum(panel_failures.values())
    print("  thumbnails seen but not read   %d" % total)
    for key, n in sorted(panel_failures.items(), key=lambda kv: -kv[1])[:6]:
        print("       %-24s %d" % (key, n))

    print("\n" + "=" * 66)
    print("REFUSALS (a table card seen but not accepted)")
    print("=" * 66)
    for kind, label in (("AMBIGUOUS", "read well, suit undecided"),
                        ("UNKNOWN", "nothing matched")):
        total = sum(refusals[kind].values())
        print("  %-10s %4d   (%s)" % (kind, total, label))
        for slot, n in sorted(refusals[kind].items(), key=lambda kv: -kv[1])[:4]:
            print("       %-16s %d" % (slot, n))
    crops = len([f for f in os.listdir(FAILURES)
                 if f.endswith(".png")]) if os.path.isdir(FAILURES) else 0
    print("  crops kept this run    %d" % crops)

    print("\n" + "=" * 66)
    print("VERDICT")
    print("=" * 66)
    bad = bool(panel_conflicts or source_conflicts or conflicts or mixed or lost > 0
               or dealer_lost)
    if not completes and not river_rounds:
        print("  Inconclusive - no rounds reached the river yet.")
    elif bad:
        print("  Problems remain. The sections above say which; a mixed-up")
        print("  round is a different fault from a card read wrongly.")
    else:
        print("  Clean: %d rounds, all stored, none contradicted, no gaps."
              % len(completes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
