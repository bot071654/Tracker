"""Headless self-test: proves a build has everything it needs, without a display, a casino or a network.

    PokerHandTracker --selftest result.json       (packaged)        python -m desktop.entry --selftest result.json (source)

Checks that the bundled runtime imports (OpenCV, NumPy, mss, openpyxl, Tk), that no automation library and no database driver
slipped in, that every per-user folder is writable, that the shipped templates and defaults are present, that the session
store protects what it writes, and - the one that matters most - that the REAL recognizer, run on recorded live card images,
returns exactly what it returned from source when the baseline in desktop/selftest_golden.json was taken.

The result is written as JSON (a windowed app has no console) and the exit code is 0 only if every check passed.
"""

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import traceback

from desktop import appconfig, paths, version

GOLDEN_NAME = "selftest_golden.json"
LIVE_CARDS_DIR = ("tests", "fixtures", "live_cards")          # recorded card images that already ship in the repository
AUTOMATION_MODULES = ("pyautogui", "pygetwindow", "pyscreeze", "pymsgbox", "pytweening", "mouseinfo", "pynput", "keyboard")
CONFIDENCE_TOLERANCE = 1e-6


def _resource(*parts):
    return os.path.join(paths.resource_root(), *parts)


def golden_path():
    return _resource("desktop", GOLDEN_NAME)


def live_cards_dir():
    return _resource(*LIVE_CARDS_DIR)


# --------------------------------------------------------------------------------------------- checks

def check_runtime_imports():
    found = {}
    for name in ("cv2", "numpy", "mss", "openpyxl", "tkinter", "ssl", "json"):
        module = importlib.import_module(name)
        found[name] = getattr(module, "__version__", getattr(module, "TkVersion", "ok"))
    import tkinter
    found["tcl"] = tkinter.Tcl().eval("info patchlevel")
    return found


def check_no_automation_or_database_driver():
    present = [name for name in AUTOMATION_MODULES if importlib.util.find_spec(name) is not None]
    drivers = [name for name in ("psycopg", "psycopg2", "dotenv") if importlib.util.find_spec(name) is not None]
    if paths.is_frozen():
        assert not present, "automation libraries are inside the build: %s" % present
        assert not drivers, "database driver / .env loader is inside the build: %s" % drivers
        return "none bundled"
    return "source run (not enforced): automation=%s drivers=%s" % (present or "none", drivers or "none")


def check_folders_are_writable():
    results = {}
    for name, where in paths.all_locations().items():
        if name in ("resources", "templates", "debug"):
            continue
        os.makedirs(where, exist_ok=True)
        probe = os.path.join(where, ".selftest-%d" % os.getpid())
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        results[name] = where
    if paths.is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        assert not any(os.path.abspath(where).startswith(exe_dir) for where in results.values()), \
            "user data would be written beside the executable"
    return results


def check_resources():
    from recognition import card_recognizer
    templates = card_recognizer.load_templates(force=True)
    missing = card_recognizer.template_status()
    assert missing == ([], []), "templates missing: %s" % (missing,)
    counts = (len(templates["ranks"]), len(templates["suits"]))
    assert counts == (13, 4), "unexpected template set %s" % (counts,)
    from config import settings
    config = settings.load_config()
    assert isinstance(config, dict) and "confidence_threshold" in config
    assert os.path.exists(golden_path()), "golden baseline not bundled"
    return {"ranks": counts[0], "suits": counts[1], "confidence_threshold": config["confidence_threshold"]}


def _summary(result):
    if not result:
        return None
    return {"card": result.get("card"), "confident": bool(result.get("confident")),
            "rank": result.get("rank"), "suit": result.get("suit"),
            "confidence": round(float(result.get("confidence", 0.0)), 6),
            "rank_margin": round(float(result.get("rank_margin", 0.0)), 6),
            "suit_margin": round(float(result.get("suit_margin", 0.0)), 6)}


def recognise_live_cards():
    """{filename: summary} for the recorded live card images, using the production recognizer unchanged."""
    import cv2
    from recognition.card_recognizer import recognize_card
    folder = live_cards_dir()
    names = sorted(name for name in os.listdir(folder) if name.lower().endswith(".png"))
    assert names, "no recorded card images found in %s" % folder
    return {name: _summary(recognize_card(cv2.imread(os.path.join(folder, name)))) for name in names}


