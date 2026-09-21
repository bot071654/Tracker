"""The Scenario Engine: every scenario, dealer qualification, the WAIT gate,
log throttling, and the dry-run hook in the tracker.

Cards are written as the tracker writes them: rank then suit, "10" for ten.
"""

import logging
import queue

import pytest

from poker import scenario_engine as se
from poker.hand_evaluator import InvalidCardError


def decide(player, flop, **config):
    return se.detect_player_decision(player, flop, se.EngineConfig(**config).validate())


def matched(player, flop, **config):
    return decide(player, flop, **config)["matched_scenarios"]


# -- HIGH CARD ----------------------------------------------------------------

def test_ace_king_on_an_unrelated_flop_plays():
    result = decide(["AS", "KD"], ["7C", "5H", "2S"])
    assert result["matched_scenarios"] == [se.HIGH_CARD_AKQ]
    assert result["detected_hand"] == se.HIGH_CARD
    assert result["primary_scenario"] == se.HIGH_CARD
    assert result["decision"] == se.PLAY


def test_high_card_1_an_ace_in_the_player_cards_plays():
    assert decide(["AS", "8D"], ["7C", "5H", "2S"])["decision"] == se.PLAY


def test_high_card_2_a_king_in_the_player_cards_plays():
    assert decide(["9S", "KD"], ["7C", "5H", "2S"])["decision"] == se.PLAY


def test_high_card_3_a_queen_in_the_player_cards_plays():
    result = decide(["QH", "8D"], ["7C", "5H", "2S"])
    assert result["matched_scenarios"] == [se.HIGH_CARD_AKQ]
    assert result["decision"] == se.PLAY


def test_high_card_4_no_ace_king_or_queen_does_not_play():
    result = decide(["JS", "9D"], ["7C", "5H", "2S"])
    assert result["matched_scenarios"] == []
    assert result["decision"] == se.DONT_PLAY


def test_the_high_card_rule_uses_the_hole_cards_by_default():
    """An A/K/Q on the flop is not the player's high card."""
    config = se.EngineConfig().validate()
    assert config.high_card_source == "player"
    assert config.high_card_ranks == ["A", "K", "Q"]
    assert config.high_card_min_count == 1
    result = decide(["JS", "9D"], ["AC", "KH", "2S"])
    assert se.HIGH_CARD_AKQ not in result["matched_scenarios"]
    assert result["decision"] == se.DONT_PLAY


def test_the_shipped_config_states_the_high_card_rule_explicitly():
    import json

    with open(se.ENGINE_CONFIG_PATH, encoding="utf-8") as handle:
        stored = json.load(handle)
    assert stored["high_card_source"] == "player"
    assert stored["high_card_ranks"] == ["A", "K", "Q"]
    assert stored["high_card_min_count"] == 1


def test_2b_requiring_two_high_cards_needs_queen_plus_another():
    assert decide(["QS", "KD"], ["7C", "3H", "2S"],
                  high_card_min_count=2)["decision"] == se.PLAY
    assert decide(["QS", "9D"], ["7C", "3H", "2S"],
                  high_card_min_count=2)["decision"] == se.DONT_PLAY


def test_2c_the_flop_only_counts_when_configured():
    assert decide(["JS", "9D"], ["AC", "3H", "2S"])["decision"] == se.DONT_PLAY
    assert decide(["JS", "9D"], ["AC", "3H", "2S"],
                  high_card_source="player_and_flop")["decision"] == se.PLAY


def test_3_no_ace_king_or_queen_does_not_play():
    result = decide(["JS", "9D"], ["7C", "3H", "2S"])
    assert result["matched_scenarios"] == []
    assert result["primary_scenario"] == se.NONE
    assert result["decision"] == se.DONT_PLAY
    assert "no pair" in result["reason"]


def test_a_ten_is_not_a_high_card():
    assert decide(["10S", "9D"], ["7C", "3H", "2S"])["decision"] == se.DONT_PLAY


# -- PAIR ---------------------------------------------------------------------

def test_4_player_pocket_pair():
    result = decide(["8S", "8H"], ["KC", "5D", "2S"])
    assert se.PLAYER_PAIR in result["matched_scenarios"]
    assert se.COMBINED_PAIR not in result["matched_scenarios"]
    assert se.FLOP_PAIR not in result["matched_scenarios"]
    assert result["primary_scenario"] == se.PAIR
    assert result["decision"] == se.PLAY


