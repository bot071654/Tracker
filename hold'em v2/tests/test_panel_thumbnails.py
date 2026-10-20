"""The result panels' thumbnails: recognition accuracy, the refusal policy, and why the dealer's row was never read.

WHAT WAS WRONG

The installed log shows the result panels almost never confirming a hand: 23 completed hands, no dealer row ever confirmed,
the player's row complete in a handful. Two separate faults, both found in the saved evidence:

  1. Spades. 215 thumbnails the tracker refused (213 spades of every rank, 2 clubs - tests/fixtures/panel_thumbnails) are all
     legible and every one was refused for the suit alone: at 34 x 47 pixels a spade beats a club by 0.028 - 0.050 on the
     template match, and a suit has to win by 0.05. The pip's outline separates the two suits cleanly at that size, so it now
     corroborates the template match - never replaces it - for panel thumbnails only (card_recognizer.pip_shape).
  2. The dealer's row. The thumbnail cache needed bit-identical pixels. The live panel's pixels move by a grey level or two
     from poll to poll, so every poll missed the cache and spent the whole reading budget (two thumbnails) on the first two
     thumbnails again; the rest of the panel, the dealer's row last of all, was never read. The cache now accepts a picture
     within CACHE_TOLERANCE grey levels of one already read.

Recognition (what card is it?) and policy (is the reading good enough to accept?) are kept apart throughout: the first is
measured on every crop whatever its confidence; the second is the unchanged strict rule - uncertain means refused.
"""

import json
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from recognition import card_recognizer as cr          # noqa: E402
from recognition import result_panel as rp             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.path.join(ROOT, "tests", "fixtures", "panel_thumbnails")
SCREENSHOT = os.path.join(ROOT, "Screenshot 2026-09-02 152130.png")


def crops():
    with open(os.path.join(DATASET, "labels.json"), encoding="utf-8") as handle:
        return [(os.path.join(DATASET, c["file"]), c["card"], c) for c in json.load(handle)["crops"]]


needs_dataset = pytest.mark.skipif(not os.path.exists(os.path.join(DATASET, "labels.json")), reason="panel crops not present")
needs_frame = pytest.mark.skipif(not os.path.exists(SCREENSHOT), reason="no casino screenshot in the project folder")


@pytest.fixture(scope="module", autouse=True)
def templates():
    cr.load_templates(force=True)


@pytest.fixture(scope="module")
def images():
    return {path: cv2.imread(path) for path, _, _ in crops()}


def one_per_card():
    seen, picked = set(), []
    for path, card, _ in crops():
        if card not in seen:
            seen.add(card)
            picked.append((path, card))
    return picked


# -- the dataset ----------------------------------------------------------------------------------------------------------

@needs_dataset
def test_the_dataset_is_the_215_saved_crops_spades_of_every_rank_and_two_clubs(images):
    labelled = crops()
    assert len(labelled) == 215 and all(os.path.exists(path) for path, _, _ in labelled)
    spades = {card[:-1] for _, card, _ in labelled if card.endswith("S")}
    assert sum(card.endswith("S") for _, card, _ in labelled) == 213
    assert sum(card.endswith("C") for _, card, _ in labelled) == 2
    assert spades == {"2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"}


