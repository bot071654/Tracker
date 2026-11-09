"""Round state, round result and decision status - three different things, kept apart.

THE FAULT

The tracker treated "cards are visible" as if it meant "the round has reached a point where a decision stands":

  * the next-round ANTE / SKIP alert fired the moment the player's seat read as empty - one poll was enough - without asking
    whether the round had finished, and decided from the last STORED hand, whichever round that was. A table that merely
    looked empty mid-round (the tracker lost the table, a window lay over it) produced a "Skip Round" during an active round
    that was really about an older round's winner;
  * the flop evaluation was shown as PLAY NOW / DON'T PLAY as soon as the player's two cards and the flop were confirmed,
    with nothing on screen saying the round was still being played.

THE MODEL

Round STATE     WAITING     - no round is under way (or the table is not showing cards)
                IN_PROGRESS - cards are being dealt and tracked; the round is not over. The flop, turn and river arriving
                              are steps of a round in progress, never its end.
                COMPLETE    - the tracker's own completion evidence: all nine cards confirmed and distinct, the dealer's two
                              included (the dealer's cards are turned up only at the showdown). Latched for the round: a
                              recognition failure after that does not un-complete it, and one before it cannot complete it.
Round RESULT    Only a COMPLETE round has one, and there are two different things it can be built from:
                  table_result  the winner calculated from the nine confirmed cards (hand_record.build_hand_record).
                                Calculated, never confirmed by anything else.
                  result        the winner CONFIRMED by the casino's own result panel: both rows complete, current for this
                                round, and agreeing with the table's cards and winner.
                result_status   none         not complete
                                unconfirmed  complete, the table result is calculated, the panel has not confirmed it -
                                             missing, unreadable, still filling in, or not yet agreeing. NOT an answer.
                                confirmed    the panel confirmed it (result_source "panel").
                                conflict     the panel contradicts the table. Neither is guessed.
                The panel is checked on every poll while the round's cards are held, so a panel that arrives late resolves
                `unconfirmed` to `confirmed` when reliable evidence arrives. Once confirmed it stays confirmed for the round
                unless the panel then contradicts the table. After the table has cleared nothing can change it: the panel
                tracker deliberately refuses to hand one round's panel to the next.
DECISION status provisional (state IN_PROGRESS) -> final (COMPLETE with a CONFIRMED result); RESULT PENDING while the result
                is unconfirmed; RESULT UNKNOWN when the panel and table disagree. The decision itself still comes from the
                Scenario Engine, unchanged: this module only says how to PRESENT it at this point in the round, and whether
                the next-round ANTE / SKIP suggestion may be made at all (only for a confirmed result).

PREVIOUS ROUND

Betting for the next round opens after the table clears, which starts a new round id. What that moment needs to know is about
the round that just ended - did it finish, and is its result confirmed - so the last round that had cards is reported alongside
as `previous`. A round that never completed is reported as such: it is not a finished round and gives no pre-round decision.

Everything here is a pure function of values the tracker already has. Nothing reads a screen and nothing acts on the game.
"""

WAITING, IN_PROGRESS, COMPLETE = "WAITING", "IN_PROGRESS", "COMPLETE"

RESULT_NONE, RESULT_UNCONFIRMED, RESULT_CONFIRMED, RESULT_CONFLICT = "none", "unconfirmed", "confirmed", "conflict"

SOURCE_PANEL, SOURCE_TABLE = "panel", "table"

# What the panels say about a complete round (panel_evidence)
PANEL_CONFIRMED, PANEL_UNCONFIRMED, PANEL_CONFLICT = "confirmed", "unconfirmed", "conflict"

# Banner keys for the presentation states. The Scenario Engine's own PLAY / DON'T_PLAY / WAIT are unchanged.
LEANING_PLAY = "LEANING_PLAY"
LEANING_DONT_PLAY = "LEANING_DONT_PLAY"
RESULT_PENDING_KEY = "RESULT_PENDING"
RESULT_UNKNOWN_KEY = "RESULT_UNKNOWN"
PRESENTATION_KEYS = (LEANING_PLAY, LEANING_DONT_PLAY, RESULT_PENDING_KEY, RESULT_UNKNOWN_KEY)

