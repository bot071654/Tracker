"""Round state, round result and decision status are three different things (round_lifecycle.py).

THE FAULT

The next-round ANTE / SKIP alert fired as soon as the player's seat read as empty, and decided from the last STORED hand: a table
that merely looked empty in the middle of a round produced a "Skip Round" that was really about an older round's winner. And the
flop's PLAY NOW / DON'T PLAY was shown as a plain decision with nothing saying the round was still being played.

Now a round is WAITING / IN_PROGRESS / COMPLETE; only a COMPLETE round has a result, and only a result the casino's panel has
CONFIRMED is final and lets the next-round alert speak (the table's own calculation is "unconfirmed"; see
test_round_result_source.py for the panel cases). Driven through the real Tracker._tick with scripted per-poll readings (no
screen, no recognition, no casino).
"""

import queue
from types import SimpleNamespace

import pytest

import round_lifecycle as rl
import tracker as tracker_module
from config.settings import CARD_SLOTS
from database import db
from poker import scenario_engine as se
from poker import scenarios as scenario_rules
from ui import ante_alert
from ui.ante_alert import AnteAlertLogic
from ui.decision_banner import CurrentDecision, label_for, spoken
from ui.scenario_panel import scenario_view

PLAYER = {"player_1": "9H", "player_2": "JH"}
FLOP = {"flop_1": "8D", "flop_2": "10D", "flop_3": "6S"}
TURN, RIVER = {"turn": "4S"}, {"river": "AD"}
DEALER = {"dealer_1": "KH", "dealer_2": "6C"}
FULL = {**PLAYER, **FLOP, **TURN, **RIVER, **DEALER}
NEXT_PLAYER = {"player_1": "2D", "player_2": "5C"}
NEXT_FLOP = {"flop_1": "9S", "flop_2": "8S", "flop_3": "7D"}


def read(card, confident=True):
    if card is None:
        return {"present": False, "card": None, "confident": False, "confidence": 0.0, "ratio": 0.0}
    return {"present": True, "card": card, "confident": confident, "confidence": 0.93 if confident else 0.1, "ratio": 0.9,
            "rank_confidence": 0.93, "suit_confidence": 0.93, "rank_margin": 0.4, "suit_margin": 0.4}


class Backend:
    """Stands in for the database and the Excel export: records what was stored, can fail."""

    def __init__(self, monkeypatch):
        self.stored, self.fail = [], False
        monkeypatch.setattr(db, "insert_hand", self.insert_hand)
        monkeypatch.setattr(tracker_module.excel_export, "append_hand", lambda row: None)

    def insert_hand(self, record):
        if self.fail:
            raise db.DatabaseError("backend unavailable")
        self.stored.append(record["hand_fingerprint"])
        return True, {"id": len(self.stored), "hand_fingerprint": record["hand_fingerprint"]}


def panel_snapshot(mode, cards, generation):
    """The tracker's per-poll panel snapshot ({"sides", "verdicts", "outcome"}) for a scripted panel."""
    from poker.hand_record import build_hand_record
    from recognition import result_panel as rp
    if mode is None:
        return None
    blank = {"state": rp.NOT_VISIBLE, "cards": [], "confirmed": [], "complete": False, "stale": False, "round_id": generation}
    sides = {side: dict(blank) for side in rp.SIDES}
    if mode == "next_deal":
        # The casino has dealt the next hand: the player's row is back to its two new hole cards and the dealer's is gone,
        # while the card memory still holds the finished round (seen live as "player panel shows 3C 4D, but the table has
        # only 0 unread place(s) for this seat").
        sides["player"] = {"state": rp.VISIBLE_PARTIAL, "cards": ["3C", "4D"], "confirmed": ["3C", "4D"], "complete": False,
                           "stale": False, "round_id": generation}
    elif mode != "missing":
        try:
            record = build_hand_record(cards)
        except (ValueError, KeyError, TypeError):
            record = None
        if record is not None:
            for side in rp.SIDES:
                five = list(record["%s_best_five" % side])
                if mode == "partial" and side == "dealer":
                    five = five[:3]
                if mode == "disagree" and side == "dealer":
                    five[0] = "2C" if "2C" not in cards.values() else "3C"        # a card the table cannot hold for the dealer
                sides[side] = {"state": rp.VISIBLE_COMPLETE if len(five) == 5 else rp.VISIBLE_PARTIAL, "cards": five,
                               "confirmed": five, "complete": len(five) == 5, "stale": False, "round_id": generation}
    verdicts = {side: rp.verify(side, sides[side], cards) for side in rp.SIDES}
    return {"sides": sides, "verdicts": verdicts, "outcome": rp.outcome(sides)}


