"""Import historical hands from data/poker_hands.xlsx into PostgreSQL.

A one-time migration, for moving a history recorded before the team shared a
database server. Normal running never needs it: the tracker writes to
PostgreSQL first and mirrors to Excel afterwards, so Excel is only ever behind,
never ahead. This tool is for the one case where that is not true - the
spreadsheet outlived the database it was written beside.

    python tools/import_excel.py --dry-run     read and report, change nothing
    python tools/import_excel.py               import

It is safe to re-run. Nothing is invented and nothing is overwritten:

  * the fingerprint comes from poker.hand_record.build_fingerprint, the one
    the tracker itself uses, so a hand already stored is recognised as the
    same hand;
  * duplicates are refused by the database, through the same
    ON CONFLICT (hand_fingerprint) DO NOTHING that db.insert_hand has always
    used - this tool adds no duplicate handling of its own;
  * the spreadsheet is opened read-only and is never written to;
  * no table is created, dropped or emptied.

The Excel columns are mapped by export.excel_export._FIELDS, which is the same
list the exporter writes them with, so the two cannot drift apart.

On the ID column: Excel's IDs are the row ids of the database that wrote them
and run 9..765 with gaps. They are not carried over. The id is the database's
own key, PostgreSQL assigns it, and a gapped sequence imported by hand would
only misrepresent how many hands there are. recorded_at is the column that
says when a hand was really played, and that IS preserved - rows are imported
oldest first so the new ids follow the same order.
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db  # noqa: E402
from export import excel_export  # noqa: E402
from poker.hand_record import build_hand_record  # noqa: E402

# Database column -> the tracker's slot name, so the cards can be handed to
# build_hand_record exactly as the tracker hands them over.
COLUMN_TO_SLOT = {
    "player_card_1": "player_1", "player_card_2": "player_2",
    "flop_card_1": "flop_1", "flop_card_2": "flop_2", "flop_card_3": "flop_3",
    "turn_card": "turn", "river_card": "river",
    "dealer_card_1": "dealer_1", "dealer_card_2": "dealer_2",
}

TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d")


def parse_timestamp(value):
    """Excel's timestamp back into a datetime, or None.

    excel_export writes these with strftime("%Y-%m-%d %H:%M:%S"), so that is
    the format to expect; openpyxl may also hand back a real datetime if the
    cell was ever stored as one.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("unrecognised timestamp %r" % value)


def parse_qualified(value):
    """The exporter writes Yes/No; turn it back into a boolean, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("yes", "true", "1"):
        return True
    if text in ("no", "false", "0"):
        return False
    raise ValueError("unrecognised dealer_qualified %r" % value)


def parse_number(value):
    if value is None or value == "":
        return None
    return float(value)


def read_rows(path):
    """Every data row of the workbook, as database-column dicts. Read-only."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    rows = list(sheet.iter_rows(values_only=True))
    workbook.close()
    if not rows:
        return [], []

    header = list(rows[0])
    if header != excel_export.HEADERS:
        raise ValueError(
            "The workbook's columns are not the ones this project writes.\n"
            "  expected: %s\n  found:    %s" % (excel_export.HEADERS, header))

    data = [row for row in rows[1:] if any(value is not None for value in row)]
    return [dict(zip(excel_export._FIELDS, row)) for row in data], header


def to_record(row):
    """One spreadsheet row as the record db.insert_hand expects.

    The nine cards are put back through build_hand_record, which is what the
    tracker does, so the hand names, the winner, the qualification and above
    all the fingerprint are produced by the project's own logic rather than
    trusted from the spreadsheet.
    """
    cards = {slot: row[column] for column, slot in COLUMN_TO_SLOT.items()}
    missing = [slot for slot, card in cards.items() if not card]
    if missing:
        raise ValueError("incomplete hand, missing %s" % ", ".join(sorted(missing)))

    record = build_hand_record(cards)
    record["recorded_at"] = parse_timestamp(row["recorded_at"])
    record["round_started_at"] = parse_timestamp(row["round_started_at"])
    for field in ("deal_to_flop_seconds", "flop_to_showdown_seconds",
                  "round_seconds", "seconds_since_previous_round"):
        record[field] = parse_number(row[field])
    return record