def test_5_pair_between_a_player_card_and_the_flop():
    result = decide(["AS", "7D"], ["KC", "7H", "3S"])
    assert se.COMBINED_PAIR in result["matched_scenarios"]
    assert se.PLAYER_PAIR not in result["matched_scenarios"]
    assert se.FLOP_PAIR not in result["matched_scenarios"]
    assert result["primary_scenario"] == se.PAIR
    assert result["decision"] == se.PLAY


def test_6_flop_pair():
    result = decide(["AS", "KD"], ["9C", "9H", "3S"])
    assert se.FLOP_PAIR in result["matched_scenarios"]
    assert se.COMBINED_PAIR not in result["matched_scenarios"]
    assert result["primary_scenario"] == se.PAIR
    assert result["decision"] == se.PLAY


def test_a_flop_pair_plays_even_without_a_high_card():
    result = decide(["JS", "6D"], ["9C", "9H", "3S"])
    assert result["matched_scenarios"] == [se.FLOP_PAIR]
    assert result["decision"] == se.PLAY


def test_flop_pair_positions_do_not_matter():
    for flop in (["9C", "9H", "3S"], ["9C", "3S", "9H"], ["3S", "9C", "9H"]):
        assert se.FLOP_PAIR in matched(["JS", "6D"], flop)


# -- TWO PAIR -----------------------------------------------------------------

def test_7_player_pair_plus_flop_pair():
    result = decide(["8S", "8H"], ["KC", "KD", "3S"])
    assert se.PLAYER_PAIR_PLUS_FLOP_PAIR in result["matched_scenarios"]
    assert se.PLAYER_PAIR in result["matched_scenarios"]
    assert result["detected_hand"] == se.TWO_PAIR
    assert result["primary_scenario"] == se.TWO_PAIR
    assert result["decision"] == se.PLAY


def test_8_two_pairs_across_player_and_flop():
    result = decide(["AS", "7D"], ["AH", "7C", "3S"])
    assert se.COMBINED_TWO_PAIR in result["matched_scenarios"]
    assert se.PLAYER_PAIR_CARD_PLUS_FLOP_PAIR not in result["matched_scenarios"]
    assert result["primary_scenario"] == se.TWO_PAIR
    assert result["decision"] == se.PLAY


def test_9_one_player_match_plus_a_flop_pair():
    result = decide(["AS", "7D"], ["AH", "9C", "9S"])
    assert se.PLAYER_PAIR_CARD_PLUS_FLOP_PAIR in result["matched_scenarios"]
    assert se.COMBINED_TWO_PAIR not in result["matched_scenarios"]
    assert result["primary_scenario"] == se.TWO_PAIR
    assert result["decision"] == se.PLAY


def test_two_pair_is_not_reported_as_a_plain_pair():
    for player, flop in ((["8S", "8H"], ["KC", "KD", "3S"]),
                         (["AS", "7D"], ["AH", "7C", "3S"]),
                         (["AS", "7D"], ["AH", "9C", "9S"])):
        assert decide(player, flop)["primary_scenario"] == se.TWO_PAIR


def test_the_flop_pair_must_be_another_rank_for_player_pair_plus_flop_pair():
    """8-8 on 8-8-3 is four of a kind, not another pair."""
    result = decide(["8S", "8H"], ["8C", "8D", "3S"])
    assert se.PLAYER_PAIR_PLUS_FLOP_PAIR not in result["matched_scenarios"]
    assert result["primary_scenario"] == se.FOUR_OF_A_KIND


# -- THREE OF A KIND ----------------------------------------------------------

def test_10_player_pair_plus_a_matching_flop_card():
    result = decide(["7S", "7D"], ["7H", "KC", "3S"])
    assert se.PLAYER_PAIR_PLUS_FLOP_MATCH in result["matched_scenarios"]
    assert result["detected_hand"] == se.THREE_OF_A_KIND
    assert result["primary_scenario"] == se.THREE_OF_A_KIND
    assert result["decision"] == se.PLAY


def test_11_one_player_card_plus_two_matching_flop_cards():
    result = decide(["AS", "KD"], ["AH", "AC", "3S"])
    assert se.PLAYER_CARD_PLUS_FLOP_PAIR in result["matched_scenarios"]
    assert result["primary_scenario"] == se.THREE_OF_A_KIND
    assert result["decision"] == se.PLAY


