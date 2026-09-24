"""Which cut of the index to believe when several read the same card.

THE FAILURE THIS IS ABOUT

The live tracker sat at `Player: 6D / ?` for a whole round with a ten of
spades face up on the table. Its own log said, five times a second:

    Player Card 2 AMBIGUOUS: 10S at 0.91, but only beat the other suit of
    its colour by 0.001

The crop was right, the card was fully visible, the rank read as a ten at
0.92-0.97 against a runner-up of 0.46. What refused it was the suit margin -
and the reason the suit margin was hopeless is that the wrong cut of the index
had been chosen:

    cut 0  10S  conf=0.898  suit margin 0.001   <- chosen, refused
    cut 1  10S  conf=0.897  suit margin 0.095   <- passed over

Both cuts read the same card. The leader won by a thousandth of confidence and
lost by a tenth of suit margin, so a card that two cuts agreed was a ten of
spades was reported as no card at all. A ten is cut more ways than other ranks
- its two digits can look like a rank beside a suit - which is why tens were
252 of the 432 refusals in this machine's logs.

THE RULE

Confidence still decides WHICH card, exactly as before. Among the cuts that
agree with it about the card, the one that is sure of the suit is believed.
Restricting it to agreeing cuts is what makes it safe: it can change whether a
card is accepted and what margins are reported, never which card is reported.
"""

import os

import pytest

cv2 = pytest.importorskip("cv2")

