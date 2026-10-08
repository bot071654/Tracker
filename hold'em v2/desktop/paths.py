"""Where the app reads its bundled resources and where it writes per-user data.

From source (python app.py) nothing moves: every location is the project folder, exactly as before.
In the packaged app nothing is written beside the executable. Per-user locations:

  Windows   %LOCALAPPDATA%\\PokerHandTracker\\{config,data,logs,debug,state,recognition\\templates}
  macOS     ~/Library/Application Support/PokerHandTracker/{config,data,state,debug,recognition/templates}
            ~/Library/Logs/PokerHandTracker        (tracker.log)
            ~/Library/Caches/PokerHandTracker      (cache)
  Linux     $XDG_DATA_HOME or ~/.local/share/PokerHandTracker   (development convenience only)

POKER_TRACKER_HOME overrides the home folder in every mode (portable use, tests, support).
"""

import json
import logging
import os
import shutil
import sys
import tempfile

from desktop.version import APP_ID

logger = logging.getLogger(__name__)

HOME_ENV = "POKER_TRACKER_HOME"
# Calibration is measured on one screen and is wrong on any other: never shipped as a default.
MACHINE_SPECIFIC_CONFIG = ("regions", "screen_size", "result_boxes")
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def source_root():
    """The project folder this code runs from (source mode)."""
    return _SOURCE_ROOT


def resource_root():
    """Read-only bundled files: the PyInstaller bundle when frozen, otherwise the project folder."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return _SOURCE_ROOT


def _user_home():
    return os.path.expanduser("~")


def _platform_home():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(_user_home(), "AppData", "Local")
        return os.path.join(base, APP_ID)
    if sys.platform == "darwin":
        return os.path.join(_user_home(), "Library", "Application Support", APP_ID)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(_user_home(), ".local", "share")
    return os.path.join(base, APP_ID)


def _overridden():
    return bool((os.environ.get(HOME_ENV) or "").strip())


def app_home():
    """The writable root. The project folder from source; a per-user folder when packaged."""
    if _overridden():
        return os.path.abspath(os.environ[HOME_ENV].strip())
    if is_frozen():
        return _platform_home()
    return _SOURCE_ROOT


def _relocated():
    """True when data lives outside the project folder (packaged, or POKER_TRACKER_HOME set)."""
    return is_frozen() or _overridden()


def config_dir():
    return os.path.join(app_home(), "config")


def data_dir():
    return os.path.join(app_home(), "data")


def template_dir():
    return os.path.join(app_home(), "recognition", "templates")


def debug_dir():
    return os.path.join(app_home(), "debug")


def state_dir():
    """Window position and similar local state. Beside the config from source, as it always was."""
    return os.path.join(app_home(), "state") if _relocated() else config_dir()


def log_dir():
    if is_frozen() and sys.platform == "darwin" and not _overridden():
        return os.path.join(_user_home(), "Library", "Logs", "PokerHandTracker")
    return os.path.join(app_home(), "logs")


def cache_dir():
    if is_frozen() and sys.platform == "darwin" and not _overridden():
        return os.path.join(_user_home(), "Library", "Caches", "PokerHandTracker")
    return os.path.join(app_home(), "cache")


def temp_dir():
    path = os.path.join(tempfile.gettempdir(), APP_ID)
    os.makedirs(path, exist_ok=True)
    return path


def all_locations():
    """For support and the About text."""
    return {
        "home": app_home(), "config": config_dir(), "data": data_dir(), "logs": log_dir(),
        "cache": cache_dir(), "state": state_dir(), "templates": template_dir(), "debug": debug_dir(),
        "resources": resource_root(),
    }


# --------------------------------------------------------------------------------------------------
# First run: copy the shipped defaults into the user's folders (never overwrite anything there)
# --------------------------------------------------------------------------------------------------

def _copy_tree_if_missing(src, dst):
    copied = 0
    for dirpath, _dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for name in filenames:
            out = os.path.join(target, name)
            if not os.path.exists(out):
                shutil.copy2(os.path.join(dirpath, name), out)
                copied += 1
    return copied


def seed_user_files(bundled=None, home=None):
    """Create the user folders and copy missing defaults into them. Idempotent; returns what it did.

    Existing user files are never overwritten (calibration, taught templates and scenarios are theirs).
    The bundled config.json has the calibration of the screen it was made on stripped out.
    """
    bundled = bundled or resource_root()
    home = home or app_home()
    done = {"templates": 0, "config": False, "scenarios": False, "engine": False}
    if os.path.abspath(bundled) == os.path.abspath(home):
        return done                                    # source mode: the files are already where they belong
    for name in ("config", "data", "logs", "debug", "state", "cache"):
        os.makedirs(os.path.join(home, name), exist_ok=True)

    src_templates = os.path.join(bundled, "recognition", "templates")
    if os.path.isdir(src_templates):
        done["templates"] = _copy_tree_if_missing(src_templates, os.path.join(home, "recognition", "templates"))

    src_config = os.path.join(bundled, "config", "config.json")
    dst_config = os.path.join(home, "config", "config.json")
    if os.path.exists(src_config) and not os.path.exists(dst_config):
        try:
            with open(src_config, "r", encoding="utf-8") as handle:
                config = json.load(handle)
            for key in MACHINE_SPECIFIC_CONFIG:
                config.pop(key, None)
            with open(dst_config, "w", encoding="utf-8") as handle:
                json.dump(config, handle, indent=4)
            done["config"] = True
        except (OSError, ValueError) as exc:
            logger.error("Could not seed config.json: %s", exc)

    for key, name in (("scenarios", "scenarios.json"), ("engine", "scenario_engine.json")):
        src = os.path.join(bundled, "config", name)
        dst = os.path.join(home, "config", name)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
                done[key] = True
            except OSError as exc:
                logger.error("Could not seed %s: %s", name, exc)
    return done
