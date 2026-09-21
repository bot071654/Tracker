"""Scenarios: your own rules about when to ante and when to fold.

Rules are ordered and the first one that matches wins; if none match, the
default applies. They live in config/scenarios.json so they survive restarts
and can be edited by hand.

Nothing here presses a button. A scenario produces a recommendation, which the
app shows you and which tools/backtest.py scores against the hands you have
already recorded - so a rule can be judged on your own data before you decide
whether to follow it.
"""

import json
import logging
import os

from poker.board_features import (
    BOARD_CONDITIONS, DRAWS, FLOP_PAIRED, FLOP_SLOTS, HIGH_AKQ, HIGH_AKQ_HOLE,
    HIGH_CARDS, HOLE_SLOTS, OVERCARDS, PAIR_SOURCES, describe_flop_from_slots,
)
from poker.hand_evaluator import HAND_NAMES
from poker.scenario_engine import ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_PATH = os.path.join(ROOT, "config", "scenarios.json")

ANY = "Any"
HAND_CHOICES = [ANY] + [HAND_NAMES[key] for key in sorted(HAND_NAMES)]

# The five cards a flop rule is about: the player's two and the flop's three.
DECISION_SLOTS = HOLE_SLOTS + FLOP_SLOTS

# The evaluator's own name for a hand with no pair or better. Taken from it
# rather than spelled out again, so the two can never drift apart.
HIGH_CARD = HAND_NAMES[1]

# What a flop rule can ask about beyond the made hand.
CONDITION_CHOICES = [ANY] + DRAWS + PAIR_SOURCES + [
    OVERCARDS[count] for count in sorted(OVERCARDS)
] + BOARD_CONDITIONS

# Who won the round that just finished, as compare_hands reports it.
PLAYER, DEALER, TIE = "Player", "Dealer", "Tie"
WINNER_CHOICES = [ANY, PLAYER, DEALER, TIE]

# How many rounds in a row the player must have won for a rule to fire, as the
# chooser offers them. ANY means the rule does not ask about a streak at all.
STREAK_CHOICES = [ANY, "2", "3", "4"]


def streak_from_choice(choice):
    """The streak a rule requires, from what the chooser shows. ANY means none."""
    if choice == ANY:
        return 0
    try:
        return int(choice)
    except (TypeError, ValueError):
        return 0


def streak_choice(streak):
    """What the chooser should show for a rule's stored streak."""
    return ANY if not streak else str(streak)

ANTE, SKIP = "ante", "skip"
PLAY, FOLD = "play", "fold"

PREROUND_ACTIONS = [ANTE, SKIP]
FLOP_ACTIONS = [PLAY, FOLD]

ACTION_LABELS = {
    ANTE: "Ante", SKIP: "Skip round", PLAY: "Play on", FOLD: "Fold",
}

DEFAULTS = {
    "preround": {"default": ANTE, "rules": []},
    "flop": {"default": PLAY, "rules": []},
}


def load(path=SCENARIOS_PATH):
    """Read scenarios.json, falling back to empty rule sets."""
    scenarios = {section: dict(body) for section, body in DEFAULTS.items()}
    if not os.path.exists(path):
        return scenarios
    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.error("Could not read %s: %s", path, exc)
        return scenarios

    for section in scenarios:
        body = stored.get(section) or {}
        scenarios[section] = {
            "default": body.get("default", DEFAULTS[section]["default"]),
            "rules": list(body.get("rules") or []),
        }
    return scenarios


