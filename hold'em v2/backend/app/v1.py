"""The authenticated API the desktop app talks to:  /api/v1/*

The backend owns everything the desktop must not: the database connection, validation, who is signed in, and
duplicate handling. Hand storage reuses database.db.insert_hand unchanged (so the unique fingerprint and
ON CONFLICT DO NOTHING remain the one duplicate rule); this module adds accounts, devices and attribution around it.

Every error is  {"detail": <safe text>, "code": <machine-readable>}. Database error text (which can contain host names)
is logged here and never sent to a client.
"""

import datetime
import hmac
import logging
import re
from contextlib import contextmanager
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend import migrate
from backend.app import security, settings
from database import db
from poker.hand_record import CARD_ORDER, build_hand_record

logger = logging.getLogger("backend.v1")

router = APIRouter(prefix="/api/v1")
limiter = security.LoginLimiter(settings.LOGIN_FAILURE_LIMIT, settings.LOGIN_FAILURE_WINDOW_SECONDS)

CARD_RE = re.compile(r"^(10|[2-9JQKA])[SHDC]$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.\-+]{1,20}$")
FINGERPRINT_RE = re.compile(r"^[0-9A-Z\-]{17,40}$")


class ApiProblem(Exception):
    def __init__(self, status, code, detail, headers=None):
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers or {}


def register(app: FastAPI):
    """Mount the v1 router and its error format on the app."""

    @app.exception_handler(ApiProblem)
    async def _problem(_request, exc: ApiProblem):
        return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status, headers=exc.headers)

    app.include_router(router)


@contextmanager
def connection():
    """A database connection, with failures turned into a safe 503."""
    try:
        conn = db.get_connection()
    except db.DatabaseError as exc:
        logger.error("Database unavailable: %s", exc)
        raise ApiProblem(503, "db_unavailable", "The service is temporarily unavailable.") from None
    try:
        yield conn
    except ApiProblem:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Database operation failed")
        raise ApiProblem(503, "db_error", "The service is temporarily unavailable.") from None
    finally:
        conn.close()


# --------------------------------------------------------------------------------------- models

class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=security.MAX_PASSWORD_LENGTH)


class RegisterIn(LoginIn):
    invite_code: Optional[str] = Field(default=None, max_length=100)


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    platform: Literal["Windows", "macOS", "Linux"]
    architecture: str = Field(min_length=1, max_length=20)
    app_version: str

    @field_validator("app_version")
    @classmethod
    def _version(cls, value):
        if not VERSION_RE.match(value):
            raise ValueError("invalid version")
        return value


class HandIn(BaseModel):
    """One completed hand. Unknown fields are rejected: only these are ever accepted."""

    model_config = ConfigDict(extra="forbid")

    player_1: str
    player_2: str
    flop_1: str
    flop_2: str
    flop_3: str
    turn: str
    river: str
    dealer_1: str
    dealer_2: str
    player_hand: str = Field(min_length=1, max_length=30)
    dealer_hand: str = Field(min_length=1, max_length=30)
    winner: Literal["Player", "Dealer", "Tie"]
    dealer_qualified: bool
    hand_fingerprint: str
    round_started_at: Optional[datetime.datetime] = None
    deal_to_flop_seconds: Optional[float] = Field(default=None, ge=0, le=86400)
    flop_to_showdown_seconds: Optional[float] = Field(default=None, ge=0, le=86400)
    round_seconds: Optional[float] = Field(default=None, ge=0, le=86400)
    seconds_since_previous_round: Optional[float] = Field(default=None, ge=0, le=10 * 86400)

    @field_validator(*CARD_ORDER)
    @classmethod
    def _card(cls, value):
        if not CARD_RE.match(value):
            raise ValueError("not a card")
        return value

    @field_validator("hand_fingerprint")
    @classmethod
    def _fingerprint(cls, value):
        if not FINGERPRINT_RE.match(value):
            raise ValueError("invalid fingerprint")
        return value


# --------------------------------------------------------------------------------------- auth

def _client_key(request: Request, email: str):
    """The sign-in rate limit is per e-mail, deliberately not per address: behind a proxy the address is either the proxy's or
    a forwarded header the client can vary, so keying on it would let password guessing rotate its way round the limit."""
    return email.strip().lower()


def _auth(authorization: Optional[str] = Header(default=None), x_device_id: Optional[str] = Header(default=None)):
    """The signed-in user for this request (and the installation, when it names a valid one)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiProblem(401, "not_signed_in", "Sign in to continue.", {"WWW-Authenticate": "Bearer"})
    token = authorization[7:].strip()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT s.id, u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now() AND u.is_active",
            (security.token_digest(token),))
        row = cur.fetchone()
        if row is None:
            raise ApiProblem(401, "session_expired", "Your session has ended. Sign in again.",
                             {"WWW-Authenticate": "Bearer"})
        device = None
        if x_device_id:
            cur.execute("SELECT id FROM devices WHERE id::text = %s AND user_id = %s AND revoked_at IS NULL",
                        (x_device_id, row[1]))
            found = cur.fetchone()
            if found:
                device = str(found[0])
                cur.execute("UPDATE devices SET last_seen_at = now() WHERE id = %s", (found[0],))
                conn.commit()
    return {"session_id": row[0], "user_id": row[1], "email": row[2], "device_id": device}


