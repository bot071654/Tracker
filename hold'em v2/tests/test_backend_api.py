"""The backend: migrations, accounts, devices, hand storage, duplicate handling and safe errors.

Runs the real FastAPI app in its own process against a throwaway PostgreSQL cluster (see tests/pgcluster.py).
Skips when no PostgreSQL binaries are installed.
"""

import itertools
import json
import random
import urllib.error
import urllib.request
import uuid

import pytest

from backend import manage, migrate
from backend.app import security
from database import db
from pgcluster import ApiServer, pg_cluster, pg_cluster_process  # noqa: F401 - the fixtures are used below
from poker.hand_record import CARD_ORDER, build_hand_record

PASSWORD = "correct horse battery"
DECK = [r + s for r in ["A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2"] for s in "SHDC"]
_random = random.Random(2026)


def make_hand(seed=None):
    """A valid, internally consistent hand with its own random cards (so every test gets a fresh fingerprint)."""
    rng = random.Random(seed) if seed is not None else _random
    cards = rng.sample(DECK, 9)
    record = build_hand_record(dict(zip(CARD_ORDER, cards)))
    keys = list(CARD_ORDER) + ["player_hand", "dealer_hand", "winner", "dealer_qualified", "hand_fingerprint"]
    return {key: record[key] for key in keys}


def call(server, method, path, body=None, token=None, headers=None):
    """(status, json, headers) without raising for HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(server.url + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}"), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {}), {k.lower(): v for k, v in exc.headers.items()}


@pytest.fixture(scope="module")
def migrated(pg_cluster):
    return migrate.migrate()


@pytest.fixture(scope="module")
def server(pg_cluster, migrated):
    api = ApiServer(pg_cluster).start()
    yield api
    api.stop()


@pytest.fixture
def user(pg_cluster, migrated):
    email = "user-%s@example.com" % uuid.uuid4().hex[:10]
    conn = db.get_connection()
    try:
        manage.create_user(conn, email, PASSWORD)
    finally:
        conn.close()
    return email


def sign_in(server, email, password=PASSWORD):
    status, body, _ = call(server, "POST", "/api/v1/auth/login", {"email": email, "password": password})
    assert status == 200, body
    return body["access_token"]


def sql(query, params=()):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall() if cur.description else None
        conn.commit()
        return rows
    finally:
        conn.close()


# ------------------------------------------------------------------------------------ migrations

