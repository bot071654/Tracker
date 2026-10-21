PokerHandTracker 1.0.3 — Windows

Update for 1.0.2. Windows x64 only. The tracker is still read-only: it never clicks, bets or automates anything.

What changed
- Faster card reading. The cards on the table are now read side by side on up to four threads instead of one after another. Each
  card is read from its own picture by the same code as before, so every reading is the same one, in the same order. Measured
  offline on a test workload (the same recorded casino frame with video-like noise, source of 1.0.2 against this source,
  alternating runs): the card-reading step took about 35% less time and a whole poll about 12% less. This has not been measured
  in live play, and the gain will differ with your machine and with what else it is running.
- Quicker screen updates. The window now collects the tracker's updates every 50 ms instead of every 200 ms, so the banner and
  status can lag a poll by less. This is an estimate from the design; it has not been measured live. The updates arrive in the
  same order and none is dropped.
- Shadow measurement. The tracker now measures, without acting on it, whether a card's last reading could have been reused when its
  picture had not changed. It reads every card exactly as before and only counts. Every few hundred polls, and when it stops, the log
  gets a `SHADOW_REUSE` line with counts only; no picture, crop or screenshot is ever stored or logged. The setting `shadow_reuse`
  turns it off, and `parallel_reads` turns the side-by-side reading off.
- Nothing about recognition thresholds, card confirmation, the result confirmation, the ANTE/SKIP alert (once per round, only after a
  confirmed result), the provisional wording or the voice has changed.

Known limitation: some rounds stay pending or unknown
Some rounds are never confirmed, so they produce no ANTE/SKIP alert. This happens, for example, when a dealer card or the river is
not read before the table view goes away. The tracker deliberately leaves such a round unconfirmed rather than record a result it is
not sure of. In four validation runs of the 1.0.2 code (practice game), 36 rounds had an end record: 26 were completed and
confirmed, one completed but stayed unconfirmed and nine never completed. These are results we observed, not a guaranteed operating
rate. They were not repeated for 1.0.3.

Privacy note: saved card crops
The setting `debug_save_failures` is enabled (`True`) in the installed configuration and in the packaged default configuration.
While it is on, the tracker can save small card-crop images associated with recognition failures under `logs/failures` in your data
folder. These images are not limited in number or total size. This setting is unchanged in this release. Be aware of this behavior
before you use the application.

Installation
1. Download `PokerHandTracker-Setup.exe`.
2. Verify its SHA-256 against `SHA256SUMS.txt`: `<to be filled in when the installer is built>`
3. Run the installer and follow `INSTALL-WINDOWS.md`. Installing over 1.0.2 or 1.0.1 keeps your settings and sign-in.

Build
- Version 1.0.3, build `<to be filled in>`, built from commit `<to be filled in>` on `release/windows-1.0.0`.
- Installer size: `<to be filled in>`.

Release status
- Windows x64 installer. The installer is unsigned, so Windows may display a security warning.
- Clean Windows machine validation is still pending (checks so far ran on a development machine that has Python).
- A complete TLS handshake with the production API was not part of these tests; HTTPS address handling was tested with a local
  logging proxy and a throwaway local backend.
- The packaged application's headless self-test was run on the build folder before packaging, not on the installed copy. Live play
  has not yet been tested with this version.
- 179 automated tests that need saved whole-screen casino frames were not run against this code; they are skipped when those frames
  are absent.
- Saving a finished hand on a background worker (added in 1.0.2) has not yet been exercised in a live session with the real database
  and Excel export.
- The reading of black suits on the result panel (spade or club) rests on limited evidence: only two real club thumbnails were
  available.
- macOS installers are not available in this release.
