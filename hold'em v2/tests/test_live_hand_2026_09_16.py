"""Regression tests for the live hand of 2026-09-16 23:55 that the tracker failed to read.

On the table: player A♣ 8♦, flop 9♥ 7♦ 3♠, turn 8♥, river 6♥, dealer J♥ 3♦.
The tracker showed Player "-- --" and stayed at WAIT, and the result panel's
"Player best 5" showed the dealer's cards. The failures, and what each test
holds:

  recognition   The 8♦ (and a 5♦ the hand before) was refused on every poll:
                the top of the card's centre barcode blurred into a bar inside
                the index strip. The turn 8♥ and river 6♥ were refused: a line
                of table art touching the tilted card stretched the face box up
                over felt. Fixtures are the real refused crops, checked by eye.
  confirmation  A confirmed card is not undone by an unreadable or ambiguous poll.
  board slots   A covered flop card does not shift the cards after it left.
  round         One player card reading differently does not start a new hand.
  result panel  A dealer row found on its own is not labelled the player's.
  scenario      The engine receives the positional, confirmed cards.
"""

import glob
import os

import pytest

cv2 = pytest.importorskip("cv2")

import tracker as tracker_module  # noqa: E402
from poker import scenario_engine as se  # noqa: E402
from recognition import result_panel, table_layout  # noqa: E402
from recognition.card_recognizer import load_templates, read_slot  # noqa: E402
from tracker import CONFIRMED, HELD, CardMemory, card_status  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CROPS = sorted(glob.glob(os.path.join(HERE, "fixtures", "live_2026-09-16", "*.png")))
needs_crops = pytest.mark.skipif(not CROPS, reason="no live crops")

HAND = {"player_1": "AC", "player_2": "8D", "flop_1": "9H", "flop_2": "7D", "flop_3": "3S",
        "turn": "8H", "river": "6H", "dealer_1": "JH", "dealer_2": "3D"}


def truth(path):
    return os.path.basename(path).split("_")[0]


@pytest.fixture(scope="module", autouse=True)
def templates():
    load_templates(force=True)


# -- recognition --------------------------------------------------------------------------

@needs_crops
@pytest.mark.parametrize("path", [p for p in CROPS if "finger" not in p and "none" not in p],
                         ids=lambda p: os.path.basename(p))
def test_the_cards_that_were_refused_live_are_read(path):
    result = read_slot(cv2.imread(path))
    assert result["confident"] and result["card"] == truth(path), result


@needs_crops
@pytest.mark.parametrize("path", CROPS, ids=lambda p: os.path.basename(p))
def test_no_live_crop_is_confidently_read_as_another_card(path):
    """Covered cards (a finger on the A♠ and 3♠) and an arm may be refused, never misread."""
    result = read_slot(cv2.imread(path))
    if result["confident"]:
        assert result["card"] == truth(path), result


# -- confirmation --------------------------------------------------------------------------

def _read(card, confident=True, confidence=0.9, margin=0.2, present=True):
    return {"card": card, "present": present, "confident": confident,
            "confidence": confidence, "suit_margin": margin}


def test_an_unreadable_poll_does_not_erase_a_confirmed_card():
    """8H detected, UNKNOWN, 8H detected -> TURN stays 8H, confirmed."""
    memory = CardMemory(clear_frames=10, confirm_frames=2)
    reads = {slot: _read(None, False, 0.0, None, present=False) for slot in tracker_module.CARD_SLOTS}
    seen = {slot: None for slot in tracker_module.CARD_SLOTS}
    statuses = []
    for turn_read in (_read("8H"), _read("8H"), _read(None, False, 0.2, None), _read("8H")):
        reads = dict(reads, turn=turn_read, player_1=_read("AC"))
        cards = memory.update(dict(seen, turn=turn_read["card"] if turn_read["confident"] else None,
                                   player_1="AC"), reads)
        statuses.append((cards.get("turn"), card_status(turn_read, memory.support("turn"))[0]))
    assert statuses[2] == ("8H", HELD)
    assert statuses[3] == ("8H", CONFIRMED)


def test_an_ambiguous_poll_does_not_unconfirm_a_card():
    confirmed = (1.8, 2, 0.9)
    status, why = card_status(_read("8D", confident=False, confidence=0.7, margin=0.01), confirmed)
    assert status == HELD and "ambiguous this poll" in why
    # ...while a card seen only once and then ambiguous is still reported AMBIGUOUS.
    assert card_status(_read("8D", False, 0.7, 0.01), (0.9, 1, 0.9))[0] == tracker_module.AMBIGUOUS


# -- round association ---------------------------------------------------------------------

def test_one_misread_player_card_does_not_start_a_new_hand():
    memory = CardMemory(clear_frames=10, confirm_frames=2)
    full = {slot: HAND[slot] for slot in ("player_1", "player_2", "flop_1", "flop_2", "flop_3")}
    reads = {slot: _read(full.get(slot)) if full.get(slot) else _read(None, False, 0.0, None, False)
             for slot in tracker_module.CARD_SLOTS}
    for _ in range(3):
        memory.update(dict(full), reads)
    generation = memory.generation
    misread = dict(full, player_2="8H")                   # one card reads differently
    for _ in range(6):
        cards = memory.update(misread, dict(reads, player_2=_read("8H")))
    assert memory.generation == generation, "a single misread card restarted the hand"
    assert cards["flop_1"] == "9H"


def test_both_player_cards_changing_is_still_a_new_hand():
    memory = CardMemory(clear_frames=10, confirm_frames=2)
    reads = {slot: _read(HAND.get(slot)) for slot in tracker_module.CARD_SLOTS}
    memory.update(dict(HAND), reads)
    generation = memory.generation
    new = {"player_1": "KS", "player_2": "2C"}
    for _ in range(2):
        memory.update(new, dict(reads, player_1=_read("KS"), player_2=_read("2C")))
    assert memory.generation > generation


# -- board slots ----------------------------------------------------------------------------

def _box(x, y, w=74, h=102):
    return (x, y, w, h)


def test_a_covered_flop_card_does_not_shift_the_board():
    """Boxes laid out as on the live 1366x768 table, flop_1 under the dealer's hand."""
    pitch = 80
    board = [_box(446 + pitch * i, 308) for i in range(5)]
    player = [_box(600, 414), _box(680, 414)]              # centred over flop_3
    dealer = [_box(600, 214), _box(680, 214)]
    rows = [dealer, board[1:], player]                      # flop_1 not found
    slots = table_layout.assign_slots(rows)
    assert slots["flop_2"] == board[1] and slots["flop_3"] == board[2]
    assert slots["turn"] == board[3] and slots["river"] == board[4]
    assert "flop_1" not in slots


