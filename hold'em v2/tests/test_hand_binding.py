"""A pre-round SKIP is binding for the whole hand it was issued for.

THE FAULT

The SKIP ROUND alert is about the hand that is about to be dealt, but it lives only while betting is open. When the player's
cards appeared the alert was hidden and the Scenario Engine's decision for the hand took the banner: WAIT, then - once the flop
was down - a provisional PLAY. The window said SKIP ROUND and, seconds later, LEANING PLAY, and the voice said both, for the
same hand.

Policy: once a pre-round SKIP is assigned to an upcoming hand it stays on the banner, and is the only decision spoken, until
that hand is over. These tests drive the real App, banner and Announcer with the lifecycle dicts the tracker sends.
"""

import pytest

tk = pytest.importorskip("tkinter")

import round_lifecycle as rl                                    # noqa: E402
from poker import scenario_engine as se                         # noqa: E402
from poker import scenarios as scenario_rules                   # noqa: E402
from config.settings import CARD_SLOTS                          # noqa: E402
from ui.decision_banner import spoken                           # noqa: E402

import test_banner_lifecycle as base                            # noqa: E402
from test_decision_voice import application, decisions_said, engine        # noqa: E402,F401

SCENARIOS = {"preround": {"default": scenario_rules.ANTE, "rules": [
    {"name": "skip after a dealer win", "action": scenario_rules.SKIP, "previous_winner": "Dealer"}]},
    "flop": {"default": "play", "rules": []}}

EMPTY = {slot: None for slot in CARD_SLOTS}
DEAL = dict(EMPTY, player_1="AS", player_2="7D")
FLOP = {slot: base.CARDS.get(slot) for slot in CARD_SLOTS}
DEAL_B = dict(EMPTY, player_1="QH", player_2="3S")              # a different pair: a genuinely new deal
FLOP_B = dict(FLOP, player_1="QH", player_2="3S")
DEAL_SHARED = dict(EMPTY, player_1="AS", player_2="KH")         # shares a card with DEAL: not proof of a new deal
HALF = dict(EMPTY, player_1="QH")                               # only one player card read

SKIPPED = "SKIP ROUND"


@pytest.fixture
def root():
    """A Tk root; starting Tk occasionally fails here, so it is retried before the test is skipped."""
    import time
    window, error = None, None
    for _ in range(6):
        try:
            window = tk.Tk()
            break
        except tk.TclError as exc:
            error = exc
            time.sleep(0.4)
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


def record(winner):
    return {"hand_fingerprint": "fp-%s" % winner, "winner": winner, "player_hand": "Pair", "dealer_hand": "Pair"}


def life(state, round_id, previous=None, **extra):
    complete = state == rl.COMPLETE
    out = {"state": state, "round_id": round_id, "complete": complete, "fingerprint": None, "result": None,
           "result_status": rl.RESULT_UNCONFIRMED if complete else rl.RESULT_NONE, "record": None, "previous": previous}
    out.update(extra)
    return out


def finished(round_id, winner, confirmed=True):
    return {"round_id": round_id, "state": rl.COMPLETE, "complete": True,
            "result_status": rl.RESULT_CONFIRMED if confirmed else rl.RESULT_UNCONFIRMED,
            "result": winner if confirmed else None, "record": record(winner)}


def send(application, state, decision, lifecycle, seen, round_id, cards=None):
    """One tracker update. `seen` is the raw frame; `cards` the confirmed cards (the same unless a test says otherwise)."""
    update = base.payload(decision, round_id)
    update.update(state=state, lifecycle=lifecycle, seen=dict(seen), cards=dict(seen if cards is None else cards))
    application.events.put(("update", update))
    application._poll_events()
    application.root.update()


def banner(application):
    application.root.update()
    return application.decision_banner.text


def new_session(application):
    application.scenarios = SCENARIOS
    base.begin(application)


def play_hand_one_to_betting(application, winner="Dealer"):
    """Hand 1 is played to its confirmed end, the table clears, and betting opens for hand 2 (gen 1 -> gen 3)."""
    send(application, "FLOP", se.WAIT, life(rl.IN_PROGRESS, 1), FLOP, 1)
    send(application, "COMPLETE", se.WAIT, life(rl.COMPLETE, 1, result_status=rl.RESULT_CONFIRMED, result=winner,
                                                   record=record(winner)), FLOP, 1)
    gap = life(rl.WAITING, 2, previous=finished(1, winner))
    send(application, "WAITING", se.WAIT, gap, EMPTY, 2)


def deal_hand_two(application, decision=se.WAIT, round_id=3):
    send(application, "PLAYER_CARDS", decision, life(rl.IN_PROGRESS, round_id), DEAL, round_id)


def flop_hand_two(application, decision, round_id=3):
    send(application, "FLOP", decision, life(rl.IN_PROGRESS, round_id), FLOP, round_id)


# -- 1. the reported fault ------------------------------------------------------------------------------------------------

def test_a_provisional_play_does_not_replace_the_skip_in_the_same_hand(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    assert banner(application) == SKIPPED                       # the alert itself

    deal_hand_two(application)
    assert banner(application) == SKIPPED                       # betting closed, the SKIP is not taken down
    flop_hand_two(application, se.PLAY)
    assert banner(application) == SKIPPED                       # the engine's provisional PLAY is not shown ...
    assert "LEANING" not in banner(application)
    flop_hand_two(application, se.DONT_PLAY)
    assert banner(application) == SKIPPED

    assert "Leaning play" not in decisions_said(application, engine)            # ... or spoken


# -- 2. it survives repeated updates and temporary WAIT / missing cards -------------------------------------------------

def test_the_skip_survives_repeated_updates_wait_and_missing_cards(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application, se.WAIT)
    for _ in range(3):
        flop_hand_two(application, se.PLAY)                      # the same update again and again
    assert banner(application) == SKIPPED

    flop_hand_two(application, se.WAIT)                          # a WAIT in the middle of the hand
    assert banner(application) == SKIPPED
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 3), DEAL, 3)       # the flop cards not read for a moment
    assert banner(application) == SKIPPED
    send(application, "FLOP", None, life(rl.IN_PROGRESS, 3), FLOP, 3)                  # no scenario decision at all
    assert banner(application) == SKIPPED
    flop_hand_two(application, se.PLAY)
    assert banner(application) == SKIPPED
    assert application._hand_binding.action == scenario_rules.SKIP


def test_the_skip_is_not_released_when_the_table_only_looks_empty_mid_hand(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    flop_hand_two(application, se.PLAY)
    # The tracker loses the table for a while: a new, card-less generation, the hand not complete.
    lost = life(rl.WAITING, 4, previous={"round_id": 3, "state": rl.IN_PROGRESS, "complete": False,
                                         "result_status": rl.RESULT_NONE, "record": None})
    send(application, "WAITING", se.WAIT, lost, EMPTY, 4)
    assert application._hand_binding.action == scenario_rules.SKIP        # whatever the notice on the banner says
    # The same hand's cards come back under a new id: still that hand, still SKIP.
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 5), FLOP, 5)
    assert banner(application) == SKIPPED
    assert application._hand_binding.action == scenario_rules.SKIP


def test_an_empty_table_id_is_not_the_hand_boundary(application, engine):
    """Generations tick on an empty table; none of them is the hand the SKIP was for."""
    new_session(application)
    play_hand_one_to_betting(application)
    for round_id in (3, 4, 5):
        send(application, "WAITING", se.WAIT, life(rl.WAITING, round_id, previous=finished(1, "Dealer")), EMPTY, round_id)
        assert application._hand_binding.action == scenario_rules.SKIP
    deal_hand_two(application, round_id=5)
    flop_hand_two(application, se.PLAY, round_id=5)
    assert banner(application) == SKIPPED


# -- 3. a confirmed new hand clears it ------------------------------------------------------------------------------------

def finish_hand_two(application, winner, round_id=3):
    send(application, "COMPLETE", se.PLAY,
         life(rl.COMPLETE, round_id, result_status=rl.RESULT_CONFIRMED, result=winner, record=record(winner)), FLOP, round_id)


def test_the_next_pre_round_decision_replaces_the_binding(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    flop_hand_two(application, se.PLAY)
    finish_hand_two(application, "Player")
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 4, previous=finished(3, "Player")), EMPTY, 4)
    assert banner(application) == "ANTE NOW"                   # hand 3 gets its own decision
    assert application._hand_binding.action is None

    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 4), DEAL, 4)
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 4), FLOP, 4)
    assert banner(application) == "LEANING PLAY"               # nothing is held over from hand 2's SKIP


def test_a_new_skip_binds_to_the_new_hand(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    finish_hand_two(application, "Dealer")
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 4, previous=finished(3, "Dealer")), EMPTY, 4)
    assert banner(application) == SKIPPED
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 4), FLOP, 4)
    assert banner(application) == SKIPPED
    assert application._hand_binding.action == scenario_rules.SKIP


def test_a_hand_that_never_completes_does_not_carry_its_skip_into_the_next_hand(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    flop_hand_two(application, se.PLAY)                                    # hand 2 is abandoned: never COMPLETE
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    assert banner(application) != SKIPPED                                  # the old SKIP is not shown for the new pair ...
    assert not application.decision_banner.visible
    assert application._hand_binding.action == scenario_rules.SKIP         # ... but one poll is not proof: still bound
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    assert application._hand_binding.action is None                        # confirmed on the second poll: a new hand
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 7), FLOP_B, 7)
    assert banner(application) == "LEANING PLAY"


def test_a_new_hand_after_a_completed_one_is_released_by_the_same_rule(application, engine):
    """No result was confirmed, so no ANTE / SKIP was issued - only the confirmed new deal ends the binding."""
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    send(application, "COMPLETE", se.PLAY, life(rl.COMPLETE, 3), FLOP, 3)
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    assert application._hand_binding.action == scenario_rules.SKIP
    assert banner(application) != SKIPPED
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 8), DEAL_B, 8)
    assert application._hand_binding.action is None
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 8), FLOP_B, 8)
    assert banner(application) == "LEANING PLAY"


def test_a_different_pair_on_one_poll_then_the_old_pair_again_is_the_same_hand(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 5), DEAL_B, 5)      # a misread, once
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 5), FLOP, 5)               # the real pair, under the new id
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 5), DEAL_B, 5)     # misread again, not consecutive
    assert application._hand_binding.action == scenario_rules.SKIP
    assert banner(application) == SKIPPED


def test_a_mid_hand_reset_that_rereads_the_same_pair_keeps_the_binding(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    flop_hand_two(application, se.PLAY)
    for round_id in (5, 6):                                                # the memory clears and starts over, twice
        send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, round_id), DEAL, round_id)
        assert banner(application) == SKIPPED
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 6), FLOP, 6)
    assert banner(application) == SKIPPED


def test_a_partly_read_pair_proves_nothing_either_way(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    for _ in range(4):                                                     # one card of the pair, under a new id
        send(application, "WAITING", se.WAIT, life(rl.IN_PROGRESS, 9), HALF, 9)
    assert application._hand_binding.action == scenario_rules.SKIP
    assert banner(application) == SKIPPED
    for _ in range(2):                                                     # then the full, different pair
        send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 9), DEAL_B, 9)
    assert application._hand_binding.action is None


def test_a_shared_card_is_not_a_new_deal(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    for _ in range(3):
        send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 9), DEAL_SHARED, 9)
    assert application._hand_binding.action == scenario_rules.SKIP         # CardMemory needs BOTH cards to differ


def test_pause_and_resume_keep_the_binding_and_do_not_repeat_the_voice(application, engine):
    import window_focus
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    flop_hand_two(application, se.PLAY)
    before = decisions_said(application, engine)
    application._show_focus(window_focus.GAME_INACTIVE)
    assert application._hand_binding.action == scenario_rules.SKIP
    assert banner(application) == ""                                       # the banner comes down while paused
    application._show_focus(window_focus.GAME_ACTIVE)
    flop_hand_two(application, se.PLAY)
    assert banner(application) == SKIPPED                                  # and the hand's SKIP, not the engine's PLAY, returns
    assert decisions_said(application, engine) == before                   # nothing new said


def test_stopping_and_restarting_forgets_the_binding(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    assert application._hand_binding.action == scenario_rules.SKIP
    base.end(application)
    assert application._hand_binding.action is None
    base.begin(application)
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 3), FLOP, 3)
    assert banner(application) == "LEANING PLAY"


# -- 4. one value for the banner and the voice --------------------------------------------------------------------------------

def test_the_banner_and_the_voice_get_the_same_decision(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    for decision in (se.PLAY, se.WAIT, se.DONT_PLAY, se.PLAY):
        flop_hand_two(application, decision)
    assert banner(application) == SKIPPED
    # Hand 1's own WAIT, then the SKIP once with the alert - and nothing for hand 2's engine decisions.
    assert decisions_said(application, engine) == [spoken(se.WAIT), spoken(scenario_rules.SKIP)]


# -- 5. what must not change ----------------------------------------------------------------------------------------------

def test_ante_now_still_wins_the_banner_and_is_spoken(application, engine):
    new_session(application)
    play_hand_one_to_betting(application, winner="Player")
    assert banner(application) == "ANTE NOW"
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 3, previous=finished(1, "Player")), EMPTY, 3)   # the WAIT that follows
    assert banner(application) == "ANTE NOW"
    assert application._hand_binding.action is None
    assert decisions_said(application, engine) == [spoken(se.WAIT), spoken(scenario_rules.ANTE)]   # hand 1's WAIT, then the ANTE


def test_the_skip_governs_through_complete_and_the_confirmed_result(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    flop_hand_two(application, se.PLAY)
    reason = application.decision_banner.reason_var
    send(application, "COMPLETE", se.PLAY, life(rl.COMPLETE, 3), FLOP, 3)
    assert banner(application) == SKIPPED
    assert "result not yet confirmed" in reason.get()
    finish_hand_two(application, "Player")
    assert banner(application) == SKIPPED                       # no PLAY NOW, no RESULT PENDING
    assert "Round complete, player won (confirmed by the result panel)" in reason.get()
    said = decisions_said(application, engine)
    assert said == [spoken(se.WAIT), spoken(scenario_rules.SKIP)]          # never a PLAY, leaning or final


def test_a_conflicting_result_is_reported_beside_the_skip(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    send(application, "COMPLETE", se.PLAY, life(rl.COMPLETE, 3, result_status=rl.RESULT_CONFLICT), FLOP, 3)
    assert banner(application) == SKIPPED
    assert "Unable to determine result" in application.decision_banner.reason_var.get()


def test_without_a_skip_the_provisional_decision_is_shown_as_before(application, engine):
    new_session(application)
    play_hand_one_to_betting(application, winner="Player")
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 3), DEAL, 3)
    flop_hand_two(application, se.PLAY)
    assert banner(application) == "LEANING PLAY"
    assert "Leaning play" in decisions_said(application, engine)


# =====================================================================================================================
# The binding's edges: a result notice about the bound hand, a new pair that is not yet confirmed, the 25 s alert
# timeout before the deal, and the person dismissing the banner.
#
# Three kinds of output are kept apart in these tests:
#   * the bound SKIP         - the decision for the bound hand
#   * a notice               - RESULT PENDING / UNKNOWN: a status about that hand's result, never a decision
#   * the next hand's output - a fresh ANTE / SKIP alert, then the engine's own decisions
# and three channels are checked: what the banner shows, the [DECISION] lines the window logs, and the phrases handed to the
# speech engine's recorder. That last one proves what the voice was told to say - not that anything was audible.
# =====================================================================================================================

import logging                                                      # noqa: E402
import re                                                           # noqa: E402
import types                                                        # noqa: E402

import ui.ante_alert as ante_alert_module                           # noqa: E402

INCOMPLETE = {"round_id": 3, "state": rl.IN_PROGRESS, "complete": False, "result_status": rl.RESULT_NONE, "record": None}
UNCONFIRMED = {"round_id": 3, "state": rl.COMPLETE, "complete": True, "result_status": rl.RESULT_UNCONFIRMED,
               "result": None, "record": record("Dealer")}


@pytest.fixture
def clock(monkeypatch):
    """The ANTE alert's own clock, so its 25 s timeout can be crossed without waiting."""
    now = [1000.0]
    monkeypatch.setattr(ante_alert_module, "time", types.SimpleNamespace(time=lambda: now[0]))
    return now


