"""Check a finished build before it is wrapped in an installer / DMG.

    python desktop/build/verify_bundle.py <bundle folder or .app> [--no-run]

1. Content scan: no secrets or private state were swept in (no .env, no database credentials, no logs, no spreadsheets,
   no calibration of the developer's screen, no automation/driver libraries), and the required resources are present.
2. Runs the packaged executable's own --selftest (headless) and requires it to pass - unless --no-run (a build for another
   CPU architecture cannot run here).

Exit status 0 only if everything passed. The report is printed; nothing is modified.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

FORBIDDEN_NAMES = (".env", ".env.local", ".env.example", "window_state.json", "poker_hands.xlsx", "tracker.log")
FORBIDDEN_SUFFIXES = (".log", ".xlsx", ".key", ".pfx", ".p12")
PUBLIC_PEM_ALLOWED = ("certifi" + os.sep + "cacert.pem",)      # the public CA bundle HTTPS needs; any OTHER .pem is scanned for private keys
FORBIDDEN_PACKAGES = ("psycopg", "psycopg2", "dotenv", "pyautogui", "pygetwindow", "pyscreeze", "pymsgbox", "mouseinfo",
                      "pytweening", "fastapi", "uvicorn", "starlette", "pydantic", "pytest")
REQUIRED = (("recognition", "templates", "ranks"), ("recognition", "templates", "suits"), ("config", "config.json"),
            ("desktop", "build_info.json"), ("desktop", "selftest_golden.json"))
# text that would mean a credential was baked into a file
SECRET_PATTERNS = [re.compile(rb"POSTGRES_PASSWORD\s*=\s*\S+"), re.compile(rb"postgres(?:ql)?://[^:/\s@]+:[^@\s]+@"),
                   re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")]
TEXT_SUFFIXES = (".json", ".txt", ".cfg", ".ini", ".md", ".py", ".plist", ".sql", ".env")


def find_exe(bundle):
    if bundle.endswith(".app"):
        return os.path.join(bundle, "Contents", "MacOS", "PokerHandTracker"), bundle
    for name in ("PokerHandTracker.exe", "PokerHandTracker"):
        if os.path.exists(os.path.join(bundle, name)):
            return os.path.join(bundle, name), bundle
    raise SystemExit("No PokerHandTracker executable in %s" % bundle)


def content_scan(root):
    problems, files = [], 0
    internal = [os.path.join(root, "_internal"), os.path.join(root, "Contents", "Resources"), root]
    for base, dirs, names in os.walk(root):
        for name in names:
            files += 1
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            if name.lower() in FORBIDDEN_NAMES or name.lower().endswith(FORBIDDEN_SUFFIXES):
                problems.append("forbidden file: " + rel)
            if name.lower().endswith(".pem"):
                if not rel.endswith(PUBLIC_PEM_ALLOWED):
                    problems.append("unexpected .pem file: " + rel)
                else:
                    with open(path, "rb") as handle:
                        if b"PRIVATE KEY" in handle.read():
                            problems.append("a private key is inside " + rel)
            if name.lower().endswith(TEXT_SUFFIXES) and os.path.getsize(path) < 2_000_000:
                with open(path, "rb") as handle:
                    blob = handle.read()
                for pattern in SECRET_PATTERNS:
                    if pattern.search(blob):
                        problems.append("credential-looking text in " + rel)
        for d in dirs:
            if d.lower() in FORBIDDEN_PACKAGES or any(d.lower().startswith(p + "-") for p in FORBIDDEN_PACKAGES):
                problems.append("forbidden package folder: " + os.path.relpath(os.path.join(base, d), root))
    for parts in REQUIRED:
        if not any(os.path.exists(os.path.join(prefix, *parts)) for prefix in internal):
            problems.append("missing resource: " + "/".join(parts))
    for prefix in internal:
        config = os.path.join(prefix, "config", "config.json")
        if os.path.exists(config):
            with open(config, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            for key in ("regions", "screen_size", "result_boxes"):
                if key in data:
                    problems.append("the developer's calibration (%s) is inside config.json" % key)
    return files, problems


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    bundle = os.path.abspath(argv[0])
    exe, root = find_exe(bundle)
    files, problems = content_scan(root)
    print("Scanned %d files in %s" % (files, bundle))
    for problem in problems:
        print("FAIL  " + problem)
    if "--no-run" not in argv:
        with tempfile.TemporaryDirectory() as home:
            report_path = os.path.join(home, "selftest.json")
            env = dict(os.environ, POKER_TRACKER_HOME=os.path.join(home, "userdata"))     # the test never touches a real profile
            result = subprocess.run([exe, "--selftest", report_path], env=env, capture_output=True, text=True, timeout=300)
            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as handle:
                    report = json.load(handle)
                for item in report["results"]:
                    print("%s  selftest: %s - %s" % ("PASS" if item["ok"] else "FAIL", item["check"], str(item["detail"])[:160]))
                    if not item["ok"]:
                        problems.append("selftest failed: " + item["check"])
            else:
                problems.append("the packaged executable produced no self-test report (exit %s) %s" % (result.returncode, result.stderr[-400:]))
    print("VERIFIED" if not problems else "NOT VERIFIED (%d problem(s))" % len(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