@pytest.mark.parametrize("player,flop", [
    (["AS", "KD"], ["AH", "AC", "3S"]),
    (["KD", "AS"], ["3S", "AH", "AC"]),
    (["2D", "AS"], ["AH", "4C", "AC"]),
])
def test_trips_are_found_whatever_the_card_positions(player, flop):
    assert se.PLAYER_CARD_PLUS_FLOP_PAIR in matched(player, flop)


@pytest.mark.parametrize("flop", [["7H", "KC", "3S"], ["KC", "7H", "3S"],
                                  ["KC", "3S", "7H"]])
def test_pocket_pair_trips_whatever_the_flop_position(flop):
    assert se.PLAYER_PAIR_PLUS_FLOP_MATCH in matched(["7S", "7D"], flop)


def test_trips_lying_only_on_the_flop_are_not_the_players_trips():
    """The evaluator says Three of a Kind, but the player holds none of it."""
    result = decide(["JS", "4D"], ["9C", "9H", "9S"])
    assert result["detected_hand"] == se.THREE_OF_A_KIND
    assert se.PLAYER_PAIR_PLUS_FLOP_MATCH not in result["matched_scenarios"]
    assert se.PLAYER_CARD_PLUS_FLOP_PAIR not in result["matched_scenarios"]
    assert result["matched_scenarios"] == [se.FLOP_PAIR]
    assert result["primary_scenario"] == se.PAIR


# -- STRAIGHT -----------------------------------------------------------------

def test_12_a_normal_straight():
    result = decide(["5S", "6D"], ["7C", "8H", "9S"])
    assert result["matched_scenarios"] == [se.STRAIGHT]
    assert result["primary_scenario"] == se.STRAIGHT
    assert result["decision"] == se.PLAY


def test_13_the_wheel_is_a_straight():
    result = decide(["AS", "2D"], ["3C", "4H", "5S"])
    assert se.STRAIGHT in result["matched_scenarios"]
    assert result["primary_scenario"] == se.STRAIGHT


def test_broadway_is_a_straight_and_the_ace_does_not_wrap():
    assert se.STRAIGHT in matched(["10S", "JD"], ["QC", "KH", "AS"])
    assert se.STRAIGHT not in matched(["QS", "KD"], ["AC", "2H", "3S"])


def test_14_non_straight_cards():
    assert se.detect_straight_scenario(["5S", "6D"], ["7C", "8H", "10S"]) == {
        se.STRAIGHT: False}
    assert se.detect_straight_scenario(["5S", "6D"], ["7C", "8H", "8S"]) == {
        se.STRAIGHT: False}


def test_a_straight_need_not_be_suited():
    assert se.detect_straight_scenario(["5S", "6D"], ["7C", "8H", "9S"])[se.STRAIGHT]


# -- flush: an actual hand, never a PLAY rule ----------------------------------

def test_flush_alone_does_not_cause_play():
    result = decide(["JH", "9H"], ["7H", "3H", "2H"])
    assert result["detected_hand"] == se.FLUSH          # shown for information
    assert result["matched_scenarios"] == []
    assert result["primary_scenario"] == se.NONE
    assert result["decision"] == se.DONT_PLAY


def test_a_flush_with_a_high_card_plays_because_of_the_high_card():
    result = decide(["AH", "9H"], ["7H", "3H", "2H"])
    assert result["detected_hand"] == se.FLUSH
    assert result["matched_scenarios"] == [se.HIGH_CARD_AKQ]
    assert result["primary_scenario"] == se.HIGH_CARD


def test_there_is_no_flush_rule():
    assert se.FLUSH not in se.ALL_SCENARIOS
    assert se.FLUSH not in se.EngineConfig().play_scenarios
    assert not hasattr(se.EngineConfig(), "play_on_flush")
    with pytest.raises(ValueError):
        se.EngineConfig(play_scenarios=[se.FLUSH]).validate()


