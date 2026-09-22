# The database

PostgreSQL is the only database this project supports. There is no SQLite, no
local file store, and no second copy to fall back to. `data/poker_hands.xlsx`
is a mirror written *after* a successful insert — useful, but never the source
of truth.

**Cloning this repository does not create a database.** It cannot: there are no
default connection settings, so a clone without an `.env` fails with a message
naming what is missing rather than quietly connecting somewhere.

---

## 1. Which architecture

There are two ways to run this, and they are not interchangeable.

| | A — one shared server | B — one container each |
|---|---|---|
| everyone sees the same hands | yes | no |
| a hand recorded by one person is in everyone's history | yes | no |
| useful for | collecting data as a team | trying things out alone |

**This project is set up for A.** The reason is the data: the point of the
tracker is to accumulate rounds, and a scenario judged against 40 hands on one
laptop and 200 on another is being judged against two different things. Option
B silently splits the history, which is exactly the failure this setup exists
to prevent.

```
   THE DATABASE HOST  (one machine, set up once)
   ─────────────────────────────────────────────
   docker compose --env-file .env.docker up -d db
        └── postgres:18   container poker_tracker_postgres
             └── volume   poker_tracker_pgdata
                  └── database poker_tracker
                       └── table poker_hands

   DEVELOPER MACHINES  (clone, configure, run)
   ─────────────────────────────────────────────
   Dev A ─┐
   Dev B ─┼── .env: POSTGRES_HOST=<the host> ──► the same poker_hands
   Dev C ─┘   no Docker, no compose, no schema creation
```

Use B only if you deliberately want a private sandbox, and know your hands stay
there.

---

## 2. Prerequisites

* Docker Desktop with the WSL2 backend (database host only)
* Python 3.12+ (everyone)

---

## 3. Setting up the database host

Once, on the one machine that will hold the data.

### If that machine also runs the tracker

`docker compose` reads `.env` by default, and `.env` already carries
`POSTGRES_DATABASE`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_PORT`
under exactly the names the compose file wants. There is nothing else to write:

```bash
docker compose up -d db
```

`DB_BIND` is not in `.env`, so the port binds to `127.0.0.1` and the container
is reachable from this machine only. That is the right default; see
[sharing over a LAN](#6-sharing-over-a-lan) to change it.

**This is how the database on this machine is currently running.**

### If that machine only hosts the database

A dedicated host has no application and no `.env`. Give it its own file, so
server credentials are not kept inside an application config:

```bash
cp .env.docker.example .env.docker
```

Edit `.env.docker`:

* `POSTGRES_PASSWORD` — generate one, don't reuse anything:
  `python -c "import secrets; print(secrets.token_urlsafe(24))"`
* `DB_BIND` — `127.0.0.1` for yourself, or the host's LAN address to share
* `POSTGRES_PORT` — `5433` by default, so the container does not collide with
  a PostgreSQL already installed on the machine

Start it:

```bash
docker compose --env-file .env.docker up -d db
```

`.env.docker` is gitignored. It is the server's credentials; never commit it.

Either way the result is the same container, and the rest of this document
applies unchanged. Where a command below says `--env-file .env.docker`, drop
that flag if you are using `.env`.

Create the table:

```bash
python tools/setup_database.py
```

### The values these settings initialise

`POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD` are read **only when the
volume is first created**. Changing `POSTGRES_PASSWORD` in `.env.docker` later
does not change the password of an existing database — see
[rotating the password](#10-rotating-the-password).

---

## 4. Setting up a developer machine

```bash
git clone https://github.com/bot071654/Tracker.git
cd "hold'em v2"
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with the host, port, user and password of the server above. Ask
whoever runs it; the password is not in the repository and never will be.

```bash
python tools/generate_templates.py     # starter card templates
python -m pytest tests -q
python app.py
```

That is the whole setup. There is **no** database to create and **no** schema
to build — both already exist on the host. If `app.py` reports a missing
table, you are pointed at the wrong database; check `POSTGRES_DATABASE`.

---

## 5. Two networking cases

**A — the application runs in the same compose project as the database.**
Use the service name; the port is the container's own.

```
POSTGRES_HOST=db
POSTGRES_PORT=5432
```

**B — the application runs on a developer's machine, the database elsewhere.**
Use the host's address and the published port.

```
POSTGRES_HOST=10.0.0.42      # the database host's LAN address
POSTGRES_PORT=5433
```

If you are the person running the container and nothing else connects,
`POSTGRES_HOST=localhost` is case B with the host being you. Do not assume
`localhost` is right for anyone else.

---

## 6. Sharing over a LAN

By default the container publishes on `127.0.0.1` and is reachable only from
the host. To share it, three things must line up.

**1. Bind the published port to the LAN address**, not to every interface. In
`.env.docker`:

```
DB_BIND=10.0.0.42
```

`DB_BIND=0.0.0.0` publishes on every interface. On a machine with a public IP
that puts PostgreSQL on the internet, where it will be found and attacked
within hours — credential stuffing against `postgres`, and any unpatched CVE
in your server version. Do not do it.

This machine's LAN address is **192.168.1.19**, so that is the value to use.
Do not use `0.0.0.0`: this machine also has a public IPv6 address
(`2401:4900:...`), and `0.0.0.0`/`[::]` would publish PostgreSQL on that too.

**2. Allow the port through Windows Firewall, for the LAN only.** You must run
this yourself, as Administrator:

```powershell
New-NetFirewallRule -DisplayName "poker_tracker PostgreSQL (LAN)" `
  -Direction Inbound -Protocol TCP -LocalPort 5433 `
  -RemoteAddress 192.168.1.0/24 -Action Allow
```

`-RemoteAddress` is the restriction that matters. Without it the rule admits
everyone who can route to the machine.