def test_a_full_board_and_a_flop_only_board_keep_their_slots():
    pitch = 80
    board = [_box(446 + pitch * i, 308) for i in range(5)]
    player = [_box(600, 414), _box(680, 414)]
    assert table_layout.assign_slots([board, player])["river"] == board[4]
    flop_only = table_layout.assign_slots([board[:3], player])
    assert [flop_only[s] for s in ("flop_1", "flop_2", "flop_3")] == board[:3]
    assert "turn" not in flop_only


def test_positions_that_do_not_fit_the_board_claim_nothing():
    board = [_box(446 + 80 * i, 308) for i in range(3)]
    far_right_player = [_box(1100, 414), _box(1180, 414)]
    # The pair is not over the board, so it is not a hand; without a hand the board
    # is read from the left as before. A hand placing the board off five slots:
    assert table_layout._board_slots(board, [_box(0, 414), _box(80, 414)]) is None


# -- result panel ---------------------------------------------------------------------------

def test_a_dealer_row_found_alone_is_not_labelled_the_players():
    """Live: the dealer's row (y=498) was found without the player's (y=581) and
    'Player best 5' showed the dealer's pair of threes."""
    height = 34
    dealer_row = [(206 + 30 * i, 498, 24, height) for i in range(5)]
    player_row = [(206 + 30 * i, 581, 24, height) for i in range(5)]
    assert result_panel._assign([dealer_row, player_row]) == {"dealer": dealer_row, "player": player_row}
    known_player = (206, 581, float(height))
    assert result_panel._assign([dealer_row], known_player) == {"dealer": dealer_row}
    assert result_panel._assign([player_row], known_player) == {"player": player_row}
    assert result_panel._assign([dealer_row]) == {"player": dealer_row}, "unchanged without history"


# -- scenario input ------------------------------------------------------------------------

def test_the_scenario_engine_receives_the_positional_confirmed_cards():
    slots = {slot: {"card": HAND[slot], "status": CONFIRMED, "readings": 4}
             for slot in se.PLAYER_SLOTS + se.FLOP_SLOTS + ["turn", "river"]}
    result = se.evaluate_round(slots, round_id=36)
    assert result["player_cards"] == ["AC", "8D"] and result["flop_cards"] == ["9H", "7D", "3S"]
    assert result["decision"] == se.PLAY and result["matched_scenarios"] == [se.HIGH_CARD_AKQ]
    dealer = se.evaluate_dealer_qualification(["JH", "3D"], ["9H", "7D", "3S", "8H", "6H"])
    assert dealer["dealer_hand_detail"] == "PAIR OF 3s" and dealer["dealer_qualified"] is False


def test_an_unconfirmed_player_card_keeps_the_scenario_waiting_and_says_why():
    slots = {slot: {"card": HAND[slot], "status": CONFIRMED, "readings": 4}
             for slot in se.PLAYER_SLOTS + se.FLOP_SLOTS}
    slots["player_2"] = {"card": None, "status": "UNKNOWN", "readings": 0}
    result = se.evaluate_round(slots, round_id=36)
    assert result["decision"] == se.WAIT
    assert result["wait_reasons"] == ["player_2: no card (UNKNOWN)"]