@needs_dataset
def test_the_crops_are_thumbnail_sized(images):
    heights = {img.shape[0] for img in images.values()}
    widths = sorted(img.shape[1] for img in images.values())
    assert heights <= {46, 47, 48}
    assert 27 <= widths[0] and widths[-1] <= 44 and widths[len(widths) // 2] in (33, 34)


# -- recognition alone, then the policy -------------------------------------------------------------------------------------

@needs_dataset
@pytest.mark.parametrize("corroborate", [False, True])
def test_recognition_alone_names_every_saved_crop_correctly(images, corroborate):
    """What card is it? - whatever the confidence. Accuracy, separate from whether the reading is accepted."""
    wrong = [(os.path.basename(path), card, best and best["card"]) for path, card, _ in crops()
             for best in [cr.recognize_card(images[path], corroborate_suit=corroborate)] if not best or best["card"] != card]
    assert wrong == []


@needs_dataset
def test_the_tables_own_rule_still_refuses_every_one_of_them(images):
    """The policy the table's cards are read under is untouched: all 215 are still refused, for the suit, by the same rule."""
    assert (cr.SUIT_MARGIN, cr.RANK_MARGIN) == (0.05, 0.10)
    for path, card, _ in crops():
        read = cr.read_slot(images[path])
        assert not read["confident"], os.path.basename(path)
        assert read["suit_margin"] < cr.SUIT_MARGIN and not read["suit_corroborated"]
        assert "other suit of its colour" in (cr.undecided_half(read) or "")


@needs_dataset
def test_the_panel_reader_accepts_every_saved_crop_as_the_right_card_by_outline(images):
    for path, card, _ in crops():
        read = rp.read_thumbnail(images[path])
        assert read["confident"] and read["card"] == card, (os.path.basename(path), read)
        assert read["suit_corroborated"] and cr.CORROBORATED_SUIT_MARGIN <= read["suit_margin"] < cr.SUIT_MARGIN


@needs_dataset
def test_nothing_the_panel_reader_accepts_is_the_wrong_card(images):
    wrong = [os.path.basename(path) for path, card, _ in crops()
             if (lambda r: r["confident"] and r["card"] != card)(rp.read_thumbnail(images[path]))]
    assert wrong == []


@needs_dataset
def test_the_confidence_threshold_and_rank_margin_are_not_what_changed(images):
    """A crop whose RANK is a near-tie is refused by the panel reader exactly as by the table's: only the suit rule moved."""
    path, card = one_per_card()[0]
    original = cr.RANK_MARGIN
    try:
        cr.RANK_MARGIN = 0.99                                            # no rank can clear this
        read = rp.read_thumbnail(images[path])
        assert not read["confident"] and read["card"] == card
    finally:
        cr.RANK_MARGIN = original
    low = rp.read_thumbnail(images[path])
    assert low["confident"]
    assert cr.recognize_card(images[path], threshold=0.99, corroborate_suit=True)["confident"] is False


# -- spade versus club -----------------------------------------------------------------------------------------------------

@needs_dataset
def test_a_spade_whose_outline_says_club_is_refused(images, monkeypatch):
    path, card = one_per_card()[0]
    monkeypatch.setattr(cr, "pip_shape", lambda glyph: ("C", 0.10))
    read = rp.read_thumbnail(images[path])
    assert not read["confident"] and not read["suit_corroborated"]


@needs_dataset
def test_a_spade_whose_outline_has_no_opinion_is_refused(images, monkeypatch):
    path, card = one_per_card()[0]
    monkeypatch.setattr(cr, "pip_shape", lambda glyph: (None, 0.30))
    assert not rp.read_thumbnail(images[path])["confident"]


@needs_dataset
def test_a_club_whose_outline_says_spade_is_refused(images, monkeypatch):
    path, card = [(p, c) for p, c in one_per_card() if c.endswith("C")][0]
    monkeypatch.setattr(cr, "pip_shape", lambda glyph: ("S", 0.50))
    assert not rp.read_thumbnail(images[path])["confident"]


@needs_dataset
def test_a_template_margin_below_the_corroborated_floor_is_refused_whatever_the_outline_says(images, monkeypatch):
    path, card = one_per_card()[0]
    monkeypatch.setattr(cr, "CORROBORATED_SUIT_MARGIN", 0.20)
    assert not rp.read_thumbnail(images[path])["confident"]


@needs_dataset
def test_the_outline_never_helps_a_card_on_the_table(images, monkeypatch):
    """Cards on the table are read with read_slot, which does not corroborate, whatever the outline says."""
    path, card = one_per_card()[0]
    monkeypatch.setattr(cr, "pip_shape", lambda glyph: ("S", 0.50))
    read = cr.read_slot(images[path])
    assert not read["confident"] and not read["suit_corroborated"]


@needs_dataset
def test_the_two_real_clubs_are_read_as_clubs(images):
    for path, card, _ in crops():
        if card.endswith("C"):
            read = rp.read_thumbnail(images[path])
            assert read["card"] == card and read["confident"], (os.path.basename(path), read)


@needs_frame
def test_the_real_panels_clubs_and_spades_still_read():
    panels = rp.read_panels(cv2.imread(SCREENSHOT))
    assert panels["player"]["cards"] == ["3D", "3H", "QH", "9C", "8S"]
    assert panels["dealer"]["cards"] == ["QD", "QH", "3D", "3H", "8D"]


@needs_dataset
def test_the_outline_separates_the_suits_on_every_real_glyph_available(images):
    """213 spade thumbnails, 2 club thumbnails, and the real table-size card crops in the fixtures: a clean gap."""
    def glyph(image):
        return cr.recognize_card(image, detail=True)["detail"]["suit_glyph"]

    for path, card, _ in crops():
        assert cr.pip_shape(glyph(images[path]))[0] == card[-1], os.path.basename(path)

    fixtures = os.path.join(ROOT, "tests", "fixtures")
    checked = {"S": 0, "C": 0}
    for folder in sorted(os.listdir(fixtures)):
        for name in sorted(os.listdir(os.path.join(fixtures, folder))):
            card = name.split("_")[0]
            if not (name.endswith(".png") and card[:1] in "0123456789AJQK" and card[-1] in "SC") or "read-as" in name:
                continue
            read = cr.read_slot(cv2.imread(os.path.join(fixtures, folder, name)), detail=True)
            if read.get("card") == card and read.get("detail"):
                shape = cr.pip_shape(read["detail"]["suit_glyph"])
                assert shape[0] == card[-1], (folder, name, shape)
                checked[card[-1]] += 1
    assert checked["S"] >= 10 and checked["C"] >= 8


def test_the_outline_has_no_opinion_without_a_pip():
    assert cr.pip_shape(None) == (None, 0.0)
    assert cr.pip_shape(np.zeros((56, 56), np.uint8)) == (None, 0.0)
    assert cr.pip_shape(np.full((6, 6), 255, np.uint8)) == (None, 0.0)


# -- partly visible, faded, blurred ----------------------------------------------------------------------------------------

def clip_left(n):
    return lambda im: np.hstack([np.full((im.shape[0], n, 3), 255, np.uint8), im[:, :-n]])


def clip_bottom(n):
    return lambda im: np.vstack([im[:-n], np.full((n, im.shape[1], 3), 255, np.uint8)])


DEGRADATIONS = {
    "left edge hidden 4px": clip_left(4), "left edge hidden 14px": clip_left(14), "bottom hidden 6px": clip_bottom(6),
    "bottom hidden 20px": clip_bottom(20), "blur 1.0": lambda im: cv2.GaussianBlur(im, (0, 0), 1.0),
    "blur 1.5": lambda im: cv2.GaussianBlur(im, (0, 0), 1.5), "faded to half": lambda im: (im * 0.5).astype(np.uint8),
    "half resolution": lambda im: cv2.resize(cv2.resize(im, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
                                             (im.shape[1], im.shape[0])),
}


@needs_dataset
@pytest.mark.parametrize("name", sorted(DEGRADATIONS))
def test_a_partly_visible_or_degraded_thumbnail_is_read_right_or_refused_never_wrong(images, name):
    wrong = []
    for path, card in one_per_card():
        read = rp.read_thumbnail(DEGRADATIONS[name](images[path]))
        if read["confident"] and read["card"] != card:
            wrong.append((card, read["card"]))
    assert wrong == []


@needs_dataset
def test_hiding_a_few_pixels_does_not_lose_the_card_but_hiding_the_pip_does(images):
    path, card = [(p, c) for p, c in one_per_card() if c == "AS"][0]
    assert rp.read_thumbnail(clip_left(2)(images[path]))["card"] == card
    assert not rp.read_thumbnail(clip_bottom(24)(images[path]))["confident"]       # the suit is under the cover
    assert not rp.read_thumbnail((images[path] * 0.4).astype(np.uint8))["confident"]  # too faded to read


# -- the dealer's row: why it was never read ------------------------------------------------------------------------------------

def noisy(frame, boxes, rng, pixels):
    """The live panel's noise: a few pixels of each thumbnail a grey level or two off from poll to poll."""
    out = frame.copy()
    for x, y, w, h in boxes:
        for spot in rng.choice(h * w, size=min(pixels, h * w), replace=False):   # distinct pixels, each at most 2 grey levels off
            yy, xx = y + spot // w, x + spot % w
            out[yy, xx] = np.clip(out[yy, xx].astype(int) + rng.choice([-2, -1, 1, 2]), 0, 255)
    return out


TABLE = {"dealer_1": "8D", "dealer_2": "QD", "flop_1": "2D", "flop_2": "QH", "flop_3": "3D", "turn": "3H", "river": "6S",
         "player_1": "8S", "player_2": "9C"}


def watch(pixels, polls=14, seed=3):
    frame = cv2.imread(SCREENSHOT)
    boxes = [box for row in rp.locate(frame).values() for box in row]
    rng = np.random.RandomState(seed)
    tracker = rp.PanelTracker(full_search_interval=0.0)
    first, reads = {"player": None, "dealer": None}, []
    for poll in range(1, polls + 1):
        before = tracker.read_count
        sides = tracker.observe(noisy(frame, boxes, rng, pixels) if pixels else frame, (0, 0), None, round_id=1, cards=TABLE)
        reads.append(tracker.read_count - before)
        for side in sides:
            if first[side] is None and sides[side]["complete"]:
                first[side] = poll
    return first, reads, tracker


@needs_frame
@pytest.mark.parametrize("pixels", [0, 10, 171])
def test_both_rows_complete_with_the_noise_the_live_panel_has(pixels):
    first, reads, _ = watch(pixels)
    assert first["player"] is not None and first["player"] <= 5
    assert first["dealer"] is not None and first["dealer"] <= 3            # the dealer's row, which was never read live
    assert sum(reads[8:]) == 0                                              # and the reading budget is then free again


@needs_frame
def test_without_the_tolerance_noise_still_starves_the_players_row_as_it_did_live(monkeypatch):
    """The cache failure, reproduced: exact-match caching with 10 noisy pixels per thumbnail never completes the player's row.

    The dealer's row no longer depends on the cache (it is read in full every poll it needs, and confirms regardless), which is
    why it is the player's row that shows the failure now.
    """
    monkeypatch.setattr(rp.PanelTracker, "CACHE_TOLERANCE", -1)
    first, reads, _ = watch(10)
    assert first["player"] is None
    assert first["dealer"] is not None
    assert reads == [rp.READS_PER_POLL + rp.DEALER_ROW_READS_PER_POLL] * 14   # the whole budget, every poll, with no cache


@needs_frame
def test_a_noisy_repeat_is_not_read_again_but_a_different_picture_is():
    _, _, tracker = watch(0, polls=6)
    frame = cv2.imread(SCREENSHOT)
    boxes = [box for row in rp.locate(frame).values() for box in row]
    x, y, w, h = boxes[0]
    original = frame[y:y + h, x:x + w].copy()
    assert tracker._cached_read(original) is not None
    rng = np.random.RandomState(1)
    for tweak in (1, 2, rp.PanelTracker.CACHE_TOLERANCE):
        near = np.clip(original.astype(int) + rng.choice([-tweak, tweak], size=original.shape), 0, 255).astype(np.uint8)
        assert tracker._cached_read(near) is not None, tweak
    far = original.copy()
    far[2:6, 2:6] = np.clip(far[2:6, 2:6].astype(int) - (rp.PanelTracker.CACHE_TOLERANCE + 15), 0, 255)   # (the corner is white)
    assert tracker._cached_read(far) is None                                # a real change is read again
    other_x, other_y, other_w, other_h = boxes[3]
    assert tracker._cached_read(frame[other_y:other_y + other_h, other_x:other_x + other_w]) is not None   # another cached card
    blank = np.full_like(original, 255)
    assert tracker._cached_read(blank) is None and tracker._cached_read(original[:, :-3]) is None


@needs_frame
def test_a_card_that_really_changes_is_read_again_and_the_row_follows(monkeypatch):
    frame = cv2.imread(SCREENSHOT)
    boxes = rp.locate(frame)
    tracker = rp.PanelTracker(full_search_interval=0.0)
    for _ in range(8):
        tracker.observe(frame, (0, 0), None, round_id=1, cards=TABLE)
    reads = tracker.read_count
    x, y, w, h = boxes["player"][0]
    x2, y2, w2, h2 = boxes["dealer"][0]
    changed = frame.copy()
    changed[y:y + h, x:x + w] = cv2.resize(frame[y2:y2 + h2, x2:x2 + w2], (w, h))   # the first player thumbnail becomes another card
    for _ in range(4):
        sides = tracker.observe(changed, (0, 0), None, round_id=1, cards=TABLE)
    assert tracker.read_count > reads                                       # the new picture was actually read
    assert sides["player"]["confirmed"] != ["3D", "3H", "QH", "9C", "8S"] or sides["player"]["state"] != rp.VISIBLE_COMPLETE


@needs_frame
def test_the_dealers_row_is_found_on_its_own_and_read_in_one_look_as_before():
    frame = cv2.imread(SCREENSHOT)
    found = rp.locate(frame)
    assert len(found["dealer"]) == 5 and len(found["player"]) == 5
    assert rp.read_panels(frame)["dealer"]["complete"]