@pytest.fixture
def run(monkeypatch):
    backend = Backend(monkeypatch)
    made = tracker_module.Tracker({"monitor": 1, "regions": {}, "game_window_title": "", "clear_frames": 3,
                                   "change_confirm_frames": 2, "stable_frames": 2}, queue.Queue())
    made._record_tick = lambda *a, **k: None
    made._verify_against_panels = lambda record: ("unknown", "scripted")
    made.backend = backend
    # What the result panels show this poll: "missing" (none visible), "partial", "agree", "disagree", "next_deal" (the next
    # hand's two hole cards), or None for no look.
    made.panel_mode = "missing"
    made._observe_panels = lambda cards: panel_snapshot(made.panel_mode, cards, made.memory.generation)

    def poll(table, unreadable=()):
        """One poll: `table` maps slot -> card; slots in `unreadable` are present but cannot be read."""
        cards = {slot: (None if slot in unreadable else table.get(slot)) for slot in CARD_SLOTS}
        reads = {slot: (read("??", confident=False) if slot in unreadable else read(table.get(slot))) for slot in CARD_SLOTS}
        for slot in unreadable:
            reads[slot]["card"] = None
        made._read_table = lambda: (cards, reads)
        made._tick()
        last = None
        while not made.events.empty():
            kind, payload = made.events.get_nowait()
            if kind == "update":
                last = payload
        return last

    made.poll = poll
    return made


def settle(run, table, polls=4, **kw):
    last = None
    for _ in range(polls):
        last = run.poll(table, **kw)
    return last


def life(payload):
    return payload["lifecycle"]


def shown(payload):
    """(banner key, final) the window would put up for this update's engine decision."""
    key, _, final = rl.present(payload["scenario"]["decision"], payload["lifecycle"])
    return key, final


# -- 1. player cards appear but the round is still active ---------------------------------------------------------

def test_a_table_with_nothing_on_it_is_not_a_round(run):
    lifecycle = life(settle(run, {}))
    assert lifecycle["state"] == rl.WAITING and lifecycle["result_status"] == rl.RESULT_NONE
    assert lifecycle["previous"] is None


def test_player_cards_appearing_start_a_round_that_is_in_progress(run):
    payload = settle(run, PLAYER)
    lifecycle = life(payload)
    assert lifecycle["state"] == rl.IN_PROGRESS
    assert not lifecycle["complete"] and lifecycle["result"] is None and lifecycle["result_status"] == rl.RESULT_NONE
    assert payload["scenario"]["decision"] == se.WAIT and shown(payload) == (se.WAIT, False)   # WAIT is untouched
    assert "ROUND IN PROGRESS" in rl.status_suffix(lifecycle)


# -- 2. the flop appears but the round is still active ----------------------------------------------------------------

def test_the_flop_is_not_the_end_of_the_round_and_its_decision_is_provisional(run):
    settle(run, PLAYER)
    payload = settle(run, {**PLAYER, **FLOP})
    assert payload["scenario"]["decision"] in (se.PLAY, se.DONT_PLAY)
    assert life(payload)["state"] == rl.IN_PROGRESS and not life(payload)["complete"]
    key, final = shown(payload)
    assert key in (rl.LEANING_PLAY, rl.LEANING_DONT_PLAY) and final is False
    assert label_for(key).startswith("LEANING") and spoken(key).startswith("Leaning")
    assert rl.annotate(payload["scenario"], life(payload))["presentation"] == key


def test_the_engines_own_decision_is_not_changed_by_being_provisional(run):
    settle(run, PLAYER)
    payload = settle(run, {**PLAYER, **FLOP})
    scenario = payload["scenario"]
    annotated = rl.annotate(scenario, life(payload))
    assert annotated is not scenario and "presentation" not in scenario
    assert {k: v for k, v in annotated.items() if k != "presentation"} == scenario


