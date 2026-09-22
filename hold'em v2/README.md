# Poker Hand Tracker

A local desktop application that watches a **Casino Hold'em** table on your own
screen, reads the cards, works out both hands and who won, and records every
completed round to PostgreSQL and Excel.

It is a **data collection tool**. It reads pixels and writes rows. It does not
click, bet, or interact with the casino in any way, and it gives no advice on
how to play.

```
Dealer      8D QD              -> Dealer hand: Two Pair
Community   2D QH 3D 3H 6S
Player      8S 9C              -> Player hand: Pair

                                  Winner: Dealer
```

For every round it records the nine cards, both evaluated hands, the winner,
whether the dealer qualified, and how long the round took.

---

## Read this first if someone sent you this folder

Three things in here are specific to the machine and account it came from, and
you will need to redo them:

1. **Calibration.** `config/config.json` holds screen coordinates measured on a
   1920×1080 screen with the browser in one particular position. They will not
   match your screen. **You must run Calibrate before the tracker will read
   anything** (section 5).
2. **Database.** You do **not** make your own. The project uses one shared
   PostgreSQL server, running as a Docker container on a machine the team has
   agreed on; you copy `.env.example` to `.env` and fill in that server's
   address and your credentials. Cloning this repository does not create a
   database, and there are no default connection settings — a missing one is
   an error, not a guess. See **[docs/DATABASE.md](docs/DATABASE.md)**.
3. **Card templates.** `recognition/templates/` contains card artwork learned
   from an **Ezugi Casino Hold'em** table. If you play the same game they should
   work as-is. A different provider draws its cards differently and you will
   need to teach it yours (section 6).

`data/poker_hands.xlsx` and `logs/` may contain the sender's recorded hands.
Delete them if you want to start clean — both are recreated automatically.

---

## 1. What it does

| | |
| --- | --- |
| **Reads the table** | Captures nine small screen regions ~3× a second and identifies each card by template matching |
| **Follows the round** | `WAITING → PLAYER_CARDS → FLOP → TURN → RIVER → COMPLETE` |
| **Evaluates hands** | Player's hand from the flop onward; dealer's at the showdown |
| **Decides the winner** | Compares both best five-card hands, and records whether the dealer qualified |
| **Times the round** | Round length, flop-to-showdown, and the gap to the next round, in seconds |
| **Stores it** | One row per round in PostgreSQL, mirrored to Excel, with duplicates impossible |
| **Scenarios** | Your own if-this-then-that rules, shown as a recommendation and testable against your recorded history |
| **Speaks** | Announces the cards, your hand and the result as they settle — offline, on its own thread, never holding up the tracker |

What it deliberately does **not** do: press buttons, place bets, automate the
game, or tell you how to play.

---

## 2. Requirements and tech stack

**You need:**

- **Windows** (it uses Windows DPI awareness and system fonts; the rest is
  cross-platform but has only been run on Windows 11)
- **Python 3.12 or newer** — check with `python --version`
- **Docker Desktop** if you are hosting the database; otherwise just the
  address of the team's PostgreSQL server (see [docs/DATABASE.md](docs/DATABASE.md))
- A screen showing the casino table (a browser window)

**Built with:**

