"""PostgreSQL insertion and duplicate-prevention tests.

These run against the real database configured in .env and are skipped when it
is unreachable. Rows they create are removed again afterwards.
"""

import pytest

from database import db
from poker.hand_record import build_hand_record

EXAMPLE = {
    "player_1": "8S", "player_2": "9C",
    "flop_1": "2D", "flop_2": "QH", "flop_3": "3D",
    "turn": "3H", "river": "6S",
    "dealer_1": "8D", "dealer_2": "QD",
}

available, reason = db.check_connection()
pytestmark = pytest.mark.skipif(not available, reason="PostgreSQL unavailable: %s" % reason)


def delete(fingerprint):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM poker_hands WHERE hand_fingerprint = %s", (fingerprint,))
        conn.commit()


@pytest.fixture
def record():
    db.ensure_schema()
    # A board that cannot collide with a real hand, so tests never touch real data.
    hand = build_hand_record(dict(EXAMPLE, river="7C", turn="4C"))
    delete(hand["hand_fingerprint"])
    yield hand
    delete(hand["hand_fingerprint"])


def test_schema_exists():
    db.ensure_schema()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.poker_hands')")
        assert cur.fetchone()[0] is not None


def test_insert_returns_the_stored_row(record):
    inserted, row = db.insert_hand(record)
    assert inserted is True
    assert row["id"] is not None
    assert row["player_card_1"] == "8S"
    assert row["player_hand"] == record["player_hand"]
    assert row["hand_fingerprint"] == record["hand_fingerprint"]


def test_duplicate_hand_is_not_inserted_again(record):
    inserted_first, _ = db.insert_hand(record)
    inserted_second, row = db.insert_hand(record)
    assert inserted_first is True
    assert inserted_second is False
    assert row is None


def test_only_one_row_exists_after_repeated_inserts(record):
    for _ in range(3):
        db.insert_hand(record)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM poker_hands WHERE hand_fingerprint = %s",
            (record["hand_fingerprint"],),
        )
        assert cur.fetchone()[0] == 1


def test_fingerprint_exists(record):
    assert db.fingerprint_exists(record["hand_fingerprint"]) is False
    db.insert_hand(record)
    assert db.fingerprint_exists(record["hand_fingerprint"]) is True


def test_fetch_all_hands_includes_the_new_row(record):
    _, row = db.insert_hand(record)
    fingerprints = [hand["hand_fingerprint"] for hand in db.fetch_all_hands()]
    assert row["hand_fingerprint"] in fingerprints


def test_unreachable_database_raises_a_clear_error(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "1")
    with pytest.raises(db.DatabaseError):
        db.get_connection()
