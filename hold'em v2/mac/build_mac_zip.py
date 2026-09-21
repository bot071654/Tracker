"""Pack the project into PokerHandTracker-mac.zip for a Mac.

Run on any machine:  python mac/build_mac_zip.py

What it does that a plain "Send to > Compressed folder" does not:

* the installer is marked executable inside the zip, and has Unix line
  endings, so it can be double-clicked after unzipping on a Mac;
* this machine's secrets and state stay behind: .env (the database password),
  the Windows virtual environment, logs, debug captures, the hands spreadsheet
  and the remembered window position;
* the calibrated card boxes are left out of config/config.json, because they
  were measured on this screen and would be wrong on the Mac's.
"""

import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "dist", "PokerHandTracker-mac.zip")
TOP = "PokerHandTracker"

SKIP_DIRS = {".venv", ".venv-mac", "__pycache__", ".pytest_cache", "logs", "debug",
             "dist", ".git"}
SKIP_FILES = {".env", "window_state.json", "install.log"}
SKIP_SUFFIXES = (".pyc", ".xlsx", ".log")
EXECUTABLE_SUFFIXES = (".command", ".sh")
MACHINE_SPECIFIC_CONFIG = ("regions", "screen_size", "result_boxes")


def wanted(relative):
    parts = relative.replace("\\", "/").split("/")
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return False
    name = parts[-1]
    return name not in SKIP_FILES and not name.endswith(SKIP_SUFFIXES)


def add(archive, arcname, data, executable=False):
    info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3                      # Unix, so the mode bits are honoured
    info.external_attr = ((0o755 if executable else 0o644) | 0o100000) << 16
    archive.writestr(info, data)


def portable_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    for key in MACHINE_SPECIFIC_CONFIG:
        config.pop(key, None)
    return json.dumps(config, indent=4).encode("utf-8")


def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    count = 0
    with zipfile.ZipFile(OUTPUT, "w") as archive:
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                path = os.path.join(folder, name)
                relative = os.path.relpath(path, ROOT).replace("\\", "/")
                if not wanted(relative):
                    continue
                arcname = "%s/%s" % (TOP, relative)
                executable = name.endswith(EXECUTABLE_SUFFIXES)
                if relative == "config/config.json":
                    data = portable_config(path)
                else:
                    with open(path, "rb") as handle:
                        data = handle.read()
                if executable:
                    data = data.replace(b"\r\n", b"\n")
                add(archive, arcname, data, executable)
                count += 1
    size = os.path.getsize(OUTPUT) / 1024 / 1024
    print("Wrote %s (%d files, %.1f MB)" % (OUTPUT, count, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
