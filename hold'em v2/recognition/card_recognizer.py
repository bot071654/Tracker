"""Template-matching card recognition.

Rank and suit are matched separately, which keeps the template library at 13
ranks + 4 suits instead of 52 whole cards.  Ink colour narrows the suit to two
candidates before matching, which makes suit recognition very reliable.

Templates live in recognition/templates/{ranks,suits}/*.png.  A file may carry
a suffix so several samples can share a label: "Q.png", "Q_casino.png" and
"Q_2.png" are all templates for the queen.
"""

import glob
import logging
import os

import cv2

from config.settings import TEMPLATE_DIR
from poker.hand_evaluator import RANKS, SUITS
from recognition.card_detector import (
    RANK_SIZE, SUIT_SIZE, card_present, extract_glyph_candidates,
)

logger = logging.getLogger(__name__)

RED_SUITS = ["H", "D"]
BLACK_SUITS = ["S", "C"]

# How far the chosen suit must beat the other suit of its own colour before
# the suit counts as settled.
#
# Colour is read from the ink and is never in doubt; which of the two suits of
# that colour it is comes down to the shape of the pip alone. A clipped pip
# loses that shape - and the ten is the card that clips, because two digits
# push its pip hard against the edge of the index strip.
#
# Measured on this casino's own cards, a correct reading wins by at least
# 0.09 (the worst was an eight of spades at 0.095, a nine of clubs at 0.106),
# and across a drawn 52-card deck the worst was 0.092. Trimming the edge off a
# real nine of clubs to imitate a clipped ten collapses the margin to 0.004 -
# a coin toss that the recogniser would otherwise report as a confident card.
# 0.05 sits in the gap between the two, closer to the ambiguous end.
SUIT_MARGIN = 0.05

# The same question, asked of the rank: how far the chosen rank must beat the
# second-best rank before the rank counts as settled.
#
# The suit has been guarded this way from the start and the rank never was,
# which left the rank the only part of a card that could be decided by a hair.
# It is also the part that goes wrong: replaying the 1,435 recorded frames of
# session 20260917_120408 against their read-by-eye cards, the recogniser
# accepted 1,248 readings and 29 of them were wrong - and the errors are
# concentrated in readings whose two best ranks were level (a nine read as a
# queen, a seven as an ace, a jack as an ace).
#
# Over those readings a correct one beats the runner-up rank by 0.183 at the
# 5th percentile; the wrong ones sit at a median of 0.105. On the session,
# 0.10 leaves 15 of the 29 errors and costs nothing in coverage - every slot
# that was ever read correctly still is, and the worst delay to a slot's first
# correct reading is two polls, about 0.4s. Live that is free, because a slot
# is read about thirty times before it matters.
#
# The 43 labelled crops in tests/fixtures are the other side of it: single
# hard frames, with no second chance. There 0.10 keeps 29 of the 35 that read
# correctly and refuses the one misread that clears every other check (a two
# of diamonds read as a three, its ranks 0.079 apart). Going further buys
# nothing there - no further misread is caught above 0.10 - and keeps costing
# correct crops, which is why it stops here rather than at the session's own
# optimum of 0.12.
#
# Two of those crops are refused that were not before: a nine of spades whose
# nine beat the queen by 0.052, and a five of clubs whose five beat the six by
# 0.073. Both are genuine near-ties - the nine/queen pair is exactly the
# confusion that put a queen of clubs in the player's hand for three polls -
# and a reading that close is not a card.
#
# What is left is the other half of the errors, which are suits - a jack of
# diamonds read as a jack of hearts - at suit margins of 0.053 to 0.107.
# Raising SUIT_MARGIN to catch them costs a quarter of the slots entirely
# (at 0.10, 24 of 104 are never read at all), so it stays where it is.
RANK_MARGIN = 0.10

_templates = None  # {"ranks": {label: [images]}, "suits": {label: [images]}}


class TemplatesMissingError(RuntimeError):
    """Raised when the template library is empty."""