class Spy:
    """What the window handed to the banner, the voice and the one decision path since `reset()`."""

    def __init__(self, application, monkeypatch):
        self.published, self.announced, self.shown = [], [], []
        publish, announce, show = application._publish_decision, application.announcer.announce, application.decision_banner.show

        def wrapped_publish(decision, *args, **kwargs):
            self.published.append((decision, (application.latest_payload or {}).get("state")))
            return publish(decision, *args, **kwargs)

        def wrapped_announce(*args, **kwargs):
            if kwargs.get("topic") == "decision":                   # the cards and the hand are announced too, on their own
                self.announced.append(args)
            return announce(*args, **kwargs)

        def wrapped_show(decision, *args, **kwargs):
            self.shown.append(decision)
            return show(decision, *args, **kwargs)

        monkeypatch.setattr(application, "_publish_decision", wrapped_publish)
        monkeypatch.setattr(application.announcer, "announce", wrapped_announce)
        monkeypatch.setattr(application.decision_banner, "show", wrapped_show)

    def reset(self):
        self.published.clear()
        self.announced.clear()
        self.shown.clear()

    def nothing(self):
        return not (self.published or self.announced or self.shown)


def logged(caplog, start=0):
    """The decisions the window logged ([DECISION] canonical=...) from record number `start` on."""
    return [m.group(1) for m in (re.search(r"\[DECISION\] canonical=(\S+)", text) for text in caplog.messages[start:]) if m]