_PLAY, _DONT_PLAY = "PLAY", "DON'T_PLAY"           # the Scenario Engine's words (poker/scenario_engine.py)

SIDES = ("player", "dealer")
PANEL_CARDS = 5


def panel_evidence(panels, table_winner):
    """What the casino's result panels say about the finished round: confirmed, conflict or unconfirmed.

    `panels` is the tracker's per-poll panel snapshot ({"sides", "verdicts", "outcome"}, see Tracker._observe_panels),
    `table_winner` the winner calculated from the table's nine cards.

    Confirmed needs everything at once: both rows complete (five confirmed cards each) and current for this round, each row
    judged consistent with the table's own cards, and the winner the panels imply the same as the table's. Anything short
    of that - no panel, an unreadable one, a row still filling in, a mismatch that has not yet lasted long enough to count as
    a conflict - is only unconfirmed.

    A conflict needs a COMPLETE, current row that the tracker judges contradicts the table (a card the table cannot hold,
    or a mismatch that persisted), or both rows complete and implying a different winner. A row that is not complete
    cannot contradict a finished round: the panel's player row drops back to the two hole cards of the NEXT deal while the
    card memory still holds this round, and the tracker rightly calls those two cards a conflict with this table - seen
    live as "player panel shows 3C 4D, but the table has only 0 unread place(s) for this seat". That is the next round
    arriving, not this one's result being wrong, so it counts for nothing here (see `ignored_conflict`).
    """
    if not panels:
        return PANEL_UNCONFIRMED
    sides = panels.get("sides") or {}
    verdicts = panels.get("verdicts") or {}
    if any((verdicts.get(side) or ("",))[0] == "conflict" and _complete_row(sides.get(side)) for side in SIDES):
        return PANEL_CONFLICT
    if not all(_complete_row(sides.get(side)) for side in SIDES):
        return PANEL_UNCONFIRMED
    if not all((verdicts.get(side) or ("",))[0] == "consistent" for side in SIDES):
        return PANEL_UNCONFIRMED
    panel_winner = (panels.get("outcome") or {}).get("winner")
    if panel_winner is None or table_winner is None:
        return PANEL_UNCONFIRMED
    return PANEL_CONFIRMED if panel_winner == table_winner else PANEL_CONFLICT


def _complete_row(data):
    """A panel row with five confirmed cards that belongs to this round (not the previous round's stale one)."""
    data = data or {}
    return bool(data.get("complete")) and not data.get("stale") and len(data.get("confirmed") or ()) == PANEL_CARDS


def ignored_conflict(panels):
    """True when the tracker reports a source conflict that `panel_evidence` does not count: it is on a row that is not
    complete, which is the next deal's row (or a row still filling in), not a contradiction of a finished round."""
    sides = (panels or {}).get("sides") or {}
    verdicts = (panels or {}).get("verdicts") or {}
    return any((verdicts.get(side) or ("",))[0] == "conflict" and not _complete_row(sides.get(side)) for side in SIDES)


def _record_of(cards):
    from poker.hand_record import build_hand_record
    try:
        return build_hand_record(cards)
    except (ValueError, KeyError, TypeError):
        return None


