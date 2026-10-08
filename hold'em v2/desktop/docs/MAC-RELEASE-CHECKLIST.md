# macOS release checklist (for the future Mac builds)

**Status today: macOS Apple Silicon — BLOCKED (no Mac build environment). macOS Intel — BLOCKED (no Mac build environment).**
Nothing on macOS has been built, run or tested; `build_macos.sh` has never been executed. Do not describe macOS as supported
until every box below is ticked **on a real Mac of that architecture**, and record the evidence next to each box.

Everything the Windows release proved (secrets audit, bundle scan, self-test baseline, backend contract) is platform-neutral
and already done; what remains is what only a Mac can show.

## 0. What you need

- [ ] An **Apple Silicon** Mac (macOS 11+) for `arm64`, and an **Intel** Mac (macOS 11+) for `x86_64`. Each architecture is built
      natively; the build is deliberately **not** universal2 (see `PokerHandTracker.spec`).
- [ ] Python **3.14** from python.org (includes Tk 8.6) of the **same architecture** as the Mac (`python3 -c "import platform;print(platform.machine())"` prints `arm64` / `x86_64`). Homebrew Python needs `python-tk`.
- [ ] Xcode command line tools (`xcode-select --install`) for `codesign`, `hdiutil`, `xcrun`.
- [ ] For a public release: an Apple Developer Program membership, a **Developer ID Application** certificate in the login keychain, a
      reverse-DNS bundle id (`BUNDLE_ID`), and a notarytool keychain profile (`xcrun notarytool store-credentials`). Without them the
      app is unsigned and users must right-click → Open once; **say so in the release notes, never hide it**.
- [ ] A clean checkout at the release commit (the Windows build recorded `+dirty`: avoid that this time) and the production API URL
      (`https://holdem-api-production.up.railway.app` at the time of writing — confirm it).

## 1. Build (once per architecture)

- [ ] `desktop/build/build_macos.sh --api-url <production url>` (add `SIGN_IDENTITY=…`, `NOTARY_PROFILE=…`, `BUNDLE_ID=…` for a release).
- [ ] Expect to fix small issues on the first run: the script was written without a Mac. Record every change made to it.
- [ ] Output exists: `desktop/build/dist-arm64/PokerHandTracker-AppleSilicon.dmg` / `dist-x86_64/PokerHandTracker-Intel.dmg`, and the `.sha256` file.
- [ ] `verify_bundle.py` printed `VERIFIED` (content scan + the packaged app's own `--selftest`; on a cross-architecture build it runs with `--no-run`, which is **not** proof).
- [ ] `file PokerHandTracker.app/Contents/MacOS/PokerHandTracker` shows the right architecture; `lipo -info` on the OpenCV/NumPy `.so` files agrees (a stray wrong-architecture library crashes at start).

## 2. Signing and notarization (public release only)

- [ ] `codesign --verify --deep --strict --verbose=2 PokerHandTracker.app` passes; `codesign -dv --verbose=4` shows the hardened runtime (`flags=0x10000(runtime)`).
- [ ] The two entitlements in `macos/entitlements.plist` are **unverified**: confirm the notarized app starts, then try removing each one and keep only what is needed.
- [ ] `xcrun notarytool submit … --wait` returns **Accepted**; `xcrun stapler validate` passes; `spctl -a -t open --context context:primary-signature -v PokerHandTracker-*.dmg` says *accepted, source=Notarized Developer ID*.
- [ ] Nothing in the process disables Gatekeeper, SIP or any other macOS protection.

## 3. Clean-Mac test (a Mac with no Python, no Xcode, no project; a new macOS user account is acceptable only if Python/dev tools are not on the system path — a second Mac or VM is better)

Run for **each** architecture, using a **test** account and test backend, never production hand data.

- [ ] Open the `.dmg`, drag the app to Applications, eject. First launch behaves as expected for the signing state (notarized: opens; unsigned: right-click → Open).
- [ ] App starts without Python installed; `PokerHandTracker --selftest` (run from `Contents/MacOS`) passes all checks, including *recognition identical to source baseline* and *TLS trust bundle*.
- [ ] **Screen Recording**: before granting, capture is blank and the app says so; after granting in System Settings → Privacy & Security → Screen & System Audio Recording and restarting the app, capture works. **Accessibility / Input Monitoring are never requested** — if macOS asks for them, that is a bug.
- [ ] **Retina**: card recognition identical on a 2× display (and on an external 1× display). Check the regions / calibration still line up.
- [ ] Data is created under `~/Library/Application Support/PokerHandTracker`, logs under `~/Library/Logs/PokerHandTracker`, cache under `~/Library/Caches/PokerHandTracker`; nothing is written inside the `.app`.
- [ ] Sign in against the **test** backend works; the session is in the **login Keychain** (`PokerHandTracker` item), not in a plain file; restart keeps the session; sign-out removes it.
- [ ] HTTPS to the API works (certifi bundle); an unreachable backend gives the readable "Cannot reach the backend" message and the offline outbox keeps hands.
- [ ] Logs contain no password or token (search the log for the test password and the token).
- [ ] Quit, reopen, reinstall over the top (settings kept), delete the app (nothing left in Applications; data kept in the folders above until the user removes it).
- [ ] No fake hands submitted to production; the production Excel/database untouched.

## 4. Release artifacts and record

- [ ] Final names: `PokerHandTracker-AppleSilicon.dmg`, `PokerHandTracker-Intel.dmg`, each with its SHA-256 (`shasum -a 256`) recorded and re-verified after copying to the release folder.
- [ ] Release folder per architecture contains only: the `.dmg`, `SHA256SUMS.txt`, and a Mac install guide (write `INSTALL-MACOS.md` from `INSTALL-WINDOWS.md`; include Screen Recording steps and the unsigned-app instructions if applicable).
- [ ] Update `README-DESKTOP.md` (supported-systems table), `BUILD-DESKTOP.md` (release-status table) with what was actually verified and on which macOS version/hardware.
- [ ] Reproducibility: build twice with the same `SOURCE_DATE_EPOCH` and compare file hashes inside the `.app` (the Windows build was byte-identical except two container files; see `BUILD-DESKTOP.md`).
- [ ] Do not push or tag until the owner approves.