def test_only_the_requested_rules_exist():
    assert set(se.ALL_SCENARIOS) == {
        se.HIGH_CARD_AKQ, se.PLAYER_PAIR, se.COMBINED_PAIR, se.FLOP_PAIR,
        se.PLAYER_PAIR_PLUS_FLOP_PAIR, se.COMBINED_TWO_PAIR,
        se.PLAYER_PAIR_CARD_PLUS_FLOP_PAIR, se.PLAYER_PAIR_PLUS_FLOP_MATCH,
        se.PLAYER_CARD_PLUS_FLOP_PAIR, se.STRAIGHT}


def test_a_straight_flush_is_the_primary_scenario():
    result = decide(["5H", "6H"], ["7H", "8H", "9H"])
    assert result["detected_hand"] == se.STRAIGHT_FLUSH
    assert result["primary_scenario"] == se.STRAIGHT_FLUSH
    assert result["matched_scenarios"] == [se.STRAIGHT]


# -- overlapping conditions ---------------------------------------------------

def test_overlap_pocket_sevens_on_seven_king_king():
    """The brief's example. It is a full house, and far more than a pair."""
    result = decide(["7S", "7D"], ["7H", "KC", "KD"])
    assert result["matched_scenarios"] == [
        se.PLAYER_PAIR, se.PLAYER_PAIR_PLUS_FLOP_PAIR, se.PLAYER_PAIR_PLUS_FLOP_MATCH]
    assert result["detected_hand"] == se.FULL_HOUSE
    assert result["primary_scenario"] == se.FULL_HOUSE
    assert result["primary_scenario"] != se.PAIR
    flags = result["group_flags"]
    assert flags[se.PAIR] and flags[se.TWO_PAIR] and flags[se.THREE_OF_A_KIND]
    assert not flags[se.STRAIGHT]
    assert result["decision"] == se.PLAY


def test_overlap_every_check_is_kept_for_debugging():
    result = decide(["AS", "7D"], ["AH", "AC", "7C"])      # aces full of sevens
    assert set(result["scenario_checks"]) == set(se.ALL_SCENARIOS)
    assert {se.HIGH_CARD_AKQ, se.COMBINED_PAIR, se.COMBINED_TWO_PAIR,
            se.PLAYER_CARD_PLUS_FLOP_PAIR} <= set(result["matched_scenarios"])
    assert result["primary_scenario"] == se.FULL_HOUSE


def test_a_weaker_scenario_never_overwrites_a_stronger_one():
    """Trips with an ace in hand match HIGH_CARD_AKQ and pairs too."""
    result = decide(["AS", "KD"], ["AH", "AC", "3S"])
    assert se.HIGH_CARD_AKQ in result["matched_scenarios"]
    assert se.COMBINED_PAIR in result["matched_scenarios"]
    assert result["primary_scenario"] == se.THREE_OF_A_KIND


def test_scenarios_removed_from_play_still_show_but_do_not_decide():
    result = decide(["JS", "6D"], ["9C", "9H", "3S"],
                    play_scenarios=[s for s in se.ALL_SCENARIOS if s != se.FLOP_PAIR])
    assert result["matched_scenarios"] == [se.FLOP_PAIR]
    assert result["decision"] == se.DONT_PLAY
    assert "none of which is set to play" in result["reason"]


def test_bad_input_is_refused():
    with pytest.raises(InvalidCardError):
        se.detect_player_decision(["AS", "AS"], ["2C", "3D", "4H"])
    with pytest.raises(InvalidCardError):
        se.detect_player_decision(["AS"], ["2C", "3D", "4H"])
    with pytest.raises(ValueError):
        se.EngineConfig(high_card_ranks=["X"]).validate()
    with pytest.raises(ValueError):
        se.EngineConfig(play_scenarios=["NOPE"]).validate()


def test_result_structure():
    result = decide(["AS", "KD"], ["7C", "3H", "2S"])
    for key in ("player_cards", "flop_cards", "detected_hand", "matched_scenarios",
                "primary_scenario", "decision"):
        assert key in result
    assert result["dry_run"] is True


# -- DEALER QUALIFICATION -----------------------------------------------------

BLANK_BOARD = ["2C", "7D", "9H", "JS", "KC"]     # no pair, no straight, no flush


def dealer(cards, board=BLANK_BOARD):
    return se.evaluate_dealer_qualification(cards, board)


OTHER_BLANK_BOARD = ["3C", "6D", "8H", "10S", "QC"]   # no rank shared with BLANK_BOARD


