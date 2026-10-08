"""The desktop app's view of the central backend: database.db's functions, answered over HTTPS.

database/db.py hands off to this module when the app is in remote mode (see desktop/appconfig.py), so the
tracker, the window and the Excel mirror keep calling db.insert_hand(), db.fetch_all_hands() and so on
unchanged. The return shapes and the error type (db.DatabaseError) are exactly the ones they already handle.

Nothing here knows a database address or a password: only the backend does.

Offline: a hand that cannot be delivered is also written to a small on-disk outbox and re-sent the next time the
backend answers (the server treats the same hand fingerprint as a duplicate, so re-sending is always safe). The
tracker keeps its own in-memory retry as before; the outbox is what survives closing the app while offline.
"""

import datetime
import json
import logging
import os
import threading

from database import db
from desktop import appconfig, paths, version
from desktop.api_client import ApiClient, ApiError, AuthError, OfflineError
from desktop.session import SessionStore

logger = logging.getLogger(__name__)

OUTBOX_FILE = "outbox.jsonl"
OUTBOX_LIMIT = 5000

# The only fields that ever leave this computer inside a hand. Anything else in a record is dropped.
HAND_FIELDS = (
    "player_1", "player_2", "flop_1", "flop_2", "flop_3", "turn", "river", "dealer_1", "dealer_2",
    "player_hand", "dealer_hand", "winner", "dealer_qualified", "hand_fingerprint",
    "round_started_at", "deal_to_flop_seconds", "flop_to_showdown_seconds", "round_seconds",
    "seconds_since_previous_round",
)
_TIME_FIELDS = ("recorded_at", "round_started_at")


def wire_record(record):
    """The hand as it is sent: whitelisted fields only, timestamps as ISO text."""
    wire = {}
    for key in HAND_FIELDS:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, (datetime.datetime, datetime.date)):
            value = value.isoformat(sep=" ")
        wire[key] = value
    return wire


def _parse_time(value):
    if isinstance(value, str) and value:
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _row_from_server(row):
    for key in _TIME_FIELDS:
        if key in row:
            row[key] = _parse_time(row[key])
    return row


