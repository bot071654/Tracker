PokerHandTracker 1.0.3 — Windows

Update for 1.0.2. Windows x64 only. The tracker is still read-only: it never clicks, bets or automates anything.

What changed
- A pre-round SKIP now stays the decision for the whole hand it was issued for. Before, the SKIP ROUND alert disappeared when the
  player's cards were dealt, and the banner and the voice then said WAIT and, after the flop, LEANING PLAY for the same hand. Now:
  * Before the first card is dealt the SKIP stays up, even after the alert's 25-second timeout. It has no automatic expiry before
    the deal.
  * From the deal until the hand is over the SKIP is the only decision shown and spoken, through the result as well. The result
    appears in the reason line beside it; the decision itself is not replaced.
  * A RESULT PENDING or RESULT UNKNOWN notice about that hand is shown as a notice, never as a decision. The SKIP governs again
    when that hand's cards come back.
  * The previous hand's SKIP is released only when a completely different pair of player cards is confirmed (the card memory's own
    rule: the same new pair on consecutive polls). While that is being confirmed, nothing from the old hand is shown or spoken,
    so the old SKIP never flashes on the new hand.
  * Clicking the banner while it shows the SKIP dismisses it for that hand: it is not shown again, and no other decision takes
    its place. A new hand gets its own decision.
  * Pausing and resuming keeps the SKIP; stopping and starting tracking clears it.
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
- Nothing about recognition thresholds, card confirmation, the result confirmation, or when the ANTE/SKIP alert is first issued (once
  per round, only after a confirmed result) has changed. The SKIP binding above changes only what happens to a SKIP after it has
  been issued.

Known limitations of the SKIP binding
- A pair that is only partly read, or a new pair that shares a card with the previous one, is not treated as a new deal, so the
  previous hand's SKIP can stay on the banner for the new hand.
- A SKIP shown before the deal has no automatic expiry: it stays until the hand is dealt (and then follows the rules above) or until
  tracking is stopped and started.

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
2. Verify its SHA-256 against `SHA256SUMS.txt`: `99070F91CAC044ECD386154A82C7FC245BA9C2820FE47DBF30D8774D87B6B639`
3. Run the installer and follow `INSTALL-WINDOWS.md`. Installing over 1.0.2 or 1.0.1 keeps your settings and sign-in.

Build
- Version 1.0.3, build `202611090517`, built from commit `7794281` on `release/windows-1.0.0`.
- Installer size: 52,283,776 bytes.

Release status
- Windows x64 installer. The installer is unsigned, so Windows may display a security warning.
- Clean Windows machine validation is still pending (checks so far ran on a development machine that has Python).
- A complete TLS handshake with the production API was not part of these tests; HTTPS address handling was tested with a local
  logging proxy and a throwaway local backend.
- Tests run on this source before packaging (development machine, scratch data folder, no network): the complete suite, 2,395 passed,
  0 failed, 191 skipped (180 that need saved whole-screen casino frames, which are deliberately not part of the repository; 7 that need
  a PostgreSQL server; 3 that need a Tk display and skipped when Tk would not start; 1 for a missing samples folder). The build's own
  step ran 112 tests (all passed), and the packaged application's 10 headless self-test checks all passed on the build folder.
- The 51 hand-binding tests passed in 16 of the 17 complete runs we made. One run failed once (`test_notice_to_confirmed_different_pair`,
  row R7) and showed twelve Tk "main thread is not in main loop" warnings; it did not recur in more than 20 reruns, including under
  heavy CPU load and with the garbage collector stressed. The cause is not established. It is probably the test window's Tk
  variables being cleaned up on another thread, but that is a guess.
- The installer has not been installed and run on a completely fresh Windows machine, and the same-hand SKIP behavior has not been
  verified on a live table: the tests drive the real window, banner and voice with the tracker's own update structures, not a casino.
- Saving a finished hand on a background worker (added in 1.0.2) has not yet been exercised in a live session with the real database
  and Excel export.
- The reading of black suits on the result panel (spade or club) rests on limited evidence: only two real club thumbnails were
  available.
- macOS installers are not available in this release.