def mark(caplog):
    return len(caplog.messages)


def hidden(application):
    application.root.update()
    return not application.decision_banner.visible and application.decision_banner.text == ""


def bound_hand_in_flop(application):
    new_session(application)
    play_hand_one_to_betting(application)
    deal_hand_two(application)
    flop_hand_two(application, se.PLAY)
    assert banner(application) == SKIPPED


def end_unfinished(application, previous=INCOMPLETE, round_id=4):
    """The bound hand's table clears before it completed: the alert says the round is not confirmed finished."""
    send(application, "WAITING", se.WAIT, life(rl.WAITING, round_id, previous=previous), EMPTY, round_id)


# -- the binding on its own: its states and transitions ---------------------------------------------------------------------

def test_the_binding_states_and_transitions():
    binding = rl.HandBinding(confirm_frames=2)
    pair_a, pair_b = {"player_1": "AS", "player_2": "7D"}, {"player_1": "QH", "player_2": "3S"}
    assert binding.observe(life(rl.WAITING, 1)) is False                     # nothing bound: nothing governs
    binding.bind(scenario_rules.SKIP, "r", life(rl.WAITING, 2, previous=finished(1, "Dealer")))
    assert binding.observe(life(rl.WAITING, 3, previous=finished(1, "Dealer"))) is True       # PREDEAL governs
    assert binding.observe(life(rl.IN_PROGRESS, 3), pair_a) is True                           # INHAND
    binding.notice()                                                                          # a notice about the hand
    assert binding.dormant and binding.observe(life(rl.WAITING, 4, previous=INCOMPLETE)) is False
    assert binding.observe(life(rl.IN_PROGRESS, 5), pair_a) is True and not binding.dormant   # the same hand returns
    binding.notice()
    assert binding.observe(life(rl.IN_PROGRESS, 6), {"player_1": "AS"}) is True               # partly read: it stands
    binding.notice()
    assert binding.observe(life(rl.IN_PROGRESS, 7), pair_b) is False and binding.holding      # different pair, once
    assert binding.action == scenario_rules.SKIP
    assert binding.observe(life(rl.IN_PROGRESS, 7), pair_a) is True and not binding.holding   # the pair reverted
    binding.notice()
    binding.observe(life(rl.IN_PROGRESS, 8), pair_b)
    assert binding.observe(life(rl.IN_PROGRESS, 8), pair_b) is False and binding.action is None   # confirmed: released


