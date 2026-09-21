"""Regression tests for faults found in the live read-only verification of 2026-09-17.

  recognition   H3 flop J♣ and H18 flop 2♦ were refused on every poll: the border
                of the tilted card crossed the index strip as a slanted line
                against its left edge, grouped with the rank and held it to
                about 0.5. Fixtures are the real crops, checked by eye.
  status        See tests/test_card_status.py (stale CONFIRMED on a new deal,
                a single sighting shown held, two weak readings confirming).
"""

import glob
import os

import numpy
import pytest

cv2 = pytest.importorskip("cv2")

from recognition import card_detector  # noqa: E402
from recognition.card_recognizer import load_templates, read_slot  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CROPS = sorted(glob.glob(os.path.join(HERE, "fixtures", "live_2026-09-17", "*.png")))


@pytest.fixture(scope="module", autouse=True)
def templates():
    load_templates(force=True)


@pytest.mark.skipif(not CROPS, reason="no live crops")
@pytest.mark.parametrize("path", CROPS, ids=[os.path.basename(p) for p in CROPS])
def test_a_slanted_card_border_does_not_block_the_rank(path):
    read = read_slot(cv2.imread(path), 0.62, 0.35)
    assert read["confident"] and read["card"] == os.path.basename(path).split("_")[0], read


def strip_with(*lines):
    binary = numpy.zeros((120, 264), "uint8")
    cv2.rectangle(binary, (160, 14), (246, 100), 255, -1)          # a suit
    for start, end, thickness in lines:
        cv2.line(binary, start, end, 255, thickness)
    return binary


def test_a_slanted_line_against_the_edge_is_dropped():
    boxes = card_detector._blobs(strip_with(((16, 17), (2, 119), 12)))
    assert all(x > 5 for x, _, _, _ in boxes), boxes


def test_an_upright_one_is_kept():
    boxes = card_detector._blobs(strip_with(((40, 10), (40, 105), 14)))
    assert any(abs(x - 33) <= 2 and h > 90 for x, _, _, h in boxes), boxes
