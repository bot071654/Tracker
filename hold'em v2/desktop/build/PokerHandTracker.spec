# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Poker Hand Tracker. One folder-style build ("onedir") per platform and architecture:
#
#   Windows x64      dist/PokerHandTracker/PokerHandTracker.exe      (wrapped by the Inno Setup installer)
#   macOS arm64      dist/PokerHandTracker.app                       (wrapped in a DMG)
#   macOS x86_64     dist/PokerHandTracker.app
#
# Why onedir and not onefile: a onefile build unpacks itself to a temp folder on every launch (slow start, a favourite
# trigger for antivirus false positives); the installer already gives the user a single file to download.
# Why one build per architecture on macOS rather than universal2: every compiled dependency (OpenCV, NumPy, mss's
# CoreGraphics use, Tk) would have to be a true fat binary; a build on each native architecture avoids depending on that.
#
# Build with:   pyinstaller --noconfirm --clean --distpath <dist> --workpath <work> desktop/build/PokerHandTracker.spec
# (use desktop/build/build_windows.ps1 or build_macos.sh, which also stage the defaults and run the checks).

import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
sys.path.insert(0, ROOT)
from desktop import version  # noqa: E402

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
STAGE = os.path.join(SPECPATH, "stage")
ASSETS = os.path.join(SPECPATH, "assets")
TARGET_ARCH = os.environ.get("BUILD_ARCH") or None            # macOS: "arm64" or "x86_64" (default: the running interpreter's)


def need(path):
    if not os.path.exists(path):
        raise SystemExit("Missing %s - run desktop/build/prepare_stage.py first (the build scripts do)." % path)
    return path


datas = [
    (need(os.path.join(ROOT, "recognition", "templates")), os.path.join("recognition", "templates")),
    (need(os.path.join(STAGE, "config", "config.json")), "config"),
    (need(os.path.join(STAGE, "config", "scenarios.json")), "config"),
    (need(os.path.join(STAGE, "config", "scenario_engine.json")), "config"),
    (need(os.path.join(STAGE, "desktop", "build_info.json")), "desktop"),
    (need(os.path.join(STAGE, "desktop", "api_defaults.json")), "desktop"),
    (need(os.path.join(ROOT, "desktop", "selftest_golden.json")), "desktop"),
    # recorded live card images: the self-test recognises these and compares with the source baseline
    (need(os.path.join(ROOT, "tests", "fixtures", "live_cards")), os.path.join("tests", "fixtures", "live_cards")),
]

hiddenimports = ["app", "pyttsx3", "pyttsx3.drivers"]
if IS_WIN:
    # pyttsx3 loads its driver by name at run time (importlib), so static analysis cannot see these
    hiddenimports += ["pyttsx3.drivers.sapi5", "comtypes", "comtypes.client", "comtypes.gen", "pythoncom", "pywintypes", "win32com", "win32com.client"]
if IS_MAC and importlib.util.find_spec("objc"):
    hiddenimports += ["pyttsx3.drivers.nsss", "objc", "Foundation", "AppKit"]

# Nothing that can drive the mouse or keyboard, no database driver, no server stack, no dev tools.
excludes = [
    "pyautogui", "pygetwindow", "pyscreeze", "pymsgbox", "pytweening", "mouseinfo", "pyperclip", "pynput", "keyboard",
    "psycopg", "psycopg_binary", "psycopg2", "dotenv",
    "fastapi", "starlette", "uvicorn", "pydantic", "pydantic_core", "anyio", "h11", "httptools", "websockets", "watchfiles", "click", "yaml",
    "pytest", "_pytest", "PIL", "IPython", "matplotlib", "scipy", "pandas",
]

a = Analysis(
    [os.path.join(ROOT, "desktop", "entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = None
for candidate in (("PokerHandTracker.ico",) if IS_WIN else ("PokerHandTracker.icns",) if IS_MAC else ()):
    if os.path.exists(os.path.join(ASSETS, candidate)):
        icon = os.path.join(ASSETS, candidate)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PokerHandTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX-packed binaries are a classic antivirus false-positive trigger
    console=False,                   # a windowed Tk application; the informational options write to files
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH if IS_MAC else None,
    codesign_identity=None,          # signing is a separate, explicit step (build scripts) with the owner's certificate
    entitlements_file=os.path.join(SPECPATH, "macos", "entitlements.plist") if IS_MAC else None,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PokerHandTracker")

if IS_MAC:
    app = BUNDLE(
        coll,
        name="PokerHandTracker.app",
        icon=icon,
        bundle_identifier=os.environ.get("BUNDLE_ID", "com.example.pokerhandtracker"),   # set BUNDLE_ID to the owner's reverse-DNS id
        version=version.VERSION,
        info_plist={
            "CFBundleName": version.APP_NAME,
            "CFBundleDisplayName": version.APP_NAME,
            "CFBundleShortVersionString": version.VERSION,
            "CFBundleVersion": os.environ.get("BUILD_NUMBER", "1"),
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
