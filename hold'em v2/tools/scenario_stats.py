"""How each poker situation has actually turned out in your recorded hands.

Not "does this scenario win often" but "does it win more often than the hands
without it". A situation winning 62% of the time is worth nothing if the hands
where it does not apply win 63%.

Run:  python tools/scenario_stats.py
      python tools/scenario_stats.py --min 30    (stricter sample-size warning)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db  # noqa: E402
from poker import analysis  # noqa: E402


def main():
    smallest = 20
    if "--min" in sys.argv:
        smallest = int(sys.argv[sys.argv.index("--min") + 1])

    try:
        rows = db.fetch_all_hands()
    except db.DatabaseError as exc:
        print("Could not read the recorded hands: %s" % exc)
        return 1

    report = analysis.statistics(rows, small_sample=smallest)
    completed = [row["winner"] for row in rows
                 if row.get("winner") in ("Player", "Dealer", "Tie")]
    baseline = analysis._tally(completed)

    print("Recorded hands with a readable flop: %d of %d"
          % (report["hands"], len(rows)))
    print("Baseline: the player won %.1f%% of %d completed rounds\n"
          % (baseline["player_percent"], baseline["rounds"]))

    print("%-26s %8s %9s %9s %11s  %s"
          % ("situation", "hands", "player %", "without", "advantage", ""))
    print("-" * 78)
    for entry in report["scenarios"]:
        triggered, without = entry["triggered"], entry["not_triggered"]
        if not triggered["rounds"]:
            continue
        print("%-26s %8d %9.1f %9.1f %+10.1f  %s"
              % (entry["label"], triggered["rounds"], triggered["player_percent"],
                 without["player_percent"], entry["advantage"],
                 "small sample" if entry["small_sample"] else ""))

    print("\nAdvantage is the player's win rate with the situation minus the rate")
    print("without it. These are historical percentages from rounds already")
    print("recorded - not the chance of winning the next hand, and not the value")
    print("of playing rather than folding, which the table does not record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
