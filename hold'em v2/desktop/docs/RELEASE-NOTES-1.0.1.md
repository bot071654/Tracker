PokerHandTracker 1.0.1 — Windows

Maintenance release for 1.0.0. Windows x64 only.

What changed
- Fixed: `PokerHandTracker.exe --backend-check` showed a modal "Unhandled exception in script" dialog when the configured
  backend address was refused by the HTTPS rules (for example `POKER_TRACKER_API_URL=http://example.com`). It now exits
  with code 1 and writes a report that says why. An unexpected error in any support switch (`--version`, `--selftest`,
  `--backend-check`, `--diagnostics`) now exits with code 2 and is written to the output file instead of opening a dialog.
- Unchanged: HTTPS is still required for every non-local address, the backend address precedence (environment variable,
  then your `app.json`, then the built-in default) is the same, and the built-in default is still the production HTTPS API.
  Normal start-up, sign-in and recording are unchanged.
- Only affects the support switches; the application window is not changed.

Installation
1. Download `PokerHandTracker-Setup.exe`.
2. Verify its SHA-256 against `SHA256SUMS.txt`:
   `7105763841FBAB765A37F4534D023BB5F0A09BB5587C3BDEDA0207328E5B77D8`
3. Run the installer and follow `INSTALL-WINDOWS.md`. Installing over 1.0.0 keeps your settings and sign-in.

Build
- Version 1.0.1, build 202610090631, built from the clean commit 4e35992 on `release/windows-1.0.0`.
- Installer size: 52,211,228 bytes.

Release status
- Windows x64 installer available. The installer is unsigned, so Windows may display a security warning.
- Clean Windows machine validation is still pending (checks so far ran on a development machine that has Python).
- A complete TLS handshake with the production API was not part of these tests; HTTPS address handling was tested with a
  local logging proxy and a throwaway local backend.
- macOS installers are not available in this release.
