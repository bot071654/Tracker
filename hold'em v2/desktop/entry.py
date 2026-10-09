"""Entry point of the packaged application (the PyInstaller script).

    PokerHandTracker                      start the tracker (app.main), exactly as `python app.py` does
    PokerHandTracker --version            write the version line to version.txt in the log folder (and stdout if there is one)
    PokerHandTracker --selftest FILE      run the headless self-test, write the JSON report to FILE, exit 0/1
    PokerHandTracker --diagnostics FILE   write the support text (version, folders, mode; no secrets) to FILE
    PokerHandTracker --backend-check FILE [--submit-test-hand] [--sign-out]
                                          headless desktop -> HTTPS API -> database check (credentials from
                                          POKER_TRACKER_CHECK_EMAIL / _PASSWORD, never argv); read-only unless asked to submit

A windowed application has no console, so the informational options write to a file. An unexpected error inside one of them exits
with code 2 and is written to that file (a windowed exe would otherwise show a modal exception dialog). app.py stays the original entry
point and is unchanged in behaviour: this file only adds these switches in front of it.
"""

import json
import os
import sys


def _option(argv, name):
    if name not in argv:
        return None
    index = argv.index(name)
    return argv[index + 1] if index + 1 < len(argv) else ""


def _emit(text, path=None):
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    if sys.stdout is not None:
        try:
            print(text)
        except OSError:
            pass


SWITCHES = ("--version", "--selftest", "--backend-check", "--diagnostics")      # the order they are looked for in


def _support_switch(argv, switch):
    """One of the informational / support switches. Returns the exit code."""
    if switch == "--version":
        from desktop import paths, version
        _emit(version.describe(), os.path.join(paths.log_dir(), "version.txt"))
        return 0
    if switch == "--selftest":
        from desktop import selftest
        report = selftest.run(_option(argv, "--selftest") or None)
        for item in report["results"]:
            _emit("%s  %s  %s" % ("PASS" if item["ok"] else "FAIL", item["check"], item["detail"]))
        _emit("SELFTEST %s" % ("PASSED" if report["ok"] else "FAILED"))
        return 0 if report["ok"] else 1
    if switch == "--backend-check":
        from desktop import selftest
        report = selftest.backend_check("--submit-test-hand" in argv, "--sign-out" in argv, _option(argv, "--backend-check") or None)
        for item in report["steps"]:
            _emit("%s  %s  %s" % ("PASS" if item["ok"] else "FAIL", item["step"], str(item["detail"])[:200]))
        _emit("BACKEND CHECK %s" % ("PASSED" if report["ok"] else "FAILED"))
        return 0 if report["ok"] else 1
    from desktop import bootstrap
    _emit(bootstrap.diagnostics_text(), _option(argv, "--diagnostics") or None)
    return 0


def _report_failure(switch, argv, exc):
    """An unexpected error in a support switch. This is a windowed program: an exception left to escape becomes a modal
    "Unhandled exception" dialog nobody can script or read from a log, so write it where the caller asked for the output."""
    message = "%s failed: %s: %s" % (switch, type(exc).__name__, exc)
    path = _option(argv, switch) if switch != "--version" else None
    if path:
        try:
            if switch == "--diagnostics":
                text = "ERROR " + message + "\n"
            else:
                text = json.dumps({"ok": False, "error": message, "steps": [{"step": switch, "ok": False, "detail": message}]}, indent=2) + "\n"
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass
    _emit("ERROR " + message)
    return 2


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    switch = next((name for name in SWITCHES if name in argv), None)
    if switch is not None:
        try:
            return _support_switch(argv, switch)
        except Exception as exc:  # noqa: BLE001 - report it, never show a modal error dialog from a support switch
            return _report_failure(switch, argv, exc)
    import app
    app.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
