"""Apply the backend's SQL migrations, in order, once each.

    python -m backend.migrate            # run from the project root

The baseline is database/schema.sql (the poker_hands table, unchanged and idempotent); the numbered files in
backend/migrations/ build on it. Each migration runs in its own transaction and is recorded in schema_migrations,
so running this twice, or against a database that already has the older tables, is safe.

Connection settings are the same POSTGRES_* variables database/db.py has always used. The migration role needs
CREATE on the schema; the running API does not.
"""

import logging
import os
import re
import sys

from database import db

logger = logging.getLogger("backend.migrate")

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS_DIR = os.path.join(HERE, "migrations")
BASELINE_VERSION = "0000_base"
_NAME = re.compile(r"^(\d{4}_[a-z0-9_]+)\.sql$")


def discover():
    """[(version, sql)] in application order: the baseline first, then the numbered files."""
    with open(db.SCHEMA_PATH, "r", encoding="utf-8") as handle:
        found = [(BASELINE_VERSION, handle.read())]
    for name in sorted(os.listdir(MIGRATIONS_DIR)):
        match = _NAME.match(name)
        if match:
            with open(os.path.join(MIGRATIONS_DIR, name), "r", encoding="utf-8") as handle:
                found.append((match.group(1), handle.read()))
    return found


def applied_versions(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        cur.execute("SELECT version FROM schema_migrations")
        rows = {row[0] for row in cur.fetchall()}
    conn.commit()
    return rows


def migrate(conn=None):
    """Apply what is missing. Returns the list of versions applied now."""
    own = conn is None
    conn = conn or db.get_connection()
    done = []
    try:
        have = applied_versions(conn)
        for version, sql in discover():
            if version in have:
                continue
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
            done.append(version)
            logger.info("Applied migration %s", version)
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()
    return done


def current_version(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT max(version) FROM schema_migrations")
        return cur.fetchone()[0]


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        done = migrate()
    except db.DatabaseError as exc:
        print("Cannot migrate: %s" % exc, file=sys.stderr)
        return 1
    print("Applied: %s" % (", ".join(done) if done else "nothing (already up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