def check_recognition_matches_the_source_baseline():
    with open(golden_path(), "r", encoding="utf-8") as handle:
        golden = json.load(handle)
    actual = recognise_live_cards()
    assert set(actual) == set(golden), "image set differs: %s vs %s" % (sorted(actual), sorted(golden))
    differences = []
    for name, expected in golden.items():
        got = actual[name]
        same = (got is not None and expected is not None
                and got["card"] == expected["card"] and got["confident"] == expected["confident"]
                and all(abs(got[key] - expected[key]) <= CONFIDENCE_TOLERANCE for key in ("confidence", "rank_margin", "suit_margin")))
        if not same:
            differences.append((name, expected, got))
    assert not differences, "recognition differs from the source baseline: %s" % differences[:3]
    confident = sum(1 for item in actual.values() if item and item["confident"])
    return "%d recorded cards identical to the source baseline (%d confident)" % (len(actual), confident)


def check_window_less_pipeline():
    """OpenCV's native code actually runs: a template matched against itself, and an empty slot read as 'no card'."""
    import cv2
    import numpy as np
    from recognition import card_recognizer
    template = card_recognizer.load_templates()["ranks"]["K"][0]
    score = float(cv2.matchTemplate(template, template, cv2.TM_CCOEFF_NORMED).max())
    assert score > 0.99, "template does not match itself (%s)" % score
    blank = np.full((90, 60, 3), 30, dtype=np.uint8)
    result = card_recognizer.read_slot(blank)
    assert not (result or {}).get("confident"), "an empty slot was read as a confident card"
    return "matchTemplate ok (%.3f), empty slot not read as a card" % score


def check_capture_backend():
    import mss
    with mss.mss() as grabber:
        monitors = grabber.monitors
    assert len(monitors) >= 1
    return "%d monitor entr%s" % (len(monitors), "y" if len(monitors) == 1 else "ies")


def check_session_store_protects_secrets():
    from desktop import session
    secret = b'{"access_token": "selftest-secret-token-0123456789"}'
    with tempfile.TemporaryDirectory() as folder:
        backend = session.default_backend(folder)
        if isinstance(backend, session.KeychainBackend):
            return "macOS Keychain backend selected (not exercised by the self-test: it would write to the login keychain)"
        backend.write(secret)
        assert backend.read() == secret
        raw = open(backend.path, "rb").read()
        if isinstance(backend, session.DpapiBackend):
            assert b"selftest-secret-token" not in raw, "the stored session is readable as plain text"
        backend.delete()
    return type(backend).__name__


def check_tls_trust_bundle():
    """HTTPS needs a CA bundle that ships WITH the app (see requirements-desktop.txt for why the system store alone is not enough)."""
    import ssl
    import certifi
    bundle = certifi.where()
    assert os.path.getsize(bundle) > 100_000, "the bundled CA file is missing or empty: %s" % bundle
    context = ssl.create_default_context(cafile=bundle)
    assert len(context.get_ca_certs()) > 100, "the CA bundle loaded no certificates"
    if paths.is_frozen():
        assert os.path.abspath(bundle).startswith(os.path.abspath(paths.resource_root())), "the CA bundle is not inside the build"
    return "%d trusted roots from %s" % (len(context.get_ca_certs()), os.path.basename(os.path.dirname(bundle)) + "/" + os.path.basename(bundle))


def check_api_client_rules():
    from desktop.api_client import ApiClient
    ApiClient("https://api.example.invalid")
    for bad in ("http://api.example.invalid", "https://user:pw@api.example.invalid"):
        try:
            appconfig.normalize_base_url(bad)
        except appconfig.ConfigError:
            continue
        raise AssertionError("accepted an unsafe backend address: %s" % bad)
    return "https enforced; configured backend: %s" % (appconfig.api_url() or "none")


CHECKS = [
    ("runtime imports", check_runtime_imports),
    ("no automation libraries or database driver", check_no_automation_or_database_driver),
    ("per-user folders writable", check_folders_are_writable),
    ("bundled resources", check_resources),
    ("native OpenCV pipeline", check_window_less_pipeline),
    ("recognition identical to source baseline", check_recognition_matches_the_source_baseline),
    ("screen-capture backend", check_capture_backend),
    ("session store protects secrets", check_session_store_protects_secrets),
    ("TLS trust bundle ships with the app", check_tls_trust_bundle),
    ("api client rules", check_api_client_rules),
]


