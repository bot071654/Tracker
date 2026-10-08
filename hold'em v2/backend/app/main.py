"""Hold'em Tracker API.

The smallest FastAPI app that lets the Chrome Extension record a completed
hand. It does NOT reimplement persistence: every database operation is
`database.db` (project root), imported unchanged - the same module the
desktop tracker already uses, pointed at the same Railway PostgreSQL via the
same root-level `.env`. There is deliberately no second database layer, no
second schema, and no second fingerprint/duplicate rule; `db.insert_hand`'s
existing `ON CONFLICT (hand_fingerprint) DO NOTHING` is exactly what backs
this endpoint's duplicate handling.

Run from the PROJECT ROOT (not from backend/), so `database` resolves:

    python -m uvicorn backend.app.main:app --reload --port 8000

Nothing here ever bets, wagers, or clicks anything - it only stores hand
records the extension already finished evaluating.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

# This process is the one place that talks to PostgreSQL directly. database/db.py reads this to know it must
# never forward to another API (the desktop build does exactly that; the server must not).
os.environ["POKER_TRACKER_BACKEND"] = "1"

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.app import settings, v1
from database import db

@asynccontextmanager
async def _lifespan(_app):
    if settings.auto_migrate():
        from backend import migrate
        applied = migrate.migrate()           # raises on failure: the API does not start against a half-migrated database
        logging.getLogger("backend").info("Migrations applied at start-up: %s", ", ".join(applied) or "none (up to date)")
    yield


app = FastAPI(title="Hold'em Tracker API", version="0.1.0", lifespan=_lifespan)

# Permissive on purpose for this local/demo phase: the extension's side
# panel and content script run from a chrome-extension://<id> origin (the
# id varies per unpacked install) and the demo page from http://localhost
# during development. Neither carries cookies/credentials, so a wildcard
# origin has no confidentiality cost here. Tighten this before any real
# deployment - see the Phase 8 report.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins(),      # ["*"] unless CORS_ALLOW_ORIGINS is set (see backend/app/settings.py)
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class HandRecordIn(BaseModel):
    """Exactly poker.hand_record.build_hand_record()'s output shape.

    Field names match the tracker's slot names and the existing hand-record
    keys verbatim (see poker/hand_record.py and database/db.py's
    SLOT_TO_COLUMN) - this is a wire format, not a new schema. db.insert_hand
    expects a dict shaped exactly like this.
    """

    player_1: str
    player_2: str
    flop_1: str
    flop_2: str
    flop_3: str
    turn: str
    river: str
    dealer_1: str
    dealer_2: str
    player_hand: str
    dealer_hand: str
    winner: Literal["Player", "Dealer", "Tie"]
    dealer_qualified: bool
    hand_fingerprint: str
    round_started_at: Optional[str] = None
    deal_to_flop_seconds: Optional[float] = None
    flop_to_showdown_seconds: Optional[float] = None
    round_seconds: Optional[float] = None
    seconds_since_previous_round: Optional[float] = None


v1.register(app)       # the authenticated API the desktop app uses: /api/v1/*


def _legacy_enabled():
    """/api/health and /api/hands below are the original UNAUTHENTICATED endpoints (Chrome extension demo).
    Set LEGACY_ANONYMOUS_API=0 on a deployment that only serves the desktop app."""
    if not settings.legacy_anonymous_api_enabled():
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/api/health")
def health():
    """Reuses database.db.check_connection() - no separate health logic."""
    _legacy_enabled()
    ok, message = db.check_connection()
    return {"ok": ok, "message": message}


@app.post("/api/hands")
def create_hand(payload: HandRecordIn):
    """Insert one completed hand. Reuses database.db.insert_hand() as-is.

    Returns {"status": "created", "id", "hand_fingerprint"} or
    {"status": "duplicate", "hand_fingerprint"} - insert_hand's own
    ON CONFLICT DO NOTHING already tells the two apart; this endpoint adds
    no dedup logic of its own.
    """
    _legacy_enabled()
    record = payload.model_dump()
    try:
        inserted, row = db.insert_hand(record)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not inserted:
        return {"status": "duplicate", "hand_fingerprint": record["hand_fingerprint"]}
    return {"status": "created", "id": row["id"], "hand_fingerprint": row["hand_fingerprint"]}