@router.post("/auth/login")
def login(body: LoginIn, request: Request):
    key = _client_key(request, body.email)
    wait = limiter.retry_after(key)
    if wait:
        raise ApiProblem(429, "too_many_attempts", "Too many failed sign-ins. Try again later.",
                         {"Retry-After": str(wait)})
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, password_hash, is_active, email FROM users WHERE lower(email) = lower(%s)",
                    (body.email.strip(),))
        user = cur.fetchone()
        valid = security.verify_password(body.password, user[1] if user else security.DUMMY_HASH)
        if not (user and valid and user[2]):
            limiter.failed(key)
            raise ApiProblem(401, "invalid_credentials", "Incorrect e-mail or password.")
        limiter.succeeded(key)
        token = security.new_token()
        expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=settings.SESSION_DAYS)
        cur.execute("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                    (user[0], security.token_digest(token), expires))
        cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user[0],))
        conn.commit()
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat(),
            "user": {"id": user[0], "email": user[3]}}


@router.post("/auth/register")
def register_user(body: RegisterIn, request: Request):
    if not settings.registration_enabled():
        raise ApiProblem(404, "not_found", "Not found.")
    invite = settings.registration_invite_code()
    if invite and not hmac.compare_digest(invite, body.invite_code or ""):
        raise ApiProblem(403, "invite_required", "A valid invitation code is required.")
    email = body.email.strip()
    if not EMAIL_RE.match(email):
        raise ApiProblem(422, "invalid_email", "Enter a valid e-mail address.")
    try:
        password_hash = security.hash_password(body.password)
    except ValueError as exc:
        raise ApiProblem(422, "weak_password", str(exc)) from None
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE lower(email) = lower(%s)", (email,))
        if cur.fetchone():
            raise ApiProblem(409, "email_taken", "An account with this e-mail already exists.")
        cur.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id", (email, password_hash))
        user_id = cur.fetchone()[0]
        conn.commit()
    return {"id": user_id, "email": email}


@router.post("/auth/logout")
def logout(auth=Depends(_auth)):
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE sessions SET revoked_at = now() WHERE id = %s", (auth["session_id"],))
        conn.commit()
    return {"ok": True}


@router.get("/me")
def me(auth=Depends(_auth)):
    return {"id": auth["user_id"], "email": auth["email"], "device_id": auth["device_id"]}


@router.post("/devices")
def create_device(body: DeviceIn, auth=Depends(_auth)):
    with connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO devices (user_id, name, platform, architecture, app_version, last_seen_at) "
                    "VALUES (%s, %s, %s, %s, %s, now()) RETURNING id",
                    (auth["user_id"], body.name, body.platform, body.architecture, body.app_version))
        device_id = str(cur.fetchone()[0])
        conn.commit()
    return {"device_id": device_id}


# --------------------------------------------------------------------------------------- data

@router.get("/health")
def health():
    """No sign-in needed: lets the app tell 'cannot reach the server' from 'not signed in'. Reveals no detail."""
    try:
        with connection() as conn:
            version = migrate.current_version(conn)
        return {"ok": version is not None, "database": True, "schema_version": version,
                "message": "ok" if version else "The database has not been migrated."}
    except ApiProblem:
        return {"ok": False, "database": False, "schema_version": None, "message": "database unavailable"}


@router.post("/hands")
def create_hand(hand: HandIn, auth=Depends(_auth)):
    cards = {slot: getattr(hand, slot) for slot in CARD_ORDER}
    try:
        built = build_hand_record(cards)
    except ValueError as exc:
        raise ApiProblem(422, "invalid_hand", str(exc)) from None
    if (built["hand_fingerprint"] != hand.hand_fingerprint or built["winner"] != hand.winner
            or bool(built["dealer_qualified"]) != hand.dealer_qualified):
        raise ApiProblem(422, "inconsistent_hand", "The hand does not match its own cards.")
    record = hand.model_dump()
    try:
        inserted, row = db.insert_hand(record)
    except db.DatabaseError as exc:
        logger.error("Insert failed: %s", exc)
        raise ApiProblem(503, "db_unavailable", "The service is temporarily unavailable.") from None
    if not inserted:
        return {"status": "duplicate", "hand_fingerprint": hand.hand_fingerprint}
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE poker_hands SET reported_by_user_id = %s, reported_by_device_id = %s WHERE id = %s",
                    (auth["user_id"], auth["device_id"], row["id"]))
        conn.commit()
    return {"status": "created", "id": row["id"], "hand_fingerprint": hand.hand_fingerprint,
            "recorded_at": row["recorded_at"].isoformat(sep=" ") if row.get("recorded_at") else None}


def _jsonable(row):
    return {key: (value.isoformat(sep=" ") if isinstance(value, (datetime.datetime, datetime.date)) else value)
            for key, value in row.items()}


@router.get("/hands")
def list_hands(limit: int = 5000, auth=Depends(_auth)):
    limit = max(1, min(int(limit), settings.MAX_HANDS_PAGE))
    try:
        rows = db.fetch_all_hands()
    except db.DatabaseError as exc:
        logger.error("Query failed: %s", exc)
        raise ApiProblem(503, "db_unavailable", "The service is temporarily unavailable.") from None
    return {"hands": [_jsonable(row) for row in rows[-limit:]]}


@router.get("/hands/{fingerprint}")
def hand_exists(fingerprint: str, auth=Depends(_auth)):
    if not FINGERPRINT_RE.match(fingerprint):
        raise ApiProblem(404, "not_found", "No such hand.")
    try:
        found = db.fingerprint_exists(fingerprint)
    except db.DatabaseError as exc:
        logger.error("Lookup failed: %s", exc)
        raise ApiProblem(503, "db_unavailable", "The service is temporarily unavailable.") from None
    if not found:
        raise ApiProblem(404, "not_found", "No such hand.")
    return {"exists": True}


@router.get("/stats")
def stats(auth=Depends(_auth)):
    try:
        return db.win_statistics()
    except db.DatabaseError as exc:
        logger.error("Statistics failed: %s", exc)
        raise ApiProblem(503, "db_unavailable", "The service is temporarily unavailable.") from None