def test_governing_and_holding_are_never_both_true():
    import itertools
    cards = [None, {"player_1": "AS"}, {"player_1": "AS", "player_2": "7D"}, {"player_1": "QH", "player_2": "3S"}]
    updates = [(life(rl.WAITING, 9, previous=INCOMPLETE), None)] + [
        (life(state, rid), pair) for state in (rl.IN_PROGRESS, rl.COMPLETE) for rid in (3, 8) for pair in cards]
    for steps in itertools.product(range(len(updates)), repeat=4):
        for notice_at in (None, 1, 2):
            binding = rl.HandBinding(confirm_frames=2)
            binding.bind(scenario_rules.SKIP, "r", life(rl.WAITING, 2, previous=finished(1, "Dealer")))
            for n, index in enumerate(steps):
                if notice_at == n:
                    binding.notice()
                lifecycle, pair = updates[index]
                binding.observe(lifecycle, pair)
                assert not (binding.governing and binding.holding)
                if binding.holding:
                    assert binding.action is not None


# -- a notice about the bound hand, then a confirmed different pair --------------------------------------------------------

def run_notice_to_new_pair(application, monkeypatch, caplog, engine, clock, row):
    """Poll by poll: the first different pair, the confirming poll, and the next hand's own decision."""
    caplog.set_level(logging.INFO)
    bound_hand_in_flop(application)
    notice = {"R1": "RESULT_PENDING", "R2": "RESULT_UNKNOWN", "R3": "RESULT_PENDING", "R4": "RESULT_PENDING",
              "R5": "RESULT_UNKNOWN", "R7": "RESULT_PENDING"}.get(row)
    if row in ("R3", "R5"):
        application.decision_banner._clicked()                      # the SKIP is dismissed before the notice
        assert application._hand_binding.dismissed
    if notice:
        end_unfinished(application)
        assert banner(application) == "RESULT PENDING" and application._hand_binding.dormant
        if notice == "RESULT_UNKNOWN":
            clock[0] += 30.0
            end_unfinished(application)
            assert banner(application) == "RESULT UNKNOWN"
        if row in ("R4", "R5"):
            application.decision_banner._clicked()                  # the notice is dismissed (it is not the bound decision)
            assert hidden(application)
    if row == "R6":
        application.decision_banner.hide()                          # hidden on screen, still SKIP inside the banner
        assert application.decision_banner.current.decision == scenario_rules.SKIP
    if row == "R8":
        application.decision_banner.current._decision, application.decision_banner.current._round_id = se.WAIT, 7
    spy = Spy(application, monkeypatch)
    before_voice, start = list(decisions_said(application, engine)), mark(caplog)

    # poll 1: a full, different pair - not yet confirmed
    raw = EMPTY if row == "R7" else DEAL_B                          # R7: the raw frame has not caught up with the cards
    send(application, "PLAYER_CARDS", se.PLAY if row == "R2" else se.WAIT, life(rl.IN_PROGRESS, 7), raw, 7, cards=DEAL_B)
    assert hidden(application)                                      # banner: no notice, no old SKIP
    assert spy.nothing()                                            # nothing published, announced or drawn
    assert logged(caplog, start) == []                              # log: no [DECISION] line
    assert decisions_said(application, engine) == before_voice      # voice: nothing handed to the engine
    assert application._hand_binding.holding and not application._hand_binding.governing

    # poll 2: the confirming poll - released, and the engine's decision for the new hand
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    assert application._hand_binding.action is None
    assert banner(application) == "WAIT"
    assert logged(caplog, start) == ["WAIT"]                        # one decision, the engine's: no skip, no RESULT_*
    assert decisions_said(application, engine) == before_voice + ["Wait"]
    assert application.ante_alert.showing is None

    # after the release: the new hand's decisions are published normally
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 7), FLOP_B, 7)
    assert banner(application) == "LEANING PLAY"
    assert logged(caplog, start) == ["WAIT", "LEANING_PLAY"]
    assert decisions_said(application, engine) == before_voice + ["Wait", "Leaning play"]