**3. Give each person their own credentials**, so access can be withdrawn
without changing everyone's. `scripts/add_developer.sql` has the statements
and the notes; run it once per person:

```bash
docker exec -it poker_tracker_postgres psql -U poker_tracker -d poker_tracker
```

Each developer gets `SELECT, INSERT` and nothing else - no `UPDATE`, no
`DELETE`. The tracker only ever inserts, and a recorded hand is a fact about a
round that happened.

Then send them **[JOIN_THE_DATABASE.md](JOIN_THE_DATABASE.md)**.

**Across the internet, use a VPN** (WireGuard or Tailscale) and keep `DB_BIND`
on the private interface. PostgreSQL's wire protocol is not something to expose
directly, and a port-forward on a home router is not a security boundary.

---

## 7. Running it

```bash
docker compose up -d db              # start          (reads .env)
docker compose ps                    # state + health
docker compose logs -f db            # follow the log
docker compose stop db               # stop, keep everything
docker compose down                  # remove the container, keep the data
```

On a dedicated database host, add `--env-file .env.docker` to the first one.

**`docker compose down` does not delete your data.** The volume is declared
`external`, so compose will not remove it — `docker compose down -v` cannot
take it either.

This was tested against the real database rather than assumed: `docker compose
down` removed the container, `docker compose up -d db` built a new one, and all
251 recorded hands were still there with the same ids and timestamps.

To destroy the data you would have to name it explicitly:

```bash
docker volume rm poker_tracker_pgdata      # deletes every recorded hand
```

Never run that as part of setup.

---

## 8. Health

The container has a healthcheck, so "running" and "ready" are different states.

```bash
docker compose ps                    # STATUS column: starting | healthy | unhealthy
docker inspect --format "{{.State.Health.Status}}" poker_tracker_postgres
```

From Python, without a container:

```bash
python -c "from database import db; print(db.check_connection()); print(db.verify_schema())"
```

```
(True, 'PostgreSQL connected')
(True, 'Schema verified')
```

---

## 9. Backup and restore

The password is never written into a script. `PGPASSWORD` is read from your
`.env` for the length of the command.

**Backup:**

```bash
bash scripts/db_backup.sh                  # -> backups/poker_tracker_<date>.dump
```

**Restore** (into an empty database — it will refuse a non-empty one unless you
pass `--force`):

```bash
bash scripts/db_restore.sh backups/poker_tracker_2026-09-22.dump
```

On Windows, `scripts\db_backup.ps1` and `scripts\db_restore.ps1` do the same.

Back up before upgrading the PostgreSQL major version. A dump from 18 will not
restore into 17.

---

## 10. Rotating the password

Changing `.env.docker` alone does nothing — those values only apply when the
volume is first created. Change it in the database:

```bash
docker exec -it poker_tracker_postgres \
  psql -U poker_tracker -d poker_tracker -c "\password poker_tracker"
```

Then update `POSTGRES_PASSWORD` in `.env.docker` on the host and in `.env` on
every developer machine.

---

## 11. Importing a history from Excel

> **This has been done, and it is final.** On 2026-09-22 all 251 hands from
> `data/poker_hands.xlsx` were imported into the container, verified field by
> field (4,769 comparisons, no mismatches), and the spreadsheet was left
> byte-identical. The owner confirmed the spreadsheet was the complete record,
> so the PostgreSQL 18 service that used to run on port 5432 holds nothing
> that is not here and is being retired. **The container is now the only
> source of truth.** Nothing below needs running again.

A one-time migration, for a spreadsheet that outlived the database beside it.
Normal running never needs this.

```bash
python tools/import_excel.py --dry-run      # read and report, change nothing
python tools/import_excel.py                # import
```

It re-derives each hand from the nine cards using
`poker.hand_record.build_hand_record` — the same code the tracker uses — so the
fingerprints match and a hand already stored is recognised rather than
duplicated. It is safe to re-run; the spreadsheet is opened read-only.

Excel's `ID` column is not carried over. Those are the row ids of whatever
database wrote the sheet; PostgreSQL assigns its own. `recorded_at` **is**
preserved, and rows are imported oldest first so the new ids follow the same
order.

If you ever do re-run it, it is harmless: every hand already stored is
recognised by its fingerprint and skipped, so the count does not move.

---

## 12. Troubleshooting

| symptom | cause |
|---|---|
| `Missing database settings: POSTGRES_HOST, ...` | no `.env`. Copy `.env.example` and fill it in. Nothing is assumed on purpose. |
| `Cannot connect to ... Connection refused` | container not running, or `POSTGRES_PORT` wrong. `docker compose ps`. |
| `Cannot connect to ... timeout expired` | firewall, or `DB_BIND` is `127.0.0.1` on a host you are reaching over the LAN. |
| `No poker_hands table in ...` | right server, wrong database — check `POSTGRES_DATABASE`. Or the table was never made: `python tools/setup_database.py`. |
| `password authentication failed` | wrong password, or it was rotated in the database but not in your `.env`. |
| `port is already allocated` | something else holds 5433. `netstat -ano | findstr 5433`. |
| hands recorded but nobody else sees them | you are on your own database. Check `POSTGRES_HOST` is the team's host, not `localhost`. |

---

## 13. Security

* `.env` and `.env.docker` are gitignored. Never commit either.
* No password appears in `docker-compose.yml`, any Python file, this document,
  or any script.
* `POSTGRES_USER` is `poker_tracker`, not `postgres`. The application has no
  reason to be a superuser.
* The published port binds to `127.0.0.1` unless you deliberately change it.
* Give each developer their own role with `SELECT, INSERT` only.
* Use a VPN for anything beyond a trusted LAN.
