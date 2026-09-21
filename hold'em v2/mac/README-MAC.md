# Poker Hand Tracker on a Mac

## Install (once)

1. Unzip `PokerHandTracker-mac.zip` somewhere permanent, e.g. your Documents
   folder. The app runs from that folder, so do not delete it afterwards.
2. Open the `mac` folder and **right-click `Install Poker Hand Tracker.command`
   → Open → Open.** (A plain double-click is blocked the first time because
   the file is not from the App Store.)
3. A Terminal window runs the installation. It may ask for your Mac login
   password once, to install Homebrew. It takes 5–15 minutes the first time.
4. When it finishes, allow **Screen Recording**:
   System Settings → Privacy & Security → Screen & System Audio Recording →
   turn on **Poker Hand Tracker**. Then quit and reopen the tracker.

The installer sets up Homebrew, Python 3.12 with Tk, PostgreSQL 16 (as a
background service), the Python packages, the `poker_tracker` database, and a
**Poker Hand Tracker** app in `~/Applications` with a shortcut on the Desktop.
Running it again is safe. Its log is `mac/install.log`.

## Start it

Open **Poker Hand Tracker** from Applications, Launchpad, or the Desktop.
If it does not open, run it from Terminal to see the error:

```bash
cd "<the unzipped folder>"
./.venv-mac/bin/python app.py
```

Its output is also written to `logs/mac-launcher.log`.

## Known limitations on macOS

- **Retina displays:** calibrate on the Mac (the Windows calibration is not
  carried over) and use **Test Recognition** before trusting live results.
  Screen capture on a Retina screen uses twice as many pixels as screen
  points; this version has not yet been verified on a Mac.
- **The Action Controller** (test-table automation) is Windows-only and stays
  off on a Mac. Hand tracking does not use it.
- Fonts fall back to macOS system fonts, and the ANTE alert has no sound.
- Hands recorded on your Windows PC are in that PC's database and do not move
  over with this folder.

## Uninstall

```bash
rm -rf ~/Applications/"Poker Hand Tracker.app" ~/Desktop/"Poker Hand Tracker.app"
brew services stop postgresql@16
```

Then delete the unzipped folder. Homebrew, Python and PostgreSQL can be removed
with `brew uninstall postgresql@16 python-tk@3.12 python@3.12` (this deletes
the recorded hands).