| Layer | Technology |
| --- | --- |
| Language | Python 3.12 |
| Screen capture | [MSS](https://python-mss.readthedocs.io/) |
| Computer vision | OpenCV (`opencv-python`) + NumPy |
| Card recognition | Template matching — no machine learning, no cloud, no OCR |
| Desktop UI | Tkinter (bundled with Python) |
| Database | PostgreSQL via [psycopg 3](https://www.psycopg.org/psycopg3/) |
| Spreadsheet | openpyxl |
| Config | JSON (`config/*.json`) + `.env` via python-dotenv |
| Image generation | Pillow — only used to draw fallback card templates |
| Tests | pytest — 1,341 tests |
| Logging | Python `logging`, rotating file in `logs/` |

Everything runs locally. Nothing is sent anywhere.

---

## 3. Installation

**Step 1 — open a terminal in this folder.**

```bash
cd "path\to\hold'em v2"
```

**Step 2 — install the Python packages.**

```bash
python -m pip install -r requirements.txt
```

**Step 3 — create the fallback card templates.**

```bash
python tools/generate_templates.py
```

This draws a starter set of rank and suit shapes from your Windows fonts. They
are only a fallback — real accuracy comes from teaching it the casino's own
cards in section 6.

**Step 4 — point at the team's database.**

```bash
copy .env.example .env
```

Fill in the address of the shared PostgreSQL server and your credentials. Ask
whoever runs it — the password is not in this repository and never will be.

```
POSTGRES_HOST=       # the database host; NOT localhost unless you run it
POSTGRES_PORT=5433
POSTGRES_DATABASE=poker_tracker
POSTGRES_USER=poker_tracker
POSTGRES_PASSWORD=
```

There is nothing to create. The database and the `poker_hands` table already
exist on that server. **Do not** run `tools/setup_database.py` — that is part
of setting up the server, not of joining it.

Every setting is required. Leave one out and you get a message naming it,
rather than a silent connection to whatever PostgreSQL happens to be on your
own machine — which is what used to happen, and it meant your hands went into
a database nobody else could see.

Setting up the server itself, sharing it over a LAN, backups, health checks
and troubleshooting are all in **[docs/DATABASE.md](docs/DATABASE.md)**.

`.env` is gitignored. No credentials appear anywhere in the code.

**Step 5 — check it all works.**

```bash
python -m pytest tests -q
```

You should see `1339 passed, 2 skipped`. Database tests skip themselves if
PostgreSQL is unreachable, so a few more skips are fine. The two that always
skip are the Action Controller test when PyAutoGUI is not installed, and a
table-layout test that needs sample frames in `samples/`.

---

## 4. Running it

```bash
python app.py
```

```
Poker Hand Tracker

Status: STOPPED

[ Start Tracker ]              [ Stop Tracker ]
[ Calibrate ]                  [ Test Recognition ]
[ Export Excel ]
[ Scenarios: before the round ][ Scenarios: after the flop ]

Current Hand:
Player: -- --
Flop:   -- -- --
Turn:   --
River:  --
Dealer: -- --

Player Hand: --
Dealer Hand: --
Winner:      --

Your scenarios say:
--

Last Saved Hand:
--
```

Anything wrong at startup — not calibrated, database unreachable, missing
templates, Excel file open — appears in red at the bottom.

### The window on a different laptop

Nothing needs editing to run this on a machine with a different screen. At
startup the tracker measures the screen it is on and sizes itself from that
(`ui/window_geometry.py`, `get_adaptive_window_geometry`):

- it is placed against the right-hand edge of the desktop, clear of the
  taskbar, so the **whole** window — the Scenario section on its right
  included — is on screen;
- it takes at most about a third of the screen's width, so the table
  underneath stays usable, and it stays above the browser as before;
- it is as tall as its content up to what the screen allows. A screen with no
  room for the whole column (a 1366x768 laptop, say) gets a **scrollbar**
  rather than a cut-off bottom;
- narrow it far enough and the layout reflows rather than clipping: the
  Scenario panel moves underneath the cards and the buttons go one to a row;
- the window can be resized by dragging its edge, with the
  `Window: − + Fit screen Reset` buttons, or with `Ctrl+-`, `Ctrl++` and
  `Ctrl+0`;
- the size and position you leave it at are remembered per machine and screen
  in `config/window_state.json`, and are checked against the screen before
  being used — a position saved on a monitor that is no longer attached is
  ignored rather than opening the window off the edge.

---

## 5. Calibration (do this first)

The app needs to know where the cards are on **your** screen. Coordinates are
never hard-coded; you point at them once.

1. Open the casino table so cards are visible.
2. Click **Calibrate**.
3. The screen freezes as a picture. Drag a box around each card in turn:

   1. Dealer Card 1
   2. Dealer Card 2
   3. Flop Card 1
   4. Flop Card 2
   5. Flop Card 3
   6. Turn
   7. River
   8. Player Card 1
   9. Player Card 2

   Draw the box around the **whole card**, not just its corner. A little
   surrounding table is fine — the app finds the white card face inside your box.

   - `Backspace` redoes the previous box
   - `m` moves the instruction bar out of the way
   - `Esc` cancels

4. Two optional questions follow:
   - **Result boxes** — 10 more boxes over the casino's own Dealer/Player result
     panels, so the app can cross-check its reading. Say No to skip.
   - **Teach the card artwork** — see the next section. Strongly recommended.

Boxes are saved to `config/config.json` with the screen size they were measured
at. If your screen size later differs the tracker warns you to recalibrate.

**Recalibrate whenever the browser window moves or resizes, or the resolution
or display scaling changes.** The coordinates are absolute.

---

## 6. Teaching it the cards

This matters more than anything else for accuracy.

The bundled fallback templates are drawn from Windows fonts. Casinos print their
ranks in their own font, so an untaught rank often reads with low confidence —
in which case the app reports "Recognition uncertain" and **does not save the
hand**. Safe, but a hand you did not record.

To teach it:

- answer **Yes** to "Teach the card artwork" at the end of calibration, or
- open **Test Recognition** and click **Teach cards...**

You are shown each visible card and type what it is — `8D`, `10H`, `QS`. Leave
the box empty to skip. Each labelled card stores its own rank and suit sample in
`recognition/templates/`, and matching takes the best of them.

Teach each of the 13 ranks and 4 suits once — a handful of hands covers it. Then
check your work:

```bash
python tools/audit_templates.py
```

This flags samples that were captured badly (a suit with a stray fragment stuck
to it, a rank that swept in the barcode). `--remove` deletes what it flags, and
you teach those cards again next time they appear. **Worth running after any
teaching session** — one bad template quietly drags every later reading toward
the wrong answer.

---

## 7. Tracking

Click **Start Tracker**. About three times a second it reads the nine regions
and follows the round.

**Cards are remembered, and every reading votes.** The dealer's hands pass over
the table constantly, and a covered card reads as absent. So a card is held once
seen — shown with a trailing dot (`8D.`) while covered — and every confident
reading adds to a tally, with the best-supported card winning. One bad frame
cannot overturn a card seen clearly ten times.

The tally is dropped when the round is genuinely over: the table empties, the
player's seat empties, or both player cards read as a different pair.

**A hand is stored only when:**

- all nine cards have been read confidently, and
- the same nine cards appear on consecutive polls, and
- no card appears twice (nine cards come from one deck — a repeat means a
  misread, and the hand is not saved until it resolves), and
- that exact hand has not been stored before.

The fingerprint `8S-9C-2D-QH-3D-3H-6S-8D-QD` is `UNIQUE` in the database, so the
same round can never be recorded twice however long it stays on screen.

---

## 8. Scenarios — your own rules

Two buttons open rule builders. Rules are checked top to bottom, first match
wins, and they are stored in `config/scenarios.json`.

**The app never presses anything.** A scenario produces a recommendation, shown
under "Your scenarios say", which you act on yourself.

### The decision banner

One banner, near the top of the screen, showing **one** decision — whichever
one is current:

```
        ANTE NOW              PLAY NOW               WAIT
        ────────              ────────               ────
```

There is never more than one. When the decision changes the words are replaced
in the same banner; when it does not change, nothing is redrawn — the tracker
polls five times a second and a stable decision must not flicker or chime.

| the decision | shown as |
| --- | --- |
| `ante` (pre-round rules) | **ANTE NOW** |
| `skip` (pre-round rules) | **SKIP ROUND** |
| `play` (flop rules) / `PLAY` (Scenario Engine) | **PLAY NOW** |
| `fold` (flop rules) | **FOLD** |
| `DON'T_PLAY` (Scenario Engine) | **DON'T PLAY** |
| `WAIT` (Scenario Engine) | **WAIT** |
| `bonus` | **BONUS NOW** — see below |

Every decision is drawn exactly the same way — same green, same position,
same size, same font, same weight, same spacing, same underline — because
they all go through the one widget (`ui/decision_banner.py`). Only the words
change, so there is no colour to decode: you read the decision.

The underline comes from the font rather than a rule drawn under a guess at
the text width, so it is exactly as long as the words and cannot go missing
for a longer or shorter decision.

A decision belongs to the round that produced it. When the round changes, the
banner clears rather than carrying the last round's answer into the new one,
using the tracker's own round id.

> **BONUS is not a decision.** Nothing in this project produces one. `bonus`
> is the name of a button on the local test table, and the Action Controller
> says of it: *"Nothing here ever clicks it: it is not an action and
> validation refuses it."* The label exists so the banner is complete if a
> bonus rule is ever written, but as things stand it will never appear.

**Before the round** — decide whether to ante based on the round that just
finished:

```
If previous round Dealer's hand was   [ Any        v ]
and previous round Player's hand was  [ High Card  v ]
then                                  [ Skip round v ]
```

**After the flop** — the decision that can actually change an outcome:

```
If the hand is             [ High Card       v ]
and on the table there is  [ four to a flush v ]
then                       [ Play on         v ]
```

The made hand alone is a poor guide, because two cards are still to come, so the
second box asks what you are drawing to — worked out from the cards, not typed
in:

| Condition | Means |
| --- | --- |
| `four to a flush` | one card short of a flush |
| `four to a straight (open)` | two ranks would complete it |
| `four to a straight (gutshot)` | one rank would complete it |
| `two overcards` / `one overcard` | hole cards above everything on the board |
| `no draw` | nothing to come to |
| `pair only on the board` | the dealer holds the identical pair — worth nothing |
| `pair uses one of my cards` | a real pair |
| `pocket pair` | both hole cards paired |
| `the flop is paired` | two of the three flop cards share a rank |
| `highest card showing is A, K or Q` | the top card of the hand, board included |
| `one of my two cards is A, K or Q` | a big card you actually hold |

**The last two are different questions.** An ace on the flop is the dealer's
ace as well and plays the same for both seats, so a hand can be “A high” with
nothing in it.

**The shipped High Card rule asks the second one** — a big card among your own
two. So does the Scenario Engine (`config/scenario_engine.json`,
`high_card_source: "player"`), so the two halves of the window agree. Measured
over 197 recorded hands, High Card hands with an A/K/Q in the hole won 39.1%
(18 of 46); the ones where the big card was only on the board won 19.2%
(10 of 52).

Both conditions stay available in the rule builder, and
`python tools/scenario_stats.py` scores both against your own recorded hands
(`A/K/Q in hand` and `A/K/Q high`), so you can check the split on your data
before changing the rule back.

### A rule is only applied to cards the tracker has settled

A flop deals in from the left, so for a poll or two the third box holds a card
on its way past — read weakly, and read repeatedly. The card table shows that
as `CONFIRMING`, and no rule is applied until all five decision cards are
`CONFIRMED` or `HELD`. Until then the panel says what it is waiting for:

```
Reading the table
(waiting for Flop Card 3)
```

This is the same gate the Scenario Engine has always used, so the two halves
of the window can no longer answer from different cards.

### Test a rule before you trust it

```bash
python tools/backtest.py
```

Every stored hand has its cards, so what a rule *would* have done is replayed
exactly:

```
Hands on record: 71
  anted             61
  skipped           10
  folded on flop    23
  played to the end 38

Of the hands folded, the eventual result was: {'Dealer': 13, 'Player': 9, 'Tie': 1}
  -> 9 of those 23 were hands the player went on to win.

Playing every hand to the end: {'Dealer': 34, 'Player': 34, 'Tie': 3}
```

A folded or skipped hand you would have won is the cost of the rule; the hands
it avoided losing are the benefit.

> **A note on the pre-round rules.** Every round is dealt from a fresh shuffle,
> so the previous round carries no information about the next. Measured over 71
> recorded hands, a player win was followed by Dealer 17 / Player 15, and a
> dealer win by Player 17 / Dealer 15 — a coin flip. Rules there change how
> often you play, not how often you win. The flop rules are the ones that matter.

### Action Controller (local TEST table only)

`automation/mouse_controller.py` turns the Scenario Engine's result into at most
one PyAutoGUI click per action per hand, on the **local test table only**
(`python -m automation.test_poker_ui`). It is **off by default**
(`config/mouse_controller.json`: `"automation_enabled": false`) and has one
mode, `TEST`; with it off the tracker behaves exactly as before and PyAutoGUI is
never loaded.

* A hand starts when the table has been empty for `ante_empty_polls` polls after
  showing cards → one TEST_ANTE. PLAY → one TEST_PLAY (only after that ANTE).
  DON'T_PLAY and WAIT → nothing.
* Before each click: the test table's heartbeat must be fresh, its window must
  have the test table's title and belong to its process, the click point must be
  inside its button, and Windows must report the test table as the window under
  that point. If the always-on-top tracker window covers the table, the table is
  raised (without taking focus) and checked again. The hand must still be the
  same hand when the button is pressed, and the table must confirm the click.
  Otherwise nothing is clicked and the reason is logged under `[ACTION]`.
* Button positions: `ante_button` / `play_button` are `null` by default, meaning
  "use the positions the running test table reports", so moving the table does
  not break anything. A configured `[x, y]` is still checked against the button.
* A PLAY computed before the current hand began is never acted on, and stopping
  the tracker cancels any click still queued.
* An action for an old hand never moves the mouse: the hand is re-checked
  before the move and again before the press.
* The test table shows ANTE, BONUS and PLAY. BONUS is layout only — the
  controller has no BONUS action and its validation refuses that button.
* Logs: `[TEST HAND START]`, `[TEST HAND]` (`round=`, `player=`, `flop=`,
  `scenario=`, matched rules), `[TEST ACTION]` (`ANTE` / `PLAY` with
  `result=CLICKED`, or `NONE` / `CANCELLED` with `reason=`) and
  `[TEST HAND SUMMARY]` (`ante=N`, `play=N`).
* To try it: set `"automation_enabled": true`, run
  `python automation/test_poker_ui.py`, then `python app.py` and Start Tracker.
  `python tools/test_automation_demo.py` runs five scripted hands plus a stop
  against the test table with real clicks and prints PASS/FAIL, without changing
  the config.
* Move the mouse into a screen corner to trigger PyAutoGUI's fail-safe, which
  halts the controller for the session.

### Scenario Engine (dry run)

`poker/scenario_engine.py` works out **PLAY / DON'T_PLAY / WAIT** from the
player's two cards and the three flop cards, and logs why. It is read-only: it
never clicks or bets, and there is no setting that makes it.

* It decides only when all five cards are CONFIRMED (or HELD after being
  confirmed) in the current round. Anything UNKNOWN, AMBIGUOUS or still
  CONFIRMING gives WAIT.
* Each decision logs a `[SCENARIO]` block, and the dealer's qualification (pair
  of 4s or better) logs a `[DEALER]` block. Each is logged once per round, or
  again if a card is revised.
* Its settings are in `config/scenario_engine.json`: which ranks count as high
  cards (A, K, Q in the player's two hole cards by default; the flop is not
  counted), and which scenarios decide PLAY. There is no flush rule: a flush is
  shown as the actual hand but never plays on its own. Set
  `"scenario_engine": false` in `config.json` to turn it off. The module
  docstring gives the exact meaning of every scenario.
* The main window shows the result beside the Current Hand cards: SCENARIO
  (decision, primary, cards, matched rules; only the reason while waiting) and
  DEALER (qualification, cards and hand). It displays what the tracker sends
  and calculates nothing itself.

---

## 9. The voice

The tracker can say what it has just worked out: your two cards, the flop, the
turn, the river, the hand you are holding, and who won.

It only ever **reads out** what the rest of the application has already
decided. It runs no recognition of its own, evaluates no hand, and does not
click, bet or wager anything.

```
recognition -> CardMemory -> scenario/evaluation -> tracker event
                                                         |
                                       the window's event loop
                                                         |
                                     Announcer.announce()  (returns at once)
                                                         |
                                        queue -> voice thread -> speaker
```

**It cannot slow the tracker down.** The recognition loop never calls it —
`tracker.py` does not import `voice` at all. The window feeds the announcer
from its own event loop, and announcing is one queue put. A phrase that never
finishes costs the tracker nothing; there is a test that wedges the speech
engine open and ticks the tracker twenty times to prove it.

### Controls

A row in the main window:

```
Voice: IDLE     [ Pause ] [ Resume ] [ Stop ] [ Mute ]
```

| | |
| --- | --- |
| **Pause** | stops the phrase being spoken and says nothing more until Resume |
| **Resume** | speaking again — without replaying what went stale while paused |
| **Stop** | stops now and clears the queue, but does not latch: the next announcement is spoken |
| **Mute** | silence; nothing is queued while muted, so unmuting does not release a backlog |

The state beside the label is one of `OFF`, `IDLE`, `SPEAKING`, `PAUSED`,
`MUTED`, `STOPPED` or `ERROR`. **None of them affect the tracker**, the card
recognition, the scenario engine or the database, all of which keep running.

### Settings

In `config/config.json`:

| | |
| --- | --- |
| `voice_enabled` | `true` by default. `false` and nothing is imported, no thread starts, no engine is created |
| `voice_rate` | words per minute, default 180 |
| `voice_volume` | 0.0 to 1.0 |
| `voice_name` | part of an installed voice's name, e.g. `"zira"`. Empty means the system default |

`voice_name` is matched case-insensitively against the installed voices, so
you never paste a registry path. A name that matches nothing logs a warning
and falls back to the default rather than failing.

To see what is installed:

```bash
python -c "import pyttsx3; e=pyttsx3.init(); [print(v.name) for v in e.getProperty('voices')]"
```

Windows ships **David** and **Zira**; more can be added under
Settings → Time & Language → Speech.

### What it will not do

* It never reads out a card that is still settling. A card is only spoken once
  the tracker calls it `CONFIRMED` or `HELD` — the same gate the Scenario
  Engine uses, downstream of `RANK_MARGIN`, `SUIT_MARGIN` and
  `CORROBORATED_READING`. Nothing about recognition was changed for the voice.
* It never repeats itself. The tracker polls five times a second; each
  announcement is keyed on `(round_id, event, value)` and said once per round.
  The round id is CardMemory's own generation — there is no second one.
* It never catches up out loud. Speech is slower than the game, so cards from
  a finished round are dropped unspoken. The **result** is kept, because that
  is the one thing worth hearing a moment late.

### The limitation worth knowing

**There is no true audio pause.** pyttsx3's engine has no `pause()` or
`resume()` — checked, not assumed. So Pause stops the current phrase and
holds; Resume does not finish the interrupted sentence. The status line says
`PAUSED`, and that is exactly what has happened.

If speech is unavailable — pyttsx3 missing, no audio device, a broken driver
— the state becomes `ERROR`, the reason is logged and shown once, and
**everything else carries on**.

---

## 10. Where the data goes

**PostgreSQL** is the source of truth — table `poker_hands`, in the shared
container described in [docs/DATABASE.md](docs/DATABASE.md). **Excel** is a
mirror at `data/poker_hands.xlsx`, appended after every successful insert, so
it is only ever behind the database and never ahead of it.

Columns: ID, Recorded At, Player Card 1/2, Flop Card 1/2/3, Turn, River, Dealer
Card 1/2, Player Hand, Dealer Hand, Winner, Dealer Qualified, plus the timings.

### Round timings (all in seconds)

| Column | Measured from | to |
| --- | --- | --- |
| `Round Started` | — | wall-clock time cards appeared on an empty table |
| `Deal To Flop (s)` | cards appear | all three flop cards readable |
| `Flop To Showdown (s)` | flop readable | dealer's cards turned over |
| `Round Length (s)` | cards appear | dealer's cards turned over |
| `Since Previous Round (s)` | previous showdown | this round's cards appearing |

Each moment is taken the first time it is reached, so polling does not drift the
figure, and durations use a monotonic clock so a clock adjustment cannot produce
a negative round. Anything not observed is left blank rather than guessed.

> In Casino Hold'em your two cards and the flop are dealt together, so
> `Deal To Flop` is typically ~1 second and reflects recognition speed rather
> than the game. `Round Length`, `Flop To Showdown` and `Since Previous Round`
> are the meaningful ones — expect roughly 23 s of play and ~35 s between rounds.

**Export Excel** rebuilds the whole spreadsheet from PostgreSQL. Use it if the
file is deleted, is out of step, or was locked while hands were being recorded.

> **Keep the spreadsheet closed while tracking.** Excel locks the file, appends
> fail, and the hands are only in the database until you rebuild.

---

## 11. Command-line tools

| Command | What it does |
| --- | --- |
| `python app.py` | Run the tracker |
| `python tools/setup_database.py` | Create the `poker_hands` table. Server setup only — not part of joining |
| `python tools/import_excel.py --dry-run` | Report what a history import from Excel would do |
| `python tools/import_excel.py` | Import `data/poker_hands.xlsx` into PostgreSQL — safe to re-run |
| `docker compose --env-file .env.docker up -d db` | Start the database server |
| `python tools/generate_templates.py` | Redraw the fallback card templates |
| `python tools/audit_templates.py` | Find badly learned templates (`--remove` to delete) |
| `python tools/backtest.py` | Score your scenarios against recorded hands |
| `python -m pytest tests -q` | Run the test suite |

---

## 12. Configuration

`config/config.json`:

| Key | Meaning |
| --- | --- |
| `regions` | The nine calibrated card boxes (written by Calibrate) |
| `monitor` | Which monitor to capture; 1 is the primary screen |
| `screen_size` | Screen size at calibration time, used to warn about changes |
| `poll_interval_seconds` | How often the screen is sampled (default 0.35) |
| `confidence_threshold` | Minimum match score to accept a card (default 0.62) |
| `presence_threshold` | Minimum white-face fraction to call a region "a card" (default 0.35) |
| `stable_frames` | Identical reads required before saving (default 2) |
| `latch_cards` | Hold a card once read, through hands passing over it (default true) |
| `clear_frames` | Empty polls before held cards are released (default 10, ~3.5 s) |
| `change_confirm_frames` | Polls of a different player pair that mean a new deal (default 2) |
| `validate_with_result_boxes` | Cross-check against the casino's result panels |

`config/scenarios.json` holds your rules — edit through the app, or by hand.

---

## 13. Logging

Everything goes to `logs/tracker.log` (rotating, 2 MB × 3): startup and
shutdown, calibration, state changes, the player's hand at each street,
completed hands with the confidence behind each card, round timings, inserts,
duplicates, and errors. Per-frame noise is deliberately not logged.

A card that could not be taught is saved as a picture in `logs/unread/` so the
failure can be looked at afterwards.

---

## 14. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Every slot shows `--`, `PRESENT` is `no` | Not calibrated for this screen, or the browser moved. Recalibrate. |
| A rank reads wrong, or shows as uncertain | That rank has not been taught yet. Use **Teach cards...** while it is on screen. This is the usual cause. |
| A card switches between two suits of the same colour (`5H`/`5D`, `KS`/`KC`) | Almost always a bad template. Run `python tools/audit_templates.py`, remove what it flags, teach those cards again. |
| Empty seats are read as cards | Raise `presence_threshold` (0.45–0.55). |
| The log shows `Same card read twice` | One of the two slots is misreading; the hand is not saved until it resolves. If it repeats for the same pair of slots, recalibrate them. |
| Cards from the previous round linger | The table is not reading as empty between rounds. Lower `clear_frames`. |
| A card keeps dropping to `--` while hands move over it | Check `latch_cards` is `true`. |
| `Database error: ...` | Check PostgreSQL is running and `.env` matches (host, port, user, password). Hands are retried every 30 seconds while the tracker runs. |
| `Cannot write data/poker_hands.xlsx` | The file is open in Excel. Close it — the hands are safe in PostgreSQL, and **Export Excel** rebuilds the file. |
| Nothing is ever saved | All nine cards must have been read — not necessarily at once, since they are held once seen. Watch the status line; `COMPLETE` is required. |
| Everything shifted after changing display scaling | Recalibrate. The app is DPI-aware, but the boxes were measured at the old scaling. |

`logs/tracker.log` has the detail behind any of these.

---

## 15. Project layout

```
hold'em v2/
├── app.py                     Tkinter UI
├── tracker.py                 polling loop, card memory, round timer, saving
├── requirements.txt
├── .env.example               copy to .env and fill in
├── .env.docker.example        the SERVER's credentials (database host only)
├── docker-compose.yml         the PostgreSQL 18 server
├── docs/DATABASE.md           architecture, setup, sharing, backup
├── scripts/                   db_backup, db_restore (.sh and .ps1)
├── voice/                     spoken announcements (announcer, engines,
│                              events, phrasing)
│
├── config/
│   ├── config.json            calibration + thresholds
│   ├── scenarios.json         your rules
│   └── settings.py            config load/save, logging setup
│
├── capture/screen_capture.py  MSS region capture
│
├── recognition/
│   ├── card_detector.py       card present? rank/suit glyph extraction
│   ├── card_recognizer.py     template matching -> "8D"
│   └── templates/             rank and suit samples
│
├── poker/
│   ├── hand_evaluator.py      best five of seven, winner, dealer qualification
│   ├── board_features.py      draws, overcards, where a pair came from
│   ├── hand_record.py         completed-hand record + fingerprint
│   └── scenarios.py           your rules and what they decide
│
├── ui/rule_windows.py         the two scenario builders
├── ui/window_geometry.py      window size/position for the screen in use
├── calibration/calibrator.py  region picker + card teaching
├── database/
│   ├── db.py                  psycopg access
│   └── schema.sql
├── export/excel_export.py     append + rebuild the spreadsheet
│
├── tools/                     setup_database, generate_templates,
│                              audit_templates, backtest
├── tests/                     1,341 tests
├── data/poker_hands.xlsx
└── logs/tracker.log
```

---

## 16. How a card is actually read

Useful if you are debugging recognition.

1. The white card face is located inside the calibrated region, so the box may
   include some surrounding table.
2. The top strip of the face is cut out — above the barcode — and thresholded to
   black-and-white ink.
3. The ink is split into a rank and a suit. This casino prints them side by side
   (`8` then a diamond); the smaller result-box cards stack the rank above the
   suit. Both are tried.

   A `10` is the awkward case, being the only two-character rank: the gap
   between its digits can be as wide as the gap before the pip, and on a tight
   index the `0` and the pip touch and arrive as one blob. So the strip is cut
   at several gap widths, and any blob wider than it is tall is also cut at its
   narrowest column. Every cut is offered as a candidate.
4. Ink colour separates red (hearts, diamonds) from black (spades, clubs), so
   the suit is only ever a choice between two.
5. Rank and suit are matched separately against the templates, and the lower of
   the two scores becomes the card's confidence. Whichever candidate reading
   matches a real card best wins.
6. Both halves must also have *beaten what they were being chosen between*. The
   suit has to beat the other suit of its colour by `SUIT_MARGIN` (0.05) and
   the rank has to beat the second-best rank by `RANK_MARGIN` (0.10). A nine
   matching at 0.80 with the queen at 0.79 behind it has not been recognised
   as anything, and saying so is different from saying the glyph matched
   nothing — the card table reports it as `AMBIGUOUS`.

Below `confidence_threshold`, or inside either margin, the card is reported as
uncertain and the hand is not saved — the failure mode is a missing row, never
a wrong one.

### A card is held by the best look at it, not the most looks

Every accepted reading goes into a tally for its slot and the slot shows
whichever card has the most support. What a reading is worth to that tally is
how far it cleared the threshold, not its whole confidence, because the deal
guarantees a run of barely-readable readings before the real card arrives: a
card sliding past the box read as a king of spades five times at 0.62–0.705,
and the two of spades that was really there arrived at 0.957 and could not
displace it for four more polls.

Confirming a card needs either two readings with one above 0.75, or three with
one above 0.72. Three weak readings used to be enough on their own, which is
how a card that was never there came to be reported `CONFIRMED`.
