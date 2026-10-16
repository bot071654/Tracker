"""The saved session at start-up: when the sign-in dialog must appear, and when the app may carry on.

THE FAILURE THIS IS ABOUT

The window opened with no sign-in dialog although the backend refused the stored token ("Your session has ended"). Start-up asked
only RemoteBackend.signed_in(), which compares the saved expiry with the clock; a 30-day session saved against another server (or
a rebuilt database) is unexpired here and unknown there, so nothing ever prompted and every call failed with 401.

Start-up now asks the server once (RemoteBackend.verify_session). No network and no display is used here: the API client is
replaced by a stub and the session file by a dictionary.
"""

import datetime
import logging

import pytest

from desktop import appconfig, backend as remote_backend, bootstrap, paths
from desktop.api_client import ApiError, AuthError, OfflineError
from desktop.backend import NO_SESSION, REJECTED, UNVERIFIED, VERIFIED, RemoteBackend
from desktop.session import SessionStore

URL = "http://127.0.0.1:8000"
NOW = datetime.datetime.now(datetime.timezone.utc)


class MemoryFile:
    """Stands in for the DPAPI file: same read / write / delete."""

    def __init__(self):
        self.data = None

    def read(self):
        return self.data

    def write(self, data):
        self.data = data

    def delete(self):
        self.data = None


class StubClient:
    """Replaces ApiClient. `me_error` is raised by me(); `register_error` by register_device()."""
    me_error = None
    register_error = None
    login_error = None
    calls = []

    def __init__(self, base_url, token=None, device_id=None, **_kwargs):
        self.base_url, self._token = base_url, token

    def me(self):
        StubClient.calls.append("me")
        if StubClient.me_error:
            raise StubClient.me_error
        return {"email": "test@example.com"}

    def login(self, email, password):
        StubClient.calls.append("login")
        if StubClient.login_error:
            raise StubClient.login_error
        return {"access_token": "new-token", "expires_at": (NOW + datetime.timedelta(days=30)).isoformat(),
                "user": {"email": email}}

    def register_device(self, *_args):
        StubClient.calls.append("register")
        if StubClient.register_error:
            raise StubClient.register_error
        return {"device_id": "dev-1"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "home"))
    monkeypatch.setenv(appconfig.API_URL_ENV, URL)
    monkeypatch.setattr(remote_backend, "ApiClient", StubClient)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)           # a packaged build is always in remote mode
    StubClient.me_error = StubClient.register_error = StubClient.login_error = None
    StubClient.calls = []
    remote_backend.reset_for_tests()
    yield
    remote_backend.reset_for_tests()


def make_backend(tmp_path, token=None, expires=None):
    store = SessionStore(backend=MemoryFile(), state_dir=str(tmp_path / "state"))
    if token:
        session = {"access_token": token, "email": "test@example.com"}
        if expires is not None:
            session["expires_at"] = expires
        store.save(session)
    return RemoteBackend(store=store, outbox_path=str(tmp_path / "outbox.jsonl"))


def future(**kwargs):
    return (NOW + datetime.timedelta(days=5)).isoformat(**kwargs)


# -- the session as stored -----------------------------------------------------------------------------

def test_no_saved_session_means_sign_in_is_required(tmp_path):
    backend = make_backend(tmp_path)
    assert backend.verify_session() == NO_SESSION
    assert bootstrap.SignInController(backend).needs_sign_in() is True
    assert StubClient.calls == []                               # nothing to check, so the server is not asked


def test_an_expired_session_means_sign_in_is_required(tmp_path):
    backend = make_backend(tmp_path, "tok", (NOW - datetime.timedelta(minutes=1)).isoformat())
    assert backend.signed_in() is False
    assert backend.verify_session() == NO_SESSION
    assert bootstrap.SignInController(backend).needs_sign_in() is True


