"""Create the poker_hands table in the database .env points at.

Reads the connection details from .env (see .env.example), so no credentials
live in the code.

Run:  python tools/setup_database.py

This creates the TABLE. It does not create the DATABASE, because the database
is created once, by the PostgreSQL container itself, from POSTGRES_DB in
docker-compose.yml - see docs/DATABASE.md.

That distinction is the whole point. When every developer ran this and it
issued CREATE DATABASE, every developer ended up with a database of their own
and the team's hands were scattered across all of them. Creating a database
now needs --create-database, said out loud, by whoever is setting up the
server.
"""

import argparse
import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db  # noqa: E402


def create_database(settings):
    """CREATE DATABASE, for setting up a server that is not a container."""
    target = settings["dbname"]
    admin = dict(settings, dbname="postgres")
    try:
        with psycopg.connect(autocommit=True, connect_timeout=5, **admin) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target,)
            ).fetchone()
            if exists:
                print("Database %r already exists" % target)
            else:
                conn.execute('CREATE DATABASE "%s"' % target)
                print("Created database %r" % target)
    except Exception as exc:  # noqa: BLE001
        print("Could not create the database: %s" % exc)
        print("Check POSTGRES_* values in your .env file.")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Create the poker_hands table.")
    parser.add_argument(
        "--create-database", action="store_true",
        help="also CREATE DATABASE if it is missing. Only for a server that "
             "is not the project's container, which makes its own.")
    args = parser.parse_args()

    try:
        settings = db.connection_settings()
    except db.DatabaseError as exc:
        print(exc)
        return 1
    print("Connecting to %s" % db.describe_target(settings))

    if args.create_database and not create_database(settings):
        return 1

    try:
        db.ensure_schema()
    except db.DatabaseError as exc:
        print("Could not create the table: %s" % exc)
        if "does not exist" in str(exc):
            print("The database itself is missing. If this server is not the "
                  "project's container, re-run with --create-database.")
        return 1

    print("Table poker_hands is ready in %r" % settings["dbname"])

    filled = backfill_winners()
    if filled:
        print("Worked out the winner for %d hand(s) recorded before that column "
              "existed" % filled)
    return 0


def backfill_winners():
    """Fill in winner/dealer_qualified for hands stored before those columns.

    The cards are already there, so the result can simply be recalculated.
    """
    from poker.hand_evaluator import compare_hands, dealer_qualifies, evaluate_showdown

    try:
        rows = db.fetch_all_hands()
    except db.DatabaseError as exc:
        print("Could not read existing hands: %s" % exc)
        return 0

    updates = []
    for row in rows:
        if row.get("winner"):
            continue
        community = [row["flop_card_1"], row["flop_card_2"], row["flop_card_3"],
                     row["turn_card"], row["river_card"]]
        try:
            player, dealer = evaluate_showdown(
                [row["player_card_1"], row["player_card_2"]],
                [row["dealer_card_1"], row["dealer_card_2"]],
                community,
            )
        except ValueError as exc:
            print("Skipping hand #%s: %s" % (row["id"], exc))
            continue
        updates.append((compare_hands(player, dealer), dealer_qualifies(dealer), row["id"]))

    if not updates:
        return 0
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE poker_hands SET winner = %s, dealer_qualified = %s WHERE id = %s",
            updates,
        )
        conn.commit()
    return len(updates)


if __name__ == "__main__":
    raise SystemExit(main())
