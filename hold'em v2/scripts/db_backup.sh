#!/usr/bin/env bash
# Back up the poker_tracker database to backups/poker_tracker_<date>.dump
#
#   bash scripts/db_backup.sh
#
# Settings are read from .env. The password is passed to pg_dump through
# PGPASSWORD for the length of this command and is never written anywhere.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$here/.env"

if [[ ! -f "$env_file" ]]; then
    echo "No .env at $env_file - copy .env.example and fill it in." >&2
    exit 1
fi

# Read the settings without echoing them.
get() { grep -E "^$1=" "$env_file" | tail -1 | cut -d= -f2- | tr -d '\r'; }
host="$(get POSTGRES_HOST)"
port="$(get POSTGRES_PORT)"
name="$(get POSTGRES_DATABASE)"
user="$(get POSTGRES_USER)"
PGPASSWORD="$(get POSTGRES_PASSWORD)"
export PGPASSWORD

if [[ -z "$host" || -z "$port" || -z "$user" || -z "$PGPASSWORD" ]]; then
    echo "POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER and POSTGRES_PASSWORD" >&2
    echo "must all be set in .env." >&2
    exit 1
fi

mkdir -p "$here/backups"
out="$here/backups/${name}_$(date +%Y-%m-%d_%H%M%S).dump"

echo "Backing up ${user}@${host}:${port}/${name}"
# -Fc is the custom format: compressed, and restorable with pg_restore.
pg_dump --host="$host" --port="$port" --username="$user" \
        --dbname="$name" --format=custom --no-owner --no-privileges \
        --file="$out"

unset PGPASSWORD
echo "Wrote $out"
ls -lh "$out" | awk '{print "  " $5}'
