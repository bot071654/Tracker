"""Turning what the tracker already knows into something worth hearing.

Every name here comes from the project's own vocabulary - hand_evaluator's
SUIT_NAMES and HAND_NAMES - so the voice cannot start calling a hand something
the rest of the application does not. Nothing in this module decides anything;
it only puts existing values into words.
"""

from poker.hand_evaluator import SUIT_NAMES, parse_card

# Ranks as they are said rather than printed. Only the four that differ are
# listed; the numbers say themselves.
SPOKEN_RANKS = {"J": "Jack", "Q": "Queen", "K": "King", "A": "Ace"}


def say_card(card):
    """"AS" -> "Ace of Spades". Unreadable input comes back as-is."""
    try:
        rank, suit = parse_card(card)
    except Exception:                       # noqa: BLE001 - never break on a bad card
        return str(card)
    return "%s of %s" % (SPOKEN_RANKS.get(rank, rank), SUIT_NAMES.get(suit, suit))


def say_cards(cards):
    """A list of cards as one spoken phrase, comma separated."""
    return ", ".join(say_card(card) for card in cards if card)


def say_hand(name):
    """A hand name as the evaluator gives it.

    HAND_NAMES are already English ("Two Pair", "Three of a Kind"), so this is
    only a lowercasing - said aloud, a capital is noise. "Four of a Kind"
    keeps its shape; nothing is re-worded.
    """
    return (name or "").lower()


def say_winner(winner, hand=None, qualified=None):
    """The result of a round, in the project's own terms.

    `winner` is compare_hands' verdict - "Player", "Dealer" or "Tie". `hand`
    is that seat's hand name, added only when it was given. `qualified` is the
    Casino Hold'em rule the record already stores: when the dealer does not
    qualify it is the reason the round paid the way it did, so it is worth
    hearing.
    """
    if not winner:
        return None
    if winner == "Tie":
        phrase = "Tie"
    else:
        phrase = "%s wins" % winner
        if hand:
            phrase += " with %s" % say_hand(hand)
    if qualified is False:
        phrase += ", dealer did not qualify"
    return phrase