class RemoteBackend:
    def __init__(self, store=None, client=None, outbox_path=None):
        self.store = store or SessionStore()
        self._client = client
        self._outbox_path = outbox_path
        self._lock = threading.Lock()

    # ---- plumbing -----------------------------------------------------------------------------

    def client(self):
        if self._client is None:
            try:
                url = appconfig.api_url()
            except appconfig.ConfigError as exc:
                raise db.ConfigurationError(str(exc)) from exc
            if not url:
                raise db.ConfigurationError(
                    "No backend address is configured. Set %s or \"api_url\" in %s."
                    % (appconfig.API_URL_ENV, appconfig.user_config_path()))
            self._client = ApiClient(url, token=self._token, device_id=self.store.device_id)
        return self._client

    def _token(self):
        session = self.store.load()
        if not session:
            return None
        expires = session.get("expires_at")
        if expires:
            try:
                if datetime.datetime.fromisoformat(expires) <= datetime.datetime.now(datetime.timezone.utc):
                    return None
            except ValueError:
                pass
        return session["access_token"]

    def signed_in(self):
        return bool(self._token())

    def outbox_path(self):
        return self._outbox_path or os.path.join(paths.data_dir(), OUTBOX_FILE)

    def _call(self, fn, *args, **kwargs):
        """Run an API call, translating its failures into the database module's error type."""
        try:
            result = fn(*args, **kwargs)
        except AuthError as exc:
            raise db.DatabaseError("Not signed in to the backend: %s" % exc) from exc
        except OfflineError as exc:
            raise db.DatabaseError("%s" % exc) from exc
        except ApiError as exc:
            raise db.DatabaseError("Backend error: %s" % exc) from exc
        return result

    # ---- sign-in (used by the sign-in window) ---------------------------------------------------

    def sign_in(self, email, password):
        """Sign in, register this installation as a device if it is not yet, and store the session."""
        client = self.client()
        reply = client.login(email, password)                       # raises ApiError / AuthError / OfflineError
        session = {"access_token": reply["access_token"], "expires_at": reply.get("expires_at"),
                   "email": (reply.get("user") or {}).get("email", email)}
        self.store.save(session)
        if not self.store.device_id():
            device = client.register_device(_device_name(), version.platform_name(), version.architecture(),
                                            version.VERSION)
            self.store.save_device_id(device["device_id"])
        logger.info("Signed in as %s", session["email"])
        return session

    def sign_out(self):
        try:
            if self.signed_in():
                self.client().logout()
        except ApiError as exc:
            logger.info("Sign-out was not confirmed by the server: %s", exc)
        self.store.clear()

    # ---- database.db's functions -----------------------------------------------------------------

    def check_connection(self):
        try:
            client = self.client()      # first: "no backend address" is the more useful thing to say than "not signed in"
            if not self.signed_in():
                return False, "Not signed in. Sign in to record hands."
            who = self._call(client.me)
            self.flush_outbox()
            return True, "Backend connected (%s)" % who.get("email", "signed in")
        except db.DatabaseError as exc:
            return False, str(exc)

    def verify_schema(self):
        try:
            health = self._call(self.client().health)
        except db.DatabaseError as exc:
            return False, str(exc)
        if not health.get("ok"):
            return False, "The backend reports its database is not ready: %s" % health.get("message", "unknown")
        return True, "Backend schema verified (version %s)" % health.get("schema_version", "?")

    def insert_hand(self, record):
        wire = wire_record(record)
        try:
            reply = self.client().submit_hand(wire)
        except (OfflineError, AuthError) as exc:
            self._queue(wire)           # offline or signed out: keep it until it can be delivered
            raise db.DatabaseError("%s" % exc) from exc
        except ApiError as exc:         # the server understood and refused: queueing would only repeat it
            raise db.DatabaseError("Backend error: %s" % exc) from exc
        self.flush_outbox()
        if reply.get("status") == "duplicate":
            logger.info("Duplicate hand ignored: %s", wire.get("hand_fingerprint"))
            return False, None
        stored = db.record_to_row(record)
        stored["id"] = reply.get("id")
        stored["recorded_at"] = _parse_time(reply.get("recorded_at"))
        logger.info("Hand #%s stored: %s", stored["id"], wire.get("hand_fingerprint"))
        return True, stored

    def fingerprint_exists(self, fingerprint):
        try:
            self.client().request("GET", "/api/v1/hands/" + fingerprint)
            return True
        except ApiError as exc:
            if exc.status == 404:
                return False
            raise db.DatabaseError("Backend error: %s" % exc) from exc

    def fetch_all_hands(self):
        reply = self._call(self.client().list_hands)
        return [_row_from_server(row) for row in reply.get("hands", [])]

    def win_statistics(self):
        counts = self._call(self.client().stats)
        return db.summarise_results({"Player": counts.get("player", 0), "Dealer": counts.get("dealer", 0),
                                     "Tie": counts.get("tie", 0)})

    # ---- offline outbox ------------------------------------------------------------------------------

    def _queue(self, wire):
        fingerprint = wire.get("hand_fingerprint")
        if not fingerprint:
            return
        with self._lock:
            pending = self._read_outbox()
            if any(item.get("hand_fingerprint") == fingerprint for item in pending) or len(pending) >= OUTBOX_LIMIT:
                return
            pending.append(wire)
            self._write_outbox(pending)
        logger.info("Hand %s kept in the outbox until the backend is reachable", fingerprint)

    def pending_count(self):
        with self._lock:
            return len(self._read_outbox())

    def flush_outbox(self):
        """Send what could not be sent earlier. Returns how many were delivered. Never raises."""
        with self._lock:
            pending = self._read_outbox()
            if not pending:
                return 0
            remaining, delivered = [], 0
            for index, wire in enumerate(pending):
                try:
                    reply = self.client().submit_hand(wire)
                except (ApiError, db.DatabaseError) as exc:
                    if isinstance(exc, ApiError) and not exc.retriable and not isinstance(exc, AuthError):
                        logger.error("Dropping an outbox hand the server rejected: %s", exc)
                        continue
                    remaining.extend(pending[index:])
                    break
                delivered += 1
                if reply.get("status") == "created":
                    self._mirror_to_excel(wire, reply)
            self._write_outbox(remaining)
        if delivered:
            logger.info("Delivered %d queued hand(s) from the outbox", delivered)
        return delivered

    @staticmethod
    def _mirror_to_excel(wire, reply):
        try:
            from export import excel_export
            row = db.record_to_row(wire)
            row["id"], row["recorded_at"] = reply.get("id"), _parse_time(reply.get("recorded_at"))
            excel_export.append_hand(row)
        except Exception as exc:  # noqa: BLE001 - the spreadsheet is a convenience, never a reason to fail
            logger.warning("Could not mirror a queued hand to Excel: %s", exc)

    def _read_outbox(self):
        items = []
        try:
            with open(self.outbox_path(), "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        try:
                            items.append(json.loads(line))
                        except ValueError:
                            logger.warning("Skipping an unreadable outbox line")
        except FileNotFoundError:
            pass
        return items

    def _write_outbox(self, items):
        path = self.outbox_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not items:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, separators=(",", ":")) + "\n")
        os.replace(temp, path)


def _device_name():
    import platform
    return (platform.node() or "this computer")[:80]


_instance = None
_instance_lock = threading.Lock()


def get():
    """The process-wide backend (created on first use)."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = RemoteBackend()
        return _instance


def reset_for_tests():
    global _instance
    _instance = None
