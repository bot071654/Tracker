"""Reading every card in the deck, and refusing everything that is not one.

Three sources of truth, because each one catches what the others cannot:

    a drawn deck        all 52, in a font the templates were not made from
    live crops          the casino's own artwork, off the real video
    live non-cards      pixels that were read as cards and should not have been

The live images under fixtures/ were kept from one recorded session. Each was
looked at before it was given a name, so the filename is what is actually
printed on the card rather than what the recogniser said at the time - two of
them are named for a card the recogniser got wrong.

What these tests are protecting is an ordering, not a score: a missing card
costs one round, and a wrong card is recorded and quietly changes the history.
So "refused" is an acceptable answer everywhere below, and "confidently
something else" is not.
"""

import glob
import os

import pytest

cv2 = pytest.importorskip("cv2")

from poker.hand_evaluator import RANKS, SUITS  # noqa: E402
from recognition import card_recognizer  # noqa: E402
from recognition.card_recognizer import (  # noqa: E402
    RANK_MARGIN, SUIT_MARGIN, load_templates, recognize_card, template_status,
)
from tests.fake_cards import fonts_available, render_card  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = sorted(glob.glob(os.path.join(HERE, "fixtures", "live_cards", "*.png")))
NOT_CARDS = sorted(glob.glob(os.path.join(HERE, "fixtures", "not_cards", "*.png")))

DECK = [rank + suit for rank in RANKS for suit in SUITS]

needs_fonts = pytest.mark.skipif(
    not fonts_available(), reason="no drawing fonts on this machine")


def label_of(path):
    """The card a fixture is named for: "10C_2.png" -> "10C"."""
    return os.path.basename(path).split("_")[0].upper()


# -- the library ---------------------------------------------------------------

def test_every_rank_and_suit_has_a_template():
    missing_ranks, missing_suits = template_status()
    assert not missing_ranks, "no template for rank(s): %s" % missing_ranks
    assert not missing_suits, "no template for suit(s): %s" % missing_suits


def test_all_52_cards_are_representable():
    """13 ranks x 4 suits, matched separately, is the whole deck."""
    templates = load_templates()
    have = [card for card in DECK
            if templates["ranks"].get(card[:-1]) and templates["suits"].get(card[-1])]
    assert len(have) == 52, "only %d of 52 cards can be formed" % len(have)


def test_no_suit_is_carrying_the_library_alone():
    """A suit with one sample is one bad crop away from being unreadable."""
    templates = load_templates()
    thin = {suit: len(images) for suit, images in templates["suits"].items()
            if len(images) < 2}
    assert not thin, "suits with only one template: %s" % thin


# -- the whole deck ------------------------------------------------------------

@needs_fonts
@pytest.mark.parametrize("layout", ["side", "stacked"])
def test_the_whole_deck_is_read_correctly(layout):
    """All 52, drawn in a different font than the templates came from."""
    wrong, refused = [], []
    for card in DECK:
        result = recognize_card(render_card(card, layout=layout))
        if result is None or not result["confident"]:
            refused.append(card)
        elif result["card"] != card:
            wrong.append("%s read as %s" % (card, result["card"]))
    assert not wrong, wrong
    assert not refused, "refused: %s" % refused


@needs_fonts
def test_no_card_is_ever_read_as_a_different_card():
    """The ordering that matters: refusing is allowed, being wrong is not."""
    for card in DECK:
        for layout in ("side", "stacked"):
            result = recognize_card(render_card(card, layout=layout))
            if result and result["confident"]:
                assert result["card"] == card, (
                    "%s (%s) confidently read as %s" % (card, layout, result["card"]))


@needs_fonts
@pytest.mark.parametrize("pair", [("10C", "10S"), ("8H", "3H"), ("QC", "QS"),
                                  ("6C", "6S"), ("9D", "9H")])
def test_the_pairs_that_get_confused_are_told_apart(pair):
    """Each side of a known-confusable pair reads as itself, not the other."""
    for card in pair:
        result = recognize_card(render_card(card))
        assert result is not None
        assert result["card"] == card, "%s read as %s" % (card, result["card"])


@needs_fonts
def test_a_clipped_pip_is_refused_rather_than_guessed():
    """Cutting the pip off is what makes a club look like a spade. With the
    shape gone there is no answer, and the honest reply is no answer."""
    image = render_card("9C")
    image[:, int(image.shape[1] * 0.62):] = (252, 252, 250)   # wipe the pip
    result = recognize_card(image)
    assert result is None or not result["confident"] or result["suit"] != "C", (
        "a card with no pip was still called a club")


# -- the casino's own artwork ---------------------------------------------------

@pytest.mark.skipif(not LIVE, reason="no live fixtures")
@pytest.mark.parametrize("path", LIVE, ids=lambda p: os.path.basename(p))
def test_a_live_card_is_never_read_as_another_card(path):
    """The weaker promise, held for every live fixture."""
    result = recognize_card(cv2.imread(path))
    if result and result["confident"]:
        assert result["card"] == label_of(path), (
            "%s confidently read as %s" % (os.path.basename(path), result["card"]))