@pytest.mark.parametrize("row", ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"])
def test_notice_to_confirmed_different_pair(application, engine, monkeypatch, caplog, clock, row):
    run_notice_to_new_pair(application, monkeypatch, caplog, engine, clock, row)


@pytest.mark.parametrize("dismissed", [False, True])
@pytest.mark.parametrize("internal_only", [False, True])
def test_revert_after_hold_restores_skip_unless_dismissed(application, engine, monkeypatch, caplog, dismissed, internal_only):
    caplog.set_level(logging.INFO)
    bound_hand_in_flop(application)
    if dismissed:
        application.decision_banner._clicked()
    if internal_only:
        application.decision_banner.hide()                          # hidden on screen, while the banner still remembers SKIP
    spy = Spy(application, monkeypatch)
    before_voice, start = list(decisions_said(application, engine)), mark(caplog)
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)                # a different pair, once
    assert hidden(application) and spy.nothing()
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 8), FLOP, 8)                         # the original pair again
    assert application._hand_binding.action == scenario_rules.SKIP and not application._hand_binding.holding
    if dismissed:
        assert hidden(application)                                                                 # still dismissed
    else:
        assert banner(application) == SKIPPED                                                      # restored
    assert set(logged(caplog, start)) <= {"skip"}                                                  # never WAIT / LEANING / PLAY
    assert decisions_said(application, engine) == before_voice                                      # and no new phrase


