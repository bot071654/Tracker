"""Measure how the result-panel reader treats the saved real thumbnails (tests/fixtures/panel_thumbnails).

    python tools/evaluate_panel_crops.py

Recognition and policy are reported apart. For every crop the reader is asked what card it sees (`recognize_card`, whatever
its confidence), and then whether it would accept that reading (`confident`). The first is accuracy; the second is the
refusal policy that has to stay strict - a wrong card is worse than a refusal.

Two readers are measured: the TABLE reader (`read_slot`, which is what every card on the table is read with and is
unchanged) and the PANEL reader (`result_panel.read_thumbnail`, which lets the outline of a black pip corroborate a suit
that won by too little for the table's rule).
"""

import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cv2  # noqa: E402

from recognition import card_recognizer as cr  # noqa: E402
from recognition import result_panel  # noqa: E402

DATASET = os.path.join(ROOT, "tests", "fixtures", "panel_thumbnails")


def load_dataset():
    """[(path, card)] for every labelled crop."""
    with open(os.path.join(DATASET, "labels.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    return [(os.path.join(DATASET, crop["file"]), crop["card"]) for crop in manifest["crops"]]


def evaluate(reader, dataset=None):
    """{suit: Counter(correct / wrong / refused / by_outline)} plus the recognition-only tally, for `reader`."""
    cr.load_templates()
    outcomes = collections.defaultdict(collections.Counter)
    recognised = collections.defaultdict(collections.Counter)
    wrong = []
    for path, truth in dataset or load_dataset():
        image = cv2.imread(path)
        best = cr.recognize_card(image, corroborate_suit=reader is result_panel.read_thumbnail)
        recognised[truth[-1]]["right" if best and best["card"] == truth else "wrong"] += 1
        read = reader(image)
        if not read.get("confident"):
            outcomes[truth[-1]]["refused"] += 1
        elif read["card"] == truth:
            outcomes[truth[-1]]["correct"] += 1
            if read.get("suit_corroborated"):
                outcomes[truth[-1]]["by_outline"] += 1
        else:
            outcomes[truth[-1]]["wrong"] += 1
            wrong.append((os.path.basename(path), truth, read["card"]))
    return outcomes, recognised, wrong


def report():
    total = len(load_dataset())
    print("saved real panel thumbnails evaluated: %d" % total)
    for name, reader in (("TABLE reader (read_slot, unchanged)", cr.read_slot),
                         ("PANEL reader (read_thumbnail)", result_panel.read_thumbnail)):
        outcomes, recognised, wrong = evaluate(reader)
        print("\n%s" % name)
        print("  %-6s %9s %9s %9s %11s | recognition alone: right / wrong" % ("suit", "correct", "wrong", "refused", "by outline"))
        for suit in "SCHD":
            if not (outcomes[suit] or recognised[suit]):
                continue
            row = outcomes[suit]
            print("  %-6s %9d %9d %9d %11d | %d / %d" % (suit, row["correct"], row["wrong"], row["refused"], row["by_outline"],
                                                         recognised[suit]["right"], recognised[suit]["wrong"]))
        print("  wrong predictions: %s" % (wrong or "none"))
        print("  strict policy: SUIT_MARGIN=%.2f RANK_MARGIN=%.2f threshold 0.62 (unchanged); corroborated floor %.2f" % (
            cr.SUIT_MARGIN, cr.RANK_MARGIN, cr.CORROBORATED_SUIT_MARGIN))


if __name__ == "__main__":
    report()
