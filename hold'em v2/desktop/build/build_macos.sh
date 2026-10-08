#!/bin/bash
# Build the macOS package (.app + .dmg) for ONE architecture. UNTESTED: written without access to a Mac; the first run on
# real hardware is expected to need small fixes. Run it ON a Mac of the architecture you are building for:
#
#   Apple Silicon Mac  ->  ARCH=arm64   (the default there)    -> PokerHandTracker-AppleSilicon.dmg
#   Intel Mac          ->  ARCH=x86_64  (the default there)    -> PokerHandTracker-Intel.dmg
#
# Usage:  desktop/build/build_macos.sh [--python /path/to/python3.14] [--api-url https://api.example.com] [--skip-tests]
#
# Optional environment (release builds):
#   BUNDLE_ID         reverse-DNS id of the app, e.g. com.yourcompany.pokerhandtracker      (default com.example.pokerhandtracker)
#   SIGN_IDENTITY     "Developer ID Application: Your Name (TEAMID)"   -> code-sign with the hardened runtime
#   NOTARY_PROFILE    a keychain profile made with `xcrun notarytool store-credentials` -> notarize + staple the DMG
# Without SIGN_IDENTITY the app is unsigned: macOS Gatekeeper will refuse to open it until the user overrides it once
# (right-click > Open). Nothing here bypasses or disables any macOS protection.
set -euo pipefail

BUILD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$BUILD/../.." && pwd)"
PYTHON="python3"; API_URL=""; SKIP_TESTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --python) PYTHON="$2"; shift 2;;
    --api-url) API_URL="$2"; shift 2;;
    --skip-tests) SKIP_TESTS=1; shift;;
    *) echo "Unknown option $1"; exit 2;;
  esac
done

step() { printf '\n=== %s\n' "$1"; }
[ "$(uname -s)" = "Darwin" ] || { echo "This script builds the macOS app and must run on a Mac."; exit 1; }

MACHINE="$(uname -m)"
ARCH="${ARCH:-$MACHINE}"
case "$ARCH" in
  arm64)  LABEL="AppleSilicon";;
  x86_64) LABEL="Intel";;
  *) echo "Unsupported ARCH '$ARCH' (use arm64 or x86_64)"; exit 2;;
esac
if [ "$ARCH" != "$MACHINE" ]; then
  echo "WARNING: building $ARCH on a $MACHINE Mac. The result cannot be self-tested here, and every dependency must be a $ARCH build."
fi
export BUILD_ARCH="$ARCH"
BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)}"; export BUILD_NUMBER
VENV="$BUILD/.venv-mac-$ARCH"; DIST="$BUILD/dist-$ARCH"; WORK="$BUILD/work-$ARCH"

step "Python"
"$PYTHON" --version
"$PYTHON" -c "import platform,sys; print(platform.machine(), sys.version.split()[0]); assert platform.machine()=='$ARCH', 'the interpreter is %s, not $ARCH' % platform.machine()"
"$PYTHON" -c "import tkinter; print('Tk', tkinter.TkVersion)"   # python.org installers include Tk; Homebrew needs python-tk

step "Build environment ($VENV)"
[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$BUILD/requirements-build.txt"
"$VENV/bin/python" -m pip freeze

if [ "$SKIP_TESTS" = 0 ]; then
  step "Tests (desktop + recognition baseline)"
  (cd "$ROOT" && "$VENV/bin/python" -m pip install pytest pillow psycopg[binary] python-dotenv fastapi "uvicorn[standard]" \
     && "$VENV/bin/python" -m pytest tests/test_desktop_paths.py tests/test_api_client.py tests/test_desktop_bootstrap.py tests/test_cards.py -q -p no:cacheprovider \
     && "$VENV/bin/python" -m pip uninstall -y psycopg psycopg-binary python-dotenv fastapi uvicorn pillow pytest)
fi

step "Stage defaults and build info"
"$VENV/bin/python" "$BUILD/prepare_stage.py" --build-number "$BUILD_NUMBER" --api-url "$API_URL"

step "PyInstaller ($ARCH)"
(cd "$ROOT" && "$VENV/bin/python" -m PyInstaller --noconfirm --clean --distpath "$DIST" --workpath "$WORK" "$BUILD/PokerHandTracker.spec")
APP="$DIST/PokerHandTracker.app"
rm -rf "$DIST/PokerHandTracker"   # PyInstaller also leaves the bare folder next to the .app

step "Verify the bundle"
if [ "$ARCH" = "$MACHINE" ]; then
  "$VENV/bin/python" "$BUILD/verify_bundle.py" "$APP"
else
  "$VENV/bin/python" "$BUILD/verify_bundle.py" "$APP" --no-run
fi

if [ -n "${SIGN_IDENTITY:-}" ]; then
  step "Code-sign (hardened runtime)"
  # Sign every nested Mach-O first (deepest first), then the bundle: the order Apple's notarization requires.
  find "$APP" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -111 \) -print0 | while IFS= read -r -d '' f; do
    if file "$f" | grep -q "Mach-O"; then
      codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$f"
    fi
  done
  codesign --force --options runtime --timestamp --entitlements "$BUILD/macos/entitlements.plist" --sign "$SIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
else
  echo "SIGN_IDENTITY not set: the app is UNSIGNED (Gatekeeper will ask the user to confirm opening it)."
fi

step "Disk image"
DMG="$DIST/PokerHandTracker-$LABEL.dmg"
STAGING="$(mktemp -d)"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG"
hdiutil create -volname "Poker Hand Tracker" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"
[ -n "${SIGN_IDENTITY:-}" ] && codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"

if [ -n "${NOTARY_PROFILE:-}" ] && [ -n "${SIGN_IDENTITY:-}" ]; then
  step "Notarize"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
fi

shasum -a 256 "$DMG" | tee "$DMG.sha256"
echo; echo "Disk image: $DMG"
