# Poker Hand Tracker @VERSION@ for Windows — installation guide

A read-only tracker for a Casino Hold'em table on your own screen. It reads the cards, works out the hands and the winner,
and records each completed round on the team's server. It never clicks, bets or controls anything, and it never sends a
screenshot anywhere.

## What you need

* Windows 10 or Windows 11, **64-bit**.
* An internet connection (the app signs in to the team's server over HTTPS).
* An **e-mail and password given to you by the person who runs the server**. There is no public sign-up.
* About 190 MB of free disk space. **You do not need Python or anything else installed.** No administrator rights are needed.

## Files you were given

| File | What it is |
| --- | --- |
| `PokerHandTracker-Setup.exe` | the installer |
| `SHA256SUMS.txt` | the installer's checksum, to prove the file arrived intact |
| `INSTALL-WINDOWS.md` | this guide |

## 1. Check the file (recommended, 20 seconds)

Open **PowerShell** in the folder that holds the installer and run:

```powershell
(Get-FileHash .\PokerHandTracker-Setup.exe -Algorithm SHA256).Hash
```

The result must be exactly this (upper or lower case does not matter):

```
@SHA256@
```

If it differs, the download is damaged or has been changed: do not run it, and ask for a new copy.

## 2. Install

1. Double-click `PokerHandTracker-Setup.exe`.
2. **Windows may show "Windows protected your PC" (SmartScreen).** This installer is not yet signed with a code-signing
   certificate, so Windows does not recognise the publisher. If the checksum in step 1 matched and you received the file from
   the team, choose **More info → Run anyway**.
3. The first dialog asks whether to install **for you only** (recommended: no administrator prompt) or for all users. Then click
   through the wizard. A per-user install puts the program in `%LOCALAPPDATA%\Programs\Poker Hand Tracker`.
4. Optionally tick **Create a desktop shortcut**. A **Poker Hand Tracker** entry in the Start Menu is always created.
5. Leave **Start Poker Hand Tracker** ticked and click **Finish**.

## 3. First start

1. A **Sign in** window appears. Enter your e-mail and password. The password is sent once to the server and is **not**
   stored on your computer; only a revocable sign-in token is kept, encrypted for your Windows account.
   *Work offline* keeps hands on your computer and sends them the next time you are signed in.
2. Open your Casino Hold'em table and press **Start**. The app finds the cards on the screen by itself. **Calibrate** is only
   needed if the table cannot be found.
3. Press **F1** at any time: version, where your files are, whether you are signed in, how many hands are waiting to be sent,
   **Copy details**, **Open logs folder** and **Sign out**.

The card artwork that ships with the app was learned from an Ezugi Casino Hold'em table. For another provider use **Teach cards**.

## What leaves your computer

Only over HTTPS and only when you are signed in: your sign-in, each **completed hand** (the nine cards, the hand names, the
winner, whether the dealer qualified, a fingerprint of the cards and round timings), and a one-time **device registration** (a
computer name, "Windows x64" and the app version). **No screenshots, card images, files, browser data or window titles are sent.**
The app contains no database address, user name or password.

## Where your files are

Everything the app writes is in **`%LOCALAPPDATA%\PokerHandTracker`** (paste that into the File Explorer address bar):

| Folder | What |
| --- | --- |
| `config` | settings, calibration, scenarios |
| `recognition\templates` | card templates, including cards you taught it |
| `data` | the spreadsheet copy `poker_hands.xlsx` and the queue of hands waiting to be sent |
| `state` | window position, sign-in session (encrypted), device id |
| `logs` | `tracker.log` — it never contains your password or sign-in token |
| `cache`, `debug` | temporary files and failed-read images for troubleshooting |

## Updating

Run the newer `PokerHandTracker-Setup.exe`; it replaces the program and **keeps your settings, taught cards and sign-in**.
The app does not update itself and does not contact anyone to look for updates.

## Uninstalling

**Settings → Apps → Installed apps → Poker Hand Tracker → Uninstall**. At the
end it asks whether to **also delete your settings, calibration, taught cards and logs**. Choose **No** to keep them for a later
reinstall, **Yes** for a complete removal. Before uninstalling you can press **F1 → Sign out** to end your session on the server.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| "Windows protected your PC" | See step 2.2. Only continue if the checksum matched. |
| Antivirus blocks or quarantines the installer | The installer is new and unsigned, so some scanners are cautious. Ask the team; do not disable your antivirus. |
| "Sign-in failed" | Check the e-mail and password. After several wrong attempts in a row, sign-in for that account is paused for about 15 minutes. Ask the team to reset a lost password. |
| "Cannot reach the backend" | Check your internet connection. The app retries by itself; hands recorded meanwhile are kept and sent later (**F1** shows how many are waiting). |
| "Your session has ended" | Sessions expire (30 days) and an administrator can end them. Sign in again. |
| The table is not read | Keep the table fully visible and uncovered by other windows; try **Calibrate**. |
| No voice | Voice uses the Windows speech engine; check your Windows sound output. |

## Asking for help

Send the team what you did, what you expected and what happened, plus the text from **F1 → Copy details** and the newest
`tracker.log` from the `logs` folder. Neither contains your password or sign-in token.