def test_the_scenario_panel_says_provisional_while_the_round_is_in_progress(run):
    settle(run, PLAYER)
    payload = settle(run, {**PLAYER, **FLOP})
    text = scenario_view(rl.annotate(payload["scenario"], life(payload)))[0]
    assert "(provisional)" in text
    assert "(provisional)" not in scenario_view(payload["scenario"])[0]


# -- 3. turn and river appear, completion is not confirmed ------------------------------------------------------------

def test_the_turn_and_river_do_not_complete_the_round(run):
    settle(run, {**PLAYER, **FLOP})
    for table in ({**PLAYER, **FLOP, **TURN}, {**PLAYER, **FLOP, **TURN, **RIVER}):
        payload = settle(run, table, polls=6)
        assert life(payload)["state"] == rl.IN_PROGRESS and not life(payload)["complete"]
        assert life(payload)["result_status"] == rl.RESULT_NONE
        assert shown(payload)[0] in (rl.LEANING_PLAY, rl.LEANING_DONT_PLAY) and shown(payload)[1] is False
    assert run.backend.stored == []                                    # and nothing was recorded either


def test_one_dealer_card_does_not_complete_the_round(run):
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    payload = settle(run, {**PLAYER, **FLOP, **TURN, **RIVER, "dealer_1": "KH"}, polls=6)
    assert life(payload)["state"] == rl.IN_PROGRESS and run.backend.stored == []


# -- 4. the result panel appears and the round completes -------------------------------------------------------------

def test_a_complete_round_is_result_pending_until_the_result_is_established(run):
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    first = run.poll(FULL)
    # The first poll with all nine: complete, but not yet checked or stored -> pending, no final decision.
    assert life(first)["state"] == rl.COMPLETE and life(first)["complete"]
    assert life(first)["result_status"] == rl.RESULT_UNCONFIRMED and life(first)["result"] is None
    key, final = shown(first)
    assert key == rl.RESULT_PENDING_KEY and final is False
    assert label_for(key) == "RESULT PENDING" and spoken(key) == "Result pending"
    assert "RESULT PENDING" in rl.status_suffix(life(first))


def test_the_round_result_is_known_once_checked_against_the_panels_and_stored(run):
    run.panel_mode = "agree"
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    payload = settle(run, FULL, polls=5)
    assert run.backend.stored, "the hand should have been stored"
    lifecycle = life(payload)
    assert lifecycle["state"] == rl.COMPLETE and lifecycle["result_status"] == rl.RESULT_CONFIRMED
    assert lifecycle["result"] in ("Player", "Dealer", "Tie")
    assert lifecycle["result"] == lifecycle["record"]["winner"]
    key, final = shown(payload)
    assert key == payload["scenario"]["decision"] and final is True       # the engine's own word, now final
    assert "ROUND COMPLETE" in rl.status_suffix(lifecycle) and "RESULT PENDING" not in rl.status_suffix(lifecycle)


def test_the_result_is_the_one_the_hand_evaluation_found(run):
    from poker.hand_record import build_hand_record
    run.panel_mode = "agree"
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    payload = settle(run, FULL, polls=5)
    assert life(payload)["result"] == build_hand_record(FULL)["winner"]
    assert life(payload)["fingerprint"] == build_hand_record(FULL)["hand_fingerprint"]


# -- 5. result unavailable / ambiguous --------------------------------------------------------------------------------

def test_panels_that_disagree_with_the_table_leave_the_result_unknown_and_unguessed(run):
    run.panel_mode = "disagree"
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    payload = settle(run, FULL, polls=5)
    lifecycle = life(payload)
    assert lifecycle["state"] == rl.COMPLETE and lifecycle["result_status"] == rl.RESULT_CONFLICT
    assert lifecycle["result"] is None                                     # never guessed
    key, final = shown(payload)
    assert key == rl.RESULT_UNKNOWN_KEY and final is False
    assert spoken(key) == "Unable to determine result"
    assert "UNABLE TO DETERMINE RESULT" in rl.status_suffix(lifecycle)


def test_an_ambiguous_dealer_card_means_the_round_is_not_complete_and_has_no_result(run):
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    payload = settle(run, {**FULL, "dealer_2": None}, polls=6, unreadable=("dealer_2",))
    assert life(payload)["state"] == rl.IN_PROGRESS and life(payload)["result_status"] == rl.RESULT_NONE
    assert run.backend.stored == []


