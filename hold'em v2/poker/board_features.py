"""What the player's situation on the flop actually looks like.

In Casino Hold'em the one decision is made on the flop, with the turn and the
river still to come. So the made hand alone says very little: "High Card" covers
both a hand that is drawing dead and one card away from a flush.

These are the things a rule can ask about, worked out from the cards rather than
typed in by hand - there are far too many board textures to enumerate.
"""

from poker.hand_evaluator import (
    RANK_VALUES, _straight_high, evaluate_hand, parse_card,
)

FLOP_SLOTS = ["flop_1", "flop_2", "flop_3"]
HOLE_SLOTS = ["player_1", "player_2"]

# Names used in the rule builder and stored in config/scenarios.json.
NO_DRAW = "no draw"
FLUSH_DRAW = "four to a flush"
OPEN_STRAIGHT = "four to a straight (open)"
GUTSHOT = "four to a straight (gutshot)"

DRAWS = [FLUSH_DRAW, OPEN_STRAIGHT, GUTSHOT, NO_DRAW]

# Where a pair (or better) came from. A pair sitting entirely on the board is
# shared with the dealer and worth nothing; one made with a hole card is real.
PAIR_ON_BOARD = "pair only on the board"
PAIR_FROM_HOLE = "pair uses one of my cards"
POCKET_PAIR = "pocket pair"

PAIR_SOURCES = [PAIR_FROM_HOLE, POCKET_PAIR, PAIR_ON_BOARD]

OVERCARDS = {0: "no overcards", 1: "one overcard", 2: "two overcards"}

# Three more things a rule can ask about, worked out from the same cards.
#
# These sit alongside the ones above rather than replacing them: PAIR_SOURCES
# asks where the player's pair came from, which says nothing when the player
# has no pair at all, and FLOP_PAIRED asks only about the board itself.
FLOP_PAIRED = "the flop is paired"

# A big card is what makes an otherwise empty hand worth playing, so it is
# asked about on its own. There are two versions of the question and they are
# different questions, so both are offered rather than one of them being
# quietly taken to mean the other:
#
#   HIGH_AKQ       the highest card anywhere - the player's two and the flop.
#                  This is the top card of the made hand, so "A high" reads
#                  the way it does at a table.
#   HIGH_AKQ_HOLE  the highest of the player's own two cards. An ace on the
#                  flop is the dealer's ace as well, and plays the same for
#                  both seats, so a rule that wants a big card the player
#                  actually holds wants this one.
#
# The Scenario Engine beside these rules asks the second question (see
# config/scenario_engine.json: high_card_source, "player" by default), so a
# flop rule written with HIGH_AKQ can recommend playing a hand the engine
# calls DON'T_PLAY. That is not a fault in either of them - they are being
# asked different things - but it is worth knowing before picking one.
HIGH_AKQ = "highest card showing is A, K or Q"
HIGH_AKQ_HOLE = "one of my two cards is A, K or Q"
HIGH_CARDS = ["A", "K", "Q"]

BOARD_CONDITIONS = [FLOP_PAIRED, HIGH_AKQ, HIGH_AKQ_HOLE]


def flush_draw(cards):
    """True when four of the five cards share a suit - one short of a flush."""
    suits = [parse_card(card)[1] for card in cards]
    return any(suits.count(suit) == 4 for suit in set(suits))


def is_flush(cards):
    suits = [parse_card(card)[1] for card in cards]
    return any(suits.count(suit) >= 5 for suit in set(suits))


def straight_outs(cards):
    """Ranks that would complete a straight, given these cards.

    Two or more means the draw is open at both ends (or a double gutshot);
    exactly one means a gutshot.
    """
    values = {RANK_VALUES[parse_card(card)[0]] for card in cards}
    if _straight_high(values):
        return []                       # already a straight; nothing to draw to
    return [rank for rank in range(2, 15) if _straight_high(values | {rank})]


def straight_draw(cards):
    """OPEN_STRAIGHT, GUTSHOT or None."""
    outs = straight_outs(cards)
    if len(outs) >= 2:
        return OPEN_STRAIGHT
    if len(outs) == 1:
        return GUTSHOT
    return None


def overcard_count(hole_cards, board_cards):
    """How many hole cards are higher than every card on the board."""
    if not board_cards:
        return 0
    highest = max(RANK_VALUES[parse_card(card)[0]] for card in board_cards)
    return sum(1 for card in hole_cards
               if RANK_VALUES[parse_card(card)[0]] > highest)


def pair_source(hole_cards, board_cards):
    """Where the player's pair comes from, or None when there is no pair.

    Only reports on a pair: with two pair or better the question stops being
    interesting, because the hand is strong either way.
    """
    hole_ranks = [parse_card(card)[0] for card in hole_cards]
    board_ranks = [parse_card(card)[0] for card in board_cards]

    if len(hole_ranks) == 2 and hole_ranks[0] == hole_ranks[1]:
        return POCKET_PAIR
    if any(rank in board_ranks for rank in hole_ranks):
        return PAIR_FROM_HOLE
    if len(set(board_ranks)) < len(board_ranks):
        return PAIR_ON_BOARD
    return None


def flop_is_paired(board_cards):
    """True when two of the board cards share a rank.

    This is about the board alone, whatever the player holds - a paired board
    is the same board for both seats, and pair_source above cannot answer it
    because it reports on the player's pair.
    """
    ranks = [parse_card(card)[0] for card in board_cards if card]
    return len(set(ranks)) < len(ranks)


def highest_rank(cards):
    """The highest rank among `cards`, e.g. "A", or None when there are none."""
    known = [card for card in cards if card]
    if not known:
        return None
    return max((parse_card(card)[0] for card in known),
               key=lambda rank: RANK_VALUES[rank])


def describe_flop(hole_cards, board_cards):
    """Everything a rule can ask about the player's flop situation.

    Returns a dict, or None until both hole cards and the whole flop are known:

        hand             the made hand, e.g. "High Card"
        draw             FLUSH_DRAW / OPEN_STRAIGHT / GUTSHOT / NO_DRAW
        pair             where a pair came from, or None
        overcards        0, 1 or 2
        flop_paired      two of the board cards share a rank
        highest          highest rank showing, across the hole cards and board
        highest_hole     highest rank among the player's own two cards
    """
    hole = [card for card in hole_cards if card]
    board = [card for card in board_cards if card]
    if len(hole) < 2 or len(board) < 3:
        return None

    cards = hole + board
    if len(set(cards)) != len(cards):
        return None                     # a card was read into two places

    draw = (FLUSH_DRAW if flush_draw(cards) and not is_flush(cards)
            else straight_draw(cards) or NO_DRAW)
    return {
        "hand": evaluate_hand(cards)["name"],
        "draw": draw,
        "pair": pair_source(hole, board),
        "overcards": overcard_count(hole, board),
        "flop_paired": flop_is_paired(board),
        "highest": highest_rank(cards),
        "highest_hole": highest_rank(hole),
    }


def describe_flop_from_slots(cards):
    """describe_flop for a slot dictionary as the tracker reports it."""
    return describe_flop(
        [cards.get(slot) for slot in HOLE_SLOTS],
        [cards.get(slot) for slot in FLOP_SLOTS],
    )


def summarise(features):
    """A short readable line, e.g. "High Card, four to a flush"."""
    if not features:
        return "--"
    parts = [features["hand"]]
    if features["draw"] != NO_DRAW:
        parts.append(features["draw"])
    if features["pair"] == PAIR_ON_BOARD:
        parts.append("pair is on the board")
    elif features["hand"] == "High Card" and features["overcards"]:
        parts.append(OVERCARDS[features["overcards"]])
    return ", ".join(parts)
