"""Teaching: turn the scenario on screen right now into one of the existing rules.

WHAT THIS IS NOT

It is not a second scenario engine, and it is not machine learning. Nothing
here decides anything and nothing here learns anything. Every rule this module
produces is built by poker/scenarios.py's own `flop_rule` and `preround_rule`,
stored in config/scenarios.json beside the rules typed in by hand, and
evaluated by `decide_flop` and `decide_preround` - the same functions, in the
same order, first match wins. A rule taught from a live hand and the same rule
picked from the dropdowns in ui/rule_windows.py are the same dictionary.

So this module only does three things:

    capture    freeze what is on screen into a ScenarioSnapshot
    scope      offer the conditions the existing rule model can actually say
    check      duplicates, conflicts, priority, and what it changes

WHY THE SNAPSHOT IS FROZEN

The tracker polls about five times a second, and the round changes underneath
whatever window happens to be open. A correction typed against the hand the
person was looking at must not be saved against the hand that arrived while
they were typing, so the snapshot is taken once, holds the round it came from,
and is checked again before anything is written. See `is_current`.

WHAT A RULE CAN AND CANNOT SAY

The flop rule model is (hand, condition) -> play | fold, and the pre-round
model is (previous winner, streak, previous hands) -> ante | skip. That is the
whole vocabulary. Two consequences, both deliberate and both surfaced rather
than worked around:

  * The five exact cards cannot be a condition. There is no card-level clause
    in the model, so "only this hand, only these cards" cannot be taught. See
    UNREPRESENTABLE.
  * WAIT cannot be taught. It is the Scenario Engine's word for "not enough
    confirmed cards yet", not a decision a rule produces, so it is not offered.

The stage is not a clause either - it is the section. A flop rule only ever
runs once the player's two and the flop's three are settled, so "stage = FLOP"
is already true of every rule in the flop section, and the preview says so.
"""

import copy
import datetime
import logging

from poker import scenario_engine as se
from poker import scenarios
from poker.board_features import (
    FLOP_PAIRED, FLOP_SLOTS, HOLE_SLOTS, PAIR_SOURCES, summarise,
)

logger = logging.getLogger(__name__)

FLOP = "flop"
PREROUND = "preround"

# The two instructions each section can be taught, as the constants the rule
# model already uses. Nothing is invented here: these are scenarios.FLOP_ACTIONS
# and scenarios.PREROUND_ACTIONS, named so a caller need not know which is which.
TEACHABLE_ACTIONS = {
    FLOP: scenarios.FLOP_ACTIONS,
    PREROUND: scenarios.PREROUND_ACTIONS,
}

# The Scenario Engine's word for the same instruction, so the dialog can show
# the person that correcting the DON'T PLAY they are looking at means writing a
# rule whose action is "fold". ui/decision_banner.py already draws se.PLAY and
# scenarios.PLAY with the same words for the same reason.
ENGINE_EQUIVALENT = {
    scenarios.PLAY: se.PLAY,
    scenarios.FOLD: se.DONT_PLAY,
}

# Things a person may reasonably want to teach that this rule model cannot
# express. Reported rather than approximated - an approximation saved as a rule
# is a rule that does something other than what it was asked for.
UNREPRESENTABLE = {
    "exact_cards":
        "The current scenario rule model cannot represent this condition: "
        "a rule asks about the made hand and one board condition, and has no "
        "clause for particular cards. The smallest extension that would add it "
        "is a new entry in board_features.CONDITION_CHOICES with a matching "
        "branch in scenarios._matches_condition - one condition, in the one "
        "rule model, evaluated by the one engine.",
    "wait":
        "WAIT is not a decision a rule can produce. It is the Scenario "
        "Engine's answer when the five decision cards are not all confirmed "
        "yet, so there is nothing for a rule to say about it.",
}

SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

# What a finding means. The dialog groups on these.
DUPLICATE = "duplicate"          # same question, same answer - already there
CONFLICT = "conflict"            # same question, different answer
SHADOWED = "shadowed"            # something above it always matches first
SHADOWS = "shadows"              # it would make something below unreachable
IMPOSSIBLE = "impossible"        # the two clauses cannot both hold, ever
UNSEEN = "unseen"                # possible, but no recorded hand matches it


# -- showing cards -------------------------------------------------------------

def symbols(card):
    """"AS" as "A<spade>". Display only; the stored notation never changes.

    The betting-automation module that once also defined these four suit
    characters has been removed - the tracker is read-only - so this is now
    the only copy.
    """
    if not card or len(card) < 2:
        return "--"
    return "%s%s" % (card[:-1], SUIT_SYMBOLS.get(card[-1], card[-1]))


