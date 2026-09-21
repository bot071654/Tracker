"""Builds the record for one completed hand: cards, hands, winner, fingerprint."""

from poker.hand_evaluator import (
    compare_hands, dealer_qualifies, evaluate_showdown, evaluate_street, normalize_card,
)

COMMUNITY_ORDER = ["flop_1", "flop_2", "flop_3", "turn", "river"]

# Order used for the fingerprint and for the database/Excel columns.
CARD_ORDER = [
    "player_1", "player_2",
    "flop_1", "flop_2", "flop_3",
    "turn", "river",
    "dealer_1", "dealer_2",
]


def build_fingerprint(cards):
    """Fingerprint for a completed hand, e.g. "8S-9C-2D-QH-3D-3H-6S-8D-QD".

    `cards` is a dict of slot name -> card string. All nine slots are required.
    """
    missing = [slot for slot in CARD_ORDER if not cards.get(slot)]
    if missing:
        raise ValueError("Incomplete hand, missing: %s" % ", ".join(missing))
    return "-".join(normalize_card(cards[slot]) for slot in CARD_ORDER)


def hands_so_far(cards):
    """Evaluate the table mid-hand, for the live display.

    `cards` is slot -> card or None. Returns a dict with:

        player_hand   the player's best hand once the flop is out, else None
        dealer_hand   the dealer's, once their cards are shown, else None
        winner        who is ahead, once both hands can be worked out
        qualified     whether the dealer's hand qualifies, or None

    Returns None values rather than raising while cards are still missing or
    momentarily misread.
    """
    result = {"player_hand": None, "dealer_hand": None,
              "winner": None, "qualified": None,
              "player_best_five": None, "dealer_best_five": None}
    community = [cards.get(slot) for slot in COMMUNITY_ORDER]

    try:
        player = evaluate_street([cards.get("player_1"), cards.get("player_2")], community)
        dealer = evaluate_street([cards.get("dealer_1"), cards.get("dealer_2")], community)
    except ValueError:
        return result       # a duplicate or unreadable card; nothing to show yet

    if player:
        result["player_hand"] = player["name"]
        result["player_best_five"] = player["best_five"]
    if dealer:
        result["dealer_hand"] = dealer["name"]
        result["dealer_best_five"] = dealer["best_five"]
    if player and dealer and not _repeated(cards):
        # Each seat is evaluated on its own, so neither would notice a card
        # misread into both. Declaring a winner from a table that cannot exist
        # would be worse than saying nothing.
        result["winner"] = compare_hands(player, dealer)
        result["qualified"] = dealer_qualifies(dealer)
    return result


def _repeated(cards):
    """True when a card appears in more than one slot."""
    known = [card for card in cards.values() if card]
    return len(set(known)) != len(known)


def build_hand_record(cards):
    """Turn nine recognised cards into the record that gets stored.

    Returns a dict with every card slot plus player_hand, dealer_hand, the
    winner, whether the dealer qualified, and hand_fingerprint.
    """
    normalized = {slot: normalize_card(cards[slot]) for slot in CARD_ORDER}

    # Nine cards come from one deck, so they must all differ. The evaluator
    # only ever sees one seat at a time and cannot catch a card that was
    # misread into both seats, so the whole table is checked here.
    seen = list(normalized.values())
    repeated = sorted({card for card in seen if seen.count(card) > 1})
    if repeated:
        raise ValueError(
            "The same card was read in more than one place: %s" % ", ".join(repeated)
        )

    community = [normalized[slot] for slot in ("flop_1", "flop_2", "flop_3", "turn", "river")]
    player, dealer = evaluate_showdown(
        [normalized["player_1"], normalized["player_2"]],
        [normalized["dealer_1"], normalized["dealer_2"]],
        community,
    )

    record = dict(normalized)
    record["player_hand"] = player["name"]
    record["dealer_hand"] = dealer["name"]
    record["winner"] = compare_hands(player, dealer)
    record["dealer_qualified"] = dealer_qualifies(dealer)
    # Kept for the optional result-box validation and for the log; not stored.
    record["player_best_five"] = player["best_five"]
    record["dealer_best_five"] = dealer["best_five"]
    record["hand_fingerprint"] = build_fingerprint(normalized)
    return record
