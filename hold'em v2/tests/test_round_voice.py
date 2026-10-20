"""What is said, sounded and written when a round is over (round_lifecycle.py + app.py).

THE FINDING

A completed round still spoke its decision - "Play now" - as though there were something left to do, and the result was spoken
as soon as the hand was saved whether or not the casino's panel agreed. And the Winner row could name a winner next to a red
SOURCE CONFLICT.

Now, with the design chosen for the lifecycle: in progress the decision is provisional and is spoken as such ("Leaning play");
once the round is complete the decision is neither sounded nor spoken as an instruction; an unconfirmed result is spoken as
"Result pending", a contradicted one as "Unable to determine result"; and the winner is spoken only after the panel has
confirmed it, once. The Winner row never shows a winner while it is in conflict, and says "unconfirmed" while it is only the
table's own calculation.
"""

import pytest

tk = pytest.importorskip("tkinter")

import round_lifecycle as rl                                    # noqa: E402
from poker import scenario_engine as se                         # noqa: E402

from test_decision_voice import (application, engine, said,       # noqa: E402,F401 - fixtures
                                 stub_everything_but_the_voice)
from test_round_lifecycle import FULL, panel_snapshot, root, window_payload   # noqa: E402,F401

IN_PROGRESS = {"state": rl.IN_PROGRESS, "result_status": rl.RESULT_NONE, "previous": None}


def complete(status, winner="Dealer", fingerprint="fp-1", record=None):
    record = record if record is not None else {"winner": winner, "player_hand": "Pair", "dealer_hand": "Pair",
                                                "dealer_qualified": True, "hand_fingerprint": fingerprint}
    return {"state": rl.COMPLETE, "complete": True, "result_status": status, "round_id": 1, "fingerprint": fingerprint,
            "result": winner if status == rl.RESULT_CONFIRMED else None, "table_result": winner, "record": record,
            "result_source": rl.SOURCE_PANEL if status == rl.RESULT_CONFIRMED else rl.SOURCE_TABLE, "previous": None}


def push(app, state, decision, lifecycle, round_id=1, **kw):
    app.events.put(("update", window_payload(state, decision, lifecycle, round_id=round_id, **kw)))
    app._poll_events()
    app.root.update()


def start(app):
    app.start()
    app.tracker._fake = True
    app.root.update()


# -- what is said ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("decision,phrase", [(se.PLAY, "Leaning play"), (se.DONT_PLAY, "Leaning don't play")])
def test_a_decision_during_the_round_is_spoken_as_provisional(application, engine, decision, phrase):
    start(application)
    push(application, "FLOP", decision, IN_PROGRESS)
    push(application, "FLOP", decision, IN_PROGRESS)
    assert said(application, engine) == [phrase]                              # once, however many polls


def test_an_unconfirmed_result_is_spoken_as_pending(application, engine):
    start(application)
    push(application, "COMPLETE", se.PLAY, complete(rl.RESULT_UNCONFIRMED))
    push(application, "COMPLETE", se.PLAY, complete(rl.RESULT_UNCONFIRMED))
    assert said(application, engine) == ["Result pending"]


def test_a_contradicted_result_is_spoken_as_undeterminable_never_as_a_winner(application, engine):
    start(application)
    push(application, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFLICT))
    assert said(application, engine) == ["Unable to determine result"]


@pytest.mark.parametrize("decision", [se.PLAY, se.DONT_PLAY])
def test_a_finished_round_never_says_play_now_or_dont_play(application, engine, decision):
    """flop -> panel not yet there -> panel confirms: the instruction is never given once the round is over."""
    start(application)
    push(application, "FLOP", decision, IN_PROGRESS)
    push(application, "COMPLETE", decision, complete(rl.RESULT_UNCONFIRMED))
    push(application, "COMPLETE", decision, complete(rl.RESULT_CONFIRMED))
    push(application, "COMPLETE", decision, complete(rl.RESULT_CONFIRMED))
    phrases = said(application, engine)
    assert phrases[:2] == ["Leaning play" if decision == se.PLAY else "Leaning don't play", "Result pending"]
    assert "Play now" not in phrases and "Don't play" not in phrases
    assert phrases == phrases[:2]                                             # and nothing at all for the final


def test_the_final_decision_is_still_shown_on_the_banner_just_not_sounded_or_spoken(application, engine, monkeypatch):
    sounds = []
    import ui.ante_alert as ante_module
    monkeypatch.setattr(ante_module, "play_sound", lambda decision: sounds.append(decision))
    start(application)
    application.decision_banner.sound = True
    push(application, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFIRMED))
    assert application.decision_banner.text == "PLAY NOW"
    assert sounds == [] and said(application, engine) == []


