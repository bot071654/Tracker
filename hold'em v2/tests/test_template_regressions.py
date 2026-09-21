"""Regression tests for the corrupt learned templates found while debugging.

The live table's 10 of spades was read as the 10 of clubs (15 of 22 readings on
recorded frames) because recognition/templates/suits/S_10s.png held a fragment
of the upside-down corner index instead of a spade pip. The 4 of diamonds was
read as the 4 of hearts because suits/H_7h.png held two stray marks that matched
any red pip at about 0.66. tools/audit_templates.py flagged eight more.

Those templates were replaced by re-teaching from clean crops of the real table.
The crops below come from recorded rounds that were NOT used for teaching.
"""

import glob
import os

import pytest

cv2 = pytest.importorskip("cv2")

from recognition.card_recognizer import TEMPLATE_DIR, load_templates, recognize_card  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CROPS = sorted(glob.glob(os.path.join(HERE, "fixtures", "live_regression", "*.png")))


def label(path):
    return os.path.basename(path).split("_")[0]


@pytest.fixture(scope="module", autouse=True)
def templates():
    load_templates(force=True)


@pytest.mark.parametrize("path", CROPS, ids=lambda p: os.path.basename(p))
def test_a_real_crop_is_never_confidently_read_as_another_card(path):
    result = recognize_card(cv2.imread(path)) or {}
    if result.get("confident"):
        assert result["card"] == label(path), "%s read as %s" % (os.path.basename(path), result["card"])


@pytest.mark.parametrize("path", [p for p in CROPS if label(p) in ("10S", "10C")],
                         ids=lambda p: os.path.basename(p))
def test_real_tens_of_spades_and_clubs_are_read_and_kept(path):
    """Regression: 10S was confidently read as 10C; 10C must not be lost fixing it."""
    result = recognize_card(cv2.imread(path)) or {}
    assert result.get("confident") and result["card"] == label(path), result


def test_four_of_diamonds_is_not_read_as_four_of_hearts():
    for path in [p for p in CROPS if label(p) == "4D"]:
        result = recognize_card(cv2.imread(path)) or {}
        assert result.get("card") != "4H" or not result.get("confident"), os.path.basename(path)


@pytest.mark.parametrize("name", ["D_2d.png", "D_3d.png", "D_ad.png", "H_5h.png", "H_7h.png",
                                  "S_2s.png", "S_4s.png"])
def test_previously_corrupt_suit_templates_are_a_single_pip(name):
    """Each was a fragment or had stray marks. A 10's pip legitimately keeps a sliver
    of the '0' beside it (the live glyphs do too), so S_10s.png is not checked here."""
    image = cv2.imread(os.path.join(TEMPLATE_DIR, "suits", name), cv2.IMREAD_GRAYSCALE)
    assert image is not None, name
    _, binary = cv2.threshold(image, 127, 255, cv2.THRESH_BINARY)
    marks, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    assert marks - 1 == 1, "%s has %d marks" % (name, marks - 1)
