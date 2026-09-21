"""Scenario Engine: PLAY / DON'T_PLAY / WAIT from the player's cards and the flop.

DRY-RUN ONLY. Nothing in this module presses, clicks or sends anything, and
nothing imports it that does. It works out a decision, says exactly why, and
that is all. There is no switch to make it act: DRY_RUN is a constant.

This sits beside poker/scenarios.py rather than inside it. That module runs the
user's own first-match rules on the evaluator's hand *name*; this one checks
where each pair came from - the player's hole cards, the flop, or one of each -
because "Two Pair" alone cannot say whether the player holds any of it.

Inputs are positional and never inferred:

    player_cards = [P1, P2]         the player_1 / player_2 slots
    flop_cards   = [F1, F2, F3]     the flop_1 / flop_2 / flop_3 slots

The turn and river are not used for the decision. The result panel is not used
at all: it shows a best five, which cannot say which two cards were the
player's.

Interpretations the brief left open, stated here rather than assumed silently
(every one of them is in EngineConfig and can be changed):

HIGH_CARD_AKQ
    Matches when at least `high_card_min_count` of the cards in
    `high_card_source` have a rank in `high_card_ranks`. Default: at least ONE
    of the player's TWO HOLE CARDS is A, K or Q. The flop is not counted by
    default, because an ace on the flop belongs to the dealer too. Set
    high_card_source="player_and_flop" to count the flop, or
    high_card_min_count=2 to require two of A/K/Q in the hole.

COMBINED_PAIR
    A hole card pairs a flop card (the hole cards differ). A pair lying only on
    the flop is FLOP_PAIR, not COMBINED_PAIR; otherwise every flop pair would
    also be a combined pair and the two could never be told apart.

FLOP_PAIR
    Two flop cards share a rank and the hole cards are not a pair (as in the
    brief). With a pocket pair the same flop is PLAYER_PAIR_PLUS_FLOP_PAIR.

COMBINED_TWO_PAIR
    The hole cards differ and EACH pairs a flop card (A7 on A-7-3).

PLAYER_PAIR_CARD_PLUS_FLOP_PAIR
    The hole cards differ, exactly one hole rank is on the flop, and the flop
    holds a pair of a different rank (A7 on A-9-9).

PLAYER_PAIR_PLUS_FLOP_PAIR
    Pocket pair, and the flop holds a pair of a *different* rank. The same rank
    would be four of a kind, not another pair.

PLAYER_CARD_PLUS_FLOP_PAIR
    Some hole card's rank appears on at least two flop cards (AK on A-A-3).

There is NO flush rule. A flush is reported as the actual hand (detected_hand)
for information, but it never causes PLAY on its own: a flush with no A/K/Q in
the hole and no pair is DON'T_PLAY. No rule beyond the ones above exists.

The rules are structural checks and are all evaluated independently, so one
hand can match several, and every match is kept in matched_scenarios whatever
the final hand is. The *decision* is PLAY when any enabled scenario matched.
detected_hand is the evaluator's actual poker hand. The *primary scenario* is
the strongest hand the matched rules support:

    * a straight, full house, four of a kind or straight flush always matches
      at least one rule (STRAIGHT, or a pair/trips rule) and always uses the
      player's cards, so for those the actual hand is the primary scenario;
    * otherwise the strongest group among the matched rules is used, NOT the
      evaluator's name - trips lying entirely on the flop are FLOP_PAIR (group
      PAIR), and a flush is whatever else matched (HIGH_CARD with an ace in
      the hole), or NONE.

Note on the brief's own overlap example, 7S 7D with 7H KC KD: that is a FULL
HOUSE (three sevens and two kings), so that is what detected_hand and the
primary scenario say. It still matches PLAYER_PAIR_PLUS_FLOP_MATCH (trips) and
PLAYER_PAIR_PLUS_FLOP_PAIR (two pair), and the decision is PLAY either way.

Dealer qualification is separate: evaluate_dealer_qualification() takes the
dealer's two cards and the board and never looks at the player's decision.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields

from poker.hand_evaluator import (
    HAND_NAMES, InvalidCardError, RANK_VALUES, _straight_high, dealer_qualifies,
    evaluate_hand, normalize_card, parse_card,
)

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_CONFIG_PATH = os.path.join(ROOT, "config", "scenario_engine.json")

# There is deliberately no way to turn this off.
DRY_RUN = True

PLAYER_SLOTS = ["player_1", "player_2"]
FLOP_SLOTS = ["flop_1", "flop_2", "flop_3"]
BOARD_SLOTS = FLOP_SLOTS + ["turn", "river"]
DEALER_SLOTS = ["dealer_1", "dealer_2"]

# -- decisions ----------------------------------------------------------------
PLAY = "PLAY"
DONT_PLAY = "DON'T_PLAY"
WAIT = "WAIT"

# -- individual scenarios -----------------------------------------------------
HIGH_CARD_AKQ = "HIGH_CARD_AKQ"
PLAYER_PAIR = "PLAYER_PAIR"
COMBINED_PAIR = "COMBINED_PAIR"
FLOP_PAIR = "FLOP_PAIR"
PLAYER_PAIR_PLUS_FLOP_PAIR = "PLAYER_PAIR_PLUS_FLOP_PAIR"
COMBINED_TWO_PAIR = "COMBINED_TWO_PAIR"
PLAYER_PAIR_CARD_PLUS_FLOP_PAIR = "PLAYER_PAIR_CARD_PLUS_FLOP_PAIR"
PLAYER_PAIR_PLUS_FLOP_MATCH = "PLAYER_PAIR_PLUS_FLOP_MATCH"
PLAYER_CARD_PLUS_FLOP_PAIR = "PLAYER_CARD_PLUS_FLOP_PAIR"
STRAIGHT = "STRAIGHT"

# -- hand groups, strongest first ---------------------------------------------
HIGH_CARD = "HIGH_CARD"
FLUSH = "FLUSH"                 # an actual hand only - there is no flush rule
PAIR = "PAIR"
TWO_PAIR = "TWO_PAIR"
THREE_OF_A_KIND = "THREE_OF_A_KIND"
FULL_HOUSE = "FULL_HOUSE"
FOUR_OF_A_KIND = "FOUR_OF_A_KIND"
STRAIGHT_FLUSH = "STRAIGHT_FLUSH"
ROYAL_FLUSH = "ROYAL_FLUSH"
NONE = "NONE"

# The evaluator's categories under this module's names. Built from HAND_NAMES
# so the two cannot drift apart.
CATEGORY_KEYS = {category: name.upper().replace(" ", "_")
                 for category, name in HAND_NAMES.items()}

GROUP_STRENGTH = {CATEGORY_KEYS[category]: category for category in CATEGORY_KEYS}

SCENARIO_GROUP = {
    HIGH_CARD_AKQ: HIGH_CARD,
    PLAYER_PAIR: PAIR,
    COMBINED_PAIR: PAIR,
    FLOP_PAIR: PAIR,
    PLAYER_PAIR_PLUS_FLOP_PAIR: TWO_PAIR,
    COMBINED_TWO_PAIR: TWO_PAIR,
    PLAYER_PAIR_CARD_PLUS_FLOP_PAIR: TWO_PAIR,
    PLAYER_PAIR_PLUS_FLOP_MATCH: THREE_OF_A_KIND,
    PLAYER_CARD_PLUS_FLOP_PAIR: THREE_OF_A_KIND,
    STRAIGHT: STRAIGHT,
}

ALL_SCENARIOS = list(SCENARIO_GROUP)

# Actual hands that always match a rule and always use the player's cards (the
# flop has only three), so the hand itself is the primary scenario. FLUSH is
# deliberately absent: no rule covers it.
PRIMARY_FROM_HAND = {STRAIGHT, FULL_HOUSE, FOUR_OF_A_KIND, STRAIGHT_FLUSH, ROYAL_FLUSH}

WAIT_REASON = "Waiting for confirmed player + flop cards"

# Hand statuses the tracker reports (tracker.CONFIRMED / tracker.HELD). Named
# here as strings so the engine never imports the capture stack; a test checks
# they still match.
CONFIRMED_STATUS = "CONFIRMED"
HELD_STATUS = "HELD"
ACCEPTED_STATUSES = (CONFIRMED_STATUS, HELD_STATUS)

HIGH_CARD_SOURCES = ("player", "player_and_flop")


@dataclass
class EngineConfig:
    """What the ambiguous parts of the brief mean. See the module docstring."""

    # HIGH_CARD_AKQ: PLAY when at least `high_card_min_count` of the player's
    # two hole cards is one of `high_card_ranks`. Flop cards are NOT counted
    # unless high_card_source is explicitly set to "player_and_flop".
    high_card_ranks: list = field(default_factory=lambda: ["A", "K", "Q"])
    high_card_source: str = "player"
    high_card_min_count: int = 1
    # Scenarios that make the decision PLAY. All of them by default; drop one
    # to keep detecting it for the log without letting it decide.
    play_scenarios: list = field(default_factory=lambda: list(ALL_SCENARIOS))
    # Readings CardMemory must hold for a card before it counts as confirmed.
    # tracker.CONFIRMING_READINGS is 2, which is what CONFIRMED already means.
    min_readings: int = 2

    def validate(self):
        ranks = []
        for rank in self.high_card_ranks:
            text = str(rank).strip().upper()
            text = "10" if text == "T" else text
            if text not in RANK_VALUES:
                raise ValueError("Unknown rank in high_card_ranks: %r" % (rank,))
            ranks.append(text)
        self.high_card_ranks = ranks
        if self.high_card_source not in HIGH_CARD_SOURCES:
            raise ValueError("high_card_source must be one of %s"
                             % ", ".join(HIGH_CARD_SOURCES))
        self.high_card_min_count = max(1, int(self.high_card_min_count))
        unknown = [name for name in self.play_scenarios if name not in SCENARIO_GROUP]
        if unknown:
            raise ValueError("Unknown scenario(s) in play_scenarios: %s"
                             % ", ".join(unknown))
        self.min_readings = max(1, int(self.min_readings))
        return self


def load_engine_config(path=ENGINE_CONFIG_PATH):
    """EngineConfig from config/scenario_engine.json, or the defaults."""
    config = EngineConfig()
    if not os.path.exists(path):
        return config.validate()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle) or {}
        known = {item.name for item in fields(EngineConfig)}
        for key, value in stored.items():
            if key in known:
                setattr(config, key, value)
        return config.validate()
    except (OSError, ValueError, TypeError) as exc:
        logger.error("Could not read %s, using the defaults: %s", path, exc)
        return EngineConfig().validate()


def save_engine_config(config, path=ENGINE_CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(asdict(config.validate()), handle, indent=4)


# -- card helpers -------------------------------------------------------------

def _ranks(cards):
    return [parse_card(card)[0] for card in cards]


def _pair_ranks(ranks):
    """Ranks appearing at least twice in `ranks`."""
    return {rank for rank in ranks if ranks.count(rank) >= 2}


def _check_cards(player_cards, flop_cards):
    """Normalised (player, flop), or InvalidCardError."""
    player = [normalize_card(card) for card in player_cards]
    flop = [normalize_card(card) for card in flop_cards]
    if len(player) != 2:
        raise InvalidCardError("Need exactly 2 player cards, got %d" % len(player))
    if len(flop) != 3:
        raise InvalidCardError("Need exactly 3 flop cards, got %d" % len(flop))
    everything = player + flop
    if len(set(everything)) != len(everything):
        raise InvalidCardError("The same card appears twice: %s" % " ".join(everything))
    return player, flop


# -- the scenario checks ------------------------------------------------------
#
# Each returns {scenario: bool} for every scenario it owns, so the log can show
# the ones that did not match as well as the ones that did.

def detect_high_card_scenario(player_cards, flop_cards, config=None):
    config = config or EngineConfig()
    pool = list(player_cards)
    if config.high_card_source == "player_and_flop":
        pool += list(flop_cards)
    count = sum(1 for rank in _ranks(pool) if rank in config.high_card_ranks)
    return {HIGH_CARD_AKQ: count >= config.high_card_min_count}


def detect_pair_scenarios(player_cards, flop_cards):
    p1, p2 = _ranks(player_cards)
    flop = _ranks(flop_cards)
    pocket = p1 == p2
    return {
        PLAYER_PAIR: pocket,
        COMBINED_PAIR: not pocket and (p1 in flop or p2 in flop),
        FLOP_PAIR: not pocket and bool(_pair_ranks(flop)),
    }


def detect_two_pair_scenarios(player_cards, flop_cards):
    p1, p2 = _ranks(player_cards)
    flop = _ranks(flop_cards)
    pocket = p1 == p2
    flop_pairs = _pair_ranks(flop)
    matched = [rank for rank in (p1, p2) if rank in flop]
    return {
        PLAYER_PAIR_PLUS_FLOP_PAIR: pocket and bool(flop_pairs - {p1}),
        COMBINED_TWO_PAIR: not pocket and p1 in flop and p2 in flop,
        PLAYER_PAIR_CARD_PLUS_FLOP_PAIR: (
            not pocket and len(matched) == 1 and bool(flop_pairs - set(matched))),
    }


def detect_three_of_kind_scenarios(player_cards, flop_cards):
    p1, p2 = _ranks(player_cards)
    flop = _ranks(flop_cards)
    return {
        PLAYER_PAIR_PLUS_FLOP_MATCH: p1 == p2 and p1 in flop,
        # Every hole card against every flop rank, not fixed positions.
        PLAYER_CARD_PLUS_FLOP_PAIR: any(flop.count(rank) >= 2 for rank in (p1, p2)),
    }


def detect_straight_scenario(player_cards, flop_cards):
    values = {RANK_VALUES[rank] for rank in _ranks(list(player_cards) + list(flop_cards))}
    # Five cards make a straight only with five distinct consecutive ranks;
    # _straight_high already counts the ace low for A-2-3-4-5.
    return {STRAIGHT: len(values) == 5 and _straight_high(values) is not None}


def detect_all_scenarios(player_cards, flop_cards, config=None):
    """{scenario: bool} for every scenario, in ALL_SCENARIOS order."""
    config = config or EngineConfig()
    found = {}
    found.update(detect_high_card_scenario(player_cards, flop_cards, config))
    found.update(detect_pair_scenarios(player_cards, flop_cards))
    found.update(detect_two_pair_scenarios(player_cards, flop_cards))
    found.update(detect_three_of_kind_scenarios(player_cards, flop_cards))
    found.update(detect_straight_scenario(player_cards, flop_cards))
    return {name: found[name] for name in ALL_SCENARIOS}


def _primary_scenario(detected_category, matched):
    detected = CATEGORY_KEYS[detected_category]
    if detected in PRIMARY_FROM_HAND and matched:
        return detected
    groups = [SCENARIO_GROUP[name] for name in matched]
    if not groups:
        return NONE
    return max(groups, key=lambda group: GROUP_STRENGTH[group])


def _group_flags(checks):
    """Group-level TRUE/FALSE for the log: is any scenario in the group matched."""
    flags = {}
    for group in (PAIR, TWO_PAIR, THREE_OF_A_KIND, STRAIGHT):
        flags[group] = any(value for name, value in checks.items()
                           if SCENARIO_GROUP[name] == group)
    flags[HIGH_CARD_AKQ] = checks[HIGH_CARD_AKQ]
    return flags


def detect_player_decision(player_cards, flop_cards, config=None):
    """The decision for five confirmed cards. Raises InvalidCardError on bad input.

    Callers with unconfirmed cards want evaluate_round(), which returns WAIT
    instead of ever reaching this.
    """
    config = config or EngineConfig()
    player, flop = _check_cards(player_cards, flop_cards)
    checks = detect_all_scenarios(player, flop, config)
    matched = [name for name, hit in checks.items() if hit]
    evaluated = evaluate_hand(player + flop)
    detected = CATEGORY_KEYS[evaluated["category"]]
    deciding = [name for name in matched if name in config.play_scenarios]
    decision = PLAY if deciding else DONT_PLAY

    if deciding:
        reason = "matched %s" % ", ".join(deciding)
    elif matched:
        reason = "matched %s, none of which is set to play" % ", ".join(matched)
    else:
        reason = ("no pair, two pair, trips or straight, and no %s in the %s"
                  % ("/".join(config.high_card_ranks),
                     "player's cards" if config.high_card_source == "player"
                     else "player's cards or the flop"))

    return {
        "player_cards": player,
        "flop_cards": flop,
        "detected_hand": detected,
        "matched_scenarios": matched,
        "scenario_checks": checks,
        "group_flags": _group_flags(checks),
        "primary_scenario": _primary_scenario(evaluated["category"], matched),
        "decision": decision,
        "reason": reason,
        "dry_run": DRY_RUN,
    }


# -- dealer qualification: separate from the player's decision ---------------

def evaluate_dealer_qualification(dealer_cards, board_cards):
    """Does the dealer's final hand qualify (pair of 4s or better)?

    `dealer_cards` are the dealer's two, `board_cards` the community cards
    (all five for a final hand). Returns:

        dealer_cards, board_cards, dealer_hand ("PAIR", ...), dealer_hand_detail
        ("PAIR of 4s"), dealer_best_five, dealer_qualified (True/False, or None
        when there are not yet enough cards), reason

    The rule itself is hand_evaluator.dealer_qualifies, the one the stored
    records already use, so the two can never disagree.
    """
    dealer = [card for card in (dealer_cards or []) if card]
    board = [card for card in (board_cards or []) if card]
    result = {"dealer_cards": dealer, "board_cards": board, "dealer_hand": None,
              "dealer_hand_detail": None, "dealer_best_five": None,
              "dealer_qualified": None, "reason": None}
    if len(dealer) != 2 or len(dealer) + len(board) < 5:
        result["reason"] = "waiting for the dealer's cards and the board"
        return result
    try:
        evaluated = evaluate_hand(dealer + board)
    except InvalidCardError as exc:
        result["reason"] = "cannot evaluate: %s" % exc
        return result

    category = evaluated["category"]
    detail = CATEGORY_KEYS[category]
    if category == 2:
        rank = _rank_name(evaluated["score"][1])
        detail = "PAIR OF %ss" % rank
    qualified = dealer_qualifies(evaluated)
    result.update(
        dealer_cards=[normalize_card(card) for card in dealer],
        board_cards=[normalize_card(card) for card in board],
        dealer_hand=CATEGORY_KEYS[category],
        dealer_hand_detail=detail,
        dealer_best_five=evaluated["best_five"],
        dealer_qualified=qualified,
        reason=("%s is pair of 4s or better" if qualified
                else "%s is below a pair of 4s") % detail,
    )
    return result


def _rank_name(value):
    return next(rank for rank, number in RANK_VALUES.items() if number == value)


# -- confirmation gate --------------------------------------------------------

def slot_problem(slot, info, config=None):
    """Why one slot's card is not confirmed, or None when it is."""
    config = config or EngineConfig()
    info = info or {}
    card, status = info.get("card"), info.get("status") or "EMPTY"
    readings = int(info.get("readings") or 0)
    if not card:
        return "%s: no card (%s)" % (slot, status)
    if status not in ACCEPTED_STATUSES:
        return "%s: %s is %s" % (slot, card, status)
    if readings < config.min_readings:
        return "%s: %s has %d reading(s), needs %d" % (
            slot, card, readings, config.min_readings)
    return None


