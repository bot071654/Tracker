# Poker Hand Tracker — desktop app

A read-only tracker for a Casino Hold'em table on your own screen. It reads the cards, works out the hands and the winner,
and records each completed round to the team's shared database **through a secure web service**. It never clicks, bets or
controls anything, and it never sends a screenshot anywhere.

> **Release status (version 1.0.0).**
> * **Windows x64 1.0.0 release is ready.** The final installer was built from the clean commit `a89f561` (build 202610081033) and
>   passes the checks listed in BUILD-DESKTOP.md. **The installer is unsigned**, so Windows SmartScreen will warn (see the install steps).
> * **Clean Windows machine validation: pending.** No clean VM or spare PC was available; the checks so far ran on the development
>   machine, which has Python. Run `desktop/build/clean_machine_check.ps1` on a clean Windows machine to complete it.
> * **macOS Apple Silicon: BLOCKED — no Mac build environment. macOS Intel: BLOCKED — no Mac build environment.** Nothing has been
>   built or tested for macOS: treat the macOS instructions below as the intended behaviour, not a promise.
> See [BUILD-DESKTOP.md](BUILD-DESKTOP.md#release-status-what-has-and-has-not-been-verified) for exactly what has been verified.
> Step-by-step instructions for end users: [desktop/docs/INSTALL-WINDOWS.md](desktop/docs/INSTALL-WINDOWS.md). Future Mac builds: [desktop/docs/MAC-RELEASE-CHECKLIST.md](desktop/docs/MAC-RELEASE-CHECKLIST.md).

## Supported systems

| System | Status |
| --- | --- |
| Windows 10 / 11, 64-bit (x64) | **1.0.0 ready** (build 202610081033, commit a89f561; installer unsigned); clean-Windows-machine validation pending |
| macOS 11 or later, Apple Silicon (M1/M2/M3…) | **BLOCKED — no Mac build environment** (planned: `PokerHandTracker-AppleSilicon.dmg`; not built, not tested) |
| macOS 11 or later, Intel | **BLOCKED — no Mac build environment** (planned: `PokerHandTracker-Intel.dmg`; not built, not tested) |

You do **not** need to install Python, pip or any library. Everything the app needs is inside it.

## Install

### Windows
1. Download `PokerHandTracker-Setup.exe` and run it. It installs for your own user account — no administrator rights needed
   (choose "all users" in the first dialog if you want that).
2. If Windows SmartScreen says "Windows protected your PC", the installer has not been code-signed yet: choose **More info →
   Run anyway** only if you got the file from the team. (A signed release will not show this.)
3. Pick whether to add a desktop shortcut. A Start Menu entry **Poker Hand Tracker** is always created.

### macOS (once the packages exist)
1. Open the `.dmg` and drag **Poker Hand Tracker** onto **Applications**.
2. The first time, if macOS says the app "cannot be opened because the developer cannot be verified", the build is not
   notarized: right-click the app → **Open** → **Open**. A notarized release opens normally.
3. Grant **Screen Recording** when asked (next section).

## First launch

1. The app asks you to **sign in** with the e-mail and password the team gave you. (Accounts are created by the person who
   runs the server; there is no public sign-up.) You can choose **Work offline**: hands are then kept on your computer and sent
   the next time you are signed in.
2. If this build was not given a server address, it asks for one once (it must start with `https://`).
3. Open your Casino Hold'em table and press **Start**. The app finds the cards on the screen by itself. **Calibrate** is only a
   fallback for frames where the table cannot be found.
4. Press **F1** at any time for the About window: version, where your files are, whether you are signed in, how many hands are
   waiting to be sent, **Copy details**, **Open logs folder** and **Sign out**.

The card artwork shipped with the app was learned from an **Ezugi Casino Hold'em** table. For another provider, teach the app
its cards (**Teach cards** in the app) — your taught cards are kept across updates.

## Permissions the app needs

| Platform | What | Why |
| --- | --- | --- |
| Windows | Nothing special | It reads the pixels of your screen like a screenshot tool does. |
| macOS | **Screen Recording** — System Settings → Privacy & Security → Screen & System Audio Recording → turn on *Poker Hand Tracker*, then quit and reopen the app | macOS requires it before any app may read other windows' pixels. Without it the capture is blank. |
| macOS | **Accessibility / Input Monitoring: not needed** | The app never sends clicks or key presses. If anything ever asks for these, do not grant it and tell the team. |

## What is sent to the server

Only these, over HTTPS, and only when you are signed in:

* your sign-in (e-mail and password) — the password is sent once and never stored; only a revocable session token is kept;
* each **completed hand**: the nine cards, both hand names, the winner, whether the dealer qualified, a fingerprint of the cards
  and the round timings;
* a one-time **device registration** (a name for this computer, Windows/macOS, x64/arm64, the app version) so the server can tell
  installations apart. It is **not** a hardware fingerprint; the id is issued by the server.

It does **not** send screenshots, card images, files, browser data, window titles, or anything else. The database itself is only
reachable from the server: this app contains no database address, user name or password.

## Where your files are

| What | Windows | macOS |
| --- | --- | --- |
| Settings, calibration, scenarios | `%LOCALAPPDATA%\PokerHandTracker\config` | `~/Library/Application Support/PokerHandTracker/config` |
| Taught card templates | `%LOCALAPPDATA%\PokerHandTracker\recognition\templates` | `~/Library/Application Support/PokerHandTracker/recognition/templates` |
| Spreadsheet mirror, offline outbox | `%LOCALAPPDATA%\PokerHandTracker\data` | `~/Library/Application Support/PokerHandTracker/data` |
| Window position, sign-in session, device id | `%LOCALAPPDATA%\PokerHandTracker\state` | `~/Library/Application Support/PokerHandTracker/state` |
| **Logs** (`tracker.log`, rotated) | `%LOCALAPPDATA%\PokerHandTracker\logs` | `~/Library/Logs/PokerHandTracker` |
| Cache | `%LOCALAPPDATA%\PokerHandTracker\cache` | `~/Library/Caches/PokerHandTracker` |
| Failed-read images for troubleshooting | `…\PokerHandTracker\debug` and `…\logs` | `…/Application Support/PokerHandTracker/debug` |

The sign-in session is stored encrypted for your Windows account (DPAPI), or in the macOS login Keychain. Nothing important is
written next to the program itself.

## Troubleshooting

* **"Not signed in" / hands are not being recorded** — press F1 to see the state; sign in again. Hands recorded while signed out
  or offline are kept and sent automatically later (the F1 window shows how many are waiting).
* **"Cannot reach the backend"** — check your internet connection. The app retries by itself; nothing is lost.
* **"Your session has ended"** — sessions expire (30 days by default) and an administrator can end them. Sign in again.
* **The table is not read** — make sure the table is fully visible and not covered by another window; try **Calibrate**. On a Mac,
  check Screen Recording is on and that you restarted the app after turning it on.
* **No voice** — voice announcements use the Windows speech engine. To turn them off set `"voice_enabled": false` in `config\config.json` (see the folder table above).
* **Anything else** — press F1 → **Copy details**, and send them with the newest `tracker.log` (it never contains your password
  or session token).

## Report a problem

Send the team: what you did, what you expected, what happened, the **F1 → Copy details** text, and `tracker.log` from the logs
folder above.

## Uninstall

* **Windows** — Settings → Apps → *Poker Hand Tracker* → Uninstall (or the Start Menu entry). It asks whether to also delete
  your settings, calibration, taught cards and logs; **No** keeps them for a later reinstall. A silent uninstall always keeps them.
* **macOS** — drag *Poker Hand Tracker* from Applications to the Trash; to remove your data too, delete the folders in the table
  above and the *PokerHandTracker* item in Keychain Access.

Signing out (F1 → **Sign out**) ends your session on the server and removes it from this computer.
