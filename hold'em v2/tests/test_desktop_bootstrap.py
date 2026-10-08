"""Start-up behaviour of the desktop build: the sign-in controller, the diagnostics text and the app.main hooks."""

import logging

import pytest

from desktop import appconfig, bootstrap, paths, version
from desktop.api_client import ApiError, AuthError, OfflineError


class FakeBackend:
    def __init__(self, error=None, signed_in=False, session=None):
        self.error, self._signed_in, self._session, self._client = error, signed_in, session, object()
        self.calls = []

        class Store:
            def load(inner):
                return self._session
        self.store = Store()

    def sign_in(self, email, password):
        self.calls.append((email, password))
        if self.error:
            raise self.error
        self._signed_in = True
        return {"email": email}

    def signed_in(self):
        return self._signed_in

    def pending_count(self):
        return 2


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "home"))
    monkeypatch.delenv(appconfig.API_URL_ENV, raising=False)


@pytest.mark.parametrize("error, fragment", [
    (AuthError("Incorrect e-mail or password."), "Incorrect e-mail or password."),
    (OfflineError("Cannot reach the backend (timed out)."), "Cannot reach the server"),
    (ApiError("The server returned HTTP 500."), "Sign-in failed"),
    (RuntimeError("boom"), "boom"),
])
def test_sign_in_failures_become_readable_messages_and_never_raise(error, fragment):
    controller = bootstrap.SignInController(FakeBackend(error=error))
    ok, message = controller.sign_in("a@example.com", "pw-123456789")
    assert not ok and fragment in message


def test_sign_in_needs_both_fields_and_succeeds_cleanly():
    backend = FakeBackend()
    controller = bootstrap.SignInController(backend)
    assert controller.sign_in("", "x")[0] is False and controller.sign_in("a@example.com", "")[0] is False
    assert backend.calls == []                                                   # nothing was sent for empty fields
    ok, message = controller.sign_in("  a@example.com ", "pw-123456789")
    assert ok and message == "Signed in as a@example.com." and backend.calls == [("a@example.com", "pw-123456789")]


def test_the_backend_address_is_validated_and_saved_for_this_user(tmp_path):
    controller = bootstrap.SignInController(FakeBackend())
    assert controller.configured() is False
    assert controller.set_backend_address("http://api.example.com")[0] is False        # plain http refused
    assert controller.set_backend_address("")[0] is False
    ok, _ = controller.set_backend_address("https://api.example.com/")
    assert ok and controller.configured() and appconfig.api_url() == "https://api.example.com"


def test_needs_sign_in_only_when_remote_configured_and_signed_out(monkeypatch):
    monkeypatch.setenv(appconfig.API_URL_ENV, "https://api.example.com")
    assert bootstrap.SignInController(FakeBackend(signed_in=False)).needs_sign_in() is True
    assert bootstrap.SignInController(FakeBackend(signed_in=True)).needs_sign_in() is False


def test_diagnostics_has_what_support_needs_and_no_secrets(monkeypatch):
    monkeypatch.setenv(appconfig.API_URL_ENV, "https://api.example.com")
    monkeypatch.setenv("POSTGRES_PASSWORD", "do-not-print-me")
    backend = FakeBackend(session={"access_token": "tok-should-not-appear", "email": "a@example.com"})
    text = bootstrap.diagnostics_text(backend)
    for expected in (version.VERSION, version.platform_name(), version.architecture(), "https://api.example.com",
                     "Signed in as a@example.com", "2 hand(s) waiting", "logs", "config"):
        assert expected in text
    assert "tok-should-not-appear" not in text and "do-not-print-me" not in text


def test_startup_from_source_writes_the_banner_and_asks_nothing(caplog):
    caplog.set_level(logging.INFO)
    root = object()                                                              # any widget use would fail on this
    bootstrap.startup(root)
    text = caplog.text
    assert "Application startup: Poker Hand Tracker %s" % version.VERSION in text
    assert "direct database (development)" in text


def test_startup_survives_a_failing_sign_in_step(monkeypatch, caplog):
    monkeypatch.setenv(appconfig.API_URL_ENV, "https://api.example.com")
    monkeypatch.setattr(bootstrap, "_sign_in_dialog", lambda *_: (_ for _ in ()).throw(RuntimeError("tk is broken")))
    bootstrap.startup(object())                                                  # must not raise: the tracker still opens
    assert "Sign-in step failed" in caplog.text


def test_finish_shows_the_version_line_and_binds_f1():
    class App:
        message = None

        def set_message(self, text):
            self.message = text

    class Root:
        bound = {}

        def bind(self, key, fn):
            self.bound[key] = fn

    app, root = App(), Root()
    bootstrap.finish(app, root)
    assert version.describe() in app.message and "<F1>" in root.bound


def test_the_about_window_builds_and_shows_the_details():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    try:
        root.withdraw()
        bootstrap.show_about(root, None)
        windows = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert windows and "About" in windows[0].title()
        texts = [c for c in windows[0].winfo_children()[0].winfo_children() if isinstance(c, tk.Text)]
        assert version.VERSION in texts[0].get("1.0", "end")
    finally:
        root.destroy()
