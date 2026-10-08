# Building the desktop app

The desktop app is the original Python application (`app.py`), unchanged in behaviour, packaged so users need no Python.
This file is for developers. Users read [README-DESKTOP.md](README-DESKTOP.md).

## Release status: what has and has not been verified

**Summary for version 1.0.0**

| Platform | Status |
| --- | --- |
| Windows x64 | **1.0.0 release is ready.** Final installer built from the clean commit `a89f561` (build `202610081033`) in a clean worktree and verified - see [Windows 1.0.0 release verification](#windows-100-release-verification). **The installer is unsigned** (SmartScreen will warn). |
| Clean Windows machine validation | **Pending.** No clean VM or PC was available; the checks so far ran on the development machine. |
| macOS Apple Silicon | **BLOCKED - no Mac build environment.** |
| macOS Intel | **BLOCKED - no Mac build environment.** |

Details:

| Item | Status |
| --- | --- |
| Python test suite (original 55 files) with the packaging changes | Passing |
| New tests: paths, API client, sign-in, import audit, backend (accounts, hands, migrations, migrate-on-start), desktop-to-backend end to end | Passing against a real PostgreSQL 18 (throwaway cluster) and the real API process |
| **Windows build** (PyInstaller 6.22.3, Python 3.14.6 x64) and **`PokerHandTracker-Setup.exe`** (Inno Setup 6.7.3), commit `a89f561`, build `202610081033` | **Built from a clean worktree** (committed `tracker.py`, no `core/`). 112 build tests pass; the bundle is verified by running the packaged `.exe --selftest` (10 of 10 checks, including recognition identical to the source baseline) |
| Installed program on the *development* machine (install, self-test, first-run data folders, sign-in, restart, GUI start/close, no development Python loaded, uninstall, reinstall) | **45 of 45 checks pass** on the final installer, against a throwaway local backend. A development-machine proxy, **not** the clean-machine test |
| **Windows clean-machine test** | **PENDING (not done):** no clean VM or spare PC was available, and this machine has Python. `clean_machine_check.ps1` automates the whole test for whoever has one |
| **macOS Apple Silicon / Intel** | **BLOCKED - no Mac build environment; NOT BUILT, NOT TESTED:** `build_macos.sh` has never run |
| **Railway** | **Deployed** (`holdem-api`), migrated, and exercised from the packaged desktop app over HTTPS |
| Windows release folder (installer, checksum, user guide) | **Assembled and verified** from the clean-commit installer: exactly three files in `desktop\build\release\PokerHandTracker-1.0.0-windows-x64\` |
| Code signing, macOS notarization | Not done: need the owner's certificates. The Windows installer is `NotSigned` (SmartScreen will warn) |

Nothing in this list is claimed to work until the corresponding row says it was run.

## How the app is split

```
Desktop app (this package)  ──HTTPS──▶  Railway FastAPI (backend/)  ──▶  Railway PostgreSQL
  desktop/api_client.py                   backend/app/v1.py   (/api/v1/*)
  desktop/backend.py (db.* facade)        backend/migrate.py  (SQL migrations)
  desktop/session.py (token storage)      backend/manage.py   (accounts)
```

* `database/db.py` is the module the tracker, window and Excel mirror already call. In *remote mode* six of its functions hand off
  to `desktop/backend.py` (same arguments, same return shapes, same `DatabaseError`), so `tracker.py`, `app.py`'s logic and the
  recognition code did not have to change. Remote mode is **always on in a packaged build**, and is on from source when
  `POKER_TRACKER_API_URL` (or `api_url` in `config/app.json`) is set. From source with neither, the old direct-database
  behaviour is unchanged (development only).
* The packaged build contains **no PostgreSQL driver, no `.env` loader and no database setting**. Only the backend process
  (`POKER_TRACKER_BACKEND=1`, set by `backend/app/main.py`) talks to PostgreSQL.

## Changes to the existing application

Required for packaging; everything else is new files. Recognition, capture, state tracking and thresholds are untouched.

| File | Change | Why |
| --- | --- | --- |
| `config/settings.py` | `ROOT`, `CONFIG_PATH`, `LOG_PATH`, `TEMPLATE_DIR`, `EXCEL_PATH` come from `desktop/paths.py`; `RESOURCE_ROOT` added; logging gets a secret-redacting filter; first-run seeding when relocated | Nothing may be written beside the executable. From source every value is identical to before (a test asserts it). |
| `poker/scenarios.py`, `poker/scenario_engine.py`, `ui/window_geometry.py` | the three path constants use `desktop/paths.py` | same |
| `database/db.py` | `psycopg` and `dotenv` imports are optional; six functions delegate to `desktop/backend.py` in remote mode | the build has no DB driver or credentials; the HTTPS API replaces direct writes |
| `app.py` | `main()` calls `bootstrap.startup(root)` before and `bootstrap.finish(app, root)` after building the window | start-up banner in the log, sign-in, version line, F1 About |
| `backend/app/main.py` (untracked, new in this work tree) | marks the process as the backend, mounts `/api/v1`, CORS origins and the anonymous legacy routes become configurable (defaults unchanged) | the authenticated API |

Behaviour differences a user can see: in a packaged build the first start asks you to sign in; hands that cannot be sent are kept in
an on-disk outbox; the messages that say "PostgreSQL unavailable" still read that way even though the backend is what is being
checked (the underlying text says "Backend…"), and the F1 window exists.

## Choosing the packager: PyInstaller, not Nuitka

The dependency tree is OpenCV (large native wheels), NumPy, mss, Tkinter, openpyxl and, on Windows, pyttsx3 → comtypes/pywin32 (SAPI
speech, which is loaded by name at run time).

* **PyInstaller** collects wheels' native libraries and has maintained hooks for Tk, NumPy, OpenCV and pywin32; run-time-imported
  modules are listed in the spec's `hiddenimports`; one spec serves Windows and macOS; macOS `.app` bundles and code signing are
  first-class. A build is minutes, and a failure points at a missing file.
* **Nuitka** compiles Python to C and can give faster start-up and a smaller attack surface, but it needs a C compiler on every
  build machine, builds take much longer, and OpenCV + Tk + COM plugins need per-package configuration that is much less
  exercised. Its main advantage (speed of pure-Python code) does not matter here: the hot loops are inside OpenCV.

Output is **one-folder** (`onedir`) rather than one-file: no per-launch unpacking, far fewer antivirus false positives. UPX is off.
macOS gets **one build per architecture** instead of `universal2`, because every compiled dependency would have to be a true
fat binary; building on each native architecture does not depend on that.

## Building

Versions are pinned in `desktop/build/requirements-desktop.txt` to what the recognition thresholds and tests were tuned on:
Python 3.14, OpenCV 5.0.0.93, NumPy 2.5.3, mss 10.2.0, openpyxl 3.1.5, pyttsx3 2.99. Use that Python version. After the first
good build, pin the exact PyInstaller version in `requirements-build.txt` and commit it so builds repeat.

### Windows x64 (needs a Windows x64 machine)

Requirements: Python 3.14 x64, Git, [Inno Setup 6](https://jrsoftware.org/isinfo.php) (for the installer), optionally a code-signing
certificate and `signtool`.

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build\build_windows.ps1 `
    -Python "C:\Path\to\Python314\python.exe" `
    -ApiUrl "https://your-api.up.railway.app"        # public address of the backend, baked in as the default
# optional: -BuildNumber 42  -SkipTests  -SkipInstaller  -SignToolArgs "/sha1 <thumbprint> /fd sha256 /tr http://timestamp.digicert.com /td sha256"
```

Result in `desktop\build\dist\`: `PokerHandTracker\` (the verified folder), `PokerHandTracker-Setup.exe` and its `.sha256`.
The script stops at the first failure. Its steps: build venv → pinned install → tests → stage defaults (the developer's screen
calibration is stripped out) → PyInstaller → `verify_bundle.py` (content scan for secrets/forbidden libraries, then runs the
packaged `.exe --selftest`, which re-recognises ten recorded live card images and requires results identical to the source
baseline in `desktop/selftest_golden.json`) → Inno Setup.

### macOS (needs a Mac; build each architecture on a Mac of that architecture)

Requirements: Xcode command-line tools, Python 3.14 **with Tk** for the architecture you build (python.org universal installer or
Homebrew `python-tk`), Git. For release: an Apple Developer ID certificate, and a notarytool keychain profile.

```bash
# Apple Silicon Mac
BUNDLE_ID=com.yourcompany.pokerhandtracker desktop/build/build_macos.sh --python /path/to/python3.14 --api-url https://your-api.up.railway.app
# Intel Mac (same command; the architecture is the machine's). Output: desktop/build/dist-<arch>/PokerHandTracker-AppleSilicon.dmg or -Intel.dmg
# release: add SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=<profile> in front
```

macOS notes: **Screen Recording** is a user permission, never an entitlement, and cannot be granted by the app — the README explains
how the user grants it. The Retina capture path (mss returns physical pixels, Tk reports points) is **unverified on a real Mac** —
calibrate on the Mac and use *Test Recognition* before trusting live results. Gatekeeper and notarization are described in
`build_macos.sh`; without a Developer ID the app is unsigned and the user must right-click → Open. Nothing here disables or
works around macOS protections.

### Versioning

`desktop/version.py` holds `VERSION` (edit it for a release). The build number and git commit are written to
`desktop/build_info.json` by `prepare_stage.py`. The app shows `Poker Hand Tracker 1.0.0 (build N) - Windows x64` in its start-up
message, in the log, in `--version` (`<logs>/version.txt`) and in the F1 window.

### Command-line switches of the packaged app

`PokerHandTracker.exe --version`, `--selftest <file.json>` (exit code 0/1), `--diagnostics <file.txt>`. A windowed program has no
console, so they write files.

## The backend

`backend/` is a FastAPI app that reuses `database/db.py` for all SQL on hands (one duplicate rule: the unique fingerprint).

### Authentication and data

* Accounts live in the backend's database (`users`); passwords are scrypt-hashed; there is **no self-registration** unless
  `REGISTRATION_ENABLED=1`. Create users with `python -m backend.manage create-user a@b.com` (password prompted, never on the
  command line).
* A sign-in returns a random token (30 days, `SESSION_DAYS`); the database stores only its SHA-256. Five failed sign-ins per
  e-mail in 15 minutes are refused with 429 (per e-mail, not per address: behind a proxy the address can be varied by the client).
* Each installation registers once and receives a server-issued device id (`devices`). Hands record who first reported them
  (`poker_hands.reported_by_user_id/_device_id`). The hand fingerprint stays globally unique: two people watching the same round
  produce one row.
* `POST /api/v1/hands` accepts only the defined fields, validates every card, **recomputes the hand from the nine cards and
  rejects a record whose fingerprint, winner or qualification disagree**, and then stores it with `ON CONFLICT DO NOTHING`.

### Migrations

```bash
python -m backend.migrate     # applies database/schema.sql (baseline, idempotent) then backend/migrations/NNNN_*.sql, once each
```

### Deploying on Railway

Railway variables for the API service (the PostgreSQL plugin supplies the `POSTGRES_*` values; set `POSTGRES_SSLMODE=require`
if the connection needs TLS):

| Variable | Value |
| --- | --- |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DATABASE`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE` | from the Railway PostgreSQL service — **only on the API service** |
| `AUTO_MIGRATE` | `1` (see below) |
| `LEGACY_ANONYMOUS_API` | `0` once nothing but the desktop app uses the service (turns off the old unauthenticated `/api/hands`). **Set to `0` on the production service on 2026-10-08.** |
| `CORS_ALLOW_ORIGINS` | `none` for a desktop-only service (no browser origin allowed); unset means `*`, which only the old Chrome-extension demo needs. **Set to `none` on production.** |
| `REGISTRATION_ENABLED` | leave off; create users with `backend.manage` |

**Deploy with the minimal bundle**, not the whole working tree (the tree holds other uncommitted work, logs and a `.env`):

```bash
python -m backend.make_deploy_bundle <some-folder>          # 23 files: backend/, database/, two poker/ modules, three desktop/ modules
railway up <some-folder> --path-as-root --service holdem-api --detach
```

Set `AUTO_MIGRATE=1` on the service: the API then applies pending migrations itself when it starts (and refuses to start if one fails),
so a deploy can never serve `/api/v1` against an unmigrated database. (A start command written in the bundle's `railway.json` was
**not** honoured by this service, whose own start command took precedence, so the migration is done by the API, not the start command.)
Health check path: `/api/v1/health` (returns `ok: false, "has not been migrated"` until migrated).
Create the first user once, from a machine with the Railway CLI, using the deployed code:
`NEW_USER_PASSWORD=... railway run --service holdem-api -- python -m backend.manage create-user you@example.com` (run from the bundle folder
with `PYTHONPATH` set to it). `.railway/railway.ts` still describes the old start command; `railway config apply` would reset it.
The rate limiter is in memory per instance; run one instance, or put a shared limiter in front, if you scale out.

## Security review

* **No secrets in the package.** The bundle holds no database setting and no driver; `verify_bundle.py` fails the build if a `.env`, a
  credential-looking string, a log, a spreadsheet, the developer's calibration, an automation library or a database driver is
  inside it. The backend address baked in is public.
* **Transport.** HTTPS is required for every address except `localhost` (development); the client verifies certificates and, on
  macOS, uses `certifi` when available.
* **Local session.** Windows: DPAPI-encrypted file. macOS: login Keychain via the system `security` tool, falling back to a
  `0600` file. The password is never stored. (On macOS the token is passed to `security` as an argument for a moment — a known
  limitation of that tool; the token is revocable.)
* **Logs.** A filter scrubs bearer tokens, passwords, secrets and connection strings from every log handler (tested). Responses
  never include database error text.
* **Backend.** Strict input models (unknown fields rejected), parameterised SQL only, no endpoint accepts SQL or files, scrypt
  passwords, hashed tokens, constant-time checks, equal timing for unknown user and wrong password.
* **Installer.** Per-user install, no administrator rights, no services, nothing written outside `{app}` and the user's data folder.
* **Open items.** The old `/api/hands` and `/api/health` are unauthenticated and CORS `*` by default (kept so the Chrome-extension
  demo keeps working) — switch them off in production. Code signing and macOS notarization need the owner's certificates.

## Tests

```bash
python -m pytest tests/test_desktop_paths.py tests/test_api_client.py tests/test_desktop_bootstrap.py -q      # no database needed
python -m pytest tests/test_backend_api.py tests/test_desktop_remote.py -q                                    # needs PostgreSQL binaries
```

The backend tests start a **throwaway** PostgreSQL cluster in a temporary folder (from the installed binaries; set `PG_BIN` to their
folder if needed), run the real API in its own process against it, and never read the project's `.env`. They skip when PostgreSQL
is not installed. The original suite is run in parts, as before (a single `pytest tests` process can crash on Windows inside
psycopg's garbage collection; that is an environment fault, not a failure). `test_database_config.py` has two tests that fail
when a developer's local `.env` sets `POSTGRES_SSLMODE` (the `.env` leaks into the process); they pass without it.

## Windows 1.0.0 release verification

Run on 2026-10-08 on the **final installer**, built from the clean commit `a89f561` in a clean git worktree (the committed `tracker.py`, no
`core/`, no uncommitted changes): `PokerHandTracker-Setup.exe`, 52,217,776 bytes, build `202610081033`, SHA-256
`011ACBE7A2078E45C46133878B5D26A097878A04DAED458D614621CABA1E762D`. Assemble the distribution folder with
`desktop\build\make_release_folder.ps1` (run from the worktree that built the installer).

An earlier installer, build `202610080800` (SHA-256 `DCC8551F...83E8`), was built from a working tree that also contained the uncommitted
`tracker.py` and `core/`. It is **superseded and is not a release artifact**; do not distribute it.

| Check | Result |
| --- | --- |
| Checksum: file == the `.sha256` the build wrote == the copy in the release folder (`sha256sum -c` also passes) | Pass |
| Installed tree == the scanned bundle: 1,196 files, every SHA-256 identical; the only extras are the uninstaller's two files | Pass |
| Secrets: every value in the developer's `.env` searched for (UTF-8 and UTF-16) in all 1,196 files - password, host and user: **no hit**; private keys, `postgres://user:pw@`, `DATABASE_URL`, Railway proxy/internal hosts, token patterns: none. Three short generic words (the SSL mode, the database name, `127.0.0.1`) occur inside ordinary libraries/the API URL; they are not secrets | Pass |
| No `.env`, spreadsheet, log, key or developer-calibration file; no developer user name or e-mail in any file; no psycopg/dotenv/automation libraries | Pass |
| Production API address: `desktop\api_defaults.json` = `https://holdem-api-production.up.railway.app`; the installed app's `--diagnostics` and `--backend-check` (no credentials, health only) reach it over HTTPS with the shipped CA bundle: database up, schema `0002_hand_attribution` | Pass |
| No Python needed: the installed exe runs with `PATH=System32` only and no `PYTHON*`; 0 modules loaded from the development Python (the embedded `python314.dll` is the app's own runtime) | Pass |
| Data location: first run creates only `%LOCALAPPDATA%\PokerHandTracker\{config,data,logs,debug,state,cache,recognition}`; nothing is written to the install folder or the working directory | Pass |
| Logs: the report and `tracker.log` of a real sign-in contain neither the password nor the session token; the stored session is DPAPI-encrypted | Pass |
| Installer/uninstaller: per-user (no admin), Start Menu entry, optional desktop shortcut (off by default), Settings > Apps entry `Poker Hand Tracker 1.0.0`, silent uninstall keeps the data, reinstall keeps settings and sign-in, final uninstall leaves no program folder, shortcut or registry entry | Pass |
| Version: `1.0.0 (build 202610081033, commit a89f561)` in the installer, the uninstall entry, `--version`, `--diagnostics`, the log and the F1 window | Pass |
| Reproducibility: **recompiling the installer from the final bundle is byte-identical** (same SHA-256). For the bundle itself (measured on the earlier build `202610080800` with the same toolchain; not repeated for the final build): a rebuild from the same inputs gave 1,194 of 1,196 files byte-identical; the other two (`PokerHandTracker.exe`, `_internal\base_library.zip`) differ only in container metadata (the zip's entries are identical). With `SOURCE_DATE_EPOCH` set, the exe is byte-identical between builds; `base_library.zip` still differs | Pass, with that exception |
| Build provenance: the bundle contains the committed `tracker.py` and **no** `core.*` module; the build tree was clean (`commit a89f561`, no `+dirty`) | Pass |
| Build tests (desktop paths, API client, sign-in, card recognition) in the clean worktree: 112 passed. Packaged `--selftest`: 10 of 10. Install/uninstall/reinstall harness on the dev machine: 45 of 45 | Pass |

Known and accepted for 1.0.0: `PokerHandTracker.exe` carries no Windows version resource (Explorer > Properties > Details is empty; the
installer and the app itself show the version); the installer is unsigned; no clean-machine run was possible (below).

## Clean-machine test (Windows)

The quickest way: copy `PokerHandTracker-Setup.exe` and `desktop\build\clean_machine_check.ps1` to the clean machine and run

```powershell
powershell -ExecutionPolicy Bypass -File clean_machine_check.ps1 -Installer .\PokerHandTracker-Setup.exe -Email you@example.com -Password 'your password'
```

It needs no Python, refuses to call a machine clean if it finds Python, and writes `clean_machine_check.log`. It never clicks anything and submits no
hands. The manual checklist it automates:

On a fresh Windows 10/11 x64 VM with no Python and no repository:

1. Run `PokerHandTracker-Setup.exe` (note any SmartScreen prompt). Check Start Menu entry, optional desktop shortcut, install folder.
2. Launch. Confirm the sign-in dialog; wrong password gives a clear message; correct sign-in reaches the main window; the message
   line shows the version and your e-mail.
3. `PokerHandTracker.exe --selftest out.json` → all PASS (recognition identical to baseline).
4. Open a **controlled local test page** (project fixtures / demo), Start, confirm cards are read and a completed hand reaches the
   backend (check the database through the backend, not directly).
5. Check `%LOCALAPPDATA%\PokerHandTracker` (config, logs, data, state) and that nothing was written under the install folder.
6. Disconnect the network, complete a hand, reconnect: F1 shows it waiting, then delivered. Quit and relaunch: still signed in.
7. Uninstall (answer No to deleting data), reinstall: settings and sign-in survive. Uninstall again, answer Yes: the data folder is gone.

Repeat the equivalent on an Apple Silicon Mac and an Intel Mac, including granting Screen Recording and a Retina display check.
Record the date, OS build and result of each step; do not report a platform as working until the list was run on it.