def describe_cards(cards, slots):
    """The cards in `slots`, in dealing order, as one line. "-" when empty."""
    shown = [symbols(cards.get(slot)) for slot in slots if cards.get(slot)]
    return "  ".join(shown) if shown else "-"


# -- the frozen scenario -------------------------------------------------------

class ScenarioSnapshot:
    """What the table and the rules said at one instant, kept still.

    Every field is copied out of the tracker's own update payload or worked
    out by the rule functions that payload feeds. Nothing is re-read later:
    that is the point. `session` and `round_id` together are the identity - the
    round id is CardMemory's generation, which the tracker already puts in
    every payload, and the session is bumped by the window each time tracking
    starts, because stopping and starting does not reset the generation.
    """

    def __init__(self, session, round_id, captured_at, stage, cards, statuses,
                 section, features, action, matched_rule, matched_index,
                 engine_decision, engine, previous, history, streak):
        self.session = session
        self.round_id = round_id
        self.captured_at = captured_at
        self.stage = stage
        self.cards = dict(cards or {})
        self.statuses = dict(statuses or {})
        self.section = section
        self.features = dict(features) if features else None
        self.action = action
        self.matched_rule = copy.deepcopy(matched_rule) if matched_rule else None
        self.matched_index = matched_index
        self.engine_decision = engine_decision
        self.engine = dict(engine or {})
        self.previous = copy.deepcopy(previous) if previous else None
        # The rounds decide_preround was given, most recent first. Kept so
        # a proposed rule set can be asked about this same moment - a rule
        # about a run of wins cannot be re-judged without them.
        self.history = copy.deepcopy(list(history or []))
        self.streak = int(streak or 0)

    # -- what it is about ------------------------------------------------------

    @property
    def hand(self):
        """The made hand on the flop, e.g. "Pair". None before the flop."""
        return self.features["hand"] if self.features else None

    @property
    def summary(self):
        """The one-line reading the main window already shows."""
        return summarise(self.features) if self.features else "-"

    def player_cards(self):
        return describe_cards(self.cards, HOLE_SLOTS)

    def flop_cards(self):
        return describe_cards(self.cards, FLOP_SLOTS)

    def turn_card(self):
        return describe_cards(self.cards, ["turn"])

    def river_card(self):
        return describe_cards(self.cards, ["river"])

    def matched_name(self):
        """The rule that produced the current decision, or the default."""
        if self.matched_rule:
            return self.matched_rule["name"]
        return "(no rule matched - the %s default, %s)" % (
            self.section, scenarios.ACTION_LABELS[self.action])

    def priority(self):
        """Where that rule sits in the order, as "#2". None for the default."""
        return None if self.matched_index is None else "#%d" % (self.matched_index + 1)

    def actions(self):
        """The instructions this section's rules can be taught."""
        return list(TEACHABLE_ACTIONS[self.section])

    # -- is it still the scenario on screen? -----------------------------------

    def is_current(self, session, round_id, running):
        """(True, "") when this snapshot still describes the live round.

        Everything the stale-state cases ask about collapses into three
        questions: is the tracker running, is it the same run, and is it the
        same round. A snapshot that fails any of them is refused rather than
        saved against the wrong hand.
        """
        if not running:
            return False, ("The tracker has stopped. Current scenario is no "
                           "longer valid. Please capture the current scenario "
                           "again.")
        if session != self.session:
            return False, ("The tracker was restarted after this scenario was "
                           "captured. Teaching has been cancelled. Capture the "
                           "new scenario.")
        if round_id != self.round_id:
            return False, ("The round has changed since this scenario was "
                           "captured. Teaching has been cancelled. Capture the "
                           "new scenario.")
        return True, ""