def _label_from_filename(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return name.split("_")[0].upper()


def _load_folder(folder, size, valid_labels):
    loaded = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.png"))):
        label = _label_from_filename(path)
        if label not in valid_labels:
            logger.warning("Ignoring template with unknown label: %s", path)
            continue
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            logger.warning("Could not read template: %s", path)
            continue
        if (image.shape[1], image.shape[0]) != size:
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        loaded.setdefault(label, []).append(image)
    return loaded


def load_templates(force=False):
    """Load (and cache) the template library."""
    global _templates
    if _templates is not None and not force:
        return _templates
    ranks = _load_folder(os.path.join(TEMPLATE_DIR, "ranks"), RANK_SIZE, set(RANKS))
    suits = _load_folder(os.path.join(TEMPLATE_DIR, "suits"), SUIT_SIZE, set(SUITS))
    _templates = {"ranks": ranks, "suits": suits}
    logger.info(
        "Loaded templates: %d ranks (%d images), %d suits (%d images)",
        len(ranks), sum(len(v) for v in ranks.values()),
        len(suits), sum(len(v) for v in suits.values()),
    )
    return _templates


def template_status():
    """(missing_ranks, missing_suits) — labels with no template image."""
    templates = load_templates()
    missing_ranks = [rank for rank in RANKS if rank not in templates["ranks"]]
    missing_suits = [suit for suit in SUITS if suit not in templates["suits"]]
    return missing_ranks, missing_suits


# How far a glyph is allowed to sit off centre before the match gives up.
#
# The glyph is letterboxed to a fixed size, so in principle it is always in
# the same place. In practice the box it was cut from moves by a pixel or two
# between frames - the card drifts, the detected edge lands differently, the
# video is re-compressed - and a correlation taken at one fixed alignment
# falls off sharply for that. The pip loses more than the digit, because it is
# smaller, and the suit is exactly what a club and a spade have to be told
# apart by.
#
# Measured on the 87 live crops that were read confidently and then refused
# for an undecided suit: matching at one alignment separates the two black
# suits by a median of 0.043, just under the 0.05 the suit has to win by, and
# only 7% of them clear it. Allowing the template to slide three pixels takes
# the median to 0.055 and 75% of them clear it - the right suit gains from the
# slack and the wrong one does not, which is what makes it a sharper match
# rather than a looser one.
#
# It is not free, but nearly: 5.81ms per glyph to 5.91ms, under 2%.
GLYPH_SHIFT = 3


def _match_score(patch, template, shift=GLYPH_SHIFT):
    """Normalised correlation between two same-sized binary glyphs (0.0 - 1.0).

    The best correlation over a small range of offsets, so that a glyph a
    couple of pixels off centre is still recognised as itself.
    """
    if patch.shape != template.shape:
        template = cv2.resize(template, (patch.shape[1], patch.shape[0]))
    if shift:
        patch = cv2.copyMakeBorder(patch, shift, shift, shift, shift,
                                   cv2.BORDER_CONSTANT, value=0)
    result = cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED)
    return float(max(0.0, result.max()))


def _label_scores(patch, candidates, templates):
    """(score, label) for each candidate label, best first."""
    scored = []
    for label in candidates:
        images = templates.get(label) or []
        if images:
            scored.append((max(_match_score(patch, image) for image in images), label))
    scored.sort(reverse=True)
    return scored


def _best_label(patch, candidates, templates):
    """(label, score) for the best-matching template among `candidates`."""
    scored = _label_scores(patch, candidates, templates)
    return (scored[0][1], scored[0][0]) if scored else (None, 0.0)


def _score_glyphs(glyphs, templates):
    """Match one candidate reading, keeping every score it was decided on.

    Returns a dict with the reading - rank, suit, rank_score, suit_score,
    rank_margin and suit_margin - plus rank_scores and suit_scores, each
    label's best score as (score, label) pairs, best first. The reading is
    exactly what it always was; the score lists are what a diagnosis needs to
    see, such as a rank that won by a hair or a suit that only just beat the
    other suit of its colour.

    A margin is how far the chosen label beat the runner-up it was actually
    choosing between - the other suit of the same colour, or the second-best
    rank - which is a different question from how well it matched: a pip can
    match its template poorly and still be plainly a club rather than a spade,
    and it can match well while being neither. The same holds for the rank: a
    nine that matches its template at 0.80 with the queen at 0.79 behind it
    has been recognised as nothing in particular.
    """
    suit_candidates = RED_SUITS if glyphs["red"] else BLACK_SUITS
    rank_scores = _label_scores(glyphs["rank"], RANKS, templates["ranks"])
    rank, rank_score = ((rank_scores[0][1], rank_scores[0][0])
                        if rank_scores else (None, 0.0))
    suit_scores = _label_scores(glyphs["suit"], suit_candidates, templates["suits"])
    reading = {"rank": rank, "suit": None, "rank_score": rank_score,
               "suit_score": 0.0, "rank_margin": 0.0, "suit_margin": 0.0,
               "rank_scores": rank_scores, "suit_scores": suit_scores}
    if rank_scores:
        # Only one rank template loaded means there is nothing to beat, so the
        # margin is the score itself rather than a free pass.
        runner_up = rank_scores[1][0] if len(rank_scores) > 1 else 0.0
        reading["rank_margin"] = rank_score - runner_up
    if suit_scores:
        reading["suit_score"], reading["suit"] = suit_scores[0]
        runner_up = suit_scores[1][0] if len(suit_scores) > 1 else 0.0
        reading["suit_margin"] = reading["suit_score"] - runner_up
    return reading