class RoundLifecycle:
    """Follows the rounds one at a time, keyed on CardMemory's generation (a new generation is a new round id)."""

    def __init__(self):
        self._generation = None
        self._complete = False
        self._record = None
        self._panel = PANEL_UNCONFIRMED      # what the panels last said about this round
        self._ignored = False                # the panels' last look held a conflict on a partial row, which counts for nothing
        self._confirmed = False              # latched: the panel confirmed this round's result
        self._carded = None                  # the last round that had cards: its round id and its summary

    def observe(self, generation, state, cards, panels=None):
        """The lifecycle for this poll as a plain dict (it travels in the tracker's update).

        `state` is tracker.derive_state(cards); `panels` is this poll's panel snapshot, or None when there was no look at
        the panels this poll (which leaves what they last said about the round as it was).
        """
        if generation != self._generation:
            self._generation, self._complete, self._record = generation, False, None
            self._panel, self._confirmed, self._ignored = PANEL_UNCONFIRMED, False, False
        if state == COMPLETE and not self._confirmed:
            # Latched at the first complete poll. Until the panel has confirmed the result the record follows the cards, so
            # a card the memory corrects a moment later corrects the record too; once confirmed it is fixed.
            record = _record_of(cards)
            if record is not None:
                self._complete, self._record = True, record
        if self._complete and panels is not None and state == COMPLETE:
            evidence = panel_evidence(panels, self._record.get("winner"))
            if evidence == PANEL_CONFIRMED:
                self._confirmed = True
            self._panel = evidence
            self._ignored = ignored_conflict(panels)
        if self._complete:
            round_state = COMPLETE
        elif state == WAITING:
            round_state = WAITING
        else:
            round_state = IN_PROGRESS

        current = self._summary(generation, round_state)
        if round_state != WAITING:
            self._carded = (generation, dict(current))
        previous = None
        if self._carded is not None and self._carded[0] != generation:
            previous = dict(self._carded[1])
            previous["previous"] = None
        current["previous"] = previous
        return current

    def _summary(self, generation, round_state):
        status, result, source, table_result, fingerprint = RESULT_NONE, None, None, None, None
        if self._complete and self._record is not None:
            fingerprint = self._record["hand_fingerprint"]
            table_result = self._record.get("winner")
            if self._panel == PANEL_CONFLICT:
                status = RESULT_CONFLICT
            elif self._confirmed:
                status, result, source = RESULT_CONFIRMED, table_result, SOURCE_PANEL
            else:
                status, source = RESULT_UNCONFIRMED, SOURCE_TABLE
        return {"state": round_state, "round_id": generation, "complete": bool(self._complete), "fingerprint": fingerprint,
                "result": result, "result_status": status, "result_source": source, "table_result": table_result,
                "panel_conflict_ignored": bool(self._ignored and self._complete),
                "record": dict(self._record) if self._complete and self._record else None}


def present(decision, lifecycle):
    """(banner key, reason, final) for the Scenario Engine's `decision` at this point in the round.

    `lifecycle` is the dict above, or None for an update that carries none (then the decision is shown as it always was).
    WAIT, and anything that is not the engine's PLAY / DON'T_PLAY, pass through untouched. `final` is True only for a
    decision shown after the round is complete with a result the casino's panel has CONFIRMED.
    """
    if lifecycle is None or decision not in (_PLAY, _DONT_PLAY):
        return decision, "", False
    if lifecycle.get("state") == COMPLETE:
        status = lifecycle.get("result_status")
        if status == RESULT_CONFLICT:
            return RESULT_UNKNOWN_KEY, "Unable to determine result: the table and the casino's result panel disagree", False
        if status != RESULT_CONFIRMED:
            return RESULT_PENDING_KEY, "Round complete - result not yet confirmed by the casino's result panel", False
        return decision, "Final - round complete, %s (confirmed by the result panel)" % result_text(lifecycle.get("result")), True
    return (LEANING_PLAY if decision == _PLAY else LEANING_DONT_PLAY), "Provisional - the round is still in progress", False


def status_suffix(lifecycle):
    """What the window's status line adds about the round: empty when there is no round or no lifecycle."""
    if not lifecycle:
        return ""
    state = lifecycle.get("state")
    if state == IN_PROGRESS:
        return " | ROUND IN PROGRESS"
    if state != COMPLETE:
        return ""
    status = lifecycle.get("result_status")
    if status == RESULT_CONFIRMED:
        return " | ROUND COMPLETE - %s (PANEL CONFIRMED)" % result_text(lifecycle.get("result")).upper()
    if status == RESULT_CONFLICT:
        return " | ROUND COMPLETE - UNABLE TO DETERMINE RESULT"
    return " | ROUND COMPLETE - RESULT PENDING"


