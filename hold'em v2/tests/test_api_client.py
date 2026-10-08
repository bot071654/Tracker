"""The desktop's HTTPS client (desktop/api_client.py) against a stub server: no database, no real backend."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from desktop import appconfig
from desktop.api_client import ApiClient, ApiError, AuthError, OfflineError
from desktop.logsetup import RedactingFilter, redact


class Stub:
    """A tiny HTTP server that plays back a scripted list of (status, body, headers) replies and records requests."""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                stub.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                  "body": self.rfile.read(length).decode() if length else ""})
                status, body, headers = stub.replies.pop(0) if stub.replies else (200, {}, {})
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_DELETE = _serve

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def stub():
    created = []

    def make(*replies):
        s = Stub(replies)
        created.append(s)
        return s
    yield make
    for s in created:
        s.close()


def client(stub_server, **kwargs):
    kwargs.setdefault("sleep", lambda _seconds: None)
    return ApiClient(stub_server.url, **kwargs)


def test_https_is_required_except_for_this_machine():
    assert appconfig.normalize_base_url("https://api.example.com/") == "https://api.example.com"
    assert appconfig.normalize_base_url("http://localhost:8000") == "http://localhost:8000"
    assert appconfig.normalize_base_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert appconfig.normalize_base_url("") == ""
    for bad in ("http://api.example.com", "ftp://x.example.com", "https://user:pw@api.example.com", "not a url", "//x"):
        with pytest.raises(appconfig.ConfigError):
            appconfig.normalize_base_url(bad)
    with pytest.raises(appconfig.ConfigError):
        ApiClient("http://api.example.com")


def test_get_retries_transient_failures_then_succeeds(stub):
    server = stub((503, {"detail": "busy"}, None), (502, {}, None), (200, {"ok": True}, None))
    assert client(server, token="t").request("GET", "/api/v1/me") == {"ok": True}
    assert len(server.seen) == 3


def test_retry_gives_up_after_the_configured_attempts(stub):
    server = stub(*[(503, {"detail": "busy"}, None)] * 5)
    with pytest.raises(ApiError) as caught:
        client(server, token="t", retries=2).request("GET", "/api/v1/me")
    assert caught.value.status == 503 and caught.value.retriable
    assert len(server.seen) == 3


def test_retry_after_is_honoured_but_capped(stub):
    waits = []
    server = stub((429, {"detail": "slow down"}, {"Retry-After": "2"}), (200, {"ok": True}, None))
    assert client(server, token="t", sleep=waits.append).request("GET", "/api/v1/me") == {"ok": True}
    assert waits == [2.0]
    waits.clear()
    server = stub((429, {}, {"Retry-After": "600"}), (200, {}, None))
    client(server, token="t", sleep=waits.append).request("GET", "/api/v1/me")
    assert waits == [5.0]


def test_a_post_is_not_retried_unless_it_is_declared_idempotent(stub):
    server = stub((503, {"detail": "busy"}, None), (200, {"ok": True}, None))
    with pytest.raises(ApiError):
        client(server, token="t").request("POST", "/api/v1/devices", {"x": 1})
    assert len(server.seen) == 1
    server = stub((503, {}, None), (200, {"status": "created"}, None))
    assert client(server, token="t").submit_hand({"a": 1}) == {"status": "created"}      # submit_hand IS idempotent
    assert len(server.seen) == 2


def test_client_errors_are_not_retried_and_keep_the_servers_safe_message(stub):
    server = stub((422, {"detail": "The hand does not match its own cards.", "code": "inconsistent_hand"}, None))
    with pytest.raises(ApiError) as caught:
        client(server, token="t").submit_hand({"a": 1})
    assert caught.value.status == 422 and caught.value.code == "inconsistent_hand" and not caught.value.retriable
    assert len(server.seen) == 1


def test_401_is_an_auth_error_and_never_retried(stub):
    server = stub((401, {"detail": "Your session has ended.", "code": "session_expired"}, None))
    with pytest.raises(AuthError) as caught:
        client(server, token="t").request("GET", "/api/v1/me")
    assert caught.value.code == "session_expired" and len(server.seen) == 1


def test_no_token_means_no_request_is_made(stub):
    server = stub()
    with pytest.raises(AuthError) as caught:
        client(server, token=None).request("GET", "/api/v1/me")
    assert caught.value.code == "not_signed_in" and server.seen == []


def test_sign_in_sends_no_authorization_header_and_other_calls_send_the_bearer_token(stub):
    server = stub((200, {"access_token": "x"}, None), (200, {}, None))
    c = client(server, token=lambda: "secret-token", device_id=lambda: "dev-1")
    c.login("a@example.com", "pw")
    c.me()
    login, me = server.seen
    assert "Authorization" not in login["headers"]
    assert me["headers"]["Authorization"] == "Bearer secret-token" and me["headers"]["X-Device-Id"] == "dev-1"
    assert me["headers"]["User-Agent"].startswith("PokerHandTracker/")


def test_unreachable_server_is_an_offline_error():
    c = ApiClient("http://127.0.0.1:1", token="t", timeout=2, retries=0)
    with pytest.raises(OfflineError) as caught:
        c.request("GET", "/api/v1/me")
    assert caught.value.retriable


def test_a_slow_server_times_out(stub):
    import time

    class Slow(Stub):
        pass

    server = stub()
    original = server.httpd.RequestHandlerClass._serve
    server.httpd.RequestHandlerClass._serve = lambda self: (time.sleep(1.5), original(self))[1]
    server.httpd.RequestHandlerClass.do_GET = server.httpd.RequestHandlerClass._serve
    with pytest.raises(OfflineError):
        client(server, token="t", timeout=0.4, retries=0).request("GET", "/api/v1/me")


def test_oversized_requests_and_garbage_responses_are_refused(stub):
    server = stub()
    with pytest.raises(ApiError) as caught:
        client(server, token="t").request("POST", "/api/v1/hands", {"blob": "x" * 70000}, idempotent=True)
    assert caught.value.code == "too_large" and server.seen == []


def test_only_the_defined_fields_of_a_hand_are_ever_sent():
    from desktop.backend import HAND_FIELDS, wire_record
    record = {key: "x" for key in HAND_FIELDS}
    record.update({"screenshot": b"\x89PNG...", "password": "hunter2", "path": "C:/Users/me/secret.txt", "recorded_at": "now"})
    assert set(wire_record(record)) == set(HAND_FIELDS)


# ---------------------------------------------------------------------------------- log redaction

@pytest.mark.parametrize("text, secret", [
    ("Authorization: Bearer abcdefghijklmnop12345", "abcdefghijklmnop12345"),
    ("sending header bearer ZZZZZZZZZZZZZZZZ", "ZZZZZZZZZZZZZZZZ"),
    ("login failed password=hunter2 for user", "hunter2"),
    ('payload {"password": "hunter2", "email": "a@b.c"}', "hunter2"),
    ("access_token=0123456789abcdef", "0123456789abcdef"),
    ("POSTGRES_PASSWORD=supersecretvalue", "supersecretvalue"),
    ("postgresql://poker:swordfish@db.internal:5432/poker", "swordfish"),
])
def test_secrets_are_redacted(text, secret):
    assert secret not in redact(text)


def test_redaction_leaves_ordinary_messages_alone():
    for text in ("Hand #12 stored: 8S-9C-2D-QH-3D-3H-6S-8D-QD", "Cannot reach the backend (timed out).", "Signed in as a@example.com"):
        assert redact(text) == text


def test_the_logging_filter_scrubs_what_handlers_receive(caplog):
    logger = logging.getLogger("redaction-test")
    handler = logging.Handler()
    seen = []
    handler.emit = lambda record: seen.append(record.getMessage())
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("login with password=%s", "hunter2")
        logger.info("token is %s", "Bearer abcdefghijklmnop")
    finally:
        logger.removeHandler(handler)
    assert seen and all("hunter2" not in m and "abcdefghijklmnop" not in m for m in seen)
