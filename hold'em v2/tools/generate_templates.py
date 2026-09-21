"""Generate a starter template library from system fonts.

Casino card artwork is consistent but not identical between providers, so
these generated templates are a *starting point*: they get recognition working
immediately, and Calibrate > Learn Cards then adds real glyphs captured from
your own screen, which match far more closely.

Run:  python tools/generate_templates.py
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import TEMPLATE_DIR  # noqa: E402
from poker.hand_evaluator import RANKS  # noqa: E402
from recognition.card_detector import RANK_SIZE, SUIT_SIZE, fit_glyph  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

SUIT_GLYPHS = {"S": "\u2660", "H": "\u2665", "D": "\u2666", "C": "\u2663"}

# Several weights and shapes, because the casino's own font is unknown until a
# card has been taught. An untaught rank then has a few different shapes to
# match against instead of one, which matters most for "10" - the only rank
# that is two characters wide, and so the one least like any single template.
RANK_FONTS = [
    "ariblk.ttf",        # Arial Black - heavy, closest to most casino artwork
    "seguibl.ttf",       # Segoe UI Black
    "arialbd.ttf",       # Arial Bold
    "verdanab.ttf",      # Verdana Bold - wide
    "trebucbd.ttf",      # Trebuchet Bold - rounded
    "DejaVuSans-Bold.ttf",
]
SUIT_FONTS = ["seguisym.ttf", "arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"]
FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
]


def find_font(candidates, size):
    """First available TrueType font from `candidates`, at `size` points."""
    fonts = find_fonts(candidates, size, limit=1)
    return fonts[0] if fonts else None


def find_fonts(candidates, size, limit=4):
    """Up to `limit` available TrueType fonts from `candidates`."""
    fonts = []
    for name in candidates:
        for folder in FONT_DIRS:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                fonts.append(ImageFont.truetype(path, size))
                break
        if len(fonts) >= limit:
            break
    return fonts


def render_glyph(text, font, out_size):
    """Render `text` and return a binary image (white ink on black), cropped."""
    canvas = Image.new("L", (400, 400), 255)
    draw = ImageDraw.Draw(canvas)
    box = draw.textbbox((0, 0), text, font=font)
    x = (400 - (box[2] - box[0])) // 2 - box[0]
    y = (400 - (box[3] - box[1])) // 2 - box[1]
    draw.text((x, y), text, font=font, fill=0)

    gray = np.array(canvas)
    _, binary = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
    coords = cv2.findNonZero(binary)
    if coords is None:
        return None
    x, y, w, h = cv2.boundingRect(coords)
    return fit_glyph(binary[y:y + h, x:x + w], out_size)


def main():
    if Image is None:
        print("Pillow is required to generate templates: pip install pillow")
        return 1

    rank_fonts = find_fonts(RANK_FONTS, 220)
    suit_font = find_font(SUIT_FONTS, 200)
    if not rank_fonts or suit_font is None:
        print("No usable TrueType font found. Use Calibrate > Learn Cards instead.")
        return 1

    rank_dir = os.path.join(TEMPLATE_DIR, "ranks")
    suit_dir = os.path.join(TEMPLATE_DIR, "suits")
    os.makedirs(rank_dir, exist_ok=True)
    os.makedirs(suit_dir, exist_ok=True)

    written = 0
    for rank in RANKS:
        for index, font in enumerate(rank_fonts):
            glyph = render_glyph(rank, font, RANK_SIZE)
            if glyph is None:
                continue
            # The first font keeps the plain name; the rest are extra samples.
            # "font" marks them as drawn rather than learned from the casino,
            # so tools/audit_templates.py knows to leave them alone.
            name = "%s.png" % rank if index == 0 else "%s_font%d.png" % (rank, index)
            cv2.imwrite(os.path.join(rank_dir, name), glyph)
            written += 1

    for suit, character in SUIT_GLYPHS.items():
        glyph = render_glyph(character, suit_font, SUIT_SIZE)
        if glyph is None:
            print("Could not render suit %s" % suit)
            continue
        cv2.imwrite(os.path.join(suit_dir, "%s.png" % suit), glyph)
        written += 1

    print("Wrote %d templates to %s" % (written, TEMPLATE_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
