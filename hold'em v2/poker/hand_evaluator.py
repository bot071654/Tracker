"""Deterministic 7-card poker hand evaluation.

Cards use the notation described in the README: a rank ("2".."10", "J", "Q",
"K", "A") followed by a suit letter (S/H/D/C).  "TD" is accepted on input and
normalised to "10D".
"""

from itertools import combinations

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["S", "H", "D", "C"]

RANK_VALUES = {rank: index + 2 for index, rank in enumerate(RANKS)}  # 2..14
SUIT_NAMES = {"S": "Spades", "H": "Hearts", "D": "Diamonds", "C": "Clubs"}

HAND_NAMES = {
    1: "High Card",
    2: "Pair",
    3: "Two Pair",
    4: "Three of a Kind",
    5: "Straight",
    6: "Flush",
    7: "Full House",
    8: "Four of a Kind",
    9: "Straight Flush",
    10: "Royal Flush",
}

# All 52 cards in canonical notation.
ALL_CARDS = [rank + suit for rank in RANKS for suit in SUITS]


class InvalidCardError(ValueError):
    """Raised when a string is not a valid card."""


def parse_card(card):
    """Split a card string into (rank, suit). Raises InvalidCardError."""
    if not isinstance(card, str):
        raise InvalidCardError("Card must be a string, got %r" % (card,))
    text = card.strip().upper()
    if len(text) < 2:
        raise InvalidCardError("Card too short: %r" % (card,))
    rank, suit = text[:-1], text[-1]
    if rank == "T":
        rank = "10"
    if rank not in RANK_VALUES:
        raise InvalidCardError("Unknown rank %r in card %r" % (rank, card))
    if suit not in SUIT_NAMES:
        raise InvalidCardError("Unknown suit %r in card %r" % (suit, card))
    return rank, suit


def normalize_card(card):
    """Return the canonical form of a card string, e.g. "td" -> "10D"."""
    rank, suit = parse_card(card)
    return rank + suit


def is_valid_card(card):
    """True when the string names a real card."""
    try:
        parse_card(card)
    except InvalidCardError:
        return False
    return True


def card_name(card):
    """Human-readable card name, e.g. "8D" -> "Eight of Diamonds"."""
    rank, suit = parse_card(card)
    words = {
        "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six",
        "7": "Seven", "8": "Eight", "9": "Nine", "10": "Ten",
        "J": "Jack", "Q": "Queen", "K": "King", "A": "Ace",
    }
    return "%s of %s" % (words[rank], SUIT_NAMES[suit])


def _straight_high(values):
    """Highest card of a straight within `values` (a set), or None.

    Handles the wheel (A-2-3-4-5), whose high card counts as 5.
    """
    unique = set(values)
    if 14 in unique:
        unique.add(1)  # ace plays low
    best = None
    for high in range(14, 4, -1):
        if all(high - offset in unique for offset in range(5)):
            best = high
            break
    return best


def _score_five(cards):
    """Score exactly five cards as (category, tiebreakers...). Higher is better."""
    parsed = [parse_card(card) for card in cards]
    values = sorted((RANK_VALUES[rank] for rank, _ in parsed), reverse=True)
    suits = [suit for _, suit in parsed]

    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    # Sort by count first, then by rank: gives the tiebreaker order directly.
    by_count = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    shape = [count for _, count in by_count]
    ordered = [value for value, _ in by_count]

    is_flush = len(set(suits)) == 1
    straight_high = _straight_high(values)

    if is_flush and straight_high:
        if straight_high == 14:
            return (10, 14)
        return (9, straight_high)
    if shape[0] == 4:
        return (8, ordered[0], ordered[1])
    if shape[0] == 3 and shape[1] == 2:
        return (7, ordered[0], ordered[1])
    if is_flush:
        return tuple([6] + values)
    if straight_high:
        return (5, straight_high)
    if shape[0] == 3:
        return tuple([4] + ordered)
    if shape[0] == 2 and shape[1] == 2:
        return tuple([3] + ordered)
    if shape[0] == 2:
        return tuple([2] + ordered)
    return tuple([1] + values)


def evaluate_hand(cards):
    """Evaluate 5-7 cards and return the best five-card hand.

    Returns a dict with:
        name       human-readable hand name, e.g. "Two Pair"
        category   1 (High Card) .. 10 (Royal Flush)
        best_five  the five cards making the hand
        score      comparable tuple; bigger beats smaller
    """
    cards = [normalize_card(card) for card in cards]
    if len(cards) < 5:
        raise InvalidCardError("Need at least 5 cards, got %d" % len(cards))
    if len(set(cards)) != len(cards):
        raise InvalidCardError("Duplicate cards: %s" % ", ".join(sorted(cards)))

    best_score = None
    best_five = None
    for combo in combinations(cards, 5):
        score = _score_five(combo)
        if best_score is None or score > best_score:
            best_score = score
            best_five = list(combo)

    return {
        "name": HAND_NAMES[best_score[0]],
        "category": best_score[0],
        "best_five": best_five,
        "score": best_score,
    }


def evaluate_street(hole_cards, community_cards):
    """One seat's best hand from the cards on the table so far.

    Returns the same dict as evaluate_hand, or None when the hand cannot be
    worked out yet - either a hole card is still unknown, or there are fewer
    than five cards in all. So it reports nothing until that seat's cards and
    the flop are both out, then improves on the turn and the river.

    Both hole cards are required: without them the community cards alone would
    be evaluated, which is the board, not the seat's hand.
    """
    hole = list(hole_cards)
    if not hole or not all(hole):
        return None
    cards = hole + [card for card in community_cards if card]
    if len(cards) < 5:
        return None
    return evaluate_hand(cards)


def compare_hands(player_result, dealer_result):
    """Who has the stronger hand: "Player", "Dealer" or "Tie"."""
    if player_result["score"] > dealer_result["score"]:
        return "Player"
    if player_result["score"] < dealer_result["score"]:
        return "Dealer"
    return "Tie"


# In Casino Hold'em the dealer needs a pair of fours or better to qualify -
# the table itself says so ("DEALER QUALIFIES WITH PAIR OF 4s OR BETTER").
QUALIFYING_PAIR = RANK_VALUES["4"]


def dealer_qualifies(dealer_result):
    """True when the dealer's hand is a pair of fours or better.

    This decides how the round pays, so it is recorded alongside the winner:
    when the dealer does not qualify the ante pays and the call bet is
    returned, whichever hand is stronger.
    """
    category = dealer_result["category"]
    if category > 2:
        return True
    if category == 2:                       # a pair - check which pair
        return dealer_result["score"][1] >= QUALIFYING_PAIR
    return False


def evaluate_showdown(player_cards, dealer_cards, community_cards):
    """Evaluate both seats against a shared board.

    Returns (player_result, dealer_result) — each the dict from evaluate_hand.
    Comparing the two `score` values tells you who has the stronger hand, but
    this application only records the hands; it does not advise on play.
    """
    player = evaluate_hand(list(player_cards) + list(community_cards))
    dealer = evaluate_hand(list(dealer_cards) + list(community_cards))
    return player, dealer
