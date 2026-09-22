"""Importing a history from the Excel mirror.

The spreadsheet has no fingerprint column, so a hand's identity has to be
rebuilt from its nine cards. These check that it is rebuilt with the project's
own logic rather than a second implementation, that the Excel columns are read
through the same mapping the exporter writes them with, and that the value
conversions the exporter applies are inverted correctly.

Nothing here touches the real database or the real workbook.
"""

import sys
import os
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import import_excel as imp  # noqa: E402
from export import excel_export  # noqa: E402
from poker.hand_record import CARD_ORDER, build_fingerprint  # noqa: E402

EXCEL_ROW = {
    "id": 81, "recorded_at": "2026-09-10 22:32:30",
    "player_card_1": "10C", "player_card_2": "2H",
    "flop_card_1": "2S", "flop_card_2": "3D", "flop_card_3": "9D",
    "turn_card": "AD", "river_card": "10D",
    "dealer_card_1": "2D", "dealer_card_2": "AC",
    "player_hand": "Two Pair", "dealer_hand": "Flush",
    "winner": "Dealer", "dealer_qualified": "Yes",
    "round_started_at": "2026-09-10 22:31:58",
    "deal_to_flop_seconds": 1.32, "flop_to_showdown_seconds": 29.46,
    "round_seconds": 30.79, "seconds_since_previous_round": None,
}


# -- the fingerprint is the project's own -------------------------------------

def test_the_importer_uses_the_projects_fingerprint():
    """Not a second algorithm that happens to agree today."""
    record = imp.to_record(EXCEL_ROW)
    cards = {slot: EXCEL_ROW[column] for column, slot in imp.COLUMN_TO_SLOT.items()}
    assert record["hand_fingerprint"] == build_fingerprint(cards)
    assert record["hand_fingerprint"] == "10C-2H-2S-3D-9D-AD-10D-2D-AC"


def test_the_fingerprint_follows_the_dealing_order():
    record = imp.to_record(EXCEL_ROW)
    cards = {slot: EXCEL_ROW[column] for column, slot in imp.COLUMN_TO_SLOT.items()}
    assert record["hand_fingerprint"] == "-".join(cards[slot] for slot in CARD_ORDER)


def test_every_card_column_maps_to_a_slot():
    assert sorted(imp.COLUMN_TO_SLOT.values()) == sorted(CARD_ORDER)


# -- the column mapping is the exporter's own ---------------------------------

def test_the_importer_reads_the_columns_the_exporter_writes():
    assert len(excel_export.HEADERS) == len(excel_export._FIELDS)
    for column in imp.COLUMN_TO_SLOT:
        assert column in excel_export._FIELDS


def test_a_workbook_with_different_columns_is_refused(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "wrong.xlsx"
    workbook = Workbook()
    workbook.active.append(["Something", "Else"])
    workbook.active.append([1, 2])
    workbook.save(path)

    with pytest.raises(ValueError) as caught:
        imp.read_rows(str(path))
    assert "columns" in str(caught.value)


# -- the exporter's conversions, inverted -------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Yes", True), ("No", False), ("yes", True), ("no", False),
    (True, True), (False, False), (None, None), ("", None),
])
def test_dealer_qualified_round_trips(text, expected):
    assert imp.parse_qualified(text) is expected


def test_an_unknown_qualified_value_is_refused():
    with pytest.raises(ValueError):
        imp.parse_qualified("perhaps")


def test_timestamps_parse_in_the_format_the_exporter_writes():
    assert imp.parse_timestamp("2026-09-10 22:32:30") == datetime(2026, 9, 10, 22, 32, 30)
    assert imp.parse_timestamp(datetime(2026, 9, 10)) == datetime(2026, 9, 10)
    assert imp.parse_timestamp(None) is None
    assert imp.parse_timestamp("") is None


def test_an_unknown_timestamp_is_refused():
    with pytest.raises(ValueError):
        imp.parse_timestamp("last Tuesday")


def test_numbers_and_nulls():
    assert imp.parse_number(1.32) == 1.32
    assert imp.parse_number(30) == 30.0
    assert imp.parse_number(None) is None
    assert imp.parse_number("") is None


# -- what a record carries ----------------------------------------------------

def test_the_record_keeps_the_original_recorded_at():
    """Otherwise every imported hand is stamped with the time of the import."""
    record = imp.to_record(EXCEL_ROW)
    assert record["recorded_at"] == datetime(2026, 9, 10, 22, 32, 30)
    assert record["round_started_at"] == datetime(2026, 9, 10, 22, 31, 58)


def test_the_hand_is_re_derived_not_trusted():
    """The evaluator decides, so a wrong sheet cannot write a wrong verdict."""
    record = imp.to_record(dict(EXCEL_ROW, player_hand="Royal Flush",
                                winner="Player"))
    assert record["player_hand"] == "Two Pair"
    assert record["winner"] == "Dealer"


def test_a_disagreement_with_the_sheet_is_reported():
    row = dict(EXCEL_ROW, player_hand="Royal Flush")
    differences = imp.compare_with_sheet(row, imp.to_record(row))
    assert any("player_hand" in d for d in differences)


def test_a_row_that_agrees_reports_nothing():
    assert imp.compare_with_sheet(EXCEL_ROW, imp.to_record(EXCEL_ROW)) == []


def test_an_incomplete_row_is_refused_by_name():
    with pytest.raises(ValueError) as caught:
        imp.to_record(dict(EXCEL_ROW, river_card=None))
    assert "river" in str(caught.value)


def test_the_excel_id_is_not_carried_into_the_record():
    """PostgreSQL assigns the id; Excel's is the id of another database."""
    assert "id" not in imp.to_record(EXCEL_ROW)