def test_dismissed_skip_same_hand_returns_after_notice(application, engine, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    bound_hand_in_flop(application)
    application.decision_banner._clicked()                          # the SKIP is dismissed
    end_unfinished(application)                                     # a notice about the hand shows
    assert banner(application) == "RESULT PENDING"
    spy, start = Spy(application, monkeypatch), mark(caplog)
    before_voice = list(decisions_said(application, engine))
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 5), FLOP, 5)                       # the same hand comes back
    assert hidden(application)                                      # the notice is gone and the SKIP stays dismissed
    assert application._hand_binding.action == scenario_rules.SKIP and application._hand_binding.dismissed
    assert not application._hand_binding.dormant
    assert spy.published == [] and logged(caplog, start) == []
    assert decisions_said(application, engine) == before_voice


@pytest.mark.parametrize("known_previous", ["incomplete", "unconfirmed"])
def test_unfinished_hand_with_a_notice_then_a_different_pair(application, engine, monkeypatch, caplog, clock, known_previous):
    """The bound hand never completed (or completed unconfirmed): its notice shows once, then a new pair is a new hand."""
    caplog.set_level(logging.INFO)
    bound_hand_in_flop(application)
    if known_previous == "unconfirmed":
        send(application, "COMPLETE", se.PLAY, life(rl.COMPLETE, 3), FLOP, 3)
    start = mark(caplog)
    end_unfinished(application, previous=INCOMPLETE if known_previous == "incomplete" else UNCONFIRMED)
    assert banner(application) == "RESULT PENDING"
    assert logged(caplog, start) == ["RESULT_PENDING"]
    assert decisions_said(application, engine)[-1] == "Result pending"
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    assert hidden(application)
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    assert banner(application) == "WAIT" and application._hand_binding.action is None
    assert "skip" not in logged(caplog, start)
    said = decisions_said(application, engine)
    assert "Skip round" not in said[said.index("Result pending"):]


# -- the notice and the bound decision, in order -----------------------------------------------------------------------------