def _read_glyphs(glyphs, templates):
    """(rank, suit, rank_score, suit_score, suit_margin) for one candidate reading."""
    reading = _score_glyphs(glyphs, templates)
    return (reading["rank"], reading["suit"], reading["rank_score"],
            reading["suit_score"], reading["suit_margin"])


def _decisive(rank_margin, suit_margin):
    """Did both halves of the card beat what they were being chosen between?

    Both have to clear their own margin. A reading where either half was a
    coin toss is not a card, however well the other half matched.
    """
    return rank_margin >= RANK_MARGIN and suit_margin >= SUIT_MARGIN


def undecided_half(read):
    """Which half of a reading was too close to call, as a phrase, or None.

    `read` is a read_slot/recognize_card result. Used for the log and the
    status line, so a refused card says which question could not be answered.
    """
    rank_margin, suit_margin = read.get("rank_margin"), read.get("suit_margin")
    if suit_margin is not None and suit_margin < SUIT_MARGIN:
        return ("only beat the other suit of its colour by %.3f" % suit_margin)
    if rank_margin is not None and rank_margin < RANK_MARGIN:
        return ("only beat the next rank by %.3f" % rank_margin)
    return None


def recognize_card(image, threshold=0.62, detail=False):
    """Recognise a single card image.

    Returns a dict:
        {"rank": "8", "suit": "D", "card": "8D", "confidence": 0.97}
    with extra keys "rank_confidence", "suit_confidence", "rank_margin",
    "suit_margin" and "confident". "confident" is False when the confidence is
    below `threshold`, or when either half of the card was decided by less
    than its margin - the rank barely beating the next rank, or the suit
    barely beating the other suit of its colour. Returns None when no reading
    could be extracted at all.

    A card index can sometimes be split in more than one way - most often a
    "10", whose two digits look like a rank beside a suit. Every reading is
    matched and the most convincing one wins.

    With `detail`, the result also carries "detail": how the answer was
    reached - every cut of the index that was tried and what it read as, the
    ink colour, the top rank scores, both suit scores of that colour, and the
    rank and suit glyph images the templates were matched against. Asking for
    detail changes nothing about the answer.
    """
    templates = load_templates()
    if not templates["ranks"] or not templates["suits"]:
        raise TemplatesMissingError(
            "No card templates found in %s - run tools/generate_templates.py "
            "or use Teach cards." % TEMPLATE_DIR
        )

    best, best_rank, chosen, tried = None, None, None, []
    for index, glyphs in enumerate(extract_glyph_candidates(image)):
        reading = _score_glyphs(glyphs, templates)
        rank, suit = reading["rank"], reading["suit"]
        rank_score, suit_score = reading["rank_score"], reading["suit_score"]
        rank_margin, suit_margin = reading["rank_margin"], reading["suit_margin"]
        if rank is None or suit is None:
            continue
        confidence = min(rank_score, suit_score)
        tried.append((index, rank + suit, round(confidence, 4), round(suit_margin, 4)))
        # Confidence decides which cut to believe, as it always has; settling
        # the card only breaks a tie between two equally convincing cuts.
        # Ordering it the other way round lets a cut that is decisive about a
        # card it barely recognises beat one that reads the card plainly - on
        # the sample frame that turned a confident eight of spades into a
        # ten of spades read at 0.35.
        ranking = (confidence, _decisive(rank_margin, suit_margin))
        if best is None or ranking > best_rank:
            best_rank = ranking
            best = {
                "rank": rank,
                "suit": suit,
                "card": rank + suit,
                "confidence": round(confidence, 4),
                "rank_confidence": round(rank_score, 4),
                "suit_confidence": round(suit_score, 4),
                "rank_margin": round(rank_margin, 4),
                "suit_margin": round(suit_margin, 4),
            }
            chosen = (index, glyphs, reading)

    if best is None:
        return None
    decisive = _decisive(best["rank_margin"], best["suit_margin"])
    best["confident"] = best["confidence"] >= threshold and decisive
    if not decisive:
        logger.debug(
            "Undecided reading %s: rank beat the next rank by %.3f (needs %.2f), "
            "suit beat the other %s suit by %.3f (needs %.2f)",
            best["card"], best["rank_margin"], RANK_MARGIN,
            "red" if best["suit"] in RED_SUITS else "black",
            best["suit_margin"], SUIT_MARGIN)
    if detail:
        index, glyphs, reading = chosen
        best["detail"] = {
            "cuts_tried": tried,              # (cut, card, confidence, suit margin)
            "cut_chosen": index,
            "ink": "red" if glyphs["red"] else "black",
            "rank_scores": [(label, round(score, 4))
                            for score, label in reading["rank_scores"][:4]],
            "suit_scores": [(label, round(score, 4))
                            for score, label in reading["suit_scores"]],
            "rank_glyph": glyphs["rank"],
            "suit_glyph": glyphs["suit"],
        }
    return best