def gate_slots(slots, config=None):
    """Which of the five decision cards are not safe to decide on yet.

    `slots` maps slot -> {"card", "status", "readings"} for at least the player
    and flop slots, where status is tracker.card_status's word and readings is
    CardMemory's count for the card it holds. Returns a list of
    "slot: why" strings; empty means all five are confirmed.

    A card is accepted when memory holds it with at least `min_readings`
    readings and this poll's status is CONFIRMED, or HELD (covered right now by
    the dealer's hand, but already settled). UNKNOWN, AMBIGUOUS, CONFIRMING and
    EMPTY all mean WAIT.
    """
    config = config or EngineConfig()
    problems = [problem for problem in
                (slot_problem(slot, slots.get(slot), config)
                 for slot in PLAYER_SLOTS + FLOP_SLOTS) if problem]
    if not problems:
        cards = [slots[slot]["card"] for slot in PLAYER_SLOTS + FLOP_SLOTS]
        if len(set(cards)) != len(cards):
            problems.append("the same card is read in two places: %s" % " ".join(cards))
    return problems


def evaluate_round(slots, round_id=None, config=None, dealer=None):
    """The full result for one poll: WAIT, or the decision with its reasons.

    `dealer` is the dict from evaluate_dealer_qualification, when there is one;
    it is attached for the log and never changes the decision.
    """
    config = config or EngineConfig()
    player = [(slots.get(slot) or {}).get("card") for slot in PLAYER_SLOTS]
    flop = [(slots.get(slot) or {}).get("card") for slot in FLOP_SLOTS]
    problems = gate_slots(slots, config)

    if problems:
        result = {
            "player_cards": player, "flop_cards": flop, "detected_hand": None,
            "matched_scenarios": [], "scenario_checks": {}, "group_flags": {},
            "primary_scenario": None, "decision": WAIT,
            "reason": WAIT_REASON, "wait_reasons": problems,
            "dry_run": DRY_RUN,
        }
    else:
        try:
            result = detect_player_decision(player, flop, config)
            result["wait_reasons"] = []
        except InvalidCardError as exc:
            result = {
                "player_cards": player, "flop_cards": flop, "detected_hand": None,
                "matched_scenarios": [], "scenario_checks": {}, "group_flags": {},
                "primary_scenario": None, "decision": WAIT,
                "reason": "cards cannot be evaluated", "wait_reasons": [str(exc)],
                "dry_run": DRY_RUN,
            }

    dealer = dealer or {}
    result["round_id"] = round_id
    result["dealer_cards"] = dealer.get("dealer_cards") or []
    result["dealer_hand"] = dealer.get("dealer_hand_detail")
    result["dealer_qualified"] = dealer.get("dealer_qualified")
    return result


