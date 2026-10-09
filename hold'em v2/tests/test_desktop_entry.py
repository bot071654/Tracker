"""The support switches of the packaged program (--backend-check, --diagnostics ...) must fail with a report and an exit code,
never with an unhandled exception: in a windowed exe that is a modal dialog.

Regression for: POKER_TRACKER_API_URL=http://example.com made `--backend-check` raise ConfigError a second time while building
its report. No test here touches the network: every attempt to open a connection is recorded and refused.
"""

import json
import socket
import urllib.error
import urllib.request

import pytest

from desktop import appconfig, backend, entry, selftest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A scratch per-user home, no configured address, and a network that refuses (and records) everything."""
    monkeypatch.setenv("POKER_TRACKER_HOME", str(tmp_path / "home"))
    for name in (appconfig.API_URL_ENV, selftest.CHECK_EMAIL_ENV, selftest.CHECK_PASSWORD_ENV):
        monkeypatch.delenv(name, raising=False)
    attempts = []

    def refuse_open(self, fullurl, *args, **kwargs):
        attempts.append(getattr(fullurl, "full_url", fullurl))
        raise urllib.error.URLError("network blocked by the test")

    def refuse_socket(*args, **kwargs):
        attempts.append("socket")
        raise AssertionError("a test tried to open a socket")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse_open)
    monkeypatch.setattr(socket, "create_connection", refuse_socket)
    backend.reset_for_tests()
    yield attempts
    backend.reset_for_tests()


def run_backend_check(tmp_path, capsys):
    out = tmp_path / "report.json"
    code = entry.main(["--backend-check", str(out)])
    return code, json.loads(out.read_text(encoding="utf-8")), capsys.readouterr().out


def test_plain_http_override_gives_a_failed_report_not_an_exception(tmp_path, monkeypatch, capsys, isolated):
    monkeypatch.setenv(appconfig.API_URL_ENV, "http://example.com")
    code, report, printed = run_backend_check(tmp_path, capsys)
    assert code == 1 and report["ok"] is False
    failed = [step for step in report["steps"] if not step["ok"]]
    assert failed and "Refusing to use plain http for example.com" in failed[0]["detail"]      # from source the mode step reports it first, packaged the address step
    assert not any(step["step"] in ("backend health", "sign in", "stored session") for step in report["steps"])   # nothing after the refusal ran
    assert report["backend"].startswith("invalid: ") and "example.com" in report["backend"]
    assert "BACKEND CHECK FAILED" in printed and "Refusing to use plain http for example.com" in printed
    assert isolated == []                                    # refused before any connection was attempted


def test_the_report_names_no_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(appconfig.API_URL_ENV, "http://example.com")
    monkeypatch.setenv(selftest.CHECK_EMAIL_ENV, "who@example.com")
    monkeypatch.setenv(selftest.CHECK_PASSWORD_ENV, "pw-must-not-appear-1234")
    out = tmp_path / "report.json"
    entry.main(["--backend-check", str(out)])
    assert "pw-must-not-appear-1234" not in out.read_text(encoding="utf-8") + capsys.readouterr().out


@pytest.mark.parametrize("url", ["http://example.com", "http://api.example.com:8000", "http://10.0.0.5", "ftp://example.com",
                                 "https://user:pw@api.example.com"])
def test_https_rule_still_refuses_non_loopback_http_and_credentials_in_the_address(url):
    with pytest.raises(appconfig.ConfigError):
        appconfig.normalize_base_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8000/", "https://holdem.example.com/", "https://api.example.com"])
def test_loopback_http_and_https_are_still_accepted(url):
    assert appconfig.normalize_base_url(url) == url.rstrip("/")


def test_valid_https_address_is_used_and_the_run_proceeds_to_the_network(tmp_path, monkeypatch, capsys, isolated):
    monkeypatch.setenv(appconfig.API_URL_ENV, "https://api.example.com/")
    code, report, _ = run_backend_check(tmp_path, capsys)
    steps = {step["step"]: step for step in report["steps"]}
    assert steps["backend address"]["ok"] and report["backend"] == "https://api.example.com"
    assert steps["backend health"]["ok"] is False and code == 1          # only the (blocked) network step fails
    assert isolated and all(url.startswith("https://api.example.com/") for url in isolated)


def test_loopback_http_is_used_for_development_and_tests(tmp_path, monkeypatch, capsys, isolated):
    monkeypatch.setenv(appconfig.API_URL_ENV, "http://127.0.0.1:9")
    _, report, _ = run_backend_check(tmp_path, capsys)
    steps = {step["step"]: step for step in report["steps"]}
    assert steps["backend address"]["ok"] and report["backend"] == "http://127.0.0.1:9"
    assert isolated and all(url.startswith("http://127.0.0.1:9/") for url in isolated)


def test_url_precedence_is_unchanged_and_an_invalid_value_never_falls_back_to_another_host(monkeypatch):
    """environment > per-user app.json (> bundled default). The scratch home keeps this off the real profile."""
    appconfig.save_user_api_url("https://from-user-file.example.com")
    assert appconfig.api_url() == "https://from-user-file.example.com"
    monkeypatch.setenv(appconfig.API_URL_ENV, "https://from-environment.example.com")
    assert appconfig.api_url() == "https://from-environment.example.com"
    monkeypatch.setenv(appconfig.API_URL_ENV, "http://example.com")
    with pytest.raises(appconfig.ConfigError):
        appconfig.api_url()                                  # the valid user-file address is NOT silently used instead


@pytest.mark.parametrize("switch, as_json", [("--backend-check", True), ("--selftest", True), ("--diagnostics", False)])
def test_an_unexpected_error_in_a_support_switch_exits_non_zero_with_a_useful_report(switch, as_json, tmp_path, monkeypatch, capsys):
    from desktop import bootstrap

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(selftest, "backend_check", boom)
    monkeypatch.setattr(selftest, "run", boom)
    monkeypatch.setattr(bootstrap, "diagnostics_text", boom)
    out = tmp_path / "out.txt"
    assert entry.main([switch, str(out)]) == 2
    text = out.read_text(encoding="utf-8")
    assert "RuntimeError" in text and "simulated failure" in text and switch in text
    if as_json:
        data = json.loads(text)
        assert data["ok"] is False and data["steps"][0]["ok"] is False
    else:
        assert text.startswith("ERROR ")
    assert "ERROR " + switch in capsys.readouterr().out


def test_an_unwritable_output_path_still_returns_the_error_code(tmp_path, monkeypatch):
    monkeypatch.setattr(selftest, "backend_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    assert entry.main(["--backend-check", str(blocker / "sub" / "out.json")]) == 2


def test_normal_start_up_is_not_wrapped(monkeypatch):
    """No support switch: the GUI is started exactly as before, and its errors are not swallowed by the support-switch guard."""
    import sys
    import types
    calls = []
    fake_app = types.SimpleNamespace(main=lambda: calls.append("main"))
    monkeypatch.setitem(sys.modules, "app", fake_app)
    assert entry.main([]) == 0 and calls == ["main"]
    fake_app.main = lambda: (_ for _ in ()).throw(RuntimeError("gui error"))
    with pytest.raises(RuntimeError):
        entry.main([])