def test_a_notice_shows_once_while_the_binding_stands_aside_and_never_on_a_card_poll(application, engine, monkeypatch, caplog, clock):
    caplog.set_level(logging.INFO)
    bound_hand_in_flop(application)
    spy = Spy(application, monkeypatch)
    start = mark(caplog)
    end_unfinished(application)
    assert banner(application) == "RESULT PENDING"
    assert application._hand_binding.dormant and not application._hand_binding.governing
    clock[0] += 30.0
    end_unfinished(application)
    assert banner(application) == "RESULT UNKNOWN"
    clock[0] += 30.0
    end_unfinished(application)                                     # the notice ends: the gap is the next hand's, not the SKIP's
    seen = logged(caplog, start)
    assert seen[:2] == ["RESULT_PENDING", "RESULT_UNKNOWN"] and "skip" not in seen
    assert all(state == "WAITING" for decision, state in spy.published
               if decision in ("RESULT_PENDING", "RESULT_UNKNOWN"))                                # never on a poll with cards


def test_the_same_hand_returning_after_a_table_loss_takes_the_skip_back(application, engine, caplog):
    caplog.set_level(logging.INFO)
    bound_hand_in_flop(application)
    start = mark(caplog)
    voice_before = len(decisions_said(application, engine))
    end_unfinished(application)                                     # the table looks empty, the hand is not complete
    assert banner(application) == "RESULT PENDING"
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 5), FLOP, 5)                       # the same pair returns
    assert banner(application) == SKIPPED                           # the SKIP replaces the notice on the return poll
    assert logged(caplog, start) == ["RESULT_PENDING", "skip"]      # nothing between: no WAIT, no LEANING
    assert decisions_said(application, engine)[voice_before:] == ["Result pending", "Skip round"]
    assert application._hand_binding.action == scenario_rules.SKIP and not application._hand_binding.dormant
    flop_hand_two(application, se.DONT_PLAY, round_id=5)
    assert banner(application) == SKIPPED


@pytest.mark.parametrize("winner,expected", [("Player", "ANTE NOW"), ("Dealer", SKIPPED)])
def test_a_late_confirmation_gives_the_next_hand_its_own_decision(application, engine, clock, winner, expected):
    bound_hand_in_flop(application)
    send(application, "COMPLETE", se.PLAY, life(rl.COMPLETE, 3), FLOP, 3)
    end_unfinished(application, previous=UNCONFIRMED)
    assert banner(application) == "RESULT PENDING" and application._hand_binding.dormant
    end_unfinished(application, previous=finished(3, winner))       # the result panel confirms it late
    assert banner(application) == expected                          # the next hand's decision, from a fresh alert
    assert application._hand_binding.dormant is False
    assert (application._hand_binding.action == scenario_rules.SKIP) is (winner == "Dealer")


# -- the 25 s timeout before the deal, and dismissal -----------------------------------------------------------------------

def test_the_alert_timeout_does_not_replace_the_skip_before_the_deal(application, engine, caplog, clock):
    caplog.set_level(logging.INFO)
    new_session(application)
    play_hand_one_to_betting(application)
    before_voice, start = list(decisions_said(application, engine)), mark(caplog)
    for round_id, step in ((3, 10.0), (4, 20.0), (5, 5.0), (6, 5.0)):     # past 25 s of empty table, round ids changing
        clock[0] += step
        send(application, "WAITING", se.WAIT, life(rl.WAITING, round_id, previous=finished(1, "Dealer")), EMPTY, round_id)
        assert banner(application) == SKIPPED and application.decision_banner.visible
    assert "WAIT" not in logged(caplog, start)
    assert decisions_said(application, engine) == before_voice
    assert application._hand_binding.action == scenario_rules.SKIP
    deal_hand_two(application, round_id=6)
    flop_hand_two(application, se.PLAY, round_id=6)
    assert banner(application) == SKIPPED
    assert decisions_said(application, engine) == [spoken(se.WAIT), spoken(scenario_rules.SKIP)]   # hand 1's WAIT, one SKIP


def test_there_is_no_automatic_expiry_before_the_deal(application, engine, clock):
    new_session(application)
    play_hand_one_to_betting(application)
    clock[0] += 3600.0
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 3, previous=finished(1, "Dealer")), EMPTY, 3)
    assert banner(application) == SKIPPED and application._hand_binding.action == scenario_rules.SKIP


def test_dismissing_the_skip_keeps_it_away_and_still_blocks_every_other_decision(application, engine, monkeypatch, caplog, clock):
    caplog.set_level(logging.INFO)
    new_session(application)
    play_hand_one_to_betting(application)
    application.decision_banner._clicked()
    assert application._hand_binding.dismissed
    spy, start = Spy(application, monkeypatch), mark(caplog)
    voice = list(decisions_said(application, engine))
    clock[0] += 40.0
    for round_id in (3, 4, 5):                                      # the table stays empty, the round id keeps changing
        send(application, "WAITING", se.WAIT, life(rl.WAITING, round_id, previous=finished(1, "Dealer")), EMPTY, round_id)
        assert hidden(application)
    deal_hand_two(application, round_id=5)
    flop_hand_two(application, se.PLAY, round_id=5)
    flop_hand_two(application, se.DONT_PLAY, round_id=5)
    assert hidden(application)                                      # not shown again, and no PLAY took its place
    assert spy.published == [] and logged(caplog, start) == []
    assert decisions_said(application, engine) == voice
    assert application._hand_binding.action == scenario_rules.SKIP and application._hand_binding.dismissed