def test_15_dealer_threes_do_not_qualify():
    result = dealer(["3S", "3D"])
    assert result["dealer_hand_detail"] == "PAIR OF 3s"
    assert result["dealer_qualified"] is False


def test_16_dealer_fours_qualify():
    result = dealer(["4S", "4D"])
    assert result["dealer_hand_detail"] == "PAIR OF 4s"
    assert result["dealer_qualified"] is True


@pytest.mark.parametrize("rank,qualifies", [
    ("2", False), ("3", False), ("4", True), ("5", True), ("6", True), ("7", True),
    ("8", True), ("9", True), ("10", True), ("J", True), ("Q", True), ("K", True),
    ("A", True)])
def test_every_dealer_pair_against_the_four_threshold(rank, qualifies):
    board = BLANK_BOARD if not any(card[:-1] == rank for card in BLANK_BOARD) \
        else OTHER_BLANK_BOARD
    result = dealer([rank + "S", rank + "D"], board)
    assert result["dealer_hand"] == se.PAIR
    assert result["dealer_hand_detail"] == "PAIR OF %ss" % rank
    assert result["dealer_qualified"] is qualifies


def test_dealer_three_of_a_kind_qualifies_even_in_twos():
    result = dealer(["2S", "2D"], ["2H", "7D", "9H", "JS", "KC"])
    assert result["dealer_hand"] == se.THREE_OF_A_KIND
    assert result["dealer_qualified"] is True


def test_dealer_full_house_qualifies():
    result = dealer(["3S", "3D"], ["3H", "KC", "KD", "8S", "9C"])
    assert result["dealer_hand"] == se.FULL_HOUSE
    assert result["dealer_qualified"] is True


def test_17_dealer_fives_qualify():
    assert dealer(["5S", "5D"])["dealer_qualified"] is True


def test_18_dealer_aces_qualify():
    assert dealer(["AS", "AD"])["dealer_qualified"] is True


def test_19_dealer_high_card_does_not_qualify():
    result = dealer(["3S", "4D"])
    assert result["dealer_hand"] == se.HIGH_CARD
    assert result["dealer_qualified"] is False


def test_20_dealer_straight_qualifies():
    result = dealer(["8S", "10D"])                 # 7-8-9-10-J
    assert result["dealer_hand"] == se.STRAIGHT
    assert result["dealer_qualified"] is True


@pytest.mark.parametrize("cards,board,hand", [
    (["2H", "5H"], ["8H", "JH", "KH", "3C", "4D"], se.FLUSH),
    (["3S", "3D"], ["3H", "KC", "KD", "8S", "9C"], se.FULL_HOUSE),
    (["3S", "3D"], ["3H", "3C", "KD", "8S", "9C"], se.FOUR_OF_A_KIND),
    (["5H", "6H"], ["7H", "8H", "9H", "2C", "KD"], se.STRAIGHT_FLUSH),
    (["2S", "3D"], ["2H", "3C", "9D", "JS", "KC"], se.TWO_PAIR),
])
def test_stronger_dealer_hands_qualify(cards, board, hand):
    result = se.evaluate_dealer_qualification(cards, board)
    assert result["dealer_hand"] == hand
    assert result["dealer_qualified"] is True


def test_a_pair_of_fours_on_the_board_counts_for_the_dealer():
    result = se.evaluate_dealer_qualification(["2S", "9D"], ["4H", "4C", "JD", "KS", "7C"])
    assert result["dealer_qualified"] is True


def test_dealer_qualification_waits_for_cards():
    result = se.evaluate_dealer_qualification(["4S", None], BLANK_BOARD)
    assert result["dealer_qualified"] is None


def test_dealer_qualification_agrees_with_the_stored_records():
    """One rule for both: hand_record uses hand_evaluator.dealer_qualifies too."""
    from poker.hand_record import build_hand_record

    record = build_hand_record({
        "player_1": "AS", "player_2": "KD", "flop_1": "2C", "flop_2": "7D",
        "flop_3": "9H", "turn": "JS", "river": "KC",
        "dealer_1": "3S", "dealer_2": "3D"})
    assert record["dealer_qualified"] == dealer(["3S", "3D"])["dealer_qualified"]


# -- the WAIT gate ------------------------------------------------------------

def confirmed(cards, status="CONFIRMED", readings=5):
    return {slot: {"card": card, "status": status, "readings": readings}
            for slot, card in cards.items()}