def save(scenarios, path=SCENARIOS_PATH):
    """Write scenarios.json."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(scenarios, handle, indent=4)
    logger.info("Scenarios saved to %s", path)


# -- building rules -----------------------------------------------------------

def preround_rule(previous_dealer_hand, previous_player_hand, action, name=None,
                  previous_winner=ANY, player_win_streak=0):
    """A rule about the round that just finished.

    A rule can ask about either seat's hand, about who won, and about how many
    rounds in a row the player has just won. Every part left at ANY (or a
    streak of 0) is simply not asked about.
    """
    if action not in PREROUND_ACTIONS:
        raise ValueError("Unknown action %r" % (action,))
    if previous_winner not in WINNER_CHOICES:
        raise ValueError("Unknown winner %r" % (previous_winner,))
    rule = {
        "previous_dealer_hand": previous_dealer_hand or ANY,
        "previous_player_hand": previous_player_hand or ANY,
        "previous_winner": previous_winner or ANY,
        "player_win_streak": int(player_win_streak or 0),
        "action": action,
    }
    rule["name"] = name or describe_preround(rule)
    return rule


def flop_rule(hand, condition, action, name=None):
    """A rule about the situation on the flop."""
    if action not in FLOP_ACTIONS:
        raise ValueError("Unknown action %r" % (action,))
    rule = {"hand": hand or ANY, "condition": condition or ANY, "action": action}
    rule["name"] = name or describe_flop_rule(rule)
    return rule


def describe_preround(rule):
    parts = []
    streak = int(rule.get("player_win_streak", 0) or 0)
    if streak:
        parts.append("the player won the last %d rounds" % streak)
    elif rule.get("previous_winner", ANY) != ANY:
        parts.append("%s won" % rule["previous_winner"].lower())
    if rule.get("previous_dealer_hand", ANY) != ANY:
        parts.append("dealer had %s" % rule["previous_dealer_hand"])
    if rule.get("previous_player_hand", ANY) != ANY:
        parts.append("player had %s" % rule["previous_player_hand"])
    condition = " and ".join(parts) if parts else "every round"
    return "If %s, %s" % (condition, ACTION_LABELS[rule["action"]].lower())


def describe_flop_rule(rule):
    parts = []
    if rule.get("hand", ANY) != ANY:
        parts.append(rule["hand"])
    if rule.get("condition", ANY) != ANY:
        parts.append(rule["condition"])
    condition = " with ".join(parts) if parts else "any flop"
    return "If %s, %s" % (condition, ACTION_LABELS[rule["action"]].lower())


# -- the standard set ---------------------------------------------------------

def standard_rules():
    """The rules the app ships with, ready to be added to a user's own set.

    Order matters, because the first rule that matches is the one used. The
    streak rule has to come before the plain "player won" rule: two wins in a
    row is also a player win, and listed the other way round the streak rule
    could never fire.
    """
    return {
        "preround": [
            # The app has no betting subsystem and no new action was added for
            # this, so "increase the bet" is carried in the rule's name - which
            # is the line already shown under "Your scenarios say:".
            preround_rule(ANY, ANY, ANTE, player_win_streak=2,
                          name="If the player won the last 2 rounds, "
                               "increase bet and play"),
            preround_rule(ANY, ANY, SKIP, previous_winner=DEALER,
                          name="If the dealer won the last round, skip this one"),
            preround_rule(ANY, ANY, ANTE, previous_winner=PLAYER,
                          name="If the player won the last round, play"),
        ],
        "flop": [
            flop_rule(ANY, FLOP_PAIRED, PLAY,
                      name="If the flop is paired, play on"),
            # "High Card" here is the hand evaluator's own name for a hand with
            # no pair or better, so the rule's existing `hand` field carries
            # that half and the condition only has to ask about the big card.
            #
            # It asks about the player's own two, not the highest card showing.
            # An ace on the flop is the dealer's ace as well and plays the same
            # for both seats, so a hand can be "A high" with nothing in it. Over
            # 197 recorded hands, High Card hands with an A/K/Q in the hole won
            # 39.1% (18 of 46); those where the big card was only on the board
            # won 19.2% (10 of 52). This is also the question the Scenario
            # Engine asks (config/scenario_engine.json, high_card_source), so
            # the two halves of the window now agree.
            flop_rule(HIGH_CARD, HIGH_AKQ_HOLE, PLAY,
                      name="If the hand is High Card and one of my two cards "
                           "is A, K or Q, play on"),
        ],
    }


def add_standard_rules(scenarios, section=None):
    """Add any standard rule the user does not already have. Returns how many.

    Pass `section` to add only "preround" or only "flop"; the default adds
    both. Existing rules are never touched, reordered or replaced - a rule
    asking the same question is left exactly as the user set it, because their
    action for that situation is the one they meant.
    """
    added = 0
    wanted = standard_rules()
    if section is not None:
        wanted = {section: wanted.get(section, [])}
    for section, rules in wanted.items():
        existing = scenarios.setdefault(
            section, dict(DEFAULTS[section])).setdefault("rules", [])
        for rule in rules:
            if any(_same_question(rule, other) for other in existing):
                continue
            existing.append(rule)
            added += 1
    return added


def _same_question(one, other):
    """True when two rules ask about the same situation, whatever they do."""
    keys = (set(one) | set(other)) - {"action", "name"}
    return all(one.get(key) == other.get(key) for key in keys)


# -- applying rules -----------------------------------------------------------

def _matches_condition(condition, features):
    """Does one flop condition hold for this situation?"""
    if condition == ANY:
        return True
    if condition in DRAWS:
        return features["draw"] == condition
    if condition in PAIR_SOURCES:
        return features["pair"] == condition
    if condition in OVERCARDS.values():
        return OVERCARDS[features["overcards"]] == condition
    if condition == FLOP_PAIRED:
        return bool(features.get("flop_paired"))
    if condition == HIGH_AKQ:
        return features.get("highest") in HIGH_CARDS
    if condition == HIGH_AKQ_HOLE:
        return features.get("highest_hole") in HIGH_CARDS
    logger.warning("Unknown scenario condition: %r", condition)
    return False


def player_win_streak(history):
    """How many rounds in a row the player has just won.

    `history` is the recorded rounds, most recent first. Counting stops at the
    first round the player did not win, so this is the run ending now rather
    than the longest run ever.
    """
    streak = 0
    for record in history or []:
        if (record or {}).get("winner") != PLAYER:
            break
        streak += 1
    return streak


def decide_preround(scenarios, previous, history=None):
    """(action, rule) for the round about to start.

    `previous` is the last stored hand record, or None for the first round.
    `history` is the recorded rounds most recent first, which is what a rule
    about consecutive wins needs; without it only `previous` is known, and a
    streak longer than one round can never be claimed.
    """
    section = scenarios.get("preround") or DEFAULTS["preround"]
    if not previous:
        return section.get("default", ANTE), None

    if history is None:
        history = [previous]
    streak = player_win_streak(history)

    for rule in section.get("rules") or []:
        dealer = rule.get("previous_dealer_hand", ANY)
        player = rule.get("previous_player_hand", ANY)
        winner = rule.get("previous_winner", ANY)
        wanted_streak = int(rule.get("player_win_streak", 0) or 0)
        if dealer != ANY and previous.get("dealer_hand") != dealer:
            continue
        if player != ANY and previous.get("player_hand") != player:
            continue
        if winner != ANY and previous.get("winner") != winner:
            continue
        if wanted_streak and streak < wanted_streak:
            continue
        return rule["action"], rule
    return section.get("default", ANTE), None


def decide_flop(scenarios, features):
    """(action, rule) for the flop decision, or (None, None) before the flop."""
    if not features:
        return None, None
    section = scenarios.get("flop") or DEFAULTS["flop"]
    for rule in section.get("rules") or []:
        if rule.get("hand", ANY) != ANY and features["hand"] != rule["hand"]:
            continue
        if not _matches_condition(rule.get("condition", ANY), features):
            continue
        return rule["action"], rule
    return section.get("default", PLAY), None


def unsettled_slots(statuses):
    """Which of the five decision cards the tracker has not settled yet.

    `statuses` is slot -> the word tracker.card_status reports (CONFIRMED,
    HELD, CONFIRMING, AMBIGUOUS, UNKNOWN, EMPTY). Returns the slots that are
    not CONFIRMED or HELD, in dealing order; empty means all five are safe to
    decide on.

    This is the same gate the Scenario Engine applies in
    scenario_engine.gate_slots, and it reads the same two words, so the two
    cannot start answering on different cards.
    """
    return [slot for slot in DECISION_SLOTS
            if (statuses or {}).get(slot) not in ACCEPTED_STATUSES]


def decide_from_cards(scenarios, cards, previous=None, history=None,
                      statuses=None):
    """What the rules say about the table as the tracker currently reads it.

    Returns a dict with the flop recommendation and how the situation reads,
    plus the pre-round recommendation for the round that is starting, and
    `waiting_for`: the decision cards that are not settled yet.

    `statuses` is slot -> tracker.card_status's word. Pass it and no flop
    recommendation is made until all five decision cards are CONFIRMED or
    HELD. This matters: CardMemory publishes whichever card has the most
    support so far, which after one weak reading is that reading - and a card
    still arriving on the flop reads as something else entirely for a poll or
    two. Replaying session 20260917_120408, three of the fourteen rounds fired
    a rule on a card that was still CONFIRMING and was revised a moment later,
    and in each one the rule that fired was the wrong rule: "the flop is
    paired" on a flop that was not paired.

    Left at None, every card is taken at face value, which is what the
    backtest and the rule builder want - a stored hand's cards are settled by
    definition.
    """
    waiting_for = unsettled_slots(statuses) if statuses is not None else []
    features = None if waiting_for else describe_flop_from_slots(cards or {})
    flop_action, flop_rule_used = decide_flop(scenarios, features)
    preround_action, preround_rule_used = decide_preround(
        scenarios, previous, history)
    return {
        "features": features,
        "waiting_for": waiting_for,
        "flop_action": flop_action,
        "flop_rule": flop_rule_used,
        "preround_action": preround_action,
        "preround_rule": preround_rule_used,
    }