def test_an_update_without_a_lifecycle_is_spoken_and_sounded_as_before(application, engine, monkeypatch):
    sounds = []
    import ui.ante_alert as ante_module
    monkeypatch.setattr(ante_module, "play_sound", lambda decision: sounds.append(decision))
    start(application)
    application.decision_banner.sound = True
    push(application, "FLOP", se.PLAY, None)
    assert said(application, engine) == ["Play now"] and sounds == [se.PLAY]


def test_the_next_round_starts_with_its_own_wording_after_a_finished_one(application, engine):
    start(application)
    push(application, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFIRMED))
    push(application, "FLOP", se.PLAY, IN_PROGRESS, round_id=2)
    assert said(application, engine) == ["Leaning play"]


# -- the winner is spoken only once the panel confirmed it ----------------------------------------------------------------

@pytest.fixture
def voice_with_cards(root, engine, monkeypatch):
    app_module = stub_everything_but_the_voice(monkeypatch)
    real_load = app_module.load_config

    def with_cards():
        config = dict(real_load())
        config["voice_announce_cards"] = True
        return config

    monkeypatch.setattr(app_module, "load_config", with_cards)
    made = app_module.App(root)
    root.update()
    try:
        yield made
    finally:
        made.tracker._fake = False
        made.on_close()


def winner_phrases(app, engine):
    return [phrase for phrase in said(app, engine) if "win" in phrase.lower() or phrase.lower().startswith("tie")]


def test_the_winner_is_not_spoken_when_the_hand_is_saved(voice_with_cards, engine):
    start(voice_with_cards)
    record = {"winner": "Dealer", "player_hand": "Pair", "dealer_hand": "Flush", "dealer_qualified": True,
              "hand_fingerprint": "fp-1", "player_1": "9H"}
    voice_with_cards._show_saved({"record": record, "row": {"id": 1}, "excel_error": None})
    assert winner_phrases(voice_with_cards, engine) == []


def test_the_winner_is_spoken_once_after_the_panel_confirms_it_and_not_before(voice_with_cards, engine):
    start(voice_with_cards)
    for _ in range(3):
        push(voice_with_cards, "COMPLETE", se.PLAY, complete(rl.RESULT_UNCONFIRMED), seen=dict(FULL))
    assert winner_phrases(voice_with_cards, engine) == []
    for _ in range(3):
        push(voice_with_cards, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFIRMED, winner="Dealer"), seen=dict(FULL))
    assert len(winner_phrases(voice_with_cards, engine)) == 1
    assert "Dealer" in winner_phrases(voice_with_cards, engine)[0]


def test_a_contradicted_result_never_speaks_a_winner(voice_with_cards, engine):
    start(voice_with_cards)
    for _ in range(3):
        push(voice_with_cards, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFLICT), seen=dict(FULL))
    assert winner_phrases(voice_with_cards, engine) == []


def test_a_conflict_that_clears_to_a_confirmed_result_speaks_that_winner_once(voice_with_cards, engine):
    start(voice_with_cards)
    push(voice_with_cards, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFLICT), seen=dict(FULL))
    push(voice_with_cards, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFIRMED, winner="Player"), seen=dict(FULL))
    push(voice_with_cards, "COMPLETE", se.PLAY, complete(rl.RESULT_CONFIRMED, winner="Player"), seen=dict(FULL))
    assert len(winner_phrases(voice_with_cards, engine)) == 1


# -- the Winner row -------------------------------------------------------------------------------------------------------------

def winner_row(app, lifecycle, mode):
    panels = panel_snapshot(mode, FULL, 1)
    app.events.put(("update", dict(window_payload("COMPLETE", se.PLAY, lifecycle, seen=dict(FULL)), panels=panels)))
    app._poll_events()
    app.root.update()
    return app.winner_var.get(), app.verification_var.get()


def test_the_winner_row_never_shows_a_winner_next_to_a_source_conflict(application):
    start(application)
    winner, verification = winner_row(application, complete(rl.RESULT_CONFLICT), "disagree")
    assert "SOURCE CONFLICT" in verification
    assert "wins" not in winner and "Tie" not in winner
    assert "Unable to determine" in winner


def test_the_winner_row_says_unconfirmed_while_it_is_only_the_tables_calculation(application):
    start(application)
    winner, _ = winner_row(application, complete(rl.RESULT_UNCONFIRMED), "missing")
    assert "(table)" in winner and winner.endswith("unconfirmed")


def test_the_winner_row_is_plain_once_the_panel_has_confirmed(application):
    start(application)
    winner, verification = winner_row(application, complete(rl.RESULT_CONFIRMED), "agree")
    assert "wins" in winner and "unconfirmed" not in winner and "Unable" not in winner
    assert "SOURCE CONFLICT" not in verification


