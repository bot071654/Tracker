PokerHandTracker 1.0.2 — Windows

Update for 1.0.1. Windows x64 only. The tracker is still read-only: it never clicks, bets or automates anything.

What changed
- Round results are now confirmed before they are acted on. A round's result counts as confirmed only when both rows of the
  casino's result panel have been read completely and agree with the table.
- The ANTE/SKIP alert appears once per round, and only after a confirmed result. When a result is not confirmed, the
  tracker intentionally shows no ANTE/SKIP alert. The banner shows RESULT PENDING (still being read) or RESULT UNKNOWN
  (it could not be confirmed) instead. A round that is not confirmed is never given a guessed winner.
- Teach: clearer decision and focus labels, and a scenario left over from an earlier round is cleared.
- Teach dialog: it now shows "On screen (banner)", the Scenario Engine's decision, separately from "Saved flop rule", the
  rule a correction would change. The two are different things and can differ for the same hand; the dialog says so when
  they do.
- Spoken result: the winner is spoken once, and only after the casino's result panel has confirmed it. Before, it was
  spoken as soon as the hand was saved.
- Finished rounds: a final decision for a round that is already over is shown silently, with no chime and no spoken
  decision. The "leaning", "result pending" and "result unknown" wording is still announced.
- Result panel: improved reading of the panel's cards.
- Dealer cards: when the two dealer cards are read as one fused shape, the tracker can now split them. It refuses the
  split when the two halves do not look like card faces (for example two card backs).
- Screen capture converts frames more efficiently.
- Covered table: the tracker's own windows are left out when it looks for the table. A card slot under one of them is
  reported as covered; it is not read as a card and not treated as an empty place. Dealer boxes seen earlier in a hand are
  carried forward only while the cards that were found both times still stand where they stood.
- Saving: a finished hand is saved on a background worker, so a slow save no longer holds up the tracker's reading. The
  saving rules are the same as before. This is covered by automated tests; it has not yet been exercised in a live
  session with the real database and Excel export.
- Changes from an earlier internal build (202610171210) are first documented in this release.
- Unchanged: HTTPS is still required for every non-local address, the backend address precedence is the same, and the
  built-in default is still the production HTTPS API.

Known limitation: some rounds stay pending or unknown
Some rounds are never confirmed, so they produce no ANTE/SKIP alert. This happens, for example, when a dealer card or the
river is not read before the table view goes away. The tracker deliberately leaves such a round unconfirmed rather than
record a result it is not sure of.
How often this happened in our testing: across four validation runs of the final code (practice game), 36 rounds had an
end record. 26 of them were completed and confirmed. Of the other 10, one round completed but stayed unconfirmed and nine
rounds never completed. These are the results we observed in those runs, not a guaranteed operating rate, and they can
differ with your screen, window layout and the table. Do not rely on every round producing an alert.

Privacy note: saved card crops
The setting `debug_save_failures` is enabled (`True`) in the installed configuration and in the packaged default
configuration. While it is on, the tracker can save small card-crop images associated with recognition failures under
`logs/failures` in your data folder. These images are not limited in number or total size. This setting is unchanged in
this release. Be aware of this behavior before you use the application.

Installation
1. Download `PokerHandTracker-Setup.exe`.
2. Verify its SHA-256 against `SHA256SUMS.txt`: `91003DC8FC4B6F24E8F71758D0E80565D74D7745752A98B76DA2D8CCCA7D89D0`
3. Run the installer and follow `INSTALL-WINDOWS.md`. Installing over 1.0.1 keeps your settings and sign-in.

Build
- Version 1.0.2, build `202610201804`, built from commit `dd4a6c0` on `release/windows-1.0.0`.
- Installer size: 52,260,165 bytes.

Release status
- Windows x64 installer. The installer is unsigned, so Windows may display a security warning.
- Clean Windows machine validation is still pending (checks so far ran on a development machine that has Python).
- A complete TLS handshake with the production API was not part of these tests; HTTPS address handling was tested with a
  local logging proxy and a throwaway local backend.
- The self-test of the packaged application has not been run, and a passing result is not claimed for this release.
- This installer has been built but not yet installed or started, so it has not been tested.
- 179 automated tests that need saved whole-screen casino frames were not run against this final code; they are skipped
  when those frames are absent.
- The reading of black suits on the result panel (spade or club) rests on limited evidence: only two real club thumbnails
  were available.
- macOS installers are not available in this release.