def result_text(result):
    return {"Player": "player won", "Dealer": "dealer won", "Tie": "tie"}.get(result, "result unknown")


def winner_override(lifecycle, panels, progress, final):
    """Replacement text for the window's Winner row when it must not show a plain winner, else None.

    The row must never show a winner while the same screen reports a source conflict, nor pick one source when the table
    and the panel name different winners. `progress` / `final` are hand_record.hands_so_far and the panels' outcome.
    """
    verdicts = (panels or {}).get("verdicts") or {}
    status = (lifecycle or {}).get("result_status")
    if status == RESULT_CONFLICT:
        return "Unable to determine result (table and panel disagree)"
    if status != RESULT_CONFIRMED and any((value or ("",))[0] == "conflict" for value in verdicts.values()):
        # Not a confirmed round, and the screen is reporting a conflict: no winner beside it. (A confirmed round keeps its
        # winner: the only conflicts it ignores are the next deal's partial row, see panel_evidence.)
        return "Unable to determine result (table and panel disagree)"
    table, panel = (progress or {}).get("winner"), (final or {}).get("winner")
    if table and panel and table != panel:
        return "Unconfirmed: table says %s, panel says %s" % (table, panel)
    return None


def winner_note(lifecycle):
    """What to add to a winner taken from the table once the round is complete: it is calculated, not yet confirmed."""
    if lifecycle and lifecycle.get("state") == COMPLETE and lifecycle.get("result_status") == RESULT_UNCONFIRMED:
        return " - unconfirmed"
    return ""


def is_provisional(decision, lifecycle):
    return present(decision, lifecycle)[0] in (LEANING_PLAY, LEANING_DONT_PLAY)