def test_a_database_failure_does_not_hide_a_result_that_was_established(run):
    """The hand could not be saved, but it was checked against the panels: the result is still known."""
    run.panel_mode = "agree"
    run.backend.fail = True
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    payload = settle(run, FULL, polls=5)
    assert life(payload)["result_status"] == rl.RESULT_CONFIRMED


# -- 6. a new round after a completed round --------------------------------------------------------------------------

def finish_round(run):
    run.panel_mode = "agree"
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    return settle(run, FULL, polls=5)


def test_clearing_the_table_ends_the_round_and_reports_it_as_the_previous_one(run):
    finished = finish_round(run)
    payload = settle(run, {}, polls=5)
    assert life(payload)["state"] == rl.WAITING and life(payload)["result_status"] == rl.RESULT_NONE
    previous = life(payload)["previous"]
    assert previous["complete"] and previous["result_status"] == rl.RESULT_CONFIRMED
    assert previous["fingerprint"] == life(finished)["fingerprint"] and previous["record"]["winner"] == life(finished)["result"]
    assert payload["round_id"] != finished["round_id"]


def test_a_new_round_starts_in_progress_with_no_result_and_nothing_of_the_old_one(run):
    finish_round(run)
    settle(run, {}, polls=5)
    payload = settle(run, NEXT_PLAYER, polls=4)
    lifecycle = life(payload)
    assert lifecycle["state"] == rl.IN_PROGRESS and lifecycle["result_status"] == rl.RESULT_NONE and lifecycle["result"] is None
    assert lifecycle["fingerprint"] is None and lifecycle["record"] is None
    assert lifecycle["previous"] is None            # a round is under way: the one before it is no longer what the table shows


def test_the_new_round_can_complete_on_its_own_and_then_it_is_the_previous_one(run):
    finished = finish_round(run)
    settle(run, {}, polls=5)
    other = {**NEXT_PLAYER, **NEXT_FLOP, "turn": "QH", "river": "3C", "dealer_1": "AD", "dealer_2": "KC"}
    settle(run, {**NEXT_PLAYER, **NEXT_FLOP, "turn": "QH", "river": "3C"})
    second = settle(run, other, polls=5)
    assert life(second)["state"] == rl.COMPLETE and life(second)["fingerprint"] != life(finished)["fingerprint"]
    cleared = settle(run, {}, polls=5)
    assert life(cleared)["previous"]["fingerprint"] == life(second)["fingerprint"]


# -- 7. stale decisions do not carry across rounds -------------------------------------------------------------------

def test_the_finished_rounds_decision_is_not_on_screen_in_the_next_round(run):
    finished = finish_round(run)
    assert shown(finished)[0] in (se.PLAY, se.DONT_PLAY)
    banner = CurrentDecision()
    assert banner.observe(shown(finished)[0], finished["round_id"])
    seen = []
    for table in ({}, NEXT_PLAYER):
        payload = settle(run, table, polls=5)
        key, _ = shown(payload)
        banner.observe(key, payload["round_id"])
        seen.append((payload["scenario"]["decision"], banner.decision))
    assert all(engine == se.WAIT for engine, _ in seen), seen
    assert all(on_screen in (se.WAIT, None) for _, on_screen in seen), seen


def test_a_provisional_decision_is_never_carried_into_the_next_round(run):
    settle(run, PLAYER)
    flop = settle(run, {**PLAYER, **FLOP})
    assert shown(flop)[0] in (rl.LEANING_PLAY, rl.LEANING_DONT_PLAY)
    settle(run, {}, polls=5)                                               # the table clears without ever completing
    payload = settle(run, NEXT_PLAYER, polls=4)
    assert shown(payload) == (se.WAIT, False)


# -- 8. temporary recognition failures do not falsely complete a round ------------------------------------------------

def test_covering_cards_in_the_middle_of_a_round_never_completes_it(run):
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    for covered in (("player_1",), ("player_1", "player_2"), ("flop_1", "flop_2", "flop_3"), ("river",), ("turn", "river")):
        payload = settle(run, {**PLAYER, **FLOP, **TURN, **RIVER}, polls=3, unreadable=covered)
        assert life(payload)["state"] == rl.IN_PROGRESS and not life(payload)["complete"], covered
    assert run.backend.stored == []