def capture(payload, rules, session, previous=None, history=None, now=None):
    """(snapshot, "") for the scenario in `payload`, or (None, why not).

    `payload` is the tracker's "update" dictionary, unmodified. The decision
    and the matched rule come from scenarios.decide_from_cards - the same call
    the main window makes for the same payload - so the dialog cannot show a
    decision the window disagrees with.
    """
    if not payload:
        return None, "There is no scenario to teach yet."

    cards = payload.get("cards") or {}
    statuses = payload.get("statuses") or {}
    engine = payload.get("scenario") or {}

    decision = scenarios.decide_from_cards(
        rules, cards, previous, history, statuses=statuses)
    features = decision["features"]

    if features is None:
        # The flop is out but a card is still being read. Teaching a pre-round
        # rule here would be teaching about the wrong moment entirely, so this
        # waits, exactly as the main window's scenario line does.
        waiting = decision["waiting_for"]
        if waiting and any(cards.get(slot) for slot in FLOP_SLOTS):
            return None, ("The table is still being read (waiting for %s). "
                          "Capture again once the cards are confirmed."
                          % ", ".join(waiting))
        if not previous:
            return None, (
                "Teaching is available once a completed scenario decision "
                "exists.\n\n"
                "Right now there is neither: the flop is not out, so there is "
                "no flop decision, and no finished round is known, so the "
                "pre-round rules have nothing to look at.\n\n"
                "A finished round becomes known either by playing one while "
                "the tracker runs, or from the recorded hands loaded at "
                "startup - so if the database was unreachable when the window "
                "opened, this says so too. Check the message line.")
        section = PREROUND
        action, rule = decision["preround_action"], decision["preround_rule"]
    else:
        section = FLOP
        action, rule = decision["flop_action"], decision["flop_rule"]

    existing = (rules.get(section) or {}).get("rules") or []
    index = None
    for position, candidate in enumerate(existing):
        if candidate is rule:
            index = position
            break

    return ScenarioSnapshot(
        session=session,
        round_id=payload.get("round_id"),
        captured_at=now or datetime.datetime.now(),
        stage=payload.get("state"),
        cards=cards,
        statuses=statuses,
        section=section,
        features=features,
        action=action,
        matched_rule=rule,
        matched_index=index,
        engine_decision=engine.get("decision"),
        engine=engine,
        previous=previous,
        history=history or ([previous] if previous else []),
        streak=scenarios.player_win_streak(
            history or ([previous] if previous else [])),
    ), ""


# -- what can be taught about this scenario ------------------------------------

class Scope:
    """One question the person can choose to teach, and the rule it produces.

    A scope is not a new kind of condition. It is a choice of which of the
    existing rule's clauses to fill in from the hand on screen and which to
    leave at "Any" - so the narrow scopes are the ones with more clauses
    filled, and every one of them builds an ordinary rule through the ordinary
    constructor.
    """

    def __init__(self, key, label, section, fields, clauses):
        self.key = key
        self.label = label
        self.section = section
        self.fields = dict(fields)
        # The clauses in plain words, for the preview's WHEN block.
        self.clauses = list(clauses)

    def build(self, action, name=None):
        """The rule this scope means, built by poker/scenarios.py itself."""
        if self.section == FLOP:
            return scenarios.flop_rule(
                self.fields.get("hand", scenarios.ANY),
                self.fields.get("condition", scenarios.ANY),
                action, name=name)
        return scenarios.preround_rule(
            self.fields.get("previous_dealer_hand", scenarios.ANY),
            self.fields.get("previous_player_hand", scenarios.ANY),
            action, name=name,
            previous_winner=self.fields.get("previous_winner", scenarios.ANY),
            player_win_streak=self.fields.get("player_win_streak", 0))

    def describe(self, action):
        """The preview's WHEN/THEN, as lines."""
        lines = ["WHEN:"]
        for clause in self.clauses:
            lines.append("    %s" % clause)
        lines.append("")
        lines.append("THEN:")
        equivalent = ENGINE_EQUIVALENT.get(action)
        lines.append("    %s%s" % (
            scenarios.ACTION_LABELS[action].upper(),
            "   (the engine's %s)" % equivalent if equivalent else ""))
        return lines


def true_conditions(features):
    """Every condition in CONDITION_CHOICES that holds for this flop.

    Asked of scenarios.condition_holds, so a condition is offered only when
    the engine that will evaluate the rule agrees it is true right now.
    """
    if not features:
        return []
    return [choice for choice in scenarios.CONDITION_CHOICES
            if choice != scenarios.ANY
            and scenarios.condition_holds(choice, features)]


def available_scopes(snapshot):
    """The scopes this snapshot can be taught, narrowest first.

    Narrowest first because a correction is about one situation, and the
    broadest scope - "every hand of this type" - is the one most likely to
    change hands the person was not thinking about. It is offered, but it is
    not the one at the top.
    """
    if snapshot.section == FLOP:
        return _flop_scopes(snapshot)
    return _preround_scopes(snapshot)