def test_dismissal_ends_with_the_hand_it_belonged_to(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    application.decision_banner._clicked()
    deal_hand_two(application)
    finish_hand_two(application, "Dealer")                                                      # the next alert is a SKIP again
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 4, previous=finished(3, "Dealer")), EMPTY, 4)
    assert banner(application) == SKIPPED and not application._hand_binding.dismissed


def test_clicking_another_banner_does_not_dismiss_a_binding(application, engine):
    new_session(application)
    play_hand_one_to_betting(application, winner="Player")                                      # ANTE NOW, nothing bound
    assert banner(application) == "ANTE NOW"
    application.decision_banner._clicked()
    assert application._hand_binding.action is None and not application._hand_binding.dismissed


def test_pause_and_resume_before_the_deal_keep_the_skip_and_the_dismissal(application, engine):
    import window_focus
    new_session(application)
    play_hand_one_to_betting(application)
    voice = list(decisions_said(application, engine))
    application._show_focus(window_focus.GAME_INACTIVE)
    assert hidden(application) and application._hand_binding.action == scenario_rules.SKIP
    application._show_focus(window_focus.GAME_ACTIVE)
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 3, previous=finished(1, "Dealer")), EMPTY, 3)
    assert banner(application) == SKIPPED                           # back on resume
    assert decisions_said(application, engine) == voice             # and not said again
    application.decision_banner._clicked()
    application._show_focus(window_focus.GAME_INACTIVE)
    application._show_focus(window_focus.GAME_ACTIVE)
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 4, previous=finished(1, "Dealer")), EMPTY, 4)
    assert hidden(application) and application._hand_binding.dismissed


def test_without_a_binding_the_alert_timeout_still_ends_in_wait(application, engine, clock):
    new_session(application)
    play_hand_one_to_betting(application, winner="Player")
    assert banner(application) == "ANTE NOW"
    clock[0] += 30.0
    send(application, "WAITING", se.WAIT, life(rl.WAITING, 3, previous=finished(1, "Player")), EMPTY, 3)
    assert banner(application) == "WAIT" and application._hand_binding.action is None


# -- stopping, starting and the tracker ending ------------------------------------------------------------------------------

def test_stop_and_start_clear_the_binding_and_the_dismissal(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    application.decision_banner._clicked()
    binding = application._hand_binding
    assert binding.action and binding.dismissed
    base.end(application)
    assert binding.action is None and not binding.dismissed and not binding.holding and not binding.dormant
    base.begin(application)
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 3), FLOP, 3)
    assert banner(application) == "LEANING PLAY"


def test_the_tracker_ending_on_its_own_clears_the_binding_through_stop(application, engine):
    new_session(application)
    play_hand_one_to_betting(application)
    application.decision_banner._clicked()
    application._tracker_seen_running = True
    application.tracker._fake = False                               # the thread has ended without Stop
    application._reconcile_tracking()
    assert not application.tracking
    assert application._hand_binding.action is None and not application._hand_binding.dismissed
    assert rl.HandBinding().action is None                          # and a new window starts with a fresh one


# -- a different pair, step by step: confirmation, interruption, and where it ends ----------------------------------------

def test_a_different_pair_needs_the_configured_number_of_consecutive_polls(application, engine, monkeypatch):
    bound_hand_in_flop(application)
    application._hand_binding.confirm_frames = 3
    spy = Spy(application, monkeypatch)
    for _ in range(2):
        send(application, "PLAYER_CARDS", se.PLAY, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
        assert hidden(application) and spy.nothing() and application._hand_binding.holding
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    assert application._hand_binding.action is None and banner(application) == "WAIT"


def test_a_different_pair_that_is_interrupted_does_not_release(application, engine):
    bound_hand_in_flop(application)
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 7), DEAL_B, 7)
    send(application, "FLOP", se.PLAY, life(rl.IN_PROGRESS, 8), FLOP, 8)                       # the original pair between
    send(application, "PLAYER_CARDS", se.WAIT, life(rl.IN_PROGRESS, 9), DEAL_B, 9)
    assert application._hand_binding.action == scenario_rules.SKIP and application._hand_binding.holding
