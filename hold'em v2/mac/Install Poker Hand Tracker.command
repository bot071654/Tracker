#!/bin/bash
# Poker Hand Tracker - one-step installer for macOS.
#
# Double-click this file in Finder. The first time, macOS may say it is from an
# unidentified developer: right-click it, choose Open, then Open again.
#
# It installs everything the tracker needs and leaves a "Poker Hand Tracker"
# app in your Applications folder:
#   - Homebrew (the macOS package manager), if it is not already there
#   - Python 3.12 with Tk (the window toolkit the tracker is drawn with)
#   - PostgreSQL 16, started as a background service
#   - the tracker's Python packages, in a private environment in this folder
#   - the poker_tracker database and its table
#
# Safe to run again: every step checks whether it is already done.

set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

LOG="$PROJECT_DIR/mac/install.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

PYTHON_FORMULA="python@3.12"
TK_FORMULA="python-tk@3.12"
PG_FORMULA="postgresql@16"
VENV="$PROJECT_DIR/.venv-mac"
APP_NAME="Poker Hand Tracker"
APP_DIR="$HOME/Applications/$APP_NAME.app"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[1;32m    %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m    %s\033[0m\n' "$1"; }
fail() {
    printf '\n\033[1;31mInstallation stopped: %s\033[0m\n' "$1"
    printf 'The full log is in: %s\n' "$LOG"
    printf '\nPress Return to close this window.'
    read -r _
    exit 1
}

printf '\033[1mPoker Hand Tracker - macOS installer\033[0m\n'
printf 'Installing into: %s\n' "$PROJECT_DIR"

[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS only."

# -- 0. Let the tracker's own files run -----------------------------------------
# Files downloaded from the internet are quarantined; without this the app
# created below would be blocked by Gatekeeper on first launch.
xattr -dr com.apple.quarantine "$PROJECT_DIR" 2>/dev/null || true
chmod +x "$PROJECT_DIR"/mac/*.command 2>/dev/null || true

# -- 1. Homebrew -----------------------------------------------------------------
step "Checking for Homebrew"
find_brew() {
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$candidate" ] && { echo "$candidate"; return 0; }
    done
    command -v brew 2>/dev/null
}
BREW="$(find_brew)"
if [ -z "$BREW" ]; then
    warn "Homebrew is not installed. Installing it now."
    warn "You will be asked for your Mac login password (nothing shows as you type)."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || fail "Homebrew could not be installed. Check your internet connection and run this again."
    BREW="$(find_brew)"
    [ -n "$BREW" ] || fail "Homebrew was installed but could not be found."
fi
eval "$("$BREW" shellenv)"
ok "Homebrew: $("$BREW" --version | head -n 1)"

# -- 2. Python with Tk, and PostgreSQL ------------------------------------------
brew_install() {
    if "$BREW" list --versions "$1" >/dev/null 2>&1; then
        ok "$1 already installed"
    else
        step "Installing $1 (this can take a few minutes)"
        "$BREW" install "$1" || fail "could not install $1."
    fi
}
brew_install "$PYTHON_FORMULA"
brew_install "$TK_FORMULA"
brew_install "$PG_FORMULA"

PYTHON="$("$BREW" --prefix "$PYTHON_FORMULA")/bin/python3.12"
PG_BIN="$("$BREW" --prefix "$PG_FORMULA")/bin"
[ -x "$PYTHON" ] || fail "Python was installed but $PYTHON is missing."
"$PYTHON" -c "import tkinter; tkinter.Tcl()" \
    || fail "Python cannot load Tk. Run: brew reinstall $TK_FORMULA"
ok "Python: $("$PYTHON" --version)"

# -- 3. Start PostgreSQL ---------------------------------------------------------
step "Starting PostgreSQL"
"$BREW" services start "$PG_FORMULA" >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
    "$PG_BIN/pg_isready" -h localhost -p 5432 >/dev/null 2>&1 && break
    sleep 1
done
"$PG_BIN/pg_isready" -h localhost -p 5432 >/dev/null 2>&1 \
    || fail "PostgreSQL did not start. Try: brew services restart $PG_FORMULA"
ok "PostgreSQL is running on port 5432"

# -- 4. The tracker's Python packages -------------------------------------------
step "Creating the tracker's Python environment"
if [ ! -x "$VENV/bin/python" ]; then
    "$PYTHON" -m venv "$VENV" || fail "could not create $VENV."
fi
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null \
    || fail "could not update pip."

# pyautogui drives the Windows-only test-table automation, and pytest only
# runs the test suite; neither is needed to track hands on a Mac.
REQS="$(mktemp)"
grep -v -E '^\s*(pyautogui|pytest)' "$PROJECT_DIR/requirements.txt" > "$REQS"
step "Installing Python packages (OpenCV, NumPy, MSS, psycopg, ...)"
"$VENV/bin/python" -m pip install -r "$REQS" || fail "the Python packages could not be installed."
rm -f "$REQS"
"$VENV/bin/python" -c "import cv2, numpy, mss, psycopg, openpyxl, dotenv, tkinter" \
    || fail "a package installed but cannot be imported."
ok "Python packages installed"

# -- 5. Database settings ---------------------------------------------------------
step "Writing the database settings (.env)"
MARKER="# Written by the macOS installer"
if [ -f .env ] && grep -q "$MARKER" .env; then
    ok ".env already set up for this Mac - left as it is"
else
    if [ -f .env ]; then
        mv .env ".env.before-mac-install"
        warn "An existing .env (from another machine) was kept as .env.before-mac-install"
    fi
    # Homebrew's PostgreSQL makes your macOS user its administrator and trusts
    # connections from this Mac, so no password is needed or stored.
    cat > .env <<EOF
$MARKER on $(date "+%Y-%m-%d %H:%M").
# Homebrew's PostgreSQL trusts local connections from your macOS user.
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DATABASE=poker_tracker
POSTGRES_USER=$(whoami)
POSTGRES_PASSWORD=
EOF
    ok ".env written"
fi

step "Creating the poker_tracker database"
"$VENV/bin/python" tools/setup_database.py || fail "the database could not be set up."

# -- 6. The app in ~/Applications -------------------------------------------------
step "Creating the $APP_NAME app"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>local.pokerhandtracker</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF
cat > "$APP_DIR/Contents/MacOS/launcher" <<EOF
#!/bin/bash
# Starts the Poker Hand Tracker from: $PROJECT_DIR
cd "$PROJECT_DIR" || exit 1
"$BREW" services start "$PG_FORMULA" >/dev/null 2>&1
exec "$VENV/bin/python" app.py >> "$PROJECT_DIR/logs/mac-launcher.log" 2>&1
EOF
chmod +x "$APP_DIR/Contents/MacOS/launcher"
mkdir -p "$PROJECT_DIR/logs"
ln -sfn "$APP_DIR" "$HOME/Desktop/$APP_NAME.app" 2>/dev/null || true
ok "Installed: $APP_DIR (a shortcut is on your Desktop)"

# -- 7. Done ----------------------------------------------------------------------
cat <<EOF

$(printf '\033[1;32m')Installation complete.$(printf '\033[0m')

One last step macOS requires: allow screen recording, so the tracker can see
the poker table.

  1. System Settings > Privacy & Security > Screen & System Audio Recording
     (it is opening now).
  2. Turn on "$APP_NAME". If it is not listed, start the app once first,
     then come back - it appears after its first attempt to read the screen.
  3. Quit and reopen the tracker after allowing it.

Start the tracker from Applications, Launchpad, or the Desktop shortcut.
To move the project folder later, run this installer again from its new place.
Log of this installation: $LOG

EOF
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" 2>/dev/null || true
printf 'Press Return to close this window.'
read -r _