from recognition import card_recognizer as recognizer          # noqa: E402
from recognition.card_recognizer import (                      # noqa: E402
    RANK_MARGIN, SUIT_MARGIN, load_templates, recognize_card,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_ROUND = sorted(
    os.path.join(HERE, "fixtures", "live_regression", name)
    for name in os.listdir(os.path.join(HERE, "fixtures", "live_regression"))
    if name.startswith("10S_cutrank-"))


@pytest.fixture(scope="module", autouse=True)
def templates():
    load_templates(force=True)


# -- the live failure ---------------------------------------------------------

def test_there_are_crops_from_the_round_that_failed():
    assert SCREENSHOT_ROUND, "the regression crops are missing"


@pytest.mark.parametrize("path", SCREENSHOT_ROUND,
                         ids=lambda p: os.path.basename(p))
def test_the_ten_of_spades_is_read_and_kept(path):
    """Every frame of that round, confidently, as the card it plainly is."""
    result = recognize_card(cv2.imread(path))
    assert result is not None, "no reading at all"
    assert result["card"] == "10S", result
    assert result["confident"], (
        "refused: rank margin %.3f (needs %.2f), suit margin %.3f (needs %.2f)"
        % (result["rank_margin"], RANK_MARGIN,
           result["suit_margin"], SUIT_MARGIN))


@pytest.mark.parametrize("path", SCREENSHOT_ROUND,
                         ids=lambda p: os.path.basename(p))
def test_the_chosen_cut_is_decisive_about_the_suit(path):
    """The specific thing that was wrong: the suit half was a coin toss."""
    result = recognize_card(cv2.imread(path))
    assert result["suit_margin"] >= SUIT_MARGIN, result


def test_a_decisive_cut_existed_all_along(path=None):
    """Not a threshold change: the evidence was already there and ignored.

    Each crop offers a cut whose suit margin clears the bar comfortably. If
    this stops being true the fix has been undone by something upstream, and
    lowering SUIT_MARGIN would be the wrong answer.
    """
    for path in SCREENSHOT_ROUND:
        result = recognize_card(cv2.imread(path), detail=True)
        margins = [suit_margin
                   for _cut, card, _confidence, suit_margin
                   in result["detail"]["cuts_tried"] if card == "10S"]
        assert any(margin >= SUIT_MARGIN for margin in margins), (
            os.path.basename(path), margins)


# -- the rule itself, without any image ---------------------------------------

class FakeGlyphs(dict):
    """Stands in for one cut of the index."""


def readings(monkeypatch, cuts):
    """Drive recognize_card over a fixed list of (card, confidence, margins).

    Each entry is (rank, suit, rank_score, suit_score, rank_margin,
    suit_margin). The glyph splitter and the scorer are both replaced, so the
    test is about the choice between cuts and nothing else.
    """
    fakes = [FakeGlyphs(rank=index, suit=index, red=False)
             for index, _ in enumerate(cuts)]
    monkeypatch.setattr(recognizer, "extract_glyph_candidates",
                        lambda image: fakes)
    monkeypatch.setattr(recognizer, "load_templates",
                        lambda *a, **k: {"ranks": {"x": 1}, "suits": {"x": 1}})

    def score(glyphs, _templates):
        rank, suit, rank_score, suit_score, rank_margin, suit_margin = \
            cuts[glyphs["rank"]]
        return {"rank": rank, "suit": suit,
                "rank_score": rank_score, "suit_score": suit_score,
                "rank_margin": rank_margin, "suit_margin": suit_margin,
                "rank_scores": [(rank_score, rank)],
                "suit_scores": [(suit_score, suit)]}

    monkeypatch.setattr(recognizer, "_score_glyphs", score)
    return recognize_card(object())


def test_the_decisive_cut_wins_when_the_cuts_agree(monkeypatch):
    """The live case, in numbers: 0.001 of confidence against 0.094 of margin."""
    result = readings(monkeypatch, [
        ("10", "S", 0.898, 0.898, 0.50, 0.001),     # leader, hopeless suit
        ("10", "S", 0.897, 0.897, 0.38, 0.095),     # same card, sure of it
    ])
    assert result["card"] == "10S"
    assert result["suit_margin"] == pytest.approx(0.095)
    assert result["confident"]


def test_confidence_still_decides_which_card(monkeypatch):
    """A cut decisive about a card it barely recognises must not win.

    This is the eight of spades that became a ten of spades read at 0.35, and
    it is why decisiveness is not simply ranked first.
    """
    result = readings(monkeypatch, [
        ("8", "S", 0.90, 0.90, 0.40, 0.02),         # plainly an eight
        ("10", "S", 0.35, 0.35, 0.60, 0.30),        # decisive about nonsense
    ])
    assert result["card"] == "8S"
    assert not result["confident"]                  # still refused, correctly


def test_a_decisive_cut_naming_another_card_is_ignored(monkeypatch):
    """It may not change which card is reported - only whether it is kept."""
    result = readings(monkeypatch, [
        ("10", "S", 0.90, 0.90, 0.40, 0.01),        # leader, indecisive
        ("K", "S", 0.66, 0.66, 0.50, 0.20),         # decisive, different card
    ])
    assert result["card"] == "10S"
    assert not result["confident"]                  # refused rather than wrong


def test_the_most_confident_agreeing_cut_breaks_a_tie(monkeypatch):
    result = readings(monkeypatch, [
        ("10", "S", 0.80, 0.80, 0.40, 0.20),
        ("10", "S", 0.85, 0.85, 0.40, 0.20),        # both decisive
    ])
    assert result["confidence"] == pytest.approx(0.85)


def test_a_single_cut_is_unaffected(monkeypatch):
    result = readings(monkeypatch, [("7", "D", 0.93, 0.93, 0.40, 0.30)])
    assert result["card"] == "7D" and result["confident"]


def test_no_cut_at_all_reads_nothing(monkeypatch):
    assert readings(monkeypatch, []) is None


def test_a_cut_with_no_rank_or_suit_is_skipped(monkeypatch):
    result = readings(monkeypatch, [
        (None, None, 0.99, 0.99, 0.9, 0.9),         # unreadable
        ("7", "D", 0.80, 0.80, 0.40, 0.30),
    ])
    assert result["card"] == "7D"


def test_the_rank_margin_still_refuses(monkeypatch):
    """Rescuing the suit half must not smuggle a doubtful rank through."""
    result = readings(monkeypatch, [
        ("10", "S", 0.90, 0.90, 0.01, 0.30),        # sure suit, doubtful rank
    ])
    assert result["card"] == "10S"
    assert not result["confident"]