def run(out_path=None):
    """Run every check. Returns the report dict (and writes it to out_path as JSON when given)."""
    results = []
    for name, check in CHECKS:
        try:
            detail = check()
            results.append({"check": name, "ok": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001 - report every failure, never stop at the first
            results.append({"check": name, "ok": False, "detail": "%s: %s" % (type(exc).__name__, exc),
                            "trace": traceback.format_exc(limit=4)})
    report = {"ok": all(item["ok"] for item in results), "version": version.as_dict(), "results": results}
    if out_path:
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
    return report


def write_golden():
    """Record the current recognizer behaviour as the baseline. Run from SOURCE, deliberately, never from a build."""
    assert not paths.is_frozen(), "the baseline must come from the source application"
    data = recognise_live_cards()
    with open(golden_path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    return data


# --------------------------------------------------------------------------------------------- backend check

CHECK_EMAIL_ENV = "POKER_TRACKER_CHECK_EMAIL"
CHECK_PASSWORD_ENV = "POKER_TRACKER_CHECK_PASSWORD"


def backend_check(submit_test_hand=False, sign_out=False, out_path=None):
    """Exercise the real desktop -> HTTPS API -> database path from the packaged code, without any window or mouse.

    Read-only unless submit_test_hand is asked for (that stores one real, internally consistent hand with random cards).
    Credentials come from POKER_TRACKER_CHECK_EMAIL / POKER_TRACKER_CHECK_PASSWORD when set (never from the command line);
    otherwise the stored session of a previous sign-in is used, which is also how the "restart keeps you signed in" test works.
    The password and token never appear in the report.
    """
    from database import db
    from desktop import backend as remote_backend
    steps = []

    def step(name, fn):
        try:
            detail = fn()
            steps.append({"step": name, "ok": True, "detail": detail})
            return True
        except Exception as exc:  # noqa: BLE001 - report, then stop at the first failure
            steps.append({"step": name, "ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)})
            return False

    backend = remote_backend.get()
    email, password = os.environ.get(CHECK_EMAIL_ENV), os.environ.get(CHECK_PASSWORD_ENV)
    ok = step("mode is remote and no direct database driver is loaded",
              lambda: _assert_remote_mode())
    ok = ok and step("backend address", lambda: appconfig.api_url() or (_ for _ in ()).throw(RuntimeError("no backend address configured")))
    ok = ok and step("backend health", lambda: backend.client().health())
    if ok and email and password:
        ok = step("sign in", lambda: "signed in as %s" % backend.sign_in(email, password)["email"])
    elif ok:
        ok = step("stored session", lambda: "using the saved sign-in" if backend.signed_in() else (_ for _ in ()).throw(RuntimeError("not signed in and no credentials given")))
    ok = ok and step("connection check", lambda: db.check_connection())
    ok = ok and step("schema check", lambda: db.verify_schema())
    ok = ok and step("statistics (read)", lambda: db.win_statistics())
    ok = ok and step("history (read)", lambda: "%d hand(s) visible" % len(db.fetch_all_hands()))
    if ok and submit_test_hand:
        ok = step("submit one test hand", lambda: _submit_test_hand())
    if sign_out:
        step("sign out", lambda: backend.sign_out() or "signed out")
    report = {"ok": all(item["ok"] for item in steps), "version": version.as_dict(), "backend": _configured_backend(), "steps": steps}
    if out_path:
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
    return report


def _configured_backend():
    """The backend address for the report. An address the HTTPS rules refuse is reported here, not raised a second time: the
    refusal is already a failed step, and the report must still be written."""
    try:
        return appconfig.api_url()
    except appconfig.ConfigError as exc:
        return "invalid: %s" % exc


def _assert_remote_mode():
    assert appconfig.remote_mode(), "not in remote mode"
    import database.db as database_module
    if paths.is_frozen():
        assert database_module.psycopg is None, "a PostgreSQL driver is loaded in the packaged app"
    return "remote (HTTPS API)" + ("; no database driver present" if database_module.psycopg is None else "")


def _submit_test_hand():
    import random
    from database import db
    from poker.hand_record import CARD_ORDER, build_hand_record
    deck = [r + s for r in "A K Q J 10 9 8 7 6 5 4 3 2".split() for s in "SHDC"]
    cards = random.SystemRandom().sample(deck, 9)
    record = build_hand_record(dict(zip(CARD_ORDER, cards)))
    inserted, row = db.insert_hand(record)
    return "stored as hand #%s (%s)" % (row["id"], record["hand_fingerprint"]) if inserted else "reported as a duplicate"
