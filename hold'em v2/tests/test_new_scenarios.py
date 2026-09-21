"""The scenarios added to the engine, each checked against sample hands.

Three about the round that just finished:

    1  the dealer won it            -> skip this round
    2  the player won it            -> play (ante)
    3  the player won the last two  -> increase bet and play

and two about the flop:

    4  the flop is paired                       -> play on
    5  High Card hand, ace/king/queen high      -> play on

Scenario 6 ("One Card Required") is deliberately not here: the term is not
defined anywhere in this codebase and the business rule is still to be
confirmed, so nothing stands in for it.

They are ordinary rules in the existing engine, built by the existing rule
builders and matched by the existing decide_preround / decide_flop, so what is
checked here is that each fires when it should and stays quiet when it should
not.
"""

import pytest

from poker import scenarios
from poker.board_features import describe_flop, describe_flop_from_slots


@pytest.fixture()
def standard():
    """A fresh rule set holding exactly the standard rules."""
    empty = {section: {"default": body["default"], "rules": []}
             for section, body in scenarios.DEFAULTS.items()}
    assert scenarios.add_standard_rules(empty) == 5
    return empty


def round_record(winner, player_hand="High Card", dealer_hand="High Card"):
    """A finished round as the tracker records it."""
    return {"winner": winner, "player_hand": player_hand,
            "dealer_hand": dealer_hand}


# -- 1: the dealer won the last round -----------------------------------------

def test_a_dealer_win_means_skip_the_next_round(standard):
    action, rule = scenarios.decide_preround(
        standard, round_record(scenarios.DEALER))
    assert action == scenarios.SKIP
    assert "dealer won" in rule["name"]


def test_a_tie_is_not_treated_as_a_dealer_win(standard):
    """Only the three situations named have an opinion; a tie falls through."""
    action, rule = scenarios.decide_preround(standard, round_record(scenarios.TIE))
    assert action == standard["preround"]["default"]
    assert rule is None


# -- 2: the player won the last round -----------------------------------------

def test_a_player_win_means_ante(standard):
    action, rule = scenarios.decide_preround(
        standard, round_record(scenarios.PLAYER))
    assert action == scenarios.ANTE
    assert "player won the last round" in rule["name"]


# -- 3: two player wins in a row ----------------------------------------------

def test_two_player_wins_in_a_row_mean_increase_bet(standard):
    history = [round_record(scenarios.PLAYER), round_record(scenarios.PLAYER)]
    action, rule = scenarios.decide_preround(standard, history[0], history)
    assert action == scenarios.ANTE
    assert "increase bet" in rule["name"]


def test_one_player_win_is_not_a_streak(standard):
    history = [round_record(scenarios.PLAYER), round_record(scenarios.DEALER)]
    action, _ = scenarios.decide_preround(standard, history[0], history)
    assert action == scenarios.ANTE, "a single win must not raise the stake"


def test_the_streak_is_the_run_ending_now(standard):
    """Two wins earlier on, broken since, is not a streak."""
    history = [round_record(scenarios.DEALER),
               round_record(scenarios.PLAYER), round_record(scenarios.PLAYER)]
    action, _ = scenarios.decide_preround(standard, history[0], history)
    assert action == scenarios.SKIP


def test_a_longer_run_still_counts_as_a_streak(standard):
    history = [round_record(scenarios.PLAYER)] * 4
    action, rule = scenarios.decide_preround(standard, history[0], history)
    assert action == scenarios.ANTE
    assert "increase bet" in rule["name"]


def test_the_streak_rule_is_checked_before_the_plain_player_win(standard):
    """Both match two wins in a row, so the order in the list decides."""
    names = [rule["name"] for rule in standard["preround"]["rules"]]
    streak = names.index("If the player won the last 2 rounds, increase bet and play")
    plain = names.index("If the player won the last round, play")
    assert streak < plain


def test_without_history_a_streak_is_never_claimed(standard):
    """One round is all the caller gave, so two in a row cannot be known."""
    action, _ = scenarios.decide_preround(standard, round_record(scenarios.PLAYER))
    assert action == scenarios.ANTE


def test_counting_a_streak_directly():
    assert scenarios.player_win_streak([]) == 0
    assert scenarios.player_win_streak(None) == 0
    assert scenarios.player_win_streak(
        [round_record(scenarios.PLAYER), round_record(scenarios.PLAYER),
         round_record(scenarios.DEALER)]) == 2


# -- 4: the flop is paired ----------------------------------------------------

def test_a_paired_flop_means_play(standard):
    features = describe_flop(["2C", "7D"], ["9H", "9S", "4D"])
    assert features["flop_paired"] is True
    action, rule = scenarios.decide_flop(standard, features)
    assert action == scenarios.PLAY
    assert "flop is paired" in rule["name"]


def test_an_unpaired_flop_does_not_fire_that_rule(standard):
    features = describe_flop(["2C", "7D"], ["9H", "5S", "4D"])
    assert features["flop_paired"] is False
    _, rule = scenarios.decide_flop(standard, features)
    assert rule is None or "flop is paired" not in rule["name"]


