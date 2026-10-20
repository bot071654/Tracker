"""The Teach / Correct Scenario window must say which decision is which.

THE REPORT (round 47)

    Player J♥ 6♣   Flop 6♥ 9♣ K♣   Turn K♥   River 2♦   Hand Type: Pair
    Current Decision: FOLD      (the Scenario Engine says PLAY)
    Matched Rule: If Pair with no draw, fold      Rule Priority: #3

The two answers were never computed inconsistently. They come from two different lists, on purpose (docs/SCENARIOS.md, section 1):

  * the green banner and the SCENARIO panel show the Scenario Engine's decision, which is PLAY whenever any structural scenario
    matches - here COMBINED_PAIR: the 6 in the hole and the 6 on the flop;
  * the saved flop rules (config/scenarios.json, including rules taught with this window) are not displayed, and are what Teach and
    the backtest reason about - here rule #3, "Pair with no draw -> fold".

The fault was the labelling: the window called the saved rule's answer "Current Decision" and described the cards as "the decision
that was on screen", which was true only in an earlier version when the tracker displayed those rules too. These tests pin the
corrected wording for this exact hand and for the case where the two agree. They do not change either list or any rule priority.
"""

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenario_engine as se        # noqa: E402
from poker import scenarios as sr              # noqa: E402
from poker import teaching                     # noqa: E402
from ui import rule_windows                    # noqa: E402

CARDS = {"player_1": "JH", "player_2": "6C", "flop_1": "6H", "flop_2": "9C", "flop_3": "KC", "turn": "KH", "river": "2D"}

RULES = {
    "preround": {"default": sr.ANTE, "rules": []},
    "flop": {"default": sr.PLAY, "rules": [
        sr.flop_rule("Any", "the flop is paired", sr.PLAY, name="If the flop is paired, play on"),
        sr.flop_rule("High Card", "one of my two cards is A, K or Q", sr.PLAY,
                     name="If the hand is High Card and one of my two cards is A, K or Q, play on"),
        sr.flop_rule("Pair", "no draw", sr.FOLD, name="If Pair with no draw, fold"),
    ]},
}


def engine_result(cards=CARDS):
    return se.detect_player_decision([cards["player_1"], cards["player_2"]],
                                     [cards["flop_1"], cards["flop_2"], cards["flop_3"]])


def payload(cards=CARDS, round_id=47):
    engine = engine_result(cards)
    return {"state": "COMPLETE", "round_id": round_id, "cards": dict(cards),
            "statuses": {slot: "CONFIRMED" for slot in cards},
            "scenario": {"decision": engine["decision"], "reason": engine["reason"],
                         "matched_scenarios": engine["matched_scenarios"], "round_id": round_id}}


@pytest.fixture
def root():
    import time
    window, error = None, None
    for _ in range(6):                                           # Tk here occasionally fails to start; see the lifecycle tests
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


def labels(widget):
    found = []
    for child in widget.winfo_children():
        try:
            found.append(str(child.cget("text")))
        except tk.TclError:
            pass
        found.extend(labels(child))
    return found


def open_window(root, tmp_path, rules, cards=CARDS):
    taken, why = teaching.capture(payload(cards), rules, session=1)
    assert taken is not None, why
    return taken, rule_windows.TeachScenarioWindow(root, taken, rules=rules, load_rows=lambda: [],
                                                   save_path=str(tmp_path / "scenarios.json"))


# -- the two answers, for the reported hand -----------------------------------------------------------------------

def test_the_engine_says_play_for_the_reported_hand_because_of_the_pair_with_the_flop():
    result = engine_result()
    assert result["decision"] == se.PLAY
    assert se.COMBINED_PAIR in result["matched_scenarios"]
    assert result["detected_hand"] == "PAIR"                     # the engine's key; the saved rules call it "Pair"


def test_the_saved_rules_say_fold_for_the_same_hand_through_rule_three(root, tmp_path):
    taken, made = open_window(root, tmp_path, RULES)
    try:
        assert taken.hand == "Pair"
        assert taken.action == sr.FOLD
        assert taken.matched_name() == "If Pair with no draw, fold"
        assert taken.priority() == "#3"
        assert taken.engine_decision == se.PLAY
    finally:
        made.destroy()


def test_both_answers_are_computed_from_the_same_five_cards_and_the_same_round(root, tmp_path):
    taken, made = open_window(root, tmp_path, RULES)
    try:
        assert taken.round_id == 47
        assert [taken.cards[s] for s in ("player_1", "player_2")] == ["JH", "6C"]
        assert [taken.cards[s] for s in ("flop_1", "flop_2", "flop_3")] == ["6H", "9C", "KC"]
        assert taken.engine["matched_scenarios"] == engine_result()["matched_scenarios"]
    finally:
        made.destroy()


# -- what the window now says --------------------------------------------------------------------------------------

def test_the_window_names_the_banner_and_the_saved_rule_separately(root, tmp_path):
    taken, made = open_window(root, tmp_path, RULES)
    try:
        root.update()
        shown = labels(made)
        assert "On screen (banner):" in shown and "PLAY NOW" in shown          # what the green banner said
        assert "Saved flop rule:" in shown and "FOLD" in shown                 # what the saved rules say
        assert "Current Decision:" not in shown                                # the label that claimed the rule was on screen
        assert "Matched Rule:" in shown and "If Pair with no draw, fold" in shown
        assert "Rule Priority:" in shown and "#3" in shown
    finally:
        made.destroy()


def test_the_window_explains_the_disagreement_and_what_a_correction_changes(root, tmp_path):
    taken, made = open_window(root, tmp_path, RULES)
    try:
        root.update()
        notice = [text for text in labels(made) if text == rule_windows.DISAGREEMENT_NOTICE]
        assert notice, "no explanation of the disagreement"
        assert "by design" in notice[0] and "not the banner" in notice[0]
        intro = " ".join(labels(made))
        assert "green banner shows the Scenario Engine" in intro
        assert "decision below is the one that was on screen" not in intro      # the claim that was not true
    finally:
        made.destroy()


def test_no_disagreement_notice_when_the_two_agree(root, tmp_path):
    agreeing = {"preround": RULES["preround"], "flop": {"default": sr.PLAY, "rules": []}}
    taken, made = open_window(root, tmp_path, agreeing)
    try:
        root.update()
        assert taken.action == sr.PLAY and taken.engine_decision == se.PLAY
        assert rule_windows.DISAGREEMENT_NOTICE not in labels(made)
        assert "PLAY NOW" in labels(made)
    finally:
        made.destroy()


def test_dont_play_is_shown_as_the_banner_draws_it(root, tmp_path):
    cards = {"player_1": "2D", "player_2": "5C", "flop_1": "9H", "flop_2": "8S", "flop_3": "7D", "turn": "QH", "river": "3H"}
    taken, made = open_window(root, tmp_path, RULES, cards)
    try:
        root.update()
        assert taken.engine_decision == se.DONT_PLAY
        assert "DON'T PLAY" in labels(made)
    finally:
        made.destroy()


def test_nothing_about_either_decision_changed(root, tmp_path):
    """The window only labels: both lists and the rule order are exactly what they were."""
    before = [rule["name"] for rule in RULES["flop"]["rules"]]
    taken, made = open_window(root, tmp_path, RULES)
    try:
        assert [rule["name"] for rule in RULES["flop"]["rules"]] == before
        assert not (tmp_path / "scenarios.json").exists()               # nothing is written before Activate
    finally:
        made.destroy()