TABLE = {"player_1": "7S", "player_2": "7D",
         "flop_1": "7H", "flop_2": "KC", "flop_3": "KD"}


def test_all_five_confirmed_gives_a_decision():
    result = se.evaluate_round(confirmed(TABLE), round_id=3)
    assert result["decision"] == se.PLAY
    assert result["round_id"] == 3
    assert result["wait_reasons"] == []


def test_a_held_card_is_still_confirmed():
    slots = confirmed(TABLE)
    slots["flop_2"]["status"] = "HELD"          # the dealer's hand is over it
    assert se.evaluate_round(slots)["decision"] == se.PLAY


@pytest.mark.parametrize("status", ["UNKNOWN", "AMBIGUOUS", "CONFIRMING", "EMPTY"])
def test_any_unconfirmed_card_means_wait(status):
    slots = confirmed(TABLE)
    slots["flop_3"]["status"] = status
    result = se.evaluate_round(slots)
    assert result["decision"] == se.WAIT
    assert result["detected_hand"] is None
    assert any("flop_3" in reason and status in reason
               for reason in result["wait_reasons"])


def test_a_missing_card_means_wait():
    slots = confirmed(dict(TABLE, flop_3=None), status="EMPTY", readings=0)
    assert se.evaluate_round(slots)["decision"] == se.WAIT


def test_wait_1_a_missing_player_card():
    slots = confirmed(TABLE)
    slots["player_2"] = {"card": None, "status": "EMPTY", "readings": 0}
    result = se.evaluate_round(slots)
    assert result["decision"] == se.WAIT
    assert result["reason"] == "Waiting for confirmed player + flop cards"
    assert result["matched_scenarios"] == [] and result["primary_scenario"] is None


def test_wait_2_a_missing_flop_card():
    slots = confirmed(TABLE)
    del slots["flop_1"]
    assert se.evaluate_round(slots)["decision"] == se.WAIT


def test_wait_3_an_ambiguous_card():
    slots = confirmed(TABLE)
    slots["player_1"]["status"] = "AMBIGUOUS"
    assert se.evaluate_round(slots)["decision"] == se.WAIT


def test_wait_4_an_unconfirmed_card():
    slots = confirmed(TABLE)
    slots["flop_2"].update(status="CONFIRMING", readings=1)
    assert se.evaluate_round(slots)["decision"] == se.WAIT


def test_wait_5_a_new_round_does_not_retain_the_previous_decision():
    monitor = se.ScenarioMonitor(log=Recorder())
    first = monitor.observe(confirmed(TABLE), round_id=1)
    assert first["decision"] == se.PLAY

    # Round 2: the new player cards have been seen once only.
    fresh = {slot: {"card": None, "status": "EMPTY", "readings": 0} for slot in TABLE}
    fresh["player_1"] = {"card": "2C", "status": "CONFIRMING", "readings": 1}
    second = monitor.observe(fresh, round_id=2)
    assert second["round_id"] == 2
    assert second["decision"] == se.WAIT
    assert second["matched_scenarios"] == []
    assert second["primary_scenario"] is None
    assert second["detected_hand"] is None
    assert "7S" not in second["player_cards"]
    assert second["dealer_qualified"] is None and second["dealer_cards"] == []


def test_too_few_readings_means_wait():
    slots = confirmed(TABLE)
    slots["player_1"]["readings"] = 1
    assert se.evaluate_round(slots)["decision"] == se.WAIT


def test_a_card_read_into_two_slots_means_wait():
    result = se.evaluate_round(confirmed(dict(TABLE, flop_1="7S")))
    assert result["decision"] == se.WAIT
    assert "two places" in result["wait_reasons"][0]


def test_turn_and_river_are_ignored_for_the_decision():
    slots = confirmed(TABLE)
    slots["turn"] = {"card": None, "status": "UNKNOWN", "readings": 0}
    assert se.evaluate_round(slots)["decision"] == se.PLAY


def test_the_engine_status_names_match_the_tracker():
    import tracker

    assert se.CONFIRMED_STATUS == tracker.CONFIRMED
    assert se.HELD_STATUS == tracker.HELD
    assert se.EngineConfig().min_readings == tracker.CONFIRMING_READINGS


# -- log throttling -----------------------------------------------------------