def test_a_pair_made_with_a_hole_card_is_not_a_paired_flop():
    """The board itself must be paired, not the player's hand."""
    features = describe_flop(["9C", "7D"], ["9H", "5S", "4D"])
    assert features["flop_paired"] is False
    assert features["pair"] == "pair uses one of my cards"


# -- 5: High Card with an ace/king/queen in the hand ---------------------------
#
# "High Card" is the hand evaluator's own name for a hand with no pair or
# better, so the rule asks for that hand and for a big card among the player's
# own two. It asked for the highest card showing until the board reading was
# measured against the recorded hands: see tests/test_high_card_hole_rule.py.

def high_card_rule_name(standard):
    """The shipped High Card rule's name, so no test spells it out."""
    return [rule for rule in standard["flop"]["rules"]
            if rule["hand"] == scenarios.HIGH_CARD][0]["name"]


@pytest.mark.parametrize("high", ["A", "K", "Q"])
def test_high_card_with_a_big_card_in_hand_means_play(standard, high):
    features = describe_flop([high + "C", "7D"], ["9H", "5S", "4D"])
    assert features["hand"] == "High Card"
    assert features["highest_hole"] == high
    action, rule = scenarios.decide_flop(standard, features)
    assert action == scenarios.PLAY
    assert rule["name"] == high_card_rule_name(standard)


def test_a_pair_is_not_a_high_card_hand(standard):
    """A big card alongside a made pair is a different situation entirely."""
    features = describe_flop(["AC", "7D"], ["9H", "9S", "4D"])
    assert features["hand"] == "Pair"
    _, rule = scenarios.decide_flop(standard, features)
    assert (rule or {}).get("name") != high_card_rule_name(standard)


def test_a_jack_high_hand_does_not_fire_that_rule(standard):
    features = describe_flop(["JC", "7D"], ["9H", "5S", "2D"])
    assert features["hand"] == "High Card"
    assert features["highest_hole"] == "J"
    _, rule = scenarios.decide_flop(standard, features)
    assert (rule or {}).get("name") != high_card_rule_name(standard)


def test_the_big_card_must_be_one_of_the_players_own(standard):
    """An ace on the flop is the dealer's ace too, so it no longer counts.

    The hand still falls through to the flop default, which is "play" - what
    changed is that no rule claims it, so the window does not say a rule
    matched when none did.
    """
    features = describe_flop(["2C", "7D"], ["AH", "5S", "4D"])
    assert features["hand"] == "High Card"
    assert features["highest"] == "A"            # showing
    assert features["highest_hole"] == "7"       # in hand
    action, rule = scenarios.decide_flop(standard, features)
    assert rule is None
    assert action == standard["flop"]["default"]


def test_a_ten_is_not_read_as_a_face_card():
    """"10" sorts above 9 and must not be mistaken for a big card."""
    features = describe_flop(["10C", "7D"], ["9H", "5S", "2D"])
    assert features["highest"] == "10"


def test_the_high_card_name_comes_from_the_evaluator():
    """If the evaluator ever renames the hand, the rule follows it."""
    from poker.hand_evaluator import HAND_NAMES

    assert scenarios.HIGH_CARD == HAND_NAMES[1]


# -- scenario 6 is deliberately absent ----------------------------------------

def test_nothing_claims_to_answer_one_card_required():
    """Waiting on the business rule; there must be no placeholder standing in."""
    import poker.board_features as board_features

    assert not hasattr(board_features, "ONE_CARD_TO_COME")
    conditions = " ".join(scenarios.CONDITION_CHOICES).lower()
    assert "one card" not in conditions and "one more card" not in conditions


# -- the whole thing through the tracker's own slot dictionary ----------------

def test_the_new_conditions_reach_the_engine_from_the_live_table(standard):
    """decide_from_cards is what the UI calls, so the path must work end to end."""
    cards = {"player_1": "AC", "player_2": "7D",
             "flop_1": "9H", "flop_2": "9S", "flop_3": "4D"}
    decision = scenarios.decide_from_cards(
        standard, cards,
        previous=round_record(scenarios.PLAYER),
        history=[round_record(scenarios.PLAYER), round_record(scenarios.PLAYER)],
    )
    assert decision["flop_action"] == scenarios.PLAY
    assert "flop is paired" in decision["flop_rule"]["name"]
    assert decision["preround_action"] == scenarios.ANTE
    assert "increase bet" in decision["preround_rule"]["name"]


def test_the_features_the_ui_shows_still_read_the_same(standard):
    """The extra keys must not disturb the existing summary line."""
    from poker.board_features import summarise

    features = describe_flop_from_slots(
        {"player_1": "2H", "player_2": "7H",
         "flop_1": "9H", "flop_2": "5H", "flop_3": "KS"})
    assert summarise(features) == "High Card, four to a flush"


# -- the two readings of "A, K or Q" ------------------------------------------
#
# Live, these two answered differently in three of fourteen rounds, and the
# window showed both answers side by side: "PLAY (High Card with A, K or Q
# high)" from the user's rules beside "DON'T_PLAY" from the Scenario Engine,
# for hands like J-3 on K-5-9 where the king is on the board. They are
# different questions and both are now offered by name.