def _flop_scopes(snapshot):
    hand = snapshot.hand
    scopes = []
    for condition in true_conditions(snapshot.features):
        scopes.append(Scope(
            "hand+%s" % condition,
            "This hand type, and %s" % condition,
            FLOP, {"hand": hand, "condition": condition},
            ["Stage = FLOP", "AND Hand type = %s" % hand,
             "AND On the table: %s" % condition]))
    scopes.append(Scope(
        "hand", "This hand type, on any board", FLOP,
        {"hand": hand, "condition": scenarios.ANY},
        ["Stage = FLOP", "AND Hand type = %s" % hand]))
    for condition in true_conditions(snapshot.features):
        scopes.append(Scope(
            "any+%s" % condition,
            "Any hand, when %s" % condition,
            FLOP, {"hand": scenarios.ANY, "condition": condition},
            ["Stage = FLOP", "AND On the table: %s" % condition]))
    return scopes


def _preround_scopes(snapshot):
    previous = snapshot.previous or {}
    winner = previous.get("winner") or scenarios.ANY
    dealer_hand = previous.get("dealer_hand") or scenarios.ANY
    player_hand = previous.get("player_hand") or scenarios.ANY
    scopes = []

    if winner != scenarios.ANY and dealer_hand != scenarios.ANY \
            and player_hand != scenarios.ANY:
        scopes.append(Scope(
            "winner+both", "After %s won with those two hands" % winner,
            PREROUND,
            {"previous_winner": winner, "previous_dealer_hand": dealer_hand,
             "previous_player_hand": player_hand},
            ["Stage = before the round", "AND Previous winner = %s" % winner,
             "AND Previous dealer hand = %s" % dealer_hand,
             "AND Previous player hand = %s" % player_hand]))
    if winner != scenarios.ANY and dealer_hand != scenarios.ANY:
        scopes.append(Scope(
            "winner+dealer", "After %s won, dealer holding %s"
            % (winner, dealer_hand), PREROUND,
            {"previous_winner": winner, "previous_dealer_hand": dealer_hand},
            ["Stage = before the round", "AND Previous winner = %s" % winner,
             "AND Previous dealer hand = %s" % dealer_hand]))
    if winner != scenarios.ANY and player_hand != scenarios.ANY:
        scopes.append(Scope(
            "winner+player", "After %s won, player holding %s"
            % (winner, player_hand), PREROUND,
            {"previous_winner": winner, "previous_player_hand": player_hand},
            ["Stage = before the round", "AND Previous winner = %s" % winner,
             "AND Previous player hand = %s" % player_hand]))
    if snapshot.streak >= 2:
        streak = min(snapshot.streak, int(scenarios.STREAK_CHOICES[-1]))
        scopes.append(Scope(
            "streak", "After the player won %d rounds in a row" % streak,
            PREROUND, {"player_win_streak": streak},
            ["Stage = before the round",
             "AND Player wins in a row >= %d" % streak]))
    if winner != scenarios.ANY:
        scopes.append(Scope(
            "winner", "Whenever %s won the last round" % winner, PREROUND,
            {"previous_winner": winner},
            ["Stage = before the round", "AND Previous winner = %s" % winner]))
    return scopes


# -- does one rule swallow another? --------------------------------------------

# Conditions that put a pair among the five cards, so the made hand cannot be
# "High Card". Used for the one impossibility this rule model can be certain
# about: every one of these means two cards share a rank. Taken from
# board_features rather than spelled out again, so renaming a condition there
# cannot leave a stale copy here.
PAIR_FORCING = tuple(PAIR_SOURCES) + (FLOP_PAIRED,)

# The evaluator's name for a hand with no pair or better, via the rule module
# that already borrows it.
HIGH_CARD = scenarios.HIGH_CARD


def _normalised(rule):
    """A pre-round rule with what its streak already implies made explicit.

    player_win_streak counts the rounds the player has just won, stopping at
    the first one they did not, so a rule wanting a streak of 1 or more is also
    a rule wanting the previous winner to be the player. Saying so here is what
    lets the priority check see that "after 2 player wins" is narrower than
    "after a player win", rather than treating them as unrelated questions.
    """
    fields = dict(rule)
    if int(fields.get("player_win_streak", 0) or 0) >= 1:
        fields["previous_winner"] = scenarios.PLAYER
    return fields