# -- log text -----------------------------------------------------------------

def format_decision(result):
    """The multi-line [SCENARIO] block for one decision."""
    lines = ["[SCENARIO] (DRY-RUN, no action taken) round %s" % result.get("round_id"),
             "Player: %s" % " ".join(card or "--" for card in result["player_cards"]),
             "Flop: %s" % " ".join(card or "--" for card in result["flop_cards"])]
    if result["decision"] == WAIT:
        lines += ["", "[DECISION]", WAIT]
        lines += ["  - %s" % reason for reason in result.get("wait_reasons") or []]
        return "\n".join(lines)

    flags = result["group_flags"]
    lines += ["", "[DETECTED HAND]", result["detected_hand"],
              "", "[SCENARIO MATCH]"]
    lines += ["%s = %s" % (name, "TRUE" if flags[name] else "FALSE")
              for name in (HIGH_CARD_AKQ, PAIR, TWO_PAIR, THREE_OF_A_KIND,
                           STRAIGHT)]
    lines += ["", "[MATCHED SCENARIOS]"]
    lines += (["- %s" % name for name in result["matched_scenarios"]] or ["- none"])
    lines += ["", "[PRIMARY]", result["primary_scenario"],
              "", "[DECISION]", "%s  (%s)" % (result["decision"], result["reason"])]
    return "\n".join(lines)


