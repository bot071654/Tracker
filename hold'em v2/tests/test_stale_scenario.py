"""The recommendation must follow the cards on the table, not a hand that has already ended.

THE SEQUENCE THIS IS ABOUT

A hand ends with DON'T PLAY on the banner. The next hand's hole cards go down, but while they are sliding in they are present and
not yet readable. CardMemory counts a present card as "the table is not empty", so it never starts a new round, and the previous
hand's cards (all held, all with enough readings) went on deciding: the banner kept the old DON'T PLAY through the new hand's
initial round and only changed - to WAIT, then PLAY NOW - once the new hole cards could be read and the new flop came out. It
looked like the recommendation flipped from DON'T PLAY to PLAY NOW merely because the flop appeared. It had nothing to do with the
flop: the first answer belonged to the last hand.

The decision stays a pure function of five confirmed cards (poker/scenario_engine.py is unchanged). What changed is what the tracker
offers it: once the whole flop has been off the table for flop_gone_polls polls the held flop is not offered, so the engine waits.

Driven through the real Tracker._tick with scripted per-poll readings (no screen, no recognition, no casino).
"""

import queue

import pytest

import tracker as tracker_module
from config.settings import CARD_SLOTS

HAND_A = {"player_1": "2D", "player_2": "5C"}                      # no pair, no A/K/Q in the hole cards ...
FLOP_A = {"flop_1": "9H", "flop_2": "8S", "flop_3": "7D"}          # ... and the flop does not pair them: DON'T PLAY
HAND_B = {"player_1": "9D", "player_2": "AS"}
FLOP_B = {"flop_1": "3H", "flop_2": "9S", "flop_3": "7C"}          # A in the hole cards, and a pair of nines: PLAY


def read(card, confident=True):
    if card is None:
        return {"present": False, "card": None, "confident": False, "confidence": 0.0, "ratio": 0.0}
    return {"present": True, "card": card, "confident": confident, "confidence": 0.93 if confident else 0.1, "ratio": 0.9,
            "rank_confidence": 0.93, "suit_confidence": 0.93, "rank_margin": 0.4, "suit_margin": 0.4}


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(tracker_module.Tracker, "_store", lambda self, cards, reads=None: None)
    made = tracker_module.Tracker({"monitor": 1, "regions": {}, "game_window_title": "", "clear_frames": 10,
                                   "change_confirm_frames": 2}, queue.Queue())
    made._record_tick = lambda *a, **k: None

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
        return last["scenario"]["decision"], last["round_id"], last

    made.poll = poll
    return made


def settle(run, table, polls=4):
    result = None
    for _ in range(polls):
        result = run.poll(table)
    return result


# -- the phases of one hand -------------------------------------------------------------------------------------

def test_hole_cards_alone_never_decide(run):
    for _ in range(6):
        assert run.poll(HAND_A)[0] == "WAIT"


def test_an_incomplete_flop_never_decides(run):
    settle(run, HAND_A)
    for table in ({**HAND_A, "flop_1": "9H"}, {**HAND_A, "flop_1": "9H", "flop_2": "8S"}):
        for _ in range(5):
            assert run.poll(table)[0] == "WAIT"


def test_the_decision_follows_the_rules_once_the_flop_is_confirmed(run):
    settle(run, HAND_A)
    seen = [run.poll({**HAND_A, **FLOP_A})[0] for _ in range(5)]
    assert seen[-1] == "DON'T_PLAY"
    assert set(seen) <= {"WAIT", "DON'T_PLAY"}                  # never PLAY for these cards


def test_the_decision_does_not_flip_when_nothing_it_depends_on_changes(run):
    settle(run, {**HAND_A, **FLOP_A})
    assert run.poll({**HAND_A, **FLOP_A})[0] == "DON'T_PLAY"
    for table in ({**HAND_A, **FLOP_A, "turn": "QH"}, {**HAND_A, **FLOP_A, "turn": "QH", "river": "3H"}):
        for _ in range(4):
            assert run.poll(table)[0] == "DON'T_PLAY"


# -- the reported sequence --------------------------------------------------------------------------------------

def test_the_previous_hands_decision_is_not_repeated_while_the_next_hole_cards_cannot_be_read(run):
    settle(run, {**HAND_A, **FLOP_A})
    assert run.poll({**HAND_A, **FLOP_A})[0] == "DON'T_PLAY"
    limit = int(run.config.get("flop_gone_polls", 3))

    decisions = []
    for _ in range(limit + 5):                                   # the next hand's hole cards: present, not readable
        decisions.append(run.poll({}, unreadable=("player_1", "player_2"))[0])
    assert decisions[:limit - 1] == ["DON'T_PLAY"] * (limit - 1)     # a brief cover is tolerated ...
    assert set(decisions[limit - 1:]) == {"WAIT"}, decisions         # ... then the old hand no longer decides


def test_the_new_hand_is_decided_by_its_own_cards_after_its_flop(run):
    settle(run, {**HAND_A, **FLOP_A})
    for _ in range(6):
        run.poll({}, unreadable=("player_1", "player_2"))
    for _ in range(4):
        decision, _, _ = run.poll(HAND_B)
        assert decision == "WAIT"                                # hole cards alone: nothing to decide yet
    decisions = [run.poll({**HAND_B, **FLOP_B})[0] for _ in range(5)]
    assert decisions[-1] == "PLAY" and "DON'T_PLAY" not in decisions


def test_the_decision_for_the_new_hand_is_a_new_round(run):
    _, first_round, _ = settle(run, {**HAND_A, **FLOP_A})
    for _ in range(6):
        run.poll({}, unreadable=("player_1", "player_2"))
    for _ in range(4):
        run.poll(HAND_B)
    _, second_round, _ = run.poll({**HAND_B, **FLOP_B})
    assert second_round != first_round


# -- what must keep working --------------------------------------------------------------------------------------

def test_a_flop_briefly_under_the_dealers_hand_does_not_change_the_decision(run):
    settle(run, {**HAND_A, **FLOP_A})
    limit = int(run.config.get("flop_gone_polls", 3))
    for _ in range(limit - 1):                                   # the whole flop hidden for fewer polls than the limit
        assert run.poll(HAND_A)[0] == "DON'T_PLAY"
    assert run.poll({**HAND_A, **FLOP_A})[0] == "DON'T_PLAY"


def test_one_flop_card_covered_for_a_long_time_does_not_make_the_decision_wait(run):
    settle(run, {**HAND_A, **FLOP_A})
    for _ in range(12):
        assert run.poll({**HAND_A, "flop_1": "9H", "flop_3": "7D"})[0] == "DON'T_PLAY"


def test_the_gate_offers_the_engine_nothing_but_leaves_card_memory_alone(run):
    settle(run, {**HAND_A, **FLOP_A})
    for _ in range(6):
        run.poll({}, unreadable=("player_1", "player_2"))
    assert run.memory.cards.get("flop_1") == "9H"                # memory is not edited by this guard


def test_the_limit_is_configurable(run):
    run.config["flop_gone_polls"] = 1
    settle(run, {**HAND_A, **FLOP_A})
    assert run.poll({}, unreadable=("player_1", "player_2"))[0] == "WAIT"


def test_a_legitimate_change_still_happens_when_the_cards_change(run):
    """Same hole cards, a different confirmed flop: the decision is recomputed from the new inputs."""
    settle(run, {**HAND_A, **FLOP_A})
    assert run.poll({**HAND_A, **FLOP_A})[0] == "DON'T_PLAY"
    # the table is cleared for long enough to start a new round, then the next hand arrives
    for _ in range(12):
        run.poll({})
    for _ in range(4):
        run.poll(HAND_B)
    decisions = [run.poll({**HAND_B, **FLOP_B})[0] for _ in range(5)]
    assert decisions[-1] == "PLAY"