class Recorder:
    def __init__(self):
        self.lines = []

    def info(self, fmt, *args):
        self.lines.append(fmt % args)


def test_a_decision_is_logged_once_however_many_polls_see_it():
    log = Recorder()
    monitor = se.ScenarioMonitor(log=log)
    for _ in range(20):
        monitor.observe(confirmed(TABLE), round_id=1)
    assert len(log.lines) == 1
    text = log.lines[0]
    assert "Player: 7S 7D" in text and "Flop: 7H KC KD" in text
    assert "THREE_OF_A_KIND = TRUE" in text and "STRAIGHT = FALSE" in text
    assert "[PRIMARY]\nFULL_HOUSE" in text
    assert "[DECISION]\nPLAY" in text


def test_a_flicker_to_wait_after_a_decision_is_not_logged():
    log = Recorder()
    monitor = se.ScenarioMonitor(log=log)
    flicker = confirmed(TABLE)
    flicker["flop_1"]["status"] = "AMBIGUOUS"
    monitor.observe(confirmed(TABLE), 1)
    result = monitor.observe(flicker, 1)
    monitor.observe(confirmed(TABLE), 1)
    assert result["decision"] == se.WAIT       # reported honestly...
    assert len(log.lines) == 1                  # ...but not logged again


def test_waiting_is_logged_once_per_round_before_a_decision():
    log = Recorder()
    monitor = se.ScenarioMonitor(log=log)
    waiting = confirmed(TABLE, status="CONFIRMING", readings=1)
    for _ in range(5):
        monitor.observe(waiting, 1)
    assert len(log.lines) == 1 and "WAIT" in log.lines[0]
    monitor.observe(confirmed(TABLE), 1)
    assert len(log.lines) == 2 and "PLAY" in log.lines[1]


def test_a_revised_card_logs_a_new_decision():
    log = Recorder()
    monitor = se.ScenarioMonitor(log=log)
    monitor.observe(confirmed(TABLE), 1)
    monitor.observe(confirmed(dict(TABLE, flop_3="2D")), 1)
    assert len(log.lines) == 2


def test_a_new_round_logs_again():
    log = Recorder()
    monitor = se.ScenarioMonitor(log=log)
    monitor.observe(confirmed(TABLE), 1)
    monitor.observe(confirmed(TABLE), 2)
    assert len(log.lines) == 2


def test_the_dealer_is_logged_once_the_board_is_ready():
    log = Recorder()
    monitor = se.ScenarioMonitor(log=log)
    slots = confirmed(dict(TABLE, turn="2C", river="9D"))
    monitor.observe(slots, 1, dealer_cards=["4S", "4H"], board_ready=False)
    assert len(log.lines) == 1
    for _ in range(3):
        result = monitor.observe(slots, 1, dealer_cards=["4S", "4H"], board_ready=True)
    assert len(log.lines) == 2
    assert "Cards: 4S 4H" in log.lines[1]
    assert "Qualification: QUALIFIED" in log.lines[1]
    assert result["dealer_qualified"] is True
    assert result["decision"] == se.PLAY       # the dealer never changes it


# -- config file --------------------------------------------------------------

def test_engine_config_round_trips(tmp_path):
    path = str(tmp_path / "engine.json")
    se.save_engine_config(se.EngineConfig(high_card_min_count=2,
                                          high_card_source="player_and_flop"), path)
    loaded = se.load_engine_config(path)
    assert loaded.high_card_min_count == 2
    assert loaded.high_card_source == "player_and_flop"


def test_a_broken_engine_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "engine.json"
    path.write_text('{"high_card_source": "somewhere"}', encoding="utf-8")
    assert se.load_engine_config(str(path)).high_card_source == "player"


def test_the_shipped_config_file_is_the_documented_default():
    assert se.load_engine_config() == se.EngineConfig().validate()


# -- dry run: nothing acts ----------------------------------------------------

def test_the_engine_has_no_way_to_act():
    import inspect

    source = inspect.getsource(se).lower()
    for word in ("pyautogui", "pynput", "mouse", "click(", "keyboard", "win32api",
                 "sendinput", "import action_executor"):
        assert word not in source
    assert se.DRY_RUN is True


# -- through the tracker ------------------------------------------------------

_BLANK = __import__("numpy").zeros((40, 60, 3), dtype="uint8")