@pytest.mark.parametrize("aware", [True, False])
def test_expiry_values_with_and_without_a_timezone_are_both_understood(tmp_path, aware):
    """The server sends an offset; an older or hand-made file may not. Comparing the two kinds used to raise TypeError, which the
    start-up step swallowed - skipping the dialog."""
    moment = NOW if aware else NOW.replace(tzinfo=None)
    backend = make_backend(tmp_path, "tok", (moment + datetime.timedelta(days=1)).isoformat())
    assert backend.signed_in() is True
    expired = make_backend(tmp_path, "tok", (moment - datetime.timedelta(days=1)).isoformat())
    assert expired.signed_in() is False


def test_an_unparseable_expiry_is_left_for_the_server_to_judge(tmp_path):
    backend = make_backend(tmp_path, "tok", "not a date")
    assert backend.signed_in() is True
    assert backend.verify_session() == VERIFIED


# -- asking the server -----------------------------------------------------------------------------------

def test_an_unexpired_session_is_checked_against_the_api(tmp_path):
    backend = make_backend(tmp_path, "tok", future())
    assert backend.verify_session() == VERIFIED
    assert StubClient.calls == ["me"]


def test_a_session_the_backend_rejects_counts_as_signed_out_but_the_file_is_kept(tmp_path):
    backend = make_backend(tmp_path, "tok", future())
    StubClient.me_error = AuthError("Your session has ended. Sign in again.")
    assert backend.verify_session() == REJECTED
    assert backend.signed_in() is False                         # never silently treated as authenticated
    assert bootstrap.SignInController(backend).needs_sign_in() is True
    assert backend.store.load()["access_token"] == "tok"        # nothing is deleted behind the user's back


def test_after_a_rejection_the_user_can_sign_in_again(tmp_path):
    backend = make_backend(tmp_path, "old-token", future())
    StubClient.me_error = AuthError("ended")
    assert backend.verify_session() == REJECTED
    ok, message = bootstrap.SignInController(backend).sign_in("test@example.com", "pw-123456789")
    assert ok is True and "test@example.com" in message
    assert backend.signed_in() is True
    assert backend.store.load()["access_token"] == "new-token"


def test_a_rejection_found_later_by_a_normal_call_also_signs_the_session_out(tmp_path):
    backend = make_backend(tmp_path, "tok", future())
    backend._client = StubClient(URL)
    StubClient.me_error = AuthError("ended")
    ok, message = backend.check_connection()
    assert ok is False
    assert backend.signed_in() is False


def test_an_unreachable_backend_is_unverified_and_keeps_the_session(tmp_path):
    backend = make_backend(tmp_path, "tok", future())
    StubClient.me_error = OfflineError("Cannot reach the backend (timed out).")
    assert backend.verify_session() == UNVERIFIED
    assert backend.signed_in() is True                          # offline is not the same as rejected


def test_any_other_api_failure_is_unverified_too(tmp_path):
    backend = make_backend(tmp_path, "tok", future())
    StubClient.me_error = ApiError("The server returned HTTP 500.", status=500)
    assert backend.verify_session() == UNVERIFIED


# -- signing in -------------------------------------------------------------------------------------------

def test_a_successful_sign_in_stores_the_session_and_registers_the_device(tmp_path):
    backend = make_backend(tmp_path)
    ok, message = bootstrap.SignInController(backend).sign_in("test@example.com", "pw-123456789")
    assert ok is True
    stored = backend.store.load()
    assert stored["access_token"] == "new-token" and stored["email"] == "test@example.com"
    assert backend.store.device_id() == "dev-1"
    assert "pw-123456789" not in str(stored) and "pw-123456789" not in message


def test_a_failed_login_gives_a_message_and_stores_nothing(tmp_path):
    backend = make_backend(tmp_path)
    StubClient.login_error = AuthError("Incorrect e-mail or password.")
    ok, message = bootstrap.SignInController(backend).sign_in("test@example.com", "wrong-password")
    assert ok is False and "Incorrect" in message
    assert backend.store.load() is None and backend.signed_in() is False


