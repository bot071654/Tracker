"""Reading a "10", whose two digits crowd the suit beside them.

Every other rank is a single character with clear space before the pip. A ten
is two, so the space between its digits can be as wide as the space before the
suit - and when the index is tight the "0" and the pip touch outright, arriving
as one connected blob. Both cases used to read as a four.

These cards are drawn in a heavy font, with the barcode and the upside-down
index this casino prints, so the layout is exercised rather than the artwork.
"""

import os

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from poker.hand_evaluator import ALL_CARDS  # noqa: E402
from recognition.card_recognizer import recognize_card  # noqa: E402

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
PIPS = {"S": "\u2660", "H": "\u2665", "D": "\u2666", "C": "\u2663"}
RED = {"H", "D"}
TENS = ["10S", "10H", "10D", "10C"]


def _font(name, size):
    path = os.path.join(FONT_DIR, name)
    return ImageFont.truetype(path, size) if os.path.exists(path) else None


pytestmark = pytest.mark.skipif(
    _font("ariblk.ttf", 20) is None, reason="needs a heavy system font"
)


def casino_card(card, width=95, height=124, gap=0.10, rank_scale=0.26,
                rank_x=0.09, rotate=0):
    """A card printed the way this casino prints them.

    The pip is placed after the rank with `gap` between them, as a fraction of
    the card width - never at a fixed position, which on a wide "10" would put
    the pip on top of the digit. Real cards do not overlap their own glyphs.
    """
    rank, suit = card[:-1], card[-1]
    colour = (215, 30, 35) if suit in RED else (25, 25, 25)
    image = Image.new("RGB", (width, height), (20, 85, 60))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([2, 2, width - 3, height - 3], radius=7, fill=(250, 250, 248))

    rank_font = _font("ariblk.ttf", int(height * rank_scale))
    pip_font = _font("seguisym.ttf", int(height * 0.22)) or rank_font

    rank_width = draw.textlength(rank, font=rank_font)
    pip_left = int(width * rank_x + rank_width + width * gap)

    def index(flip):
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        pen.text((int(width * rank_x), int(height * 0.05)), rank,
                 font=rank_font, fill=colour)
        pen.text((pip_left, int(height * 0.06)), PIPS[suit],
                 font=pip_font, fill=colour)
        image.paste(layer.rotate(180) if flip else layer, (0, 0),
                    layer.rotate(180) if flip else layer)

    index(False)
    index(True)                                     # the inverted foot index
    for y in range(int(height * 0.44), int(height * 0.56), 3):
        draw.line([(int(width * 0.30), y), (int(width * 0.62), y)],
                  fill=(90, 90, 95), width=2)       # the barcode
    if rotate:
        image = image.rotate(rotate, expand=False, fillcolor=(20, 85, 60))
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def read(card, **layout):
    result = recognize_card(casino_card(card, **layout))
    return result["card"] if result else None


# How close the pip is printed to the rank, from generous to touching outright.
LAYOUTS = {
    "roomy": {"gap": 0.18},
    "normal": {},
    "tight": {"gap": 0.04},
    "touching": {"gap": 0.0},
    "large rank": {"rank_scale": 0.32},
    "large rank, tight": {"rank_scale": 0.32, "gap": 0.03},
    "rank shifted right": {"rank_x": 0.16},
    "rotated": {"rotate": 4},
    "small card": {"width": 72, "height": 95},
    "small card, tight": {"width": 72, "height": 95, "gap": 0.04},
}


@pytest.mark.parametrize("name", sorted(LAYOUTS))
@pytest.mark.parametrize("card", TENS)
def test_a_ten_reads_as_a_ten(card, name):
    assert read(card, **LAYOUTS[name]) == card


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_no_other_rank_is_mistaken_for_a_ten(name):
    """The extra split hypotheses must not turn ordinary cards into tens."""
    mistaken = [card for card in ALL_CARDS if not card.startswith("10")
                and (read(card, **LAYOUTS[name]) or "").startswith("10")]
    assert mistaken == []


def test_the_whole_deck_reads_on_a_tight_index():
    """The layout that used to break tens must not break anything else."""
    wrong = [card for card in ALL_CARDS if read(card, gap=0.04) != card]
    assert wrong == []


# -- the split of last resort --------------------------------------------------

def build_strip(blobs, width=330, height=170):
    """A binary index strip with ink at the given (left, width) positions."""
    strip = np.zeros((height, width), np.uint8)
    for left, blob_width in blobs:
        strip[30:height - 30, left:left + blob_width] = 255
    return strip


def test_an_index_that_never_splits_still_yields_a_reading():
    """The failure that stopped a ten being taught.

    The digits of the "10" sit close enough to group together while the "0" is
    fused to the pip, so the ink is two blobs that no gap width separates into
    a rank and a suit. There has to be a last-resort cut, or the card cannot be
    read or taught at all.
    """
    from recognition.card_detector import _splits

    strip = build_strip([(20, 40), (70, 150)])     # "1", then "0"+pip fused
    assert _splits(strip, axis=0), "no reading offered for a fused index"


def test_a_single_merged_blob_still_yields_a_reading():
    from recognition.card_detector import _splits

    strip = build_strip([(20, 200)])               # everything fused together
    assert _splits(strip, axis=0), "no reading offered for one merged blob"


def test_teaching_accepts_a_ten_whose_pip_is_fused_to_the_digit():
    """Teaching knows the answer, so it must not refuse on a shape rule."""
    from recognition.card_recognizer import save_glyph_templates
    import shutil
    import tempfile

    import recognition.card_recognizer as recognizer
    from config import settings

    original = settings.TEMPLATE_DIR
    scratch = tempfile.mkdtemp()
    try:
        shutil.copytree(original, os.path.join(scratch, "templates"))
        settings.TEMPLATE_DIR = os.path.join(scratch, "templates")
        recognizer.TEMPLATE_DIR = settings.TEMPLATE_DIR
        recognizer.load_templates(force=True)

        rank_path, suit_path = save_glyph_templates(
            casino_card("10C", gap=0.0), "10C"
        )
        assert os.path.exists(rank_path) and os.path.exists(suit_path)
    finally:
        settings.TEMPLATE_DIR = original
        recognizer.TEMPLATE_DIR = original
        shutil.rmtree(scratch, ignore_errors=True)
        recognizer.load_templates(force=True)
