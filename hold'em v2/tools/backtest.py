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


def walk(scenarios, rows):
    """Each recorded hand with the decision these rules would have given it.

    The walk `replay` has always done, pulled out so that it can be run twice
    - once for the saved rules and once for a proposed set - and the two lined
    up hand by hand. Nothing about the order or the decisions changed in the
    extraction: `replay` below is the same tally built from these items.

    Each hand is judged on the rounds *recorded* before it, never on what the
    rules decided about them, so the two walks stay comparable row by row: a
    different rule set cannot shift the history the next hand sees.
    """
    previous = None
    # The rounds already walked, most recent first, so that a rule about
    # consecutive wins is scored here the same way the tracker applies it.
    history = []
    for row in rows:
        slots = as_slots(row)
        action, rule = rules.decide_preround(scenarios, previous, history)
        item = {
            "row": row,
            "result": row.get("winner") or "unknown",
            "preround_action": action, "preround_rule": rule,
            "features": None, "flop_action": None, "flop_rule": None,
        }
        previous = row
        history.insert(0, row)

        if action != rules.SKIP:
            features = describe_flop(
                [slots["player_1"], slots["player_2"]],
                [slots["flop_1"], slots["flop_2"], slots["flop_3"]],
            )
            item["features"] = features
            if features is not None:
                item["flop_action"], item["flop_rule"] = rules.decide_flop(
                    scenarios, features)
        yield item


def outcome(item):
    """The one thing that happened to this hand: skip, fold, or play to the end.

    What a comparison is actually about - two rule sets differ on a hand when
    this differs, whichever rule got them there.
    """
    if item["preround_action"] == rules.SKIP:
        return rules.SKIP
    if item["features"] is None:
        return "unreadable"
    return item["flop_action"]


def compare(current, proposed, rows):
    """What changes if `proposed` is used instead of `current`. Saves nothing.

    Both rule sets are walked over the same recorded hands and lined up. The
    caller gets the hands whose outcome differs, with the rule that produced
    each side, so "hands affected" is a list it can show rather than a number
    it has to trust.
    """
    before = list(walk(current, rows))
    after = list(walk(proposed, rows))
    changed = []
    for old, new in zip(before, after):
        if outcome(old) == outcome(new):
            continue
        changed.append({
            "row": old["row"],
            "from": outcome(old), "to": outcome(new),
            "from_rule": (old["flop_rule"] or old["preround_rule"] or {}).get("name"),
            "to_rule": (new["flop_rule"] or new["preround_rule"] or {}).get("name"),
            "result": old["result"],
        })
    return {
        "hands": len(rows),
        "changed": changed,
        "before": Counter(outcome(item) for item in before),
        "after": Counter(outcome(item) for item in after),
        "before_results": {name: Counter(
            item["result"] for item in before if outcome(item) == name)
            for name in {outcome(item) for item in before}},
        "after_results": {name: Counter(
            item["result"] for item in after if outcome(item) == name)
            for name in {outcome(item) for item in after}},
    }


def matches(rule, section, rows):
    """How many recorded hands the rule matches, on its own, at any priority.

    Deliberately not "how often would it fire": a rule sitting below something
    broader never fires and still describes a situation that happens. Asking
    the question this way tells "no recorded hand ever looks like this" apart
    from "something above it always gets there first", which are different
    problems with different fixes.

    Answered by handing decide_* a rule set containing only this rule, so the
    match is the engine's own and not a second reading of the condition.
    """
    probe = {section: {"default": "__no_match__", "rules": [rule]}}
    count = 0
    previous, history = None, []
    for row in rows:
        if section == "preround":
            if rules.decide_preround(probe, previous, history)[1] is not None:
                count += 1
            previous = row
            history.insert(0, row)
            continue
        slots = as_slots(row)
        features = describe_flop(
            [slots["player_1"], slots["player_2"]],
            [slots["flop_1"], slots["flop_2"], slots["flop_3"]],
        )
        if features is not None and rules.decide_flop(probe, features)[1] is not None:
            count += 1
    return count


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

    for item in walk(scenarios, rows):
        result = item["result"]
        rule = item["preround_rule"]

        if item["preround_action"] == rules.SKIP:
            tally["skipped"] += 1
            tally["skipped_results"][result] += 1
            if rule:
                tally["rules_used"][rule["name"]] += 1
            continue

        tally["played"] += 1
        if item["features"] is None:
            tally["no_features"] += 1
            continue

        if item["flop_rule"]:
            tally["rules_used"][item["flop_rule"]["name"]] += 1
        if item["flop_action"] == rules.FOLD:
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
