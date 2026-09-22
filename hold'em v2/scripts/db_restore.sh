#!/usr/bin/env bash
# Restore a dump made by scripts/db_backup.sh
#
#   bash scripts/db_restore.sh backups/poker_tracker_2026-09-22.dump
#   bash scripts/db_restore.sh <dump> --force     # allow a non-empty table
#
# Refuses by default if poker_hands already has rows, because restoring over a
# live history is how a history gets lost. Settings come from .env; the
# password is never written anywhere.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$here/.env"
dump="${1:-}"
force="${2:-}"

if [[ -z "$dump" ]]; then
    echo "Usage: bash scripts/db_restore.sh <dump file> [--force]" >&2
    exit 1
fi
if [[ ! -f "$dump" ]]; then
    echo "No such dump: $dump" >&2
    exit 1
fi
if [[ ! -f "$env_file" ]]; then
    echo "No .env at $env_file - copy .env.example and fill it in." >&2
    exit 1
fi

get() { grep -E "^$1=" "$env_file" | tail -1 | cut -d= -f2- | tr -d '\r'; }
host="$(get POSTGRES_HOST)"
port="$(get POSTGRES_PORT)"
name="$(get POSTGRES_DATABASE)"
user="$(get POSTGRES_USER)"
PGPASSWORD="$(get POSTGRES_PASSWORD)"
export PGPASSWORD

echo "Restoring into ${user}@${host}:${port}/${name}"

# How many rows are there now? A missing table counts as empty.
rows="$(psql --host="$host" --port="$port" --username="$user" --dbname="$name" \
        -tAc "SELECT coalesce((SELECT count(*) FROM poker_hands), 0)" 2>/dev/null || echo 0)"
rows="$(echo "$rows" | tr -d '[:space:]')"

if [[ "$rows" != "0" && "$force" != "--force" ]]; then
    echo >&2
    echo "poker_hands already holds $rows row(s). Refusing to restore over it." >&2
    echo "Back up first:   bash scripts/db_backup.sh" >&2
    echo "Then re-run:     bash scripts/db_restore.sh \"$dump\" --force" >&2
    unset PGPASSWORD
    exit 1
fi

# --data-only would fail on a fresh database with no table; this restores the
# schema too, and skips anything already present.
pg_restore --host="$host" --port="$port" --username="$user" \
           --dbname="$name" --no-owner --no-privileges \
           --if-exists --clean "$dump"

after="$(psql --host="$host" --port="$port" --username="$user" --dbname="$name" \
         -tAc "SELECT count(*) FROM poker_hands")"
unset PGPASSWORD
echo "Restored. poker_hands now holds $(echo "$after" | tr -d '[:space:]') row(s)."