def test_a_dealer_card_read_in_one_poll_only_does_not_complete_the_round(run):
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    flicker = run.poll(FULL)                                               # one look at both dealer cards ...
    assert life(flicker)["result_status"] in (rl.RESULT_NONE, rl.RESULT_UNCONFIRMED)
    assert life(flicker)["result"] is None                                 # ... never gives a result by itself
    assert run.backend.stored == []


def test_the_table_looking_empty_mid_round_gives_no_result_and_no_complete_round(run):
    settle(run, {**PLAYER, **FLOP, **TURN})
    payload = settle(run, {}, polls=5)                                     # lost the table: nothing readable at all
    lifecycle = life(payload)
    assert lifecycle["state"] != rl.COMPLETE and lifecycle["previous"] is not None
    assert lifecycle["previous"]["complete"] is False and lifecycle["previous"]["result_status"] == rl.RESULT_NONE


def test_a_recognition_failure_after_completion_does_not_undo_it(run):
    run.panel_mode = "agree"
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    done = settle(run, FULL, polls=5)
    assert life(done)["result_status"] == rl.RESULT_CONFIRMED
    payload = settle(run, FULL, polls=3, unreadable=("dealer_1", "dealer_2", "river"))
    assert life(payload)["state"] == rl.COMPLETE and life(payload)["result_status"] == rl.RESULT_CONFIRMED
    assert life(payload)["result"] == life(done)["result"]


# -- the next-round alert: only after the finished round is confirmed -------------------------------------------------

def previous_of(payload):
    return life(payload)["previous"]


class Session:
    """The alert logic fed by real tracker updates, answered by the app's own readiness rule."""

    def __init__(self, scenarios=None, history=()):
        self.logic = AnteAlertLogic(empty_polls=1, timeout=25.0)
        self.scenarios = scenarios or {"preround": {"default": scenario_rules.ANTE, "rules": []}}
        self.history = list(history)
        self.last_record = None
        self.teach_session = 1
        self.events = []
        self.now = 1000.0

    def feed(self, payload, step=0.2):
        self.now += step
        from app import App
        event = self.logic.observe(
            payload["state"], payload["seen"],
            lambda: App._preround_for(self, payload),
            now=self.now, ready=lambda: App._preround_ready(self, payload), key=App._preround_key(self, payload))
        if event:
            self.events.append(event)
        return event

    def actions(self):
        return [e[1] for e in self.events if e[0] == ante_alert.SHOW]


def test_an_empty_looking_table_mid_round_never_says_skip_or_ante(run):
    session = Session()
    for table in (PLAYER, {**PLAYER, **FLOP}, {**PLAYER, **FLOP, **TURN}):
        for _ in range(4):
            session.feed(run.poll(table))
    for _ in range(8):                                                     # the tracker loses the table mid-round
        session.feed(run.poll({}))
    assert scenario_rules.SKIP not in session.actions() and scenario_rules.ANTE not in session.actions()
    assert session.actions() == [rl.RESULT_PENDING_KEY]                    # it says so, once, and decides nothing


def test_the_pending_notice_gives_way_to_unable_to_determine_the_result(run):
    session = Session()
    for _ in range(3):
        session.feed(run.poll({**PLAYER, **FLOP}))
    for _ in range(3):
        session.feed(run.poll({}))
    session.feed(run.poll({}), step=30.0)                                  # still nothing, past the alert's timeout
    assert session.actions() == [rl.RESULT_PENDING_KEY, rl.RESULT_UNKNOWN_KEY]
    for _ in range(5):
        session.feed(run.poll({}))
    assert session.actions() == [rl.RESULT_PENDING_KEY, rl.RESULT_UNKNOWN_KEY]       # and does not start again


def test_after_a_confirmed_finished_round_the_alert_decides_from_that_rounds_result(run):
    rules = {"preround": {"default": scenario_rules.ANTE, "rules": [
        {"name": "skip after a dealer win", "action": scenario_rules.SKIP, "previous_winner": "Dealer"},
        {"name": "ante after a player win", "action": scenario_rules.ANTE, "previous_winner": "Player"}]}}
    stale = {"hand_fingerprint": "an-older-round", "winner": "Dealer", "player_hand": "Pair", "dealer_hand": "Pair"}
    session = Session(rules, history=[stale])
    session.last_record = stale                                            # an older round's winner is what used to decide
    finished = finish_round(run)
    session.feed(finished)
    for _ in range(5):
        session.feed(run.poll({}))
    winner = life(finished)["result"]
    expected = {"Dealer": scenario_rules.SKIP, "Player": scenario_rules.ANTE}.get(winner, scenario_rules.ANTE)
    assert session.actions() == [expected], (winner, session.events)
    assert "default rule" not in session.events[0][2] or winner == "Tie"


