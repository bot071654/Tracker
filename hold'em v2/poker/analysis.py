"""What situation the table is in, and how those situations have turned out.

This is the analysis half of the scenario work, and it is deliberately separate
from poker/scenarios.py. That module answers "what should I do?" from rules the
user wrote. This one answers two different questions:

    which of the known situations is this hand in?
    how have hands in that situation actually gone?

Nothing here decides anything. It names what is on the table and looks up what
happened the last time the table looked like that - which is a statement about
recorded history, not a prediction about the next card.

The situations are detected from the same five cards the flop decision is made
on: the player's two and the three community cards. Everything is worked out by
board_features and hand_evaluator, which already do this; the catalogue below
only gives the situations names, groups and an order of precedence.

The priorities are engineering precedence, not probabilities. Priority 85 means
"mention this before the overcards"; it does not mean 85% of anything.
"""

import logging

from poker.board_features import (
    FLUSH_DRAW, GUTSHOT, HIGH_CARDS, OPEN_STRAIGHT, PAIR_FROM_HOLE,
    PAIR_ON_BOARD, POCKET_PAIR, describe_flop, describe_flop_from_slots,
)

logger = logging.getLogger(__name__)

MADE_HAND, DRAW, HIGH_CARD, BOARD = "made hand", "draw", "high card", "board"

# Below this many recorded hands a percentage is not worth reading. Five hands
# at 100% is five hands, not a hundred per cent.
SMALL_SAMPLE = 20


def _hand_is(features, *names):
    return features["hand"] in names


# key, label, group, priority, test. Ordered by priority so the strongest
# description of a hand comes first; several can be true at once, and all of
# them are reported.
CATALOGUE = [
    ("straight_flush", "Straight Flush", MADE_HAND, 120,
     lambda f: _hand_is(f, "Straight Flush", "Royal Flush")),
    ("four_of_a_kind", "Four of a Kind", MADE_HAND, 115,
     lambda f: _hand_is(f, "Four of a Kind")),
    ("full_house", "Full House", MADE_HAND, 110,
     lambda f: _hand_is(f, "Full House")),
    ("flush", "Flush", MADE_HAND, 100, lambda f: _hand_is(f, "Flush")),
    ("straight", "Straight", MADE_HAND, 100, lambda f: _hand_is(f, "Straight")),
    ("three_of_a_kind", "Three of a Kind", MADE_HAND, 100,
     lambda f: _hand_is(f, "Three of a Kind")),
    ("two_pair", "Two Pair", MADE_HAND, 90, lambda f: _hand_is(f, "Two Pair")),
    ("pocket_pair", "Pocket Pair", MADE_HAND, 85,
     lambda f: f["pair"] == POCKET_PAIR),
    ("pair_from_hole", "Pair using a hole card", MADE_HAND, 80,
     lambda f: f["pair"] == PAIR_FROM_HOLE),
    ("four_to_flush", "Four to a flush", DRAW, 70,
     lambda f: f["draw"] == FLUSH_DRAW),
    ("open_straight", "Open-ended straight draw", DRAW, 65,
     lambda f: f["draw"] == OPEN_STRAIGHT),
    ("gutshot", "Gutshot straight draw", DRAW, 60,
     lambda f: f["draw"] == GUTSHOT),
    ("two_overcards", "Two overcards", HIGH_CARD, 55,
     lambda f: f["overcards"] == 2),
    ("one_overcard", "One overcard", HIGH_CARD, 45,
     lambda f: f["overcards"] == 1),
    # The same hand asked twice: about the player's own two cards, and about
    # everything showing. Both are listed because they are different
    # situations - an ace on the flop is the dealer's ace too - and which of
    # them is worth playing is exactly the sort of question the recorded hands
    # can answer and guesswork cannot.
    ("high_card_akq_hole", "A/K/Q in hand", HIGH_CARD, 41,
     lambda f: f["hand"] == "High Card" and f["highest_hole"] in HIGH_CARDS),
    ("high_card_akq", "A/K/Q high", HIGH_CARD, 40,
     lambda f: f["hand"] == "High Card" and f["highest"] in HIGH_CARDS),
    ("paired_board", "Paired flop", BOARD, 30,
     lambda f: bool(f["flop_paired"])),
    ("pair_on_board", "Pair only on the board", BOARD, 25,
     lambda f: f["pair"] == PAIR_ON_BOARD),
]

BY_KEY = {key: (label, group, priority)
          for key, label, group, priority, _ in CATALOGUE}


def detect(features):
    """Which situations this flop is in, strongest first.

    `features` is what board_features.describe_flop returns. None - the flop is
    not fully readable yet - gives an empty list rather than a guess.
    """
    if not features:
        return []
    found = []
    for key, label, group, priority, applies in CATALOGUE:
        try:
            if applies(features):
                found.append({"key": key, "label": label,
                              "group": group, "priority": priority})
        except (KeyError, TypeError) as exc:  # noqa: PERF203 - a bad frame
            logger.debug("Could not test %s: %s", key, exc)
    return found


def detect_from_slots(cards):
    """detect() for the tracker's slot dictionary."""
    return detect(describe_flop_from_slots(cards or {}))