def subsumes(section, one, other):
    """True when every situation `other` matches, `one` matches as well.

    Which is to say: put `one` above `other` and `other` can never fire.
    Exact for this rule model, because each clause is an independent test and
    "Any" means the clause is not asked about at all.
    """
    if section == FLOP:
        keys, one, other = ("hand", "condition"), dict(one), dict(other)
    else:
        keys = ("previous_winner", "previous_dealer_hand", "previous_player_hand")
        one, other = _normalised(one), _normalised(other)
    for key in keys:
        value = one.get(key, scenarios.ANY)
        if value != scenarios.ANY and value != other.get(key, scenarios.ANY):
            return False
    if section == PREROUND:
        return (int(one.get("player_win_streak", 0) or 0)
                <= int(other.get("player_win_streak", 0) or 0))
    return True


def impossible(section, rule):
    """Why the two clauses can never both hold, or None when they can.

    Deliberately only the cases this model can prove. A rule taught from a live
    hand is satisfiable by construction - the hand on screen satisfies it - so
    this is about rules assembled by hand.
    """
    if section != FLOP:
        return None
    if rule.get("hand") == HIGH_CARD and rule.get("condition") in PAIR_FORCING:
        return ("%s cannot happen with %s: those five cards contain a pair, so "
                "the made hand is a Pair or better, never a High Card."
                % (rule["hand"], rule["condition"]))
    return None


# -- conflicts -----------------------------------------------------------------

class Finding:
    """One thing worth saying about a rule before it is saved."""

    def __init__(self, kind, message, rule=None, index=None):
        self.kind = kind
        self.message = message
        self.rule = rule
        self.index = index

    @property
    def blocking(self):
        """A duplicate is the one finding there is no point overriding."""
        return self.kind in (DUPLICATE, IMPOSSIBLE)

    def __repr__(self):
        return "<Finding %s: %s>" % (self.kind, self.message)


def analyse(rules, section, rule, index):
    """Everything wrong, or worth knowing, about inserting `rule` at `index`.

    Checks, in the order they are reported: the rule already exists; the same
    question is already answered differently; something above it always matches
    first so it can never fire; it would make something below it unreachable;
    its two clauses cannot both hold.
    """
    existing = (rules.get(section) or {}).get("rules") or []
    findings = []

    why = impossible(section, rule)
    if why:
        findings.append(Finding(IMPOSSIBLE, why))

    for position, other in enumerate(existing):
        if not scenarios.same_question(rule, other):
            continue
        if other.get("action") == rule.get("action"):
            findings.append(Finding(
                DUPLICATE,
                "This rule already exists, as #%d: %s"
                % (position + 1, other["name"]), other, position))
        else:
            findings.append(Finding(
                CONFLICT,
                "A conflicting rule already exists. #%d asks the same question "
                "and answers %s: %s"
                % (position + 1, scenarios.ACTION_LABELS[other["action"]].upper(),
                   other["name"]), other, position))

    for position, other in enumerate(existing[:index]):
        if subsumes(section, other, rule):
            findings.append(Finding(
                SHADOWED,
                "This rule would never run. Rules are checked top to bottom and "
                "the first match wins, and #%d above it matches everything this "
                "one matches: %s"
                % (position + 1, other["name"]), other, position))

    for offset, other in enumerate(existing[index:]):
        if subsumes(section, rule, other):
            findings.append(Finding(
                SHADOWS,
                "#%d would stop running once this rule is above it, because "
                "this rule matches everything it matches: %s"
                % (index + offset + 1, other["name"]), other, index + offset))

    return findings


def suggested_index(rules, section, rule):
    """Where the rule has to go to be able to fire at all.

    Above the first existing rule that would match everything it matches;
    otherwise at the end, where it changes the least. This is the whole of the
    priority suggestion: no rule is moved, removed or rewritten.
    """
    existing = (rules.get(section) or {}).get("rules") or []
    for position, other in enumerate(existing):
        if subsumes(section, other, rule):
            return position
    return len(existing)


def propose(rules, section, rule, index):
    """A copy of the rule set with `rule` inserted at `index`. Saves nothing.

    A copy, because the caller is going to backtest it and may well throw it
    away; the live configuration must be untouched until someone presses
    Activate.
    """
    proposed = copy.deepcopy(rules)
    body = proposed.setdefault(section, copy.deepcopy(scenarios.DEFAULTS[section]))
    body.setdefault("rules", []).insert(index, copy.deepcopy(rule))
    return proposed