def test_the_alert_uses_the_finished_rounds_own_record_not_the_last_stored_one(run):
    rules = {"preround": {"default": scenario_rules.ANTE, "rules": [
        {"name": "skip after a dealer win", "action": scenario_rules.SKIP, "previous_winner": "Dealer"}]}}
    finished = finish_round(run)
    if life(finished)["result"] != "Dealer":
        pytest.skip("the scripted hand is not a dealer win")
    session = Session(rules)
    session.last_record = {"hand_fingerprint": "older", "winner": "Player"}      # the stale record would say ANTE
    session.feed(finished)
    for _ in range(4):
        session.feed(run.poll({}))
    assert session.actions() == [scenario_rules.SKIP]


def test_an_unconfirmed_result_waits_and_then_the_alert_fires_once_it_is_confirmed(run):
    session = Session()
    run.panel_mode = "missing"                                             # the casino's panel is not (yet) readable
    settle(run, {**PLAYER, **FLOP, **TURN, **RIVER})
    session.feed(run.poll(FULL))
    session.feed(run.poll(FULL))
    for _ in range(5):
        session.feed(run.poll({}))
    assert session.actions() == [rl.RESULT_PENDING_KEY]                    # complete, panel not confirming: pending, no ANTE
    ready = AnteAlertLogic(empty_polls=1)
    ready.observe("FLOP", {"player_1": "9H"}, lambda: None, now=1.0)
    steps = iter([(ante_alert.PENDING, "being checked"), (ante_alert.READY, "")])
    first = ready.observe("WAITING", {}, lambda: (scenario_rules.ANTE, "rule"), now=2.0, ready=lambda: next(steps))
    second = ready.observe("WAITING", {}, lambda: (scenario_rules.ANTE, "rule"), now=2.2, ready=lambda: next(steps))
    assert first[:2] == (ante_alert.SHOW, rl.RESULT_PENDING_KEY)
    assert second[:2] == (ante_alert.SHOW, scenario_rules.ANTE)            # the pending notice is replaced by the real alert
    assert ready.observe("WAITING", {}, lambda: None, now=2.4, ready=lambda: (ante_alert.READY, "")) is None   # once only


def test_cards_dealt_remove_the_pending_notice(run):
    session = Session()
    for _ in range(3):
        session.feed(run.poll({**PLAYER, **FLOP}))
    for _ in range(5):
        session.feed(run.poll({}))
    assert session.logic.showing and session.logic.showing[0] == rl.RESULT_PENDING_KEY
    event = session.feed(run.poll(NEXT_PLAYER))
    assert event and event[0] == ante_alert.HIDE and session.logic.showing is None


def test_without_a_readiness_check_the_alert_behaves_as_it_always_did():
    logic = AnteAlertLogic(empty_polls=1)
    logic.observe("FLOP", {"player_1": "9H"}, lambda: None, now=1.0)
    event = logic.observe("WAITING", {}, lambda: (scenario_rules.SKIP, "default"), now=2.0)
    assert event[:2] == (ante_alert.SHOW, scenario_rules.SKIP)


def test_a_payload_without_a_lifecycle_is_answered_as_before():
    from app import App
    assert App._preround_ready(None, {"state": "WAITING"}) == (ante_alert.READY, "")


# -- presentation rules, directly ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("decision", [se.PLAY, se.DONT_PLAY])
def test_presentation_through_the_whole_round(decision):
    in_progress = {"state": rl.IN_PROGRESS}
    pending = {"state": rl.COMPLETE, "result_status": rl.RESULT_UNCONFIRMED}
    known = {"state": rl.COMPLETE, "result_status": rl.RESULT_CONFIRMED, "result": "Dealer"}
    conflict = {"state": rl.COMPLETE, "result_status": rl.RESULT_CONFLICT}
    leaning = rl.LEANING_PLAY if decision == se.PLAY else rl.LEANING_DONT_PLAY
    assert rl.present(decision, in_progress)[::2] == (leaning, False)
    assert rl.present(decision, pending)[::2] == (rl.RESULT_PENDING_KEY, False)
    assert rl.present(decision, known)[::2] == (decision, True)
    assert rl.present(decision, conflict)[::2] == (rl.RESULT_UNKNOWN_KEY, False)
    assert rl.present(decision, None)[::2] == (decision, False)           # no lifecycle: shown as it always was