def fake_reads(cards):
    from config.settings import CARD_SLOTS

    reads, seen = {}, {}
    for slot in CARD_SLOTS:
        card = cards.get(slot)
        reads[slot] = {"card": card, "present": bool(card), "confident": bool(card),
                       "confidence": 0.9 if card else 0.0, "suit_margin": 0.2}
        seen[slot] = card
    return seen, reads


@pytest.fixture
def driven_tracker(monkeypatch):
    import tracker as tracker_module

    screen = {}
    monkeypatch.setattr(tracker_module, "grab_full_screen", lambda monitor=1: _BLANK)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "read_table",
                        lambda config, images=None: fake_reads(screen))
    monkeypatch.setattr(se, "load_engine_config", lambda: se.EngineConfig())
    events = queue.Queue()
    tracker = tracker_module.Tracker({"monitor": 1}, events)
    return tracker, screen, events


def last_update(events):
    update = None
    while not events.empty():
        kind, payload = events.get_nowait()
        if kind == "update":
            update = payload
    return update


def test_the_tracker_waits_then_decides_from_confirmed_cards(driven_tracker, caplog):
    tracker, screen, events = driven_tracker
    screen.update(TABLE)

    with caplog.at_level(logging.INFO, logger="poker.scenario_engine"):
        tracker._tick()
        first = last_update(events)["scenario"]
        tracker._tick()
        second = last_update(events)["scenario"]
        for _ in range(5):
            tracker._tick()

    assert first["decision"] == se.WAIT         # seen once: not yet confirmed
    assert second["decision"] == se.PLAY
    assert second["primary_scenario"] == se.FULL_HOUSE
    decisions = [r.getMessage() for r in caplog.records if "[DECISION]\nPLAY" in r.getMessage()]
    assert len(decisions) == 1


def test_the_engine_leaves_the_tracker_cards_alone(driven_tracker):
    tracker, screen, events = driven_tracker
    screen.update(TABLE)
    for _ in range(3):
        tracker._tick()
    update = last_update(events)
    assert update["cards"] == dict({slot: None for slot in update["cards"]}, **TABLE)
    assert dict(tracker.memory.cards) == TABLE


def test_the_engine_can_be_switched_off(driven_tracker):
    tracker, screen, events = driven_tracker
    tracker.config["scenario_engine"] = False
    screen.update(TABLE)
    tracker._tick()
    assert last_update(events)["scenario"] is None


def test_the_tracker_resets_to_wait_when_a_new_round_is_dealt(driven_tracker):
    tracker, screen, events = driven_tracker
    screen.update(TABLE)
    for _ in range(3):
        tracker._tick()
    played = last_update(events)["scenario"]
    assert played["decision"] == se.PLAY

    screen.clear()                             # the table empties between rounds
    for _ in range(12):
        tracker._tick()
    screen.update({"player_1": "2C", "player_2": "9H"})
    tracker._tick()
    fresh = last_update(events)["scenario"]
    assert fresh["round_id"] != played["round_id"]
    assert fresh["decision"] == se.WAIT
    assert fresh["matched_scenarios"] == [] and fresh["primary_scenario"] is None


def test_the_update_payload_carries_the_result_structure(driven_tracker):
    tracker, screen, events = driven_tracker
    screen.update(TABLE)
    for _ in range(2):
        tracker._tick()
    scenario = last_update(events)["scenario"]
    assert scenario["player_cards"] == ["7S", "7D"]
    assert scenario["flop_cards"] == ["7H", "KC", "KD"]
    assert scenario["detected_hand"] == se.FULL_HOUSE
    assert scenario["matched_scenarios"] == [
        se.PLAYER_PAIR, se.PLAYER_PAIR_PLUS_FLOP_PAIR, se.PLAYER_PAIR_PLUS_FLOP_MATCH]
    for key in ("primary_scenario", "decision", "dealer_cards", "dealer_hand",
                "dealer_qualified"):
        assert key in scenario


def test_an_engine_failure_never_stops_a_poll(driven_tracker, monkeypatch):
    tracker, screen, events = driven_tracker

    def boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(se, "evaluate_round", boom)
    screen.update(TABLE)
    tracker._tick()
    tracker._tick()
    update = last_update(events)
    assert update["scenario"] is None
    assert update["state"] == "FLOP"
