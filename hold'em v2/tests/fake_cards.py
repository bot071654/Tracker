"""Renders synthetic card images so recognition can be tested without a casino.

Two layouts are drawn, matching what the app has to cope with:

    "side"     rank left, suit right - how the live table prints its cards
    "stacked"  rank above suit - how the small result-box cards are printed

They are deliberately drawn with a different font than the generated
templates, so a passing test means the matcher tolerates artwork differences.
"""

import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from poker.hand_evaluator import parse_card

SUIT_GLYPHS = {"S": "\u2660", "H": "\u2665", "D": "\u2666", "C": "\u2663"}
RED_SUITS = {"H", "D"}

FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
]


def _font(names, size):
    for name in names:
        for folder in FONT_DIRS:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    return None


def fonts_available():
    """True when this machine has fonts to draw test cards with."""
    return _font(["arial.ttf", "DejaVuSans.ttf"], 20) is not None


def render_card(card, width=120, height=170, layout="side"):
    """Draw a face-up card as a BGR image."""
    rank, suit = parse_card(card)
    colour = (200, 20, 30) if suit in RED_SUITS else (20, 20, 20)

    image = Image.new("RGB", (width, height), (14, 90, 55))  # table felt border
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([3, 3, width - 4, height - 4], radius=8, fill=(252, 252, 250))

    if layout == "side":
        rank_font = _font(["arial.ttf", "DejaVuSans.ttf"], int(height * 0.24))
        suit_font = _font(["seguisym.ttf", "arial.ttf", "DejaVuSans.ttf"],
                          int(height * 0.20))
        draw.text((int(width * 0.12), int(height * 0.05)), rank, font=rank_font,
                  fill=colour)
        draw.text((int(width * 0.55), int(height * 0.07)), SUIT_GLYPHS[suit],
                  font=suit_font, fill=colour)
        # The centre marking, well below the index - as on the real cards.
        draw.rectangle([int(width * 0.30), int(height * 0.52),
                        int(width * 0.70), int(height * 0.60)], fill=(90, 90, 90))
    else:
        rank_font = _font(["arial.ttf", "DejaVuSans.ttf"], int(height * 0.26))
        suit_font = _font(["seguisym.ttf", "arial.ttf", "DejaVuSans.ttf"],
                          int(height * 0.20))
        draw.text((int(width * 0.30), int(height * 0.05)), rank, font=rank_font,
                  fill=colour)
        draw.text((int(width * 0.31), int(height * 0.40)), SUIT_GLYPHS[suit],
                  font=suit_font, fill=colour)

    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def render_empty(width=120, height=170):
    """An empty seat: table felt only."""
    return np.full((height, width, 3), (55, 90, 14), dtype=np.uint8)


def render_card_back(width=120, height=170):
    """A face-down card: coloured back, no white face."""
    image = Image.new("RGB", (width, height), (14, 90, 55))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([3, 3, width - 4, height - 4], radius=8, fill=(150, 25, 35))
    for offset in range(0, width, 10):
        draw.line([(offset, 0), (offset, height)], fill=(110, 18, 26), width=2)
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