def read_slot(image, confidence_threshold=0.62, presence_threshold=0.35,
              detail=False):
    """Read one calibrated region.

    Returns a dict describing the slot:
        {"present": bool, "ratio": float, "card": str|None, "confidence": float,
         "confident": bool, "rank_margin": float, "suit_margin": float}
    With `detail`, a recognised card also carries recognize_card's "detail".
    Never raises for an unreadable card - the tracker must survive bad frames.
    """
    result = {
        "present": False, "ratio": 0.0, "card": None,
        "confidence": 0.0, "confident": False,
    }
    if image is None:
        return result

    present, ratio = card_present(image, presence_threshold)
    result["present"] = present
    result["ratio"] = round(ratio, 4)
    if not present:
        return result

    try:
        recognized = recognize_card(image, confidence_threshold, detail=detail)
    except TemplatesMissingError:
        raise
    except Exception as exc:  # noqa: BLE001 - a bad frame must not stop tracking
        logger.error("Recognition error: %s", exc)
        return result

    if recognized is None:
        return result

    result.update({
        "card": recognized["card"],
        "confidence": recognized["confidence"],
        "confident": recognized["confident"],
        "rank_confidence": recognized["rank_confidence"],
        "suit_confidence": recognized["suit_confidence"],
        "rank_margin": recognized["rank_margin"],
        "suit_margin": recognized["suit_margin"],
    })
    if "detail" in recognized:
        result["detail"] = recognized["detail"]
    return result


def _choose_candidate(candidates, rank, suit):
    """Pick the reading that best agrees with the card we were told this is.

    Teaching knows the answer, so the ambiguous readings of an index (a "10"
    especially) can be settled by seeing which one has the right ink colour and
    looks most like the suit it should be.
    """
    if len(candidates) < 2:
        return candidates[0]

    templates = load_templates()
    best, best_score = candidates[0], -1.0
    for glyphs in candidates:
        score = 0.0
        for template in templates["ranks"].get(rank, []):
            score = max(score, _match_score(glyphs["rank"], template))
        for template in templates["suits"].get(suit, []):
            score += _match_score(glyphs["suit"], template)
        if (suit in RED_SUITS) == glyphs["red"]:
            score += 0.5
        if score > best_score:
            best, best_score = glyphs, score
    return best


def save_glyph_templates(image, card):
    """Save the glyphs from `image` as templates for `card` (e.g. "8D").

    Used by Teach cards to learn the casino's own artwork. Each card keeps its
    own sample - the eight of diamonds and the eight of spades are printed
    slightly differently, and matching takes the best of them - while teaching
    the same card twice simply refreshes its sample.

    Returns the paths written.
    """
    from poker.hand_evaluator import parse_card

    rank, suit = parse_card(card)
    # Teaching knows the answer, so every reading of the index is worth
    # considering - including ones whose suit position does not look like a pip
    # on its own. _choose_candidate settles it against the card it was told.
    candidates = extract_glyph_candidates(image, require_pip_shape=False)
    if not candidates:
        raise ValueError(
            "Could not find a rank and a suit in that card image. The region "
            "may be clipping the card, or something may be covering it."
        )
    glyphs = _choose_candidate(candidates, rank, suit)

    rank_dir = os.path.join(TEMPLATE_DIR, "ranks")
    suit_dir = os.path.join(TEMPLATE_DIR, "suits")
    os.makedirs(rank_dir, exist_ok=True)
    os.makedirs(suit_dir, exist_ok=True)

    tag = (rank + suit).lower()
    rank_path = os.path.join(rank_dir, "%s_%s.png" % (rank, tag))
    suit_path = os.path.join(suit_dir, "%s_%s.png" % (suit, tag))
    cv2.imwrite(rank_path, glyphs["rank"])
    cv2.imwrite(suit_path, glyphs["suit"])
    load_templates(force=True)
    logger.info("Learned templates for %s -> %s, %s", card, rank_path, suit_path)
    return rank_path, suit_path
