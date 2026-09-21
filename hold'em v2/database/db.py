"""PostgreSQL access - the permanent source of truth for recorded hands.

Credentials come from environment variables (see .env.example); nothing is
hard-coded.
"""

import logging
import os

import psycopg
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "database", "schema.sql")

# Column order used everywhere (database, Excel, UI).
COLUMNS = [
    "player_card_1", "player_card_2",
    "flop_card_1", "flop_card_2", "flop_card_3",
    "turn_card", "river_card",
    "dealer_card_1", "dealer_card_2",
    "player_hand", "dealer_hand",
    "winner", "dealer_qualified",
    "round_started_at", "deal_to_flop_seconds", "flop_to_showdown_seconds",
    "round_seconds", "seconds_since_previous_round",
]

# Maps the tracker's slot names onto database column names.
SLOT_TO_COLUMN = {
    "player_1": "player_card_1",
    "player_2": "player_card_2",
    "flop_1": "flop_card_1",
    "flop_2": "flop_card_2",
    "flop_3": "flop_card_3",
    "turn": "turn_card",
    "river": "river_card",
    "dealer_1": "dealer_card_1",
    "dealer_2": "dealer_card_2",
}


class DatabaseError(RuntimeError):
    """Raised when PostgreSQL is unavailable or a query fails."""


def connection_settings():
    """Connection settings from the environment (.env is loaded if present)."""
    load_dotenv(os.path.join(ROOT, ".env"))
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DATABASE", "poker_tracker"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
    }


def get_connection():
    """Open a connection. Raises DatabaseError if PostgreSQL is unreachable."""
    settings = connection_settings()
    try:
        return psycopg.connect(connect_timeout=5, **settings)
    except Exception as exc:  # noqa: BLE001 - psycopg raises several types
        raise DatabaseError(
            "Cannot connect to PostgreSQL at %s:%s/%s - %s"
            % (settings["host"], settings["port"], settings["dbname"], exc)
        ) from exc


def ensure_schema():
    """Create the poker_hands table if it does not exist yet."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema_sql = handle.read()
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(schema_sql)
            conn.commit()
    except DatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError("Could not create schema: %s" % exc) from exc
    logger.info("Database schema verified")


def check_connection():
    """(ok, message) - a cheap connectivity probe for the UI."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True, "PostgreSQL connected"
    except DatabaseError as exc:
        return False, str(exc)


def record_to_row(record):
    """Convert a hand record (slot names) into database column values."""
    row = {SLOT_TO_COLUMN[slot]: record[slot] for slot in SLOT_TO_COLUMN}
    row["player_hand"] = record["player_hand"]
    row["dealer_hand"] = record["dealer_hand"]
    row["winner"] = record.get("winner")
    row["dealer_qualified"] = record.get("dealer_qualified")
    for field in ("round_started_at", "deal_to_flop_seconds",
                  "flop_to_showdown_seconds", "round_seconds",
                  "seconds_since_previous_round"):
        row[field] = record.get(field)
    row["hand_fingerprint"] = record["hand_fingerprint"]
    return row


def insert_hand(record):
    """Insert one completed hand.

    Returns (inserted, row) where `inserted` is False when the fingerprint was
    already stored, and `row` is the full stored row (id + recorded_at + data).
    """
    row = record_to_row(record)
    columns = COLUMNS + ["hand_fingerprint"]
    placeholders = ", ".join("%s" for _ in columns)
    values = [row[column] for column in columns]

    sql = (
        "INSERT INTO poker_hands (%s) VALUES (%s) "
        "ON CONFLICT (hand_fingerprint) DO NOTHING RETURNING id, recorded_at"
        % (", ".join(columns), placeholders)
    )

    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, values)
            result = cur.fetchone()
            conn.commit()
    except DatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError("Insert failed: %s" % exc) from exc

    if result is None:
        logger.info("Duplicate hand ignored: %s", row["hand_fingerprint"])
        return False, None

    stored = dict(row)
    stored["id"] = result[0]
    stored["recorded_at"] = result[1]
    logger.info("Hand #%s stored: %s", stored["id"], row["hand_fingerprint"])
    return True, stored


def fingerprint_exists(fingerprint):
    """True when this hand has already been stored."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM poker_hands WHERE hand_fingerprint = %s", (fingerprint,)
            )
            return cur.fetchone() is not None
    except DatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError("Lookup failed: %s" % exc) from exc


def fetch_all_hands():
    """Every stored hand, oldest first, as a list of dicts."""
    columns = ["id", "recorded_at"] + COLUMNS + ["hand_fingerprint"]
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT %s FROM poker_hands ORDER BY id" % ", ".join(columns))
            rows = cur.fetchall()
    except DatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError("Query failed: %s" % exc) from exc
    return [dict(zip(columns, row)) for row in rows]


def win_statistics():
    """How the recorded rounds turned out: counts and percentages.

    Worked out from the winner already stored against each hand rather than
    from a table of its own - a second copy of the same facts could disagree
    with the first. Only rounds that were recorded count, and a round is only
    recorded once all nine cards were read, so an abandoned or uncertain round
    is absent rather than counted as anything.

    Returns:
        {"rounds": n, "player": n, "dealer": n, "tie": n,
         "player_percent": f, "dealer_percent": f, "tie_percent": f}
    """
    counts = {"Player": 0, "Dealer": 0, "Tie": 0}
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT winner, COUNT(*) FROM poker_hands "
                "WHERE winner IS NOT NULL GROUP BY winner"
            )
            for winner, count in cur.fetchall():
                if winner in counts:
                    counts[winner] += int(count)
    except DatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseError("Could not count results: %s" % exc) from exc
    return summarise_results(counts)


def summarise_results(counts):
    """Counts to counts-and-percentages. Safe on an empty history."""
    player = int(counts.get("Player", 0))
    dealer = int(counts.get("Dealer", 0))
    tie = int(counts.get("Tie", 0))
    rounds = player + dealer + tie
    share = (lambda n: round(100.0 * n / rounds, 1)) if rounds else (lambda n: 0.0)
    return {
        "rounds": rounds,
        "player": player, "dealer": dealer, "tie": tie,
        "player_percent": share(player),
        "dealer_percent": share(dealer),
        "tie_percent": share(tie),
    }


def running_win_percentages(rows):
    """The player's win rate after each completed round, oldest first.

    A history rather than a prediction: round three's figure is what the first
    three rounds came to, not what the fourth is expected to do.
    """
    running, wins, played = [], 0, 0
    for row in rows:
        winner = row.get("winner")
        if winner not in ("Player", "Dealer", "Tie"):
            continue                      # never completed; not a round that counts
        played += 1
        wins += 1 if winner == "Player" else 0
        running.append({"round": played, "winner": winner, "player_wins": wins,
                        "player_percent": round(100.0 * wins / played, 1)})
    return running