class HandBinding:
    """A pre-round decision held to the hand it was issued for (a SKIP stays a SKIP until that hand is over).

    The pre-round alert is about the hand that is about to be dealt, but the alert only lives while betting is open; once the
    player's cards appear it is hidden and the Scenario Engine's provisional PLAY took the banner. Binding the decision to the
    hand keeps it on the banner and in the voice for the whole hand - through COMPLETE and the result panel's confirmation too:
    the result is shown in the reason line (`result_note`), the decision stays the pre-round one.

    Three kinds of output are kept apart. The bound decision is a decision for the bound hand. A RESULT PENDING / UNKNOWN
    notice is a status about that hand's result, issued by the ANTE alert, never a decision. What follows is for the next hand.

    States (what `observe` leaves in `governing` / `holding` / `dormant`):
      * PREDEAL  - bound, none of its cards seen yet: the decision governs (the alert's 25 s timeout does not end it);
      * INHAND   - its cards are on the table, or it is still the hand being re-read: the decision governs;
      * HOLDING  - a fully different player pair has been read but not yet confirmed: nothing is published, the old decision
                   is taken off the banner (`holding`);
      * DORMANT  - a notice was issued for the bound hand (`notice`): nothing is published for it while the table is empty;
      * CLEARED  - released (`clear`).
    Which hand is bound, and when it ends, comes only from the lifecycle and the confirmed cards - never from a round id seen
    on an empty table (the card memory starts a new generation while the table is empty, see CardMemory.clear):
      * the hand is the first round that HOLDS CARDS (state not WAITING) after the decision, other than the finished round the
        decision was made from (`previous`);
      * a round that holds cards under another id is the same hand re-read after a clear - unless both of its player cards
        read as a different pair, on `confirm_frames` polls in a row. That is CardMemory._is_new_deal, and the only thing here
        that ends a hand: a new id alone, an empty table, missing cards and WAIT never do. The first such poll is HOLDING; a
        partly read pair proves nothing either way and leaves the binding as it was;
      * after a notice (DORMANT) cards coming back with an overlapping or partly read pair are the same hand returning and
        the decision governs again; a fully different pair is HOLDING and then released like any other;
      * a newly issued pre-round decision replaces the binding (`bind`), and the window drops it when tracking starts or stops.
    `dismissed` is the person's click on the banner while it showed the bound decision: it is not shown again for this hand,
    but it still blocks every other decision.
    """

    def __init__(self, confirm_frames=2):
        self.confirm_frames = max(1, int(confirm_frames))
        self.clear()

    def clear(self):
        self.action = self.reason = None
        self.governing = False       # True when the bound decision applies to the update last observed
        self.holding = False         # True on a poll that read a different pair which is not yet confirmed
        self.dormant = False         # True after a result notice for the bound hand, until its cards come back
        self.dismissed = False       # the person dismissed the bound decision from the banner
        self._prior = None           # the finished round the decision was made from
        self._hand = None            # the round id of the bound hand once its cards are on the table
        self._pair = None            # its two player cards, once both have been read
        self._streak = 0             # consecutive polls on which the player's pair read as a different one

    def bind(self, action, reason, lifecycle):
        """Hold `action` for the hand about to be dealt (the one after the finished round `lifecycle` reports)."""
        self.clear()
        self.action, self.reason = action, reason
        self._prior = ((lifecycle or {}).get("previous") or {}).get("round_id")

    def notice(self):
        """A RESULT PENDING / UNKNOWN notice was issued for the bound hand: it stops asserting until its cards return."""
        if self.action is not None and self._hand is not None:
            self.dormant = True

    def dismiss(self):
        if self.action is not None:
            self.dismissed = True

    def observe(self, lifecycle, cards=None):
        """Fold in one update (its lifecycle and confirmed cards). Returns True when the bound decision governs it."""
        self.governing = self.holding = False
        if self.action is None or not lifecycle:
            return False
        rid, state = lifecycle.get("round_id"), lifecycle.get("state")
        if state == WAITING:
            self._streak = 0
            if self._hand is None:
                self.governing = True                       # PREDEAL: the hand it was issued for is not on the table yet
            elif not self.dormant:
                previous = lifecycle.get("previous") or {}
                self.governing = previous.get("round_id") == self._hand
            return self.governing
        pair = ((cards or {}).get("player_1"), (cards or {}).get("player_2"))
        pair = pair if all(pair) else None
        if self._hand is None:
            if rid == self._prior:
                return False
            self._hand, self._pair = rid, pair
        elif rid == self._hand and not self.dormant:
            self._streak = 0
            self._pair = pair or self._pair
        elif pair is None or self._pair is None:
            self._streak = 0                                # not enough read to say anything: the binding stands
            self.dormant = False
        elif set(pair) & set(self._pair):
            self._hand, self._pair, self._streak = rid, pair, 0     # the same hand, re-read after a clear or a notice
            self.dormant = False
        else:
            self._streak += 1
            if self._streak >= self.confirm_frames:
                self.clear()                                # a new deal
                return False
            self.holding = True                             # a different pair, not yet confirmed: publish nothing
            return False
        self.governing = True
        return True


def result_note(lifecycle):
    """What to say about the bound hand's result beside its decision: empty until the hand is complete.

    Read from the hand's own lifecycle, or from `previous` once the table has cleared. The wording is `present`'s.
    """
    life = lifecycle or {}
    if life.get("state") == WAITING:
        life = life.get("previous") or {}
    if life.get("state") != COMPLETE:
        return ""
    status = life.get("result_status")
    if status == RESULT_CONFLICT:
        return "Unable to determine result: the table and the casino's result panel disagree"
    if status != RESULT_CONFIRMED:
        return "Round complete - result not yet confirmed by the casino's result panel"
    return "Round complete, %s (confirmed by the result panel)" % result_text(life.get("result"))


def annotate(scenario, lifecycle):
    """`scenario` for the SCENARIO panel, carrying how its decision is presented at this point in the round.

    Only PLAY / DON'T_PLAY are touched, and only while they are provisional or their result is unconfirmed; a copy is
    returned, never the original.
    """
    if scenario:
        key = present(scenario.get("decision"), lifecycle)[0]
        if key in PRESENTATION_KEYS:
            return dict(scenario, presentation=key)
    return scenario
