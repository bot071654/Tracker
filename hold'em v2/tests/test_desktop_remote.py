"""End to end: the tracker's own database calls, answered over HTTPS by the real backend and PostgreSQL.

What the tracker, the window and the Excel mirror call (database.db) is unchanged; in remote mode it is answered by
desktop/backend.py. This proves the whole chain, offline behaviour, session secrecy, and that the packaged build never
reaches for PostgreSQL itself.
"""

import datetime
import logging
import os
import sys
import uuid

import pytest

from backend import manage, migrate
from database import db
from desktop import appconfig, backend as remote_backend, paths
from desktop.api_client import ApiClient
from desktop.session import SessionStore
from pgcluster import ApiServer, pg_cluster, pg_cluster_process  # noqa: F401 - the fixtures are used below
from poker.hand_record import CARD_ORDER, build_hand_record
from test_backend_api import DECK, PASSWORD, make_hand, sql

import random


def tracker_record(seed):
    """A record exactly as tracker.py builds it: the slot cards, the evaluated hands, and the timer's datetimes."""
    cards = random.Random(seed).sample(DECK, 9)
    record = build_hand_record(dict(zip(CARD_ORDER, cards)))
    record["round_started_at"] = datetime.datetime(2026, 10, 8, 12, 0, 0)
    record.update({"deal_to_flop_seconds": 12.5, "flop_to_showdown_seconds": 30.25, "round_seconds": 42.75,
                   "seconds_since_previous_round": None})
    return record


@pytest.fixture(scope="module")
def api(pg_cluster):
    migrate.migrate()
    server = ApiServer(pg_cluster).start()
    yield server
    server.stop()


REAL_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _snapshot(folder):
    return {name: (os.path.getmtime(os.path.join(folder, name)), os.path.getsize(os.path.join(folder, name))) for name in os.listdir(folder)} if os.path.isdir(folder) else {}


@pytest.fixture(autouse=True)
def never_touch_the_real_spreadsheet_or_outbox(tmp_path, monkeypatch):
    """Delivering a queued hand mirrors it to the Excel file. By default that is the project's REAL data/poker_hands.xlsx, so every
    test here is pointed at a temporary workbook, and the real data folder is checked to be exactly as it was afterwards."""
    from export import excel_export
    original = excel_export.append_hand
    monkeypatch.setattr(excel_export, "append_hand", lambda row, path=None: original(row, str(tmp_path / "mirror.xlsx")))
    before = _snapshot(REAL_DATA)
    yield
    assert _snapshot(REAL_DATA) == before, "a test modified the real data folder (spreadsheet or outbox)"


@pytest.fixture
def desktop(api, pg_cluster, tmp_path, monkeypatch):
    """A fresh installation: its own home folder, pointed at the API, with a registered account."""
    email = "desk-%s@example.com" % uuid.uuid4().hex[:8]
    conn = db.get_connection()
    try:
        manage.create_user(conn, email, PASSWORD)
    finally:
        conn.close()
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "home"))
    monkeypatch.setenv(appconfig.API_URL_ENV, api.url)
    remote_backend.reset_for_tests()
    yield type("Desktop", (), {"email": email, "api": api, "home": tmp_path / "home", "backend": remote_backend.get()})
    remote_backend.reset_for_tests()


def test_remote_mode_is_on_when_a_backend_is_configured(desktop):
    assert appconfig.remote_mode()
    ok, message = db.verify_schema()
    assert ok and "0002_hand_attribution" in message


def test_not_signed_in_nothing_is_lost_it_waits_in_the_outbox(desktop):
    assert db.check_connection() == (False, "Not signed in. Sign in to record hands.")
    record = tracker_record(11)
    with pytest.raises(db.DatabaseError):
        db.insert_hand(record)                                                   # the tracker's existing retry handles this
    assert desktop.backend.pending_count() == 1
    assert sql("SELECT count(*) FROM poker_hands WHERE hand_fingerprint = %s", (record["hand_fingerprint"],)) == [(0,)]
    desktop.backend.sign_in(desktop.email, PASSWORD)
    assert db.check_connection()[0]                                              # connecting delivers what was waiting
    assert desktop.backend.pending_count() == 0
    assert sql("SELECT count(*) FROM poker_hands WHERE hand_fingerprint = %s", (record["hand_fingerprint"],)) == [(1,)]


def test_signing_in_registers_a_device_once(desktop):
    desktop.backend.sign_in(desktop.email, PASSWORD)
    first = desktop.backend.store.device_id()
    assert first and uuid.UUID(first)
    desktop.backend.sign_in(desktop.email, PASSWORD)
    assert desktop.backend.store.device_id() == first
    user_id = sql("SELECT id FROM users WHERE email = %s", (desktop.email,))[0][0]
    assert sql("SELECT count(*) FROM devices WHERE user_id = %s", (user_id,)) == [(1,)]


def test_the_tracker_path_stores_a_hand_and_reports_a_duplicate(desktop):
    desktop.backend.sign_in(desktop.email, PASSWORD)
    record = tracker_record(21)
    inserted, row = db.insert_hand(record)                                       # exactly what tracker.py calls
    assert inserted and row["id"] and row["player_card_1"] == record["player_1"] and row["hand_fingerprint"] == record["hand_fingerprint"]
    assert isinstance(row["recorded_at"], datetime.datetime)
    user_id, device = sql("SELECT reported_by_user_id, reported_by_device_id::text FROM poker_hands WHERE id = %s", (row["id"],))[0]
    assert user_id and device == desktop.backend.store.device_id()
    stored = sql("SELECT round_started_at, deal_to_flop_seconds, round_seconds, seconds_since_previous_round FROM poker_hands WHERE id = %s", (row["id"],))[0]
    assert stored[0] == datetime.datetime(2026, 10, 8, 12, 0, 0) and stored[1] == 12.5 and stored[2] == 42.75 and stored[3] is None
    assert db.insert_hand(record) == (False, None)                               # same shape the Excel/UI code already handles
    assert db.fingerprint_exists(record["hand_fingerprint"]) is True
    assert db.fingerprint_exists("2S-3S-4S-5S-6S-7S-8S-9S-10S") is False