def test_the_winner_row_does_not_pick_a_source_when_table_and_panel_name_different_winners():
    from poker.hand_record import hands_so_far
    progress = hands_so_far(FULL)
    other = "Player" if progress["winner"] != "Player" else "Dealer"
    text = rl.winner_override(complete(rl.RESULT_UNCONFIRMED), {"verdicts": {}}, progress, {"winner": other})
    assert text and progress["winner"] in text and other in text
    assert rl.winner_override(complete(rl.RESULT_UNCONFIRMED), {"verdicts": {}}, progress, {"winner": progress["winner"]}) is None
    assert rl.winner_override(None, {"verdicts": {"dealer": ("conflict", "x")}}, progress, {}) is not None


# -- the real window's wiring of the once-per-round protection --------------------------------------------------------------

def finished(round_id=1, status=rl.RESULT_CONFIRMED, winner="Dealer"):
    record = {"hand_fingerprint": "fp-%s" % round_id, "winner": winner, "player_hand": "Pair", "dealer_hand": "Pair"}
    return {"round_id": round_id, "state": rl.COMPLETE, "complete": True, "result_status": status, "record": record,
            "result": winner if status == rl.RESULT_CONFIRMED else None}


def test_the_window_gives_the_same_finished_hand_one_alert_even_after_a_stray_card_read(application):
    """The review's exact sequence, through App._update_ante_alert rather than the alert logic alone."""
    from poker import scenarios as scenario_rules
    import test_banner_lifecycle as base
    application.scenarios = {"preround": {"default": scenario_rules.ANTE, "rules": [
        {"name": "skip after a dealer win", "action": scenario_rules.SKIP, "previous_winner": "Dealer"}]},
        "flop": {"default": "play", "rules": []}}
    start(application)
    application.ante_alert.timeout = 0.0                                       # the banner is gone by the next poll
    cards = {slot: base.CARDS.get(slot) for slot in base.CARDS}
    empty = {slot: None for slot in cards}
    after = {"state": rl.WAITING, "result_status": rl.RESULT_NONE, "previous": finished()}
    push(application, "FLOP", se.WAIT, IN_PROGRESS, round_id=1, seen=dict(cards))
    push(application, "WAITING", se.WAIT, after, round_id=2, seen=dict(empty))
    assert application.ante_alert.alerts == 1 and application.decision_banner.text == "SKIP ROUND"
    push(application, "WAITING", se.WAIT, after, round_id=2, seen=dict(empty))        # (timed out here)
    stray = dict(empty, player_1="2C")
    push(application, "WAITING", se.WAIT, after, round_id=3, seen=stray)             # a stray single card read
    for round_id in (3, 4, 5):
        push(application, "WAITING", se.WAIT, after, round_id=round_id, seen=dict(empty))
    assert application.ante_alert.alerts == 1


def test_the_window_gives_a_hand_with_no_confirmed_result_no_suggestion_and_one_notice(application):
    from poker import scenarios as scenario_rules
    import test_banner_lifecycle as base
    application.scenarios = {"preround": {"default": scenario_rules.ANTE, "rules": []}, "flop": {"default": "play", "rules": []}}
    start(application)
    cards = {slot: base.CARDS.get(slot) for slot in base.CARDS}
    empty = {slot: None for slot in cards}
    after = {"state": rl.WAITING, "result_status": rl.RESULT_NONE, "previous": finished(status=rl.RESULT_UNCONFIRMED)}
    push(application, "FLOP", se.WAIT, IN_PROGRESS, round_id=1, seen=dict(cards))
    push(application, "WAITING", se.WAIT, after, round_id=2, seen=dict(empty))
    assert application.decision_banner.text == "RESULT PENDING" and application.ante_alert.alerts == 0
    for round_id in (2, 3, 4):
        push(application, "WAITING", se.WAIT, after, round_id=round_id, seen=dict(empty))
    assert application.ante_alert.alerts == 0 and application.decision_banner.text == "RESULT PENDING"


# -- the window while the panel has moved on to the next deal ---------------------------------------------------------------

def test_a_confirmed_round_shows_its_winner_and_not_a_source_conflict_when_the_next_deal_reaches_the_panel(application):
    start(application)
    lifecycle = dict(complete(rl.RESULT_CONFIRMED, winner="Dealer"), panel_conflict_ignored=True)
    winner, verification = winner_row(application, lifecycle, "next_deal")
    assert "wins" in winner and "Unable" not in winner
    assert "SOURCE CONFLICT" not in verification and "next deal" in verification


def test_an_unconfirmed_round_with_a_conflicting_partial_row_shows_no_winner(application):
    start(application)
    winner, verification = winner_row(application, complete(rl.RESULT_UNCONFIRMED), "next_deal")
    assert "SOURCE CONFLICT" in verification and "Unable to determine" in winner and "wins" not in winner