def describe(found):
    """A short line naming the situations, strongest first."""
    return ", ".join(entry["label"] for entry in found) if found else "--"


# -- how these situations have actually gone ---------------------------------

def _row_features(row):
    """The flop situation of a recorded hand, from the decision point only.

    The player's two cards and the three flop cards - not the turn, the river
    or the dealer's, none of which were known when the decision was made.
    """
    try:
        return describe_flop(
            [row.get("player_card_1"), row.get("player_card_2")],
            [row.get("flop_card_1"), row.get("flop_card_2"), row.get("flop_card_3")],
        )
    except Exception as exc:  # noqa: BLE001 - one bad row must not stop the count
        logger.debug("Could not read a recorded hand: %s", exc)
        return None


def _tally(outcomes):
    player = outcomes.count("Player")
    dealer = outcomes.count("Dealer")
    tie = outcomes.count("Tie")
    rounds = player + dealer + tie
    percent = (100.0 * player / rounds) if rounds else 0.0
    return {"rounds": rounds, "player": player, "dealer": dealer, "tie": tie,
            "player_percent": round(percent, 1)}


def statistics(rows, small_sample=SMALL_SAMPLE):
    """How each situation has turned out, against how the rest have.

    The figure that matters is not the win rate when a situation applies but
    the difference between that and the win rate when it does not. A situation
    winning 62% is worth nothing if the hands without it win 63%.

    Returns a list, strongest situation first:

        {"key", "label", "group", "priority",
         "triggered": {...}, "not_triggered": {...},
         "advantage": +3.4, "small_sample": False}
    """
    usable = []
    for row in rows:
        winner = row.get("winner")
        if winner not in ("Player", "Dealer", "Tie"):
            continue                      # never completed; not a round that counts
        features = _row_features(row)
        if features is None:
            continue                      # the flop could not be read back
        usable.append((set(entry["key"] for entry in detect(features)), winner))

    results = []
    for key, label, group, priority, _ in CATALOGUE:
        hit = [winner for keys, winner in usable if key in keys]
        miss = [winner for keys, winner in usable if key not in keys]
        triggered, not_triggered = _tally(hit), _tally(miss)
        results.append({
            "key": key, "label": label, "group": group, "priority": priority,
            "triggered": triggered,
            "not_triggered": not_triggered,
            "advantage": round(
                triggered["player_percent"] - not_triggered["player_percent"], 1),
            "small_sample": triggered["rounds"] < small_sample,
        })
    results.sort(key=lambda entry: -entry["priority"])
    return {"hands": len(usable), "scenarios": results}