def test_a_big_card_on_the_board_counts_for_the_showing_condition():
    from poker.board_features import HIGH_AKQ

    features = describe_flop(["JS", "3D"], ["KS", "5D", "9H"])
    assert scenarios._matches_condition(HIGH_AKQ, features)


def test_a_big_card_on_the_board_does_not_count_for_the_in_hand_condition():
    from poker.board_features import HIGH_AKQ_HOLE

    features = describe_flop(["JS", "3D"], ["KS", "5D", "9H"])
    assert not scenarios._matches_condition(HIGH_AKQ_HOLE, features)


def test_the_in_hand_condition_agrees_with_the_scenario_engine():
    """Same question, same answer, whichever half of the app is asking."""
    from poker.board_features import HIGH_AKQ_HOLE
    from poker.scenario_engine import HIGH_CARD_AKQ, detect_high_card_scenario

    hands = [(["JS", "3D"], ["KS", "5D", "9H"]),      # king on the board only
             (["AH", "5C"], ["KC", "2D", "QH"]),      # ace in the hand
             (["QC", "5S"], ["JC", "4S", "2H"]),      # queen in the hand
             (["7C", "2D"], ["9H", "5S", "4D"])]      # nothing anywhere
    for hole, board in hands:
        features = describe_flop(hole, board)
        engine = detect_high_card_scenario(hole, board)[HIGH_CARD_AKQ]
        assert scenarios._matches_condition(HIGH_AKQ_HOLE, features) == engine


def test_both_readings_are_offered_to_the_rule_builder():
    from poker.board_features import HIGH_AKQ, HIGH_AKQ_HOLE

    assert HIGH_AKQ in scenarios.CONDITION_CHOICES
    assert HIGH_AKQ_HOLE in scenarios.CONDITION_CHOICES


def test_the_shipped_rule_asks_about_the_hole_cards():
    """Both readings stay defined; the shipped rule uses the hole-card one."""
    from poker.board_features import HIGH_AKQ_HOLE

    high = [rule for rule in scenarios.standard_rules()["flop"]
            if "A, K or Q" in rule["condition"]]
    assert [rule["condition"] for rule in high] == [HIGH_AKQ_HOLE]


# -- the rules the user already had -------------------------------------------

def test_existing_rules_are_never_replaced():
    """A user who already decided what to do in one of these situations keeps it."""
    mine = {section: {"default": body["default"], "rules": []}
            for section, body in scenarios.DEFAULTS.items()}
    mine["preround"]["rules"] = [
        scenarios.preround_rule(scenarios.ANY, scenarios.ANY, scenarios.ANTE,
                                previous_winner=scenarios.DEALER,
                                name="my own idea")
    ]
    scenarios.add_standard_rules(mine)

    names = [rule["name"] for rule in mine["preround"]["rules"]]
    assert "my own idea" in names
    assert "If the dealer won the last round, skip this one" not in names
    action, rule = scenarios.decide_preround(mine, round_record(scenarios.DEALER))
    assert action == scenarios.ANTE, "the user's own action must still win"


def test_adding_the_standard_rules_twice_adds_nothing_the_second_time():
    empty = {section: {"default": body["default"], "rules": []}
             for section, body in scenarios.DEFAULTS.items()}
    assert scenarios.add_standard_rules(empty) == 5
    assert scenarios.add_standard_rules(empty) == 0


def test_one_section_can_be_added_on_its_own():
    empty = {section: {"default": body["default"], "rules": []}
             for section, body in scenarios.DEFAULTS.items()}
    assert scenarios.add_standard_rules(empty, "flop") == 2
    assert empty["preround"]["rules"] == []


def test_a_rule_saved_before_these_conditions_existed_still_works():
    """scenarios.json on disk predates the winner and streak fields."""
    old = {"preround": {"default": scenarios.ANTE, "rules": [
        {"previous_dealer_hand": "Pair", "previous_player_hand": scenarios.ANY,
         "action": scenarios.SKIP, "name": "old rule"}]},
        "flop": {"default": scenarios.PLAY, "rules": []}}
    action, rule = scenarios.decide_preround(
        old, round_record(scenarios.PLAYER, dealer_hand="Pair"))
    assert action == scenarios.SKIP
    assert rule["name"] == "old rule"


# -- the new action -----------------------------------------------------------

def test_no_new_action_types_were_introduced():
    """The engine still offers exactly the actions it always did."""
    assert scenarios.PREROUND_ACTIONS == [scenarios.ANTE, scenarios.SKIP]
    assert scenarios.FLOP_ACTIONS == [scenarios.PLAY, scenarios.FOLD]
    assert set(scenarios.ACTION_LABELS) == {
        scenarios.ANTE, scenarios.SKIP, scenarios.PLAY, scenarios.FOLD}


def test_an_unknown_winner_is_rejected():
    with pytest.raises(ValueError):
        scenarios.preround_rule(scenarios.ANY, scenarios.ANY, scenarios.ANTE,
                                previous_winner="Nobody")