def test_migrations_are_idempotent_and_ordered(pg_cluster, migrated):
    assert migrated[0] == "0000_base" and migrated == sorted(migrated)
    assert migrate.migrate() == []                                    # second run applies nothing
    versions = [row[0] for row in sql("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [name for name, _ in migrate.discover()]
    tables = {row[0] for row in sql("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    assert {"poker_hands", "users", "devices", "sessions", "schema_migrations"} <= tables


def test_migration_keeps_hands_recorded_before_accounts_existed(pg_cluster, monkeypatch):
    """A database that already holds hands (and no account tables) upgrades without losing or invalidating them."""
    import subprocess
    from pgcluster import EXE
    import os
    subprocess.run([os.path.join(pg_cluster.bin, "psql" + EXE), "-h", "127.0.0.1", "-p", str(pg_cluster.port),
                    "-U", pg_cluster.user, "-d", "postgres", "-c", "CREATE DATABASE legacy_db"], check=True, capture_output=True)
    monkeypatch.setenv("POSTGRES_DATABASE", "legacy_db")
    db.ensure_schema()                                                # what tools/setup_database.py has always done
    hand = make_hand(1)
    inserted, row = db.insert_hand(hand)
    assert inserted
    applied = migrate.migrate()
    assert "0001_users_devices_sessions" in applied and "0002_hand_attribution" in applied
    rows = db.fetch_all_hands()
    assert [r["hand_fingerprint"] for r in rows] == [hand["hand_fingerprint"]]
    assert sql("SELECT reported_by_user_id, reported_by_device_id FROM poker_hands") == [(None, None)]


# ------------------------------------------------------------------------------------ passwords and tokens

def test_password_hashing():
    stored = security.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert security.verify_password(PASSWORD, stored)
    assert not security.verify_password("wrong password!", stored)
    assert not security.verify_password(PASSWORD, "garbage")
    assert security.hash_password(PASSWORD) != stored                   # a fresh salt every time
    with pytest.raises(ValueError):
        security.hash_password("short")


def test_login_limiter():
    now = [0.0]
    limiter = security.LoginLimiter(limit=3, window=100, clock=lambda: now[0])
    key = ("a@example.com", "1.2.3.4")
    for _ in range(3):
        assert limiter.retry_after(key) == 0
        limiter.failed(key)
    assert limiter.retry_after(key) > 0
    now[0] = 101
    assert limiter.retry_after(key) == 0


# ------------------------------------------------------------------------------------ accounts

def test_sign_in_and_session(server, user):
    status, body, _ = call(server, "POST", "/api/v1/auth/login", {"email": user, "password": PASSWORD})
    assert status == 200 and body["token_type"] == "bearer" and body["user"]["email"] == user
    token = body["access_token"]
    assert sql("SELECT count(*) FROM sessions WHERE token_hash = %s", (security.token_digest(token),)) == [(1,)]
    assert sql("SELECT count(*) FROM sessions WHERE token_hash = %s", (token,)) == [(0,)]       # only a hash is stored
    status, me, _ = call(server, "GET", "/api/v1/me", token=token)
    assert status == 200 and me["email"] == user
    assert call(server, "POST", "/api/v1/auth/logout", {}, token=token)[0] == 200
    assert call(server, "GET", "/api/v1/me", token=token)[0] == 401                              # revoked


def test_wrong_password_and_unknown_user_look_the_same(server, user):
    wrong = call(server, "POST", "/api/v1/auth/login", {"email": user, "password": "not the password"})
    unknown = call(server, "POST", "/api/v1/auth/login", {"email": "nobody@example.com", "password": "not the password"})
    assert wrong[0] == unknown[0] == 401
    assert wrong[1] == unknown[1] == {"detail": "Incorrect e-mail or password.", "code": "invalid_credentials"}


def test_repeated_failures_are_rate_limited(server, user):
    codes = [call(server, "POST", "/api/v1/auth/login", {"email": user, "password": "bad password %d" % i})[0] for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    status, body, headers = call(server, "POST", "/api/v1/auth/login", {"email": user, "password": PASSWORD})
    assert status == 429 and int(headers["retry-after"]) > 0                                   # even the right password waits


def test_disabled_users_and_expired_sessions_are_refused(server, user, pg_cluster):
    token = sign_in(server, user)
    conn = db.get_connection()
    try:
        manage.set_active(conn, user, False)
    finally:
        conn.close()
    assert call(server, "GET", "/api/v1/me", token=token)[0] == 401
    assert call(server, "POST", "/api/v1/auth/login", {"email": user, "password": PASSWORD})[0] == 401
    conn = db.get_connection()
    try:
        manage.set_active(conn, user, True)
    finally:
        conn.close()
    token = sign_in(server, user)
    sql("UPDATE sessions SET expires_at = now() - interval '1 minute' WHERE token_hash = %s", (security.token_digest(token),))
    status, body, _ = call(server, "GET", "/api/v1/me", token=token)
    assert status == 401 and body["code"] == "session_expired"


def test_registration_is_off_unless_enabled(server, pg_cluster):
    status, _, _ = call(server, "POST", "/api/v1/auth/register", {"email": "new@example.com", "password": PASSWORD})
    assert status == 404
    open_server = ApiServer(pg_cluster, {"REGISTRATION_ENABLED": "1", "REGISTRATION_INVITE_CODE": "let-me-in"}).start()
    try:
        email = "reg-%s@example.com" % uuid.uuid4().hex[:8]
        assert call(open_server, "POST", "/api/v1/auth/register", {"email": email, "password": PASSWORD})[0] == 403
        assert call(open_server, "POST", "/api/v1/auth/register", {"email": email, "password": PASSWORD, "invite_code": "wrong"})[0] == 403
        assert call(open_server, "POST", "/api/v1/auth/register", {"email": email, "password": "short", "invite_code": "let-me-in"})[1]["code"] == "weak_password"
        assert call(open_server, "POST", "/api/v1/auth/register", {"email": email, "password": PASSWORD, "invite_code": "let-me-in"})[0] == 200
        assert call(open_server, "POST", "/api/v1/auth/register", {"email": email.upper(), "password": PASSWORD, "invite_code": "let-me-in"})[1]["code"] == "email_taken"
        assert sign_in(open_server, email)
    finally:
        open_server.stop()


def test_devices_are_issued_by_the_server_and_belong_to_one_user(server, user):
    token = sign_in(server, user)
    status, body, _ = call(server, "POST", "/api/v1/devices", {"name": "Laptop", "platform": "Windows", "architecture": "x64", "app_version": "1.0.0"}, token=token)
    assert status == 200 and uuid.UUID(body["device_id"])
    assert call(server, "POST", "/api/v1/devices", {"name": "x", "platform": "BeOS", "architecture": "x64", "app_version": "1.0.0"}, token=token)[0] == 422
    other = "other-%s@example.com" % uuid.uuid4().hex[:8]
    conn = db.get_connection()
    try:
        manage.create_user(conn, other, PASSWORD)
    finally:
        conn.close()
    other_token = sign_in(server, other)
    status, me, _ = call(server, "GET", "/api/v1/me", token=other_token, headers={"X-Device-Id": body["device_id"]})
    assert me["device_id"] is None                                                             # someone else's device is not accepted


# ------------------------------------------------------------------------------------ hands

def test_hands_require_a_session(server):
    assert call(server, "POST", "/api/v1/hands", make_hand())[0] == 401
    assert call(server, "GET", "/api/v1/hands")[0] == 401
    assert call(server, "GET", "/api/v1/stats")[0] == 401
    assert call(server, "POST", "/api/v1/hands", make_hand(), token="not-a-token")[0] == 401


def test_a_hand_is_stored_once_and_attributed(server, user):
    token = sign_in(server, user)
    device = call(server, "POST", "/api/v1/devices", {"name": "PC", "platform": "Windows", "architecture": "x64", "app_version": "1.0.0"}, token=token)[1]["device_id"]
    hand = make_hand()
    status, body, _ = call(server, "POST", "/api/v1/hands", hand, token=token, headers={"X-Device-Id": device})
    assert status == 200 and body["status"] == "created" and body["id"] and body["recorded_at"]
    user_id = sql("SELECT id FROM users WHERE email = %s", (user,))[0][0]
    assert sql("SELECT reported_by_user_id, reported_by_device_id::text FROM poker_hands WHERE hand_fingerprint = %s", (hand["hand_fingerprint"],)) == [(user_id, device)]
    again = call(server, "POST", "/api/v1/hands", hand, token=token)
    assert again[0] == 200 and again[1] == {"status": "duplicate", "hand_fingerprint": hand["hand_fingerprint"]}
    assert sql("SELECT count(*) FROM poker_hands WHERE hand_fingerprint = %s", (hand["hand_fingerprint"],)) == [(1,)]
    assert call(server, "GET", "/api/v1/hands/" + hand["hand_fingerprint"], token=token)[0] == 200
    assert call(server, "GET", "/api/v1/hands/2S-3S-4S-5S-6S-7S-8S-9S-10S", token=token)[0] == 404


def test_a_second_user_reporting_the_same_round_is_a_duplicate(server, user):
    first, other = sign_in(server, user), None
    email = "second-%s@example.com" % uuid.uuid4().hex[:8]
    conn = db.get_connection()
    try:
        manage.create_user(conn, email, PASSWORD)
    finally:
        conn.close()
    other = sign_in(server, email)
    hand = make_hand()
    assert call(server, "POST", "/api/v1/hands", hand, token=first)[1]["status"] == "created"
    assert call(server, "POST", "/api/v1/hands", hand, token=other)[1]["status"] == "duplicate"
    first_id = sql("SELECT id FROM users WHERE email = %s", (user,))[0][0]
    assert sql("SELECT reported_by_user_id FROM poker_hands WHERE hand_fingerprint = %s", (hand["hand_fingerprint"],)) == [(first_id,)]


@pytest.mark.parametrize("mutate, code", [
    (lambda h: h.update(player_1="1X"), None),                                   # not a card
    (lambda h: h.update(unknown_field="x"), None),                               # only defined fields are accepted
    (lambda h: h.update(winner="Nobody"), None),
    (lambda h: h.update(hand_fingerprint="NOT A FINGERPRINT"), None),
    (lambda h: h.update(winner="Tie" if h["winner"] != "Tie" else "Dealer"), "inconsistent_hand"),
    (lambda h: h.update(dealer_qualified=not h["dealer_qualified"]), "inconsistent_hand"),
    (lambda h: h.update(hand_fingerprint="2S-3S-4S-5S-6S-7S-8S-9S-10S"), "inconsistent_hand"),
    (lambda h: h.update(player_2=h["player_1"]), "invalid_hand"),                # the same card twice
    (lambda h: h.update(round_seconds=-5), None),
    (lambda h: h.pop("river"), None),
])
def test_invalid_hands_are_rejected_without_storing_anything(server, user, mutate, code):
    token = sign_in(server, user)
    hand = make_hand()
    mutate(hand)
    before = sql("SELECT count(*) FROM poker_hands")[0][0]
    status, body, _ = call(server, "POST", "/api/v1/hands", hand, token=token)
    assert status == 422, body
    if code:
        assert body["code"] == code
    assert sql("SELECT count(*) FROM poker_hands")[0][0] == before


def test_listing_and_statistics(server, user):
    token = sign_in(server, user)
    hands = [make_hand() for _ in range(3)]
    for hand in hands:
        assert call(server, "POST", "/api/v1/hands", hand, token=token)[0] == 200
    status, body, _ = call(server, "GET", "/api/v1/hands?limit=3", token=token)
    assert status == 200 and [h["hand_fingerprint"] for h in body["hands"]] == [h["hand_fingerprint"] for h in hands]
    row = body["hands"][0]
    assert {"id", "recorded_at", "player_card_1", "winner", "hand_fingerprint"} <= set(row)
    assert isinstance(row["recorded_at"], str)
    status, stats, _ = call(server, "GET", "/api/v1/stats", token=token)
    assert status == 200 and stats["rounds"] == stats["player"] + stats["dealer"] + stats["tie"] >= 3


def test_health_needs_no_sign_in_and_reveals_nothing(server):
    status, body, _ = call(server, "GET", "/api/v1/health")
    assert status == 200 and body["ok"] is True and body["database"] is True and body["schema_version"] == "0002_hand_attribution"
    assert "127.0.0.1" not in json.dumps(body) and "pgtest" not in json.dumps(body)


# ------------------------------------------------------------------------------------ legacy and failure behaviour

def test_the_legacy_anonymous_endpoints_still_work_and_can_be_switched_off(server, pg_cluster):
    hand = make_hand()
    assert call(server, "POST", "/api/hands", hand)[1]["status"] == "created"            # unchanged default behaviour
    closed = ApiServer(pg_cluster, {"LEGACY_ANONYMOUS_API": "0"}).start()
    try:
        assert call(closed, "POST", "/api/hands", make_hand())[0] == 404
        assert call(closed, "GET", "/api/health")[0] == 404
        assert call(closed, "GET", "/api/v1/health")[0] == 200                           # the authenticated API is unaffected
    finally:
        closed.stop()


def test_database_errors_never_leak_connection_details(pg_cluster, migrated):
    broken = ApiServer(pg_cluster, {"POSTGRES_PORT": "1"})                                # nothing listens on port 1
    broken.start_error = None
    try:
        broken.start()
    except RuntimeError:
        pass
    # the health endpoint answers without the database, so start() succeeds; fall back to a direct check if it did not
    if broken.process is None or broken.process.poll() is not None:
        pytest.skip("server did not start with an unreachable database")
    try:
        status, body, _ = call(broken, "GET", "/api/v1/health")
        assert status == 200 and body["ok"] is False and body["database"] is False
        status, body, _ = call(broken, "POST", "/api/v1/auth/login", {"email": "a@example.com", "password": "whatever"})
        assert status == 503 and body["code"] == "db_unavailable"
        text = json.dumps(body)
        for secret in ("127.0.0.1", "pgtest", "poker_tracker_test", "unused-trust-auth", ":1"):
            assert secret not in text
    finally:
        broken.stop()


def test_the_api_can_apply_pending_migrations_itself_at_start(pg_cluster):
    """AUTO_MIGRATE=1 (set on the deployed service) migrates a brand-new database before serving; without it, the API says so."""
    import os
    import subprocess
    from pgcluster import EXE
    for name in ("auto_on_db", "auto_off_db"):
        subprocess.run([os.path.join(pg_cluster.bin, "psql" + EXE), "-h", "127.0.0.1", "-p", str(pg_cluster.port), "-U", pg_cluster.user,
                        "-d", "postgres", "-c", "CREATE DATABASE " + name], check=True, capture_output=True)
    on = ApiServer(pg_cluster, {"POSTGRES_DATABASE": "auto_on_db", "AUTO_MIGRATE": "1"}).start()
    off = ApiServer(pg_cluster, {"POSTGRES_DATABASE": "auto_off_db"}).start()
    try:
        status, body, _ = call(on, "GET", "/api/v1/health")
        assert status == 200 and body["ok"] is True and body["schema_version"] == "0002_hand_attribution"
        status, body, _ = call(off, "GET", "/api/v1/health")
        assert status == 200 and body["ok"] is False and body["schema_version"] is None and "not been migrated" in body["message"]
    finally:
        on.stop()
        off.stop()