@pytest.mark.skipif(not LIVE, reason="no live fixtures")
def test_most_live_cards_are_actually_accepted():
    """...and the stronger one, held across the set.

    All ten of these were refused by the reader as it stood - read correctly,
    at up to 0.91 confidence, and thrown away because clubs beat spades by
    0.047 where 0.05 was wanted. A queen of clubs filling the card is not a
    marginal case, and a reader that cannot keep it is not usable.

    Eight of the ten came back with that fix. Seven do now, because the rank
    is held to a margin of its own as well as the suit (RANK_MARGIN), and two
    of these ten are rank near-ties:

        9S_1   the nine beat the queen by 0.052
        5C_1   the five beat the six by 0.073

    They are refused on purpose. A nine and a queen separated by 0.05 is the
    confusion that put a queen of clubs in the player's hand for three polls
    on the live table, and a reading that close is not a card. The tens that
    were already refused for their suit still are.

    The number is what was measured, and it is asserted so that it cannot
    quietly fall further.
    """
    accepted = [p for p in LIVE
                if (recognize_card(cv2.imread(p)) or {}).get("confident")]
    assert len(accepted) >= 7, (
        "only %d of %d live cards accepted: missing %s"
        % (len(accepted), len(LIVE),
           [os.path.basename(p) for p in LIVE if p not in accepted]))


@pytest.mark.skipif(not LIVE, reason="no live fixtures")
def test_the_cards_refused_for_their_rank_are_the_near_ties():
    """Which two were given up, and why - so a wider loss is not mistaken for this."""
    refused = {}
    for path in LIVE:
        result = recognize_card(cv2.imread(path))
        if result and not result["confident"] and result["rank_margin"] < RANK_MARGIN:
            refused[os.path.basename(path)] = result
    assert sorted(refused) == ["5C_1.png", "9S_1.png"], sorted(refused)
    # and each of them read the right card - it is the closeness that refuses
    # them, not a wrong answer
    for name, result in refused.items():
        assert result["card"] == name.split("_")[0]


@pytest.mark.skipif(not LIVE, reason="no live fixtures")
def test_the_black_suits_are_separated_on_live_pixels():
    """C against S is where the margin is spent, so measure it there."""
    black = [p for p in LIVE if label_of(p)[-1] in ("C", "S")]
    margins = []
    for path in black:
        result = recognize_card(cv2.imread(path))
        if result and result["card"] == label_of(path):
            margins.append(result["suit_margin"])
    assert margins
    clear = [m for m in margins if m >= SUIT_MARGIN]
    assert len(clear) >= len(margins) * 0.6, (
        "only %d of %d black-suit readings clear the margin: %s"
        % (len(clear), len(margins), [round(m, 4) for m in margins]))


# -- things that are not cards ---------------------------------------------------

@pytest.mark.skipif(not NOT_CARDS, reason="no non-card fixtures")
@pytest.mark.parametrize("path", NOT_CARDS, ids=lambda p: os.path.basename(p))
def test_another_window_is_not_read_as_a_card(path):
    """These are crops of a text sidebar that the tracker captured as the
    dealer's first card while the search covered the whole desktop. One of
    them was read as the ten of spades at 0.68. Whatever else happens, pale
    UI panels must not come back as playing cards."""
    result = recognize_card(cv2.imread(path))
    assert result is None or not result["confident"], (
        "%s was read as %s at %.2f"
        % (os.path.basename(path), result["card"], result["confidence"]))


# -- the mechanism the live readings depend on ------------------------------------

@pytest.mark.skipif(not LIVE, reason="no live fixtures")
def test_letting_the_glyph_sit_off_centre_is_what_carries_them():
    """Guards the reason the live cards pass, not just that they do.

    Without the slack the suit separation collapses below what is asked of
    it, which is how a correctly-read queen of clubs became a dash.
    """
    black = [p for p in LIVE if label_of(p)[-1] in ("C", "S")]

    def margins(shift):
        original = card_recognizer._match_score
        card_recognizer._match_score = lambda p, t, s=shift: original(p, t, s)
        try:
            out = []
            for path in black:
                result = recognize_card(cv2.imread(path))
                if result:
                    out.append(result["suit_margin"])
            return out
        finally:
            card_recognizer._match_score = original

    rigid, slack = margins(0), margins(card_recognizer.GLYPH_SHIFT)
    assert sum(slack) / len(slack) > sum(rigid) / len(rigid), (
        "the slack is not buying anything: rigid %.4f, slack %.4f"
        % (sum(rigid) / len(rigid), sum(slack) / len(slack)))
    assert sum(m >= SUIT_MARGIN for m in slack) > sum(m >= SUIT_MARGIN for m in rigid)