def test_history_and_statistics_have_the_shapes_the_app_already_uses(desktop):
    desktop.backend.sign_in(desktop.email, PASSWORD)
    record = tracker_record(31)
    db.insert_hand(record)
    rows = db.fetch_all_hands()
    mine = [r for r in rows if r["hand_fingerprint"] == record["hand_fingerprint"]][0]
    assert isinstance(mine["recorded_at"], datetime.datetime) and isinstance(mine["round_started_at"], datetime.datetime)
    assert set(mine) == {"id", "recorded_at"} | set(db.COLUMNS) | {"hand_fingerprint"}
    stats = db.win_statistics()
    assert set(stats) == {"rounds", "player", "dealer", "tie", "player_percent", "dealer_percent", "tie_percent"}
    assert stats["rounds"] == stats["player"] + stats["dealer"] + stats["tie"] > 0


def test_offline_hands_survive_closing_the_app_and_are_delivered_later(desktop):
    desktop.backend.sign_in(desktop.email, PASSWORD)
    record = tracker_record(41)
    desktop.api.stop()
    try:
        with pytest.raises(db.DatabaseError) as caught:
            db.insert_hand(record)
        assert "Cannot reach the backend" in str(caught.value)
        assert desktop.backend.pending_count() == 1
        assert desktop.backend.flush_outbox() == 0                                # still offline: nothing lost, nothing dropped
        assert desktop.backend.pending_count() == 1
        remote_backend.reset_for_tests()                                          # "the app was closed and reopened"
        assert remote_backend.get().pending_count() == 1
    finally:
        desktop.api.start()
    assert remote_backend.get().flush_outbox() == 1
    assert remote_backend.get().pending_count() == 0
    assert sql("SELECT count(*) FROM poker_hands WHERE hand_fingerprint = %s", (record["hand_fingerprint"],)) == [(1,)]
    assert remote_backend.get().flush_outbox() == 0


def test_an_ended_session_is_reported_as_signed_out_and_keeps_the_hand(desktop):
    desktop.backend.sign_in(desktop.email, PASSWORD)
    sql("UPDATE sessions SET revoked_at = now() WHERE user_id = (SELECT id FROM users WHERE email = %s)", (desktop.email,))
    record = tracker_record(51)
    with pytest.raises(db.DatabaseError) as caught:
        db.insert_hand(record)
    assert "Sign in again" in str(caught.value)
    assert desktop.backend.pending_count() == 1


def test_sign_out_ends_the_server_session_and_clears_the_stored_one(desktop):
    session = desktop.backend.sign_in(desktop.email, PASSWORD)
    token = session["access_token"]
    desktop.backend.sign_out()
    assert desktop.backend.store.load() is None and not desktop.backend.signed_in()
    client = ApiClient(desktop.api.url, token=token)
    with pytest.raises(Exception) as caught:
        client.me()
    assert getattr(caught.value, "status", None) == 401                          # revoked on the server, not just forgotten


def test_the_stored_session_is_not_readable_as_plain_text(desktop):
    session = desktop.backend.sign_in(desktop.email, PASSWORD)
    stored = os.path.join(paths.state_dir(), "session.bin")
    raw = open(stored, "rb").read()
    assert session["access_token"].encode() not in raw
    assert PASSWORD.encode() not in raw
    if sys.platform == "win32":
        assert desktop.email.encode() not in raw                                  # DPAPI-encrypted, not merely hidden
    assert SessionStore().load()["access_token"] == session["access_token"]       # and it round-trips for this user


def test_neither_the_password_nor_the_token_reaches_the_log(desktop, caplog):
    from desktop.logsetup import install_redaction
    root = logging.getLogger()
    handler = logging.StreamHandler()
    messages = []
    handler.emit = lambda record: messages.append(handler.format(record))
    root.addHandler(handler)
    install_redaction(root)
    caplog.set_level(logging.DEBUG)
    try:
        session = desktop.backend.sign_in(desktop.email, PASSWORD)
        db.insert_hand(tracker_record(61))
        db.check_connection()
    finally:
        root.removeHandler(handler)
    blob = "\n".join(messages) + "\n" + caplog.text
    assert session["access_token"] not in blob and PASSWORD not in blob


def test_the_wrong_password_is_a_clear_error_and_stores_nothing(desktop):
    from desktop.api_client import AuthError
    with pytest.raises(AuthError):
        desktop.backend.sign_in(desktop.email, "not the password")
    assert desktop.backend.store.load() is None and not desktop.backend.signed_in()


def test_the_packaged_build_never_reaches_for_postgresql(monkeypatch, tmp_path):
    """Frozen with no backend configured: remote mode is forced, there is nothing to connect to, and no database
    connection is ever attempted - the build holds no credentials to attempt one with."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    monkeypatch.delenv(appconfig.API_URL_ENV, raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "must-never-be-used")
    monkeypatch.setattr(db, "get_connection", lambda: (_ for _ in ()).throw(AssertionError("direct PostgreSQL attempted")))
    remote_backend.reset_for_tests()
    try:
        assert appconfig.remote_mode()
        ok, message = db.check_connection()
        assert not ok and "backend address" in message
        with pytest.raises(db.DatabaseError):
            db.insert_hand(tracker_record(71))
        assert db.verify_schema()[0] is False
    finally:
        remote_backend.reset_for_tests()
