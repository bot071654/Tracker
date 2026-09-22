# Joining the team database

Hand this page to anyone who is going to work on the project.

**You do not install PostgreSQL. You do not run Docker. You do not create a
database.** There is one database, it lives in a container on the project
owner's machine, and it already holds every recorded hand. You connect to it.

```
   OWNER'S MACHINE                      YOUR MACHINE
   ───────────────                      ────────────
   Docker                               git clone   (files only)
    └── postgres:18                     .env        (points at the owner)
         └── poker_tracker      ◄─────── python app.py
              └── poker_hands
                   251 hands            no Docker, no PostgreSQL,
                   and counting          no database of your own
```

---

## What you need from the owner

Four values. Ask for them; they are not in the repository and never will be.

| | example | what it is |
|---|---|---|
| host | `192.168.1.19` | the owner's machine on your network |
| port | `5433` | the published port |
| user | `dev_yourname` | **your own** login, not the owner's |
| password | — | yours, given to you privately |

---

## Setup

```bash
git clone https://github.com/bot071654/Tracker.git
cd "hold'em v2"
python -m pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:

```
POSTGRES_HOST=192.168.1.19     # the owner's machine - NOT localhost
POSTGRES_PORT=5433
POSTGRES_DATABASE=poker_tracker
POSTGRES_USER=dev_yourname
POSTGRES_PASSWORD=             # the one you were given
```

`POSTGRES_HOST=localhost` means *your own machine*. Unless you are the owner,
that is wrong, and the application will tell you so rather than silently
recording hands where nobody else can see them.

Check it:

```bash
python -c "from database import db; print(db.check_connection()); print(db.verify_schema())"
```

```
(True, 'PostgreSQL connected')
(True, 'Schema verified')
```

Then:

```bash
python tools/generate_templates.py
python app.py
```

Every hand you record goes into the same table as everyone else's, and the
whole history is there when you run `python tools/backtest.py`.

---

## If it does not connect

| message | meaning |
|---|---|
| `Missing database settings: ...` | no `.env`, or a blank value. Nothing is assumed on purpose. |
| `Connection refused` | wrong port, or the owner's container is not running. |
| `timeout expired` | you are not on the same network, or the owner's firewall is not open to you. |
| `password authentication failed` | wrong password, or your role was not created yet. |
| `No poker_hands table in ...` | right server, wrong `POSTGRES_DATABASE`. |

None of these are fixed by installing PostgreSQL locally. If you find yourself
about to do that, stop and ask the owner instead — a second database is the one
thing this setup exists to prevent.

---

## Two things to know

**You must be on the owner's network.** Over the internet, the owner sets up a
VPN (WireGuard or Tailscale) and you use the VPN address as `POSTGRES_HOST`.
PostgreSQL is not exposed to the internet directly.

**If the owner's machine is off, you cannot record hands.** The tracker keeps
retrying every 30 seconds and tells you in red at the bottom of the window. The
Excel mirror at `data/poker_hands.xlsx` is written only after a successful
insert, so it is never ahead of the database.