def compare_with_sheet(row, record):
    """Where the spreadsheet's own verdict differs from the recomputed one.

    Reported, not corrected: the recomputed values are what gets stored, and a
    difference is worth knowing about because it means the spreadsheet and the
    evaluator disagree about a hand.
    """
    differences = []
    for field in ("player_hand", "dealer_hand", "winner"):
        if row[field] is not None and row[field] != record[field]:
            differences.append("%s %r != %r" % (field, row[field], record[field]))
    qualified = parse_qualified(row["dealer_qualified"])
    if qualified is not None and qualified != record["dealer_qualified"]:
        differences.append("dealer_qualified %r != %r"
                           % (qualified, record["dealer_qualified"]))
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", default=None,
                        help="defaults to the project's data/poker_hands.xlsx")
    parser.add_argument("--dry-run", action="store_true",
                        help="read and report; touch nothing")
    args = parser.parse_args()

    path = args.workbook or excel_export.EXCEL_PATH
    if not os.path.exists(path):
        print("No workbook at %s" % path)
        return 1

    try:
        rows, _ = read_rows(path)
    except (OSError, ValueError) as exc:
        print("Could not read %s: %s" % (path, exc))
        return 1
    print("Workbook            : %s" % path)
    print("Rows found          : %d" % len(rows))

    try:
        print("Database            : %s" % db.describe_target())
        ok, message = db.verify_schema()
        if not ok:
            print("  %s" % message)
            return 1
        before = current_count()
        print("Rows already stored : %d" % before)
    except db.DatabaseError as exc:
        print("  %s" % exc)
        return 1

    prepared, unreadable = [], []
    disagreements = []
    for index, row in enumerate(rows, start=2):        # row 1 is the header
        try:
            record = to_record(row)
        except ValueError as exc:
            unreadable.append((index, row.get("id"), str(exc)))
            continue
        differences = compare_with_sheet(row, record)
        if differences:
            disagreements.append((index, row.get("id"), differences))
        prepared.append((index, row.get("id"), record))

    # Oldest first, so the ids PostgreSQL assigns follow the order the hands
    # were actually played.
    prepared.sort(key=lambda item: (item[2]["recorded_at"] or datetime.min, item[0]))

    fingerprints = {}
    for index, excel_id, record in prepared:
        fingerprints.setdefault(record["hand_fingerprint"], []).append(excel_id)
    repeated = {fp: ids for fp, ids in fingerprints.items() if len(ids) > 1}

    print("Readable            : %d" % len(prepared))
    print("Unreadable          : %d" % len(unreadable))
    for index, excel_id, reason in unreadable[:10]:
        print("    sheet row %-5s Excel ID %-5s %s" % (index, excel_id, reason))
    print("Distinct hands      : %d" % len(fingerprints))
    if repeated:
        print("Repeated in sheet   : %d" % len(repeated))
        for fingerprint, ids in list(repeated.items())[:10]:
            print("    %s  Excel IDs %s" % (fingerprint, ids))
    if disagreements:
        print("Sheet disagrees with the evaluator on %d row(s):" % len(disagreements))
        for index, excel_id, differences in disagreements[:10]:
            print("    Excel ID %-5s %s" % (excel_id, "; ".join(differences)))

    if args.dry_run:
        print()
        print("Dry run - nothing was written.")
        print("Would attempt %d insert(s); the database would keep %d of them."
              % (len(prepared), len(fingerprints)))
        return 0

    print()
    inserted = duplicate = failed = 0
    for index, excel_id, record in prepared:
        try:
            was_inserted, _ = db.insert_hand(record)
        except db.DatabaseError as exc:
            failed += 1
            print("  Excel ID %-5s failed: %s" % (excel_id, exc))
            continue
        if was_inserted:
            inserted += 1
        else:
            duplicate += 1

    after = current_count()
    print("Inserted            : %d" % inserted)
    print("Already present     : %d" % duplicate)
    print("Failed              : %d" % failed)
    print("Rows now stored     : %d  (was %d)" % (after, before))
    return 0 if failed == 0 else 1


def current_count():
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM poker_hands")
        return cur.fetchone()[0]


if __name__ == "__main__":
    raise SystemExit(main())