def test_wait_and_other_decisions_are_never_touched():
    for lifecycle in ({"state": rl.IN_PROGRESS}, {"state": rl.COMPLETE, "result_status": rl.RESULT_UNCONFIRMED}, None):
        for decision in (se.WAIT, None, scenario_rules.ANTE, scenario_rules.SKIP):
            assert rl.present(decision, lifecycle)[::2] == (decision, False)


def test_every_presentation_key_has_a_label_and_a_phrase():
    for key in rl.PRESENTATION_KEYS:
        assert label_for(key) and spoken(key)


def test_the_panel_names_a_pending_or_unknown_result():
    scenario = {"decision": se.PLAY, "reason": "x", "presentation": rl.RESULT_PENDING_KEY}
    assert scenario_view(scenario)[0] == "Decision: RESULT PENDING"
    scenario["presentation"] = rl.RESULT_UNKNOWN_KEY
    assert scenario_view(scenario)[0] == "Decision: RESULT UNKNOWN"
    assert scenario_view({"decision": se.WAIT, "presentation": rl.RESULT_PENDING_KEY})[0] == "Decision: WAIT"


def test_the_banner_stays_silent_for_a_decision_shown_after_the_round_is_over():
    import inspect

    from ui.decision_banner import DecisionBanner
    assert "chime" in inspect.signature(DecisionBanner.show).parameters
    source = inspect.getsource(DecisionBanner.show)
    assert "chime and settled in CHIMES" in source


def test_the_teach_dialog_says_what_the_banner_said_while_the_round_is_in_progress(tmp_path):
    tk = pytest.importorskip("tkinter")
    import test_teach_decision_display as display
    from poker import teaching
    from ui import rule_windows

    def labels_for(lifecycle):
        for _ in range(6):
            try:
                window = tk.Tk()
                break
            except tk.TclError:
                import time
                time.sleep(0.4)
        else:
            pytest.skip("no display available")
        window.withdraw()
        try:
            made_payload = display.payload()
            if lifecycle is not None:
                made_payload["lifecycle"] = lifecycle
            taken, why = teaching.capture(made_payload, display.RULES, session=1)
            assert taken is not None, why
            made = rule_windows.TeachScenarioWindow(window, taken, rules=display.RULES, load_rows=lambda: [],
                                                    save_path=str(tmp_path / "scenarios.json"))
            window.update()
            found = display.labels(made)
            made.destroy()
            return found
        finally:
            window.destroy()

    assert "LEANING PLAY" in labels_for({"state": rl.IN_PROGRESS}) and "PLAY NOW" not in labels_for({"state": rl.IN_PROGRESS})
    assert "PLAY NOW" in labels_for({"state": rl.COMPLETE, "result_status": rl.RESULT_CONFIRMED, "result": "Player"})
    assert "PLAY NOW" in labels_for(None)                                  # an update with no lifecycle: as before


# -- through the real window --------------------------------------------------------------------------------------------

def window_payload(state, decision, lifecycle, round_id=1, seen=None):
    import time as _time
    import test_banner_lifecycle as base
    update = base.payload(decision, round_id)
    update["state"] = state
    update["lifecycle"] = lifecycle
    if seen is not None:
        update["seen"] = seen
        update["cards"] = seen
    return update


def push(application, update):
    application.events.put(("update", update))
    application._poll_events()
    application.root.update()


def banner_text(application):
    application.root.update()
    return application.decision_banner.text


from test_banner_lifecycle import application              # noqa: F401,E402 - the real window with everything stubbed


@pytest.fixture
def root():
    """A Tk root; starting Tk occasionally fails here, so it is retried before the test is skipped."""
    import time as _time
    tk = pytest.importorskip("tkinter")
    window, error = None, None
    for _ in range(6):
        try:
            window = tk.Tk()
            break
        except tk.TclError as exc:
            error = exc
            _time.sleep(0.4)
    if window is None:
        pytest.skip("no display available: %s" % error)
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