def format_dealer(dealer, round_id=None):
    """The [DEALER] block."""
    qualified = dealer.get("dealer_qualified")
    word = {True: "QUALIFIED", False: "NOT QUALIFIED"}.get(qualified, "PENDING")
    return "\n".join([
        "[DEALER] round %s" % round_id,
        "Cards: %s" % (" ".join(dealer.get("dealer_cards") or []) or "--"),
        "Board: %s" % (" ".join(dealer.get("board_cards") or []) or "--"),
        "Hand: %s  (best five %s)" % (dealer.get("dealer_hand_detail") or "--",
                                      " ".join(dealer.get("dealer_best_five") or []) or "--"),
        "Qualification: %s  (%s)" % (word, dealer.get("reason")),
    ])


# -- following a live session without spamming the log -----------------------

class ScenarioMonitor:
    """Runs the engine each poll and logs only when something changes.

    Logged once per round:
        * the first WAIT, before any decision (so a round that never confirms
          still leaves a trace);
        * every new decision - a different decision or different five cards,
          for instance when CardMemory revises a card;
        * the dealer's qualification, once the dealer's cards and the whole
          board are in.

    A WAIT after a decision (a card briefly AMBIGUOUS under the dealer's hand)
    is reported in the result but not logged again, so a flicker cannot fill
    the log. A new round id starts everything afresh.
    """

    def __init__(self, config=None, log=None):
        self.config = config or EngineConfig()
        self.log = log or logger
        self.round_id = object()
        self._logged_wait = False
        self._last_decision_key = None
        self._dealer_key = None
        self.last_result = None

    def _new_round(self, round_id):
        self.round_id = round_id
        self._logged_wait = False
        self._last_decision_key = None
        self._dealer_key = None

    def observe(self, slots, round_id, dealer_cards=None, board_ready=False):
        """Fold one poll in and return the result.

        `dealer_cards` are the dealer's two as the tracker accepts them;
        `board_ready` says the turn and river are confirmed too, so the
        dealer's final hand can be judged.
        """
        if round_id != self.round_id:
            self._new_round(round_id)

        dealer = None
        board = [(slots.get(slot) or {}).get("card") for slot in BOARD_SLOTS]
        if board_ready and dealer_cards and all(dealer_cards) and all(board):
            dealer = evaluate_dealer_qualification(dealer_cards, board)

        result = evaluate_round(slots, round_id, self.config, dealer)
        self.last_result = result

        if result["decision"] == WAIT:
            if self._last_decision_key is None and not self._logged_wait:
                self._logged_wait = any(
                    (slots.get(slot) or {}).get("card") for slot in FLOP_SLOTS)
                if self._logged_wait:
                    self.log.info("%s", format_decision(result))
        else:
            key = (tuple(result["player_cards"]), tuple(result["flop_cards"]),
                   result["decision"], tuple(result["matched_scenarios"]))
            if key != self._last_decision_key:
                self._last_decision_key = key
                self.log.info("%s", format_decision(result))

        if dealer and dealer.get("dealer_qualified") is not None:
            key = (tuple(dealer["dealer_cards"]), tuple(dealer["board_cards"]))
            if key != self._dealer_key:
                self._dealer_key = key
                self.log.info("%s", format_dealer(dealer, round_id))
        return result