def test_a_device_registration_failure_leaves_no_half_signed_in_session(tmp_path):
    backend = make_backend(tmp_path)
    StubClient.register_error = OfflineError("Cannot reach the backend (timed out).")
    ok, message = bootstrap.SignInController(backend).sign_in("test@example.com", "pw-123456789")
    assert ok is False and "Cannot reach" in message
    assert backend.store.load() is None and backend.signed_in() is False
    StubClient.register_error = None                            # and it is recoverable by simply trying again
    ok, _ = bootstrap.SignInController(backend).sign_in("test@example.com", "pw-123456789")
    assert ok is True and backend.signed_in() is True


# -- start-up ----------------------------------------------------------------------------------------------

@pytest.fixture
def dialog(monkeypatch):
    shown = []
    monkeypatch.setattr(bootstrap, "_sign_in_dialog", lambda root, controller, notice=None: shown.append(notice) or False)
    return shown


def start(monkeypatch, backend):
    monkeypatch.setattr(remote_backend, "_instance", backend)
    bootstrap.startup(object())


def test_startup_asks_for_sign_in_when_there_is_no_session(monkeypatch, tmp_path, dialog):
    start(monkeypatch, make_backend(tmp_path))
    assert dialog == [None]


def test_startup_asks_for_sign_in_again_when_the_stored_session_is_rejected(monkeypatch, tmp_path, dialog):
    """The reported case: unexpired locally, refused by the server."""
    StubClient.me_error = AuthError("ended")
    start(monkeypatch, make_backend(tmp_path, "tok", future()))
    assert dialog == [bootstrap.SESSION_ENDED_NOTICE]


def test_startup_does_not_prompt_when_the_session_is_good(monkeypatch, tmp_path, dialog):
    start(monkeypatch, make_backend(tmp_path, "tok", future()))
    assert dialog == []


def test_startup_does_not_prompt_when_the_backend_is_down_and_says_so(monkeypatch, tmp_path, dialog):
    StubClient.me_error = OfflineError("Cannot reach the backend (timed out).")
    start(monkeypatch, make_backend(tmp_path, "tok", future()))
    assert dialog == []
    assert "not reachable" in bootstrap._session_note


def test_choosing_to_work_offline_is_respected(monkeypatch, tmp_path, dialog):
    backend = make_backend(tmp_path)
    start(monkeypatch, backend)                                 # the stub dialog returns False: "Work offline"
    assert dialog == [None] and backend.signed_in() is False    # no session is invented and nothing is retried
    assert StubClient.calls == []


def test_a_dialog_that_fails_never_leaves_the_user_authenticated(monkeypatch, tmp_path, caplog):
    StubClient.me_error = AuthError("ended")
    monkeypatch.setattr(bootstrap, "_sign_in_dialog", lambda *_: (_ for _ in ()).throw(RuntimeError("tk is broken")))
    backend = make_backend(tmp_path, "tok", future())
    start(monkeypatch, backend)                                 # must not raise
    assert "Sign-in step failed" in caplog.text
    assert backend.signed_in() is False                         # the rejected token is not usable


def test_startup_never_logs_the_token(monkeypatch, tmp_path, dialog, caplog):
    caplog.set_level(logging.DEBUG)
    StubClient.me_error = AuthError("ended")
    start(monkeypatch, make_backend(tmp_path, "super-secret-token", future()))
    assert "super-secret-token" not in caplog.text


# -- the dialog itself ---------------------------------------------------------------------------------------

def test_the_dialog_shows_why_it_has_appeared(monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:                                  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    root.withdraw()
    seen = []

    def inspect_then_close():
        for window in root.winfo_children():
            if isinstance(window, tk.Toplevel):
                texts = []

                def walk(widget):
                    try:
                        texts.append(str(widget.cget("text")))
                    except tk.TclError:
                        pass
                    for child in widget.winfo_children():
                        walk(child)
                walk(window)
                seen.extend(texts)
                window.destroy()

    try:
        root.after(150, inspect_then_close)
        controller = bootstrap.SignInController(make_backend(tmp_path))
        assert bootstrap._sign_in_dialog(root, controller, bootstrap.SESSION_ENDED_NOTICE) is False
    finally:
        root.destroy()
    assert bootstrap.SESSION_ENDED_NOTICE in seen
    assert "Work offline" in seen
