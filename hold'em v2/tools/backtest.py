"""Score your scenarios against the hands you have already recorded.

Every stored hand has the cards, so what a rule *would* have done can be
replayed exactly. That is the point of the scenarios: to be judged on your own
data before you decide whether to follow them.

Run:  python tools/backtest.py
      python tools/backtest.py --scenarios other.json
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db  # noqa: E402
from poker import scenarios as rules  # noqa: E402
from poker.board_features import describe_flop  # noqa: E402


def as_slots(row):
    """A stored row in the slot form the rules expect."""
    return {
        "player_1": row["player_card_1"], "player_2": row["player_card_2"],
        "flop_1": row["flop_card_1"], "flop_2": row["flop_card_2"],
        "flop_3": row["flop_card_3"],
        "turn": row["turn_card"], "river": row["river_card"],
        "dealer_1": row["dealer_card_1"], "dealer_2": row["dealer_card_2"],
    }


def replay(scenarios, rows):
    """Walk the recorded hands in order, applying the rules to each."""
    tally = {
        "hands": len(rows),
        "skipped": 0, "played": 0, "folded": 0, "went_to_showdown": 0,
        "showdown": Counter(),          # results of hands played to the end
        "folded_results": Counter(),    # what the folded hands turned out to be
        "skipped_results": Counter(),
        "no_features": 0,
        "rules_used": Counter(),
    }

    previous = None
    # The rounds already walked, most recent first, so that a rule about
    # consecutive wins is scored here the same way the tracker applies it.
    history = []
    for row in rows:
        slots = as_slots(row)
        result = row.get("winner") or "unknown"

        action, rule = rules.decide_preround(scenarios, previous, history)
        previous = row
        history.insert(0, row)
        if action == rules.SKIP:
            tally["skipped"] += 1
            tally["skipped_results"][result] += 1
            if rule:
                tally["rules_used"][rule["name"]] += 1
            continue

        tally["played"] += 1
        features = describe_flop(
            [slots["player_1"], slots["player_2"]],
            [slots["flop_1"], slots["flop_2"], slots["flop_3"]],
        )
        if features is None:
            tally["no_features"] += 1
            continue

        action, rule = rules.decide_flop(scenarios, features)
        if rule:
            tally["rules_used"][rule["name"]] += 1
        if action == rules.FOLD:
            tally["folded"] += 1
            tally["folded_results"][result] += 1
        else:
            tally["went_to_showdown"] += 1
            tally["showdown"][result] += 1
    return tally


def report(tally):
    lines = []
    lines.append("Hands on record: %d" % tally["hands"])
    lines.append("")
    lines.append("  anted            %d" % tally["played"])
    lines.append("  skipped          %d" % tally["skipped"])
    lines.append("  folded on flop   %d" % tally["folded"])
    lines.append("  played to the end %d" % tally["went_to_showdown"])
    if tally["no_features"]:
        lines.append("  unreadable        %d" % tally["no_features"])

    lines.append("")
    lines.append("Of the hands played to the end: %s"
                 % (dict(tally["showdown"]) or "none"))
    if tally["folded"]:
        lines.append("Of the hands folded, the eventual result was: %s"
                     % dict(tally["folded_results"]))
        won = tally["folded_results"].get("Player", 0)
        lines.append("  -> %d of those %d were hands the player went on to win."
                     % (won, tally["folded"]))
    if tally["skipped"]:
        lines.append("Of the hands skipped, the eventual result was: %s"
                     % dict(tally["skipped_results"]))
        won = tally["skipped_results"].get("Player", 0)
        lines.append("  -> %d of those %d were hands the player went on to win."
                     % (won, tally["skipped"]))

    if tally["rules_used"]:
        lines.append("")
        lines.append("Rules that fired:")
        for name, count in tally["rules_used"].most_common():
            lines.append("  %-52s %d" % (name[:52], count))
    return "\n".join(lines)


def compare_with_playing_everything(rows):
    """The baseline: ante every round and never fold."""
    counts = Counter(row.get("winner") or "unknown" for row in rows)
    return "Playing every hand to the end: %s" % dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", help="a scenarios file to test instead of the saved one")
    args = parser.parse_args()

    scenarios = rules.load(args.scenarios) if args.scenarios else rules.load()
    if not scenarios["preround"]["rules"] and not scenarios["flop"]["rules"]:
        print("No rules defined yet. Add some with the Scenarios buttons in the app.")
        print("The defaults are: %s before the round, %s on the flop."
              % (scenarios["preround"]["default"], scenarios["flop"]["default"]))

    try:
        rows = db.fetch_all_hands()
    except db.DatabaseError as exc:
        print("Could not read the recorded hands: %s" % exc)
        return 1
    if not rows:
        print("No hands recorded yet - nothing to test against.")
        return 0

    print(report(replay(scenarios, rows)))
    print("")
    print(compare_with_playing_everything(rows))
    print("")
    print("A folded or skipped hand the player would have won is the cost of "
          "the rule;\nthe hands it avoided losing are the benefit. Judge it on "
          "both.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