def test_the_window_shows_a_flop_decision_as_provisional_and_the_round_as_in_progress(application):
    import test_banner_lifecycle as base
    base.begin(application)
    push(application, window_payload("FLOP", se.PLAY, {"state": rl.IN_PROGRESS, "result_status": rl.RESULT_NONE, "previous": None}))
    assert banner_text(application) == "LEANING PLAY"
    assert "ROUND IN PROGRESS" in application.status_var.get()
    assert "(provisional)" in application.scenario_panel.decision_var.get()


def test_the_window_makes_the_decision_final_only_after_the_result_is_known(application):
    import test_banner_lifecycle as base
    base.begin(application)
    pending = {"state": rl.COMPLETE, "complete": True, "result_status": rl.RESULT_UNCONFIRMED, "previous": None}
    push(application, window_payload("COMPLETE", se.PLAY, pending))
    assert banner_text(application) == "RESULT PENDING"
    assert "RESULT PENDING" in application.status_var.get()
    known = {"state": rl.COMPLETE, "complete": True, "result_status": rl.RESULT_CONFIRMED, "result": "Player", "previous": None}
    push(application, window_payload("COMPLETE", se.PLAY, known))
    assert banner_text(application) == "PLAY NOW"
    assert "PLAYER WON" in application.status_var.get()
    conflict = {"state": rl.COMPLETE, "complete": True, "result_status": rl.RESULT_CONFLICT, "previous": None}
    push(application, window_payload("COMPLETE", se.PLAY, conflict))
    assert banner_text(application) == "RESULT UNKNOWN"


def test_the_window_never_shows_skip_for_a_table_that_only_looked_empty(application):
    import test_banner_lifecycle as base
    application.scenarios = {"preround": {"default": scenario_rules.SKIP, "rules": []}, "flop": {"default": "play", "rules": []}}
    base.begin(application)
    cards = {slot: base.CARDS.get(slot) for slot in CARD_SLOTS}
    mid_round = {"state": rl.IN_PROGRESS, "result_status": rl.RESULT_NONE, "previous": None}
    push(application, window_payload("FLOP", se.WAIT, mid_round, round_id=1, seen=cards))
    empty = {slot: None for slot in CARD_SLOTS}
    lost = {"state": rl.WAITING, "result_status": rl.RESULT_NONE,
            "previous": {"round_id": 1, "state": rl.IN_PROGRESS, "complete": False, "result_status": rl.RESULT_NONE, "record": None}}
    push(application, window_payload("WAITING", se.WAIT, lost, round_id=2, seen=empty))
    assert banner_text(application) == "RESULT PENDING"
    assert application.decision_banner.text != "SKIP ROUND"


def test_the_window_shows_the_next_round_alert_once_the_finished_round_is_known(application):
    import test_banner_lifecycle as base
    application.scenarios = {"preround": {"default": scenario_rules.ANTE, "rules": [
        {"name": "skip after a dealer win", "action": scenario_rules.SKIP, "previous_winner": "Dealer"}]},
        "flop": {"default": "play", "rules": []}}
    base.begin(application)
    cards = {slot: base.CARDS.get(slot) for slot in CARD_SLOTS}
    push(application, window_payload("FLOP", se.WAIT, {"state": rl.IN_PROGRESS, "result_status": rl.RESULT_NONE, "previous": None},
                                     round_id=1, seen=cards))
    record = {"hand_fingerprint": "abc", "winner": "Dealer", "player_hand": "Pair", "dealer_hand": "Pair"}
    previous = {"round_id": 1, "state": rl.COMPLETE, "complete": True, "result_status": rl.RESULT_UNCONFIRMED, "record": record}
    empty = {slot: None for slot in CARD_SLOTS}
    push(application, window_payload("WAITING", se.WAIT, {"state": rl.WAITING, "previous": previous}, round_id=2, seen=empty))
    assert banner_text(application) == "RESULT PENDING"                    # complete, but its result is not established yet
    previous = dict(previous, result_status=rl.RESULT_CONFIRMED, result="Dealer")
    push(application, window_payload("WAITING", se.WAIT, {"state": rl.WAITING, "previous": previous}, round_id=2, seen=empty))
    assert banner_text(application) == "SKIP ROUND"                        # from THAT round's own record, not last_record
