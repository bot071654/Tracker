"""Application identity: name, version, build number, platform, architecture.

VERSION is edited by hand for a release. BUILD comes from desktop/build_info.json, which the build
scripts write (build number + git commit); a source checkout has no such file and reports "dev".
"""

import json
import os
import platform
import sys

APP_NAME = "Poker Hand Tracker"
APP_ID = "PokerHandTracker"          # folder / bundle name: no spaces
VERSION = "1.0.0"

_HERE = os.path.dirname(os.path.abspath(__file__))


def _build_info():
    candidates = [os.path.join(_HERE, "build_info.json")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, os.path.join(meipass, "desktop", "build_info.json"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def build_number():
    return str(_build_info().get("build", "dev"))


def commit():
    return str(_build_info().get("commit", "unknown"))


def platform_name():
    """'Windows', 'macOS' or the raw system name (Linux is a development convenience only)."""
    system = platform.system()
    return {"Darwin": "macOS"}.get(system, system)


def architecture():
    """Normalised CPU architecture: x64, arm64 or whatever the machine reports."""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine or "unknown"


def describe():
    """One line for the About text and the log: 'Poker Hand Tracker 1.0.0 (build 42) - Windows x64'."""
    return "%s %s (build %s) - %s %s" % (APP_NAME, VERSION, build_number(), platform_name(), architecture())


def as_dict():
    return {
        "name": APP_NAME, "version": VERSION, "build": build_number(), "commit": commit(),
        "platform": platform_name(), "architecture": architecture(),
        "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False)),
    }