def stamp(rule, snapshot, action):
    """Leave the note on the rule saying where it came from.

    Kept on the rule itself rather than in a log beside it: the rule builder
    loads and re-saves config/scenarios.json wholesale, so anything stored
    outside the two sections would be dropped the next time somebody moved a
    rule up. scenarios.METADATA_KEYS keeps this note out of the comparison that
    decides whether two rules ask the same question.
    """
    rule["taught"] = {
        "session": snapshot.session,
        "round_id": snapshot.round_id,
        "captured_at": snapshot.captured_at.isoformat(timespec="seconds"),
        "stage": snapshot.stage,
        "player_cards": [snapshot.cards.get(slot) for slot in HOLE_SLOTS],
        "flop_cards": [snapshot.cards.get(slot) for slot in FLOP_SLOTS],
        "hand": snapshot.hand,
        "original_decision": snapshot.action,
        "original_engine_decision": snapshot.engine_decision,
        "corrected_decision": action,
        "matched_rule": (snapshot.matched_rule or {}).get("name"),
    }
    return rule


# -- what would change ---------------------------------------------------------

def decide_with(rules, snapshot):
    """(action, rule) that `rules` give the frozen scenario.

    The same two functions the tracker calls, on the cards and the history the
    snapshot was taken with - so "this is what the correction does to the hand
    you were looking at" is the engine's answer, not a claim about it.
    """
    if snapshot.section == FLOP:
        return scenarios.decide_flop(rules, snapshot.features)
    return scenarios.decide_preround(rules, snapshot.previous, snapshot.history)


def impact(current, proposed, rows, section, rule, snapshot=None):
    """Everything Save & Test has to show, every number from an actual run.

    `rows` are the recorded hands. Both rule sets are replayed over them by
    tools/backtest.py - the backtest the project already has, not a second one -
    and lined up hand by hand. Nothing is written to config/scenarios.json:
    `proposed` is an in-memory copy and stays one until Activate.
    """
    from tools import backtest

    report = backtest.compare(current, proposed, rows)
    report["matches"] = backtest.matches(rule, section, rows)
    report["section"] = section
    if snapshot is not None:
        was_action, was_rule = decide_with(current, snapshot)
        now_action, now_rule = decide_with(proposed, snapshot)
        report["snapshot"] = {
            "before": was_action, "before_rule": was_rule,
            "after": now_action, "after_rule": now_rule,
            "changed": was_action != now_action,
        }
    return report


def describe_impact(report):
    """The Save & Test result as lines, ready for a text box."""
    lines = ["CURRENT SCENARIO", ""]
    snapshot = report.get("snapshot")
    if snapshot:
        if snapshot["changed"]:
            lines.append("    %s  ->  %s" % (
                scenarios.ACTION_LABELS[snapshot["before"]].upper(),
                scenarios.ACTION_LABELS[snapshot["after"]].upper()))
        else:
            lines.append("    %s  (unchanged - the new rule does not decide "
                         "this hand)"
                         % scenarios.ACTION_LABELS[snapshot["before"]].upper())
        lines.append("    now decided by: %s"
                     % ((snapshot["after_rule"] or {}).get("name")
                        or "the section default"))
    else:
        lines.append("    (no live scenario to test against)")

    lines += ["", "RECORDED HAND IMPACT", ""]
    lines.append("    Hands on record:     %d" % report["hands"])
    lines.append("    This rule matches:   %d of them" % report["matches"])
    if not report["matches"]:
        lines.append("      -> no recorded hand looks like this. The rule is "
                     "not wrong;")
        lines.append("         there is just nothing on record to check it "
                     "against.")
    lines.append("    Hands affected:      %d" % len(report["changed"]))
    lines += ["", "    Previous:"]
    for name in sorted(report["before"]):
        lines.append("        %-12s %d" % (name, report["before"][name]))
    lines += ["", "    New:"]
    for name in sorted(report["after"]):
        lines.append("        %-12s %d" % (name, report["after"][name]))

    if report["changed"]:
        lines += ["", "    The hands that change:"]
        for change in report["changed"][:12]:
            lines.append("        #%-5s %s -> %-10s (actual result: %s)"
                         % (change["row"].get("id"), change["from"],
                            change["to"], change["result"]))
        if len(report["changed"]) > 12:
            lines.append("        ... and %d more"
                         % (len(report["changed"]) - 12))
    return lines


def activate(proposed, path=None):
    """Write the proposed rule set. The only function here that saves anything.

    Persisted by scenarios.save into config/scenarios.json - the one file the
    rule builder reads and the one the engine loads - so an activated rule is
    an ordinary rule from that moment on, editable, movable and removable in
    the window that was already there.
    """
    if path is None:
        scenarios.save(proposed)
    else:
        scenarios.save(proposed, path)
    logger.info("Scenario rule activated from a taught correction")
    return proposed
