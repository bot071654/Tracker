"""LOCAL TEST BETTING SCREEN - a Casino Hold'em betting loop that runs by itself.

    python automation/test_betting_screen.py                  run it
    python automation/test_betting_screen.py --seed 7         the same deals every time
    python automation/test_betting_screen.py --betting 10 --decision 10
    python automation/test_betting_screen.py --expire-round 4 session expires at round 4

A local stand-in for a live betting screen, for testing the tracker and the
Action Controller end to end. It is not a casino and is not connected to one:
no account, no money - it plays with TEST credits that reset every run, and it
carries no operator's name or branding.

Each round goes through the phases a live table does:

    NEXT_GAME   "NEXT GAME SOON"        nothing can be clicked
    BETTING     "PLACE YOUR BETS" + countdown - ANTE and BONUS open
    DEALING     player cards and flop dealt
    DECISION    "PLAY OR FOLD" + countdown - PLAY open (only if you anted)
    REVEAL      turn, river and the dealer's cards shown
    RESULT      who won and what the TEST credits did
    SESSION_EXPIRED   (optional) everything closed until "Log in again (TEST)"

A button pressed outside its phase is refused and counted as rejected - just
as a live table ignores a late bet. The window has the title the Action
Controller looks for and publishes the same heartbeat as automation/test_poker_ui.py
(window handle, pid, button rectangles, click counts), plus the phase, the
countdown, the round and the cards on show, so the controller's checks work
unchanged and a click can be refused while betting is closed.

GameEngine holds the rules and is plain Python, so it is tested without a
display; BettingScreen is the window.
"""

import argparse
import json
import os
import random
import sys
import time
import tkinter as tk

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation.mouse_controller import ANTE, BONUS, PLAY_BUTTON, load_mouse_config  # noqa: E402
from poker.hand_evaluator import (  # noqa: E402
    ALL_CARDS, compare_hands, dealer_qualifies, evaluate_hand,
)

NEXT_GAME, BETTING, DEALING, DECISION, REVEAL, RESULT = (
    "NEXT_GAME", "BETTING", "DEALING", "DECISION", "REVEAL", "RESULT")
SESSION_EXPIRED = "SESSION_EXPIRED"
PHASE_ORDER = [NEXT_GAME, BETTING, DEALING, DECISION, REVEAL, RESULT]

# Which phase each button is open in.
OPEN_IN = {ANTE: BETTING, BONUS: BETTING, PLAY_BUTTON: DECISION}

PLAYER_SLOTS = ["player_1", "player_2"]
FLOP_SLOTS = ["flop_1", "flop_2", "flop_3"]
LATER_SLOTS = ["turn", "river", "dealer_1", "dealer_2"]
ALL_SLOTS = PLAYER_SLOTS + FLOP_SLOTS + LATER_SLOTS

STARTING_CREDITS = 10000
ANTE_STAKE = 50
BONUS_STAKE = 50

# Casino Hold'em pay tables, by hand category (hand_evaluator.HAND_NAMES).
ANTE_PAYS = {10: 100, 9: 20, 8: 10, 7: 3, 6: 2}          # anything lower pays 1:1
BONUS_PAYS = {10: 100, 9: 50, 8: 40, 7: 30, 6: 20}       # pair of aces to straight 7:1


def default_durations():
    return {NEXT_GAME: 3.0, BETTING: 12.0, DEALING: 2.0, DECISION: 12.0,
            REVEAL: 3.0, RESULT: 4.0}


class GameEngine:
    """The round loop, bets and settlement. Time is passed in, never read."""

    def __init__(self, seed=None, durations=None, expire_round=None, expire_seconds=8.0):
        self.random = random.Random(seed)
        self.durations = dict(default_durations(), **(durations or {}))
        self.expire_round = expire_round
        self.expire_seconds = float(expire_seconds)
        self.round = 0
        self.phase = None
        self.phase_started = 0.0
        self.credits = STARTING_CREDITS
        self.counts = {ANTE: 0, BONUS: 0, PLAY_BUTTON: 0}
        self.rejected = {ANTE: 0, BONUS: 0, PLAY_BUTTON: 0}
        self.history = []            # one entry per finished round
        self.presses = []            # (time, button, round, accepted, message)
        self.message = ""
        self._expired_at = None
        self.expired_periods = []    # [start, end or None] of each expired session
        self._new_round()

    # -- time --------------------------------------------------------------------

    def start(self, now):
        self._enter(NEXT_GAME, now)

    def remaining(self, now):
        if self.phase == SESSION_EXPIRED:
            return 0.0
        return max(0.0, self.phase_started + self.durations[self.phase] - now)

    def tick(self, now):
        """Advance through every phase whose time is up. Returns True if the phase changed."""
        if self.phase is None:
            self.start(now)
            return True
        changed = False
        while self.phase != SESSION_EXPIRED and now >= self.phase_started + self.durations[self.phase]:
            end = self.phase_started + self.durations[self.phase]
            self._advance(end)
            changed = True
        if self.phase == SESSION_EXPIRED and self._expired_at is not None \
                and self.expire_seconds > 0 and now >= self._expired_at + self.expire_seconds:
            self.log_in_again(now)
            changed = True
        return changed

    def _advance(self, now):
        if self.phase == RESULT:
            self._new_round()
            if self.expire_round is not None and self.round == self.expire_round:
                self.expire(now)
                return
            self._enter(NEXT_GAME, now)
            return
        following = PHASE_ORDER[PHASE_ORDER.index(self.phase) + 1]
        self._enter(following, now)

    def _enter(self, phase, now):
        self.phase, self.phase_started = phase, now
        if phase == NEXT_GAME:
            self.message = "NEXT GAME SOON"
        elif phase == BETTING:
            self.message = "PLACE YOUR BETS"
        elif phase == DEALING:
            for slot in PLAYER_SLOTS + FLOP_SLOTS:
                self.shown[slot] = self.deal[slot]
            self.message = "DEALING"
        elif phase == DECISION:
            self.message = "PLAY OR FOLD" if self.bets[ANTE] else "NO ANTE THIS ROUND"
        elif phase == REVEAL:
            for slot in LATER_SLOTS:
                self.shown[slot] = self.deal[slot]
            self.message = "REVEAL"
        elif phase == RESULT:
            self._settle(now)

    # -- rounds ------------------------------------------------------------------

    def _new_round(self):
        self.round += 1
        cards = self.random.sample(ALL_CARDS, len(ALL_SLOTS))
        self.deal = dict(zip(ALL_SLOTS, cards))
        self.shown = {slot: None for slot in ALL_SLOTS}
        self.bets = {ANTE: 0, BONUS: 0, PLAY_BUTTON: 0}
        self.outcome = None

    def expire(self, now):
        """The session times out: nothing can be clicked and the table is hidden."""
        self.phase, self.phase_started = SESSION_EXPIRED, now
        self.shown = {slot: None for slot in ALL_SLOTS}
        self._expired_at = now
        self.expired_periods.append([round(now, 3), None])
        self.message = "SESSION EXPIRED (TEST) - log in again"

    def log_in_again(self, now):
        """No credentials: this is a local test screen. Resumes with a fresh round."""
        if self.phase != SESSION_EXPIRED:
            return
        self._expired_at = None
        self.expire_round = None
        if self.expired_periods and self.expired_periods[-1][1] is None:
            self.expired_periods[-1][1] = round(now, 3)
        self._enter(NEXT_GAME, now)

    # -- buttons -----------------------------------------------------------------

    def press(self, button, now):
        """(accepted, message). A refused press changes nothing but its counter."""
        accepted, message = self._check(button)
        if accepted:
            self.counts[button] += 1
            if button == ANTE:
                self.bets[ANTE] = ANTE_STAKE
                self.credits -= ANTE_STAKE
            elif button == BONUS:
                self.bets[BONUS] = BONUS_STAKE
                self.credits -= BONUS_STAKE
            else:
                self.bets[PLAY_BUTTON] = 2 * self.bets[ANTE]
                self.credits -= self.bets[PLAY_BUTTON]
        else:
            self.rejected[button] += 1
        self.presses.append((round(now, 3), button, self.round, accepted, message))
        return accepted, message

    def _check(self, button):
        if button not in OPEN_IN:
            return False, "unknown button %r" % (button,)
        if self.phase == SESSION_EXPIRED:
            return False, "session expired"
        if self.phase != OPEN_IN[button]:
            return False, "%s is closed during %s" % (button.upper(), self.phase)
        if self.bets[button]:
            return False, "%s already placed this round" % button.upper()
        if button == BONUS and not self.bets[ANTE]:
            return False, "BONUS needs an ANTE first"
        if button == PLAY_BUTTON and not self.bets[ANTE]:
            return False, "PLAY needs an ANTE this round"
        return True, "%s placed" % button.upper()

    # -- settlement ----------------------------------------------------------------

    def _settle(self, now):
        board = [self.deal[slot] for slot in FLOP_SLOTS + ["turn", "river"]]
        player = evaluate_hand([self.deal[s] for s in PLAYER_SLOTS] + board)
        dealer = evaluate_hand([self.deal[s] for s in ("dealer_1", "dealer_2")] + board)
        winner = compare_hands(player, dealer)
        qualified = dealer_qualifies(dealer)
        ante, play, bonus = self.bets[ANTE], self.bets[PLAY_BUTTON], self.bets[BONUS]
        returned = 0
        if ante and play:
            if not qualified:
                returned += ante + ante * ANTE_PAYS.get(player["category"], 1) + play
            elif winner == "Player":
                returned += ante + ante * ANTE_PAYS.get(player["category"], 1) + 2 * play
            elif winner == "Tie":
                returned += ante + play
        if bonus:
            first_five = evaluate_hand([self.deal[s] for s in PLAYER_SLOTS + FLOP_SLOTS])
            pays = BONUS_PAYS.get(first_five["category"])
            if pays is None and _aces_or_better(first_five):
                pays = 7
            if pays:
                returned += bonus + bonus * pays
        self.credits += returned
        staked = ante + play + bonus
        self.outcome = {
            "round": self.round, "cards": dict(self.deal),
            "player_hand": player["name"], "dealer_hand": dealer["name"],
            "winner": winner, "dealer_qualified": qualified,
            "ante": bool(ante), "bonus": bool(bonus), "play": bool(play),
            "folded": bool(ante) and not play, "net": returned - staked,
            "credits": self.credits, "settled_at": round(now, 3),
        }
        self.history.append(self.outcome)
        if not ante:
            verdict = "no bet"
        elif not play:
            verdict = "folded"
        else:
            verdict = "%+d TEST credits" % (returned - staked)
        self.message = "RESULT: %s wins (%s vs %s) - %s" % (
            winner, player["name"], dealer["name"], verdict)

    # -- status ------------------------------------------------------------------

    def status(self, now):
        return {"phase": self.phase, "remaining": round(self.remaining(now), 2),
                "sim_round": self.round, "cards": dict(self.shown),
                "bets": dict(self.bets), "credits": self.credits,
                "counts": dict(self.counts), "rejected": dict(self.rejected),
                "message": self.message, "results": self.history[-20:],
                "presses": self.presses[-50:], "expired_periods": self.expired_periods}


def _aces_or_better(result):
    """A pair of aces or better, by the evaluator's own score."""
    if result["category"] > 2:
        return True
    return result["category"] == 2 and result["score"][1] == 14


# -- the window --------------------------------------------------------------------------

SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
FELT, GOLD, CARD_BACK = "#0f5c55", "#d9b64a", "#8a1c2b"
TICK_MS = 100
HEARTBEAT_MS = 250


def card_text(card):
    return "%s%s" % (card[:-1], SUIT_SYMBOLS[card[-1]]) if card else ""


class BettingScreen:
    __test__ = False

    def __init__(self, root, config, engine):
        self.root, self.config, self.engine = root, config, engine
        root.title(config.test_window_title)
        x, y = config.test_window_position
        root.geometry("+%d+%d" % (x, y))
        root.resizable(False, False)
        root.attributes("-topmost", True)
        root.configure(background="#111")

        header = tk.Frame(root, background="#111")
        header.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(header, text="TEST CASINO HOLD'EM", fg="white", bg="#111",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        tk.Label(header, text="LOCAL TEST SCREEN - NOT A REAL CASINO - NO MONEY",
                 fg="#ff6b6b", bg="#111", font=("Segoe UI", 9, "bold")).pack(side="right")

        self.table = tk.Canvas(root, width=720, height=330, background=FELT,
                               highlightthickness=0)
        self.table.pack(padx=10, pady=8)
        self.table.create_text(360, 30, text="TEST HOLD'EM", fill=GOLD,
                               font=("Georgia", 22, "bold"))
        self.table.create_text(360, 312, text="DEALER QUALIFIES WITH PAIR OF 4s OR BETTER",
                               fill=GOLD, font=("Segoe UI", 9, "bold"))
        self.card_items = {}
        rows = {"dealer_1": (310, 70), "dealer_2": (390, 70),
                "flop_1": (200, 150), "flop_2": (280, 150), "flop_3": (360, 150),
                "turn": (440, 150), "river": (520, 150),
                "player_1": (310, 230), "player_2": (390, 230)}
        for slot, (left, top) in rows.items():
            rect = self.table.create_rectangle(left, top, left + 66, top + 70,
                                               outline="#2a8077", width=2, fill="")
            text = self.table.create_text(left + 33, top + 35, text="",
                                          font=("Segoe UI", 18, "bold"))
            self.card_items[slot] = (rect, text)

        self.message_var = tk.StringVar()
        self.countdown_var = tk.StringVar()
        tk.Label(root, textvariable=self.message_var, fg="white", bg="#111",
                 font=("Segoe UI", 16, "bold")).pack()
        tk.Label(root, textvariable=self.countdown_var, fg=GOLD, bg="#111",
                 font=("Segoe UI", 12, "bold")).pack()

        controls = tk.Frame(root, background="#111")
        controls.pack(pady=8)
        self.buttons = {}
        for name, label in ((ANTE, "ANTE"), (BONUS, "BONUS"), (PLAY_BUTTON, "PLAY")):
            button = tk.Button(controls, text=label, width=10, height=2,
                               font=("Segoe UI", 12, "bold"), relief="raised",
                               command=lambda n=name: self.pressed(n))
            button.pack(side="left", padx=10)
            self.buttons[name] = button

        footer = tk.Frame(root, background="#111")
        footer.pack(fill="x", padx=10, pady=(0, 8))
        self.credits_var = tk.StringVar()
        self.round_var = tk.StringVar()
        self.last_var = tk.StringVar(value="Last Action:\nNONE")
        tk.Label(footer, textvariable=self.credits_var, fg="white", bg="#111",
                 font=("Consolas", 10)).pack(side="left")
        tk.Label(footer, textvariable=self.round_var, fg="white", bg="#111",
                 font=("Consolas", 10)).pack(side="left", padx=20)
        self.expire_button = tk.Button(footer, text="Simulate session expired",
                                       command=lambda: self.engine.expire(time.time()))
        self.expire_button.pack(side="right")
        self.login_button = tk.Button(footer, text="Log in again (TEST)",
                                      command=lambda: self.engine.log_in_again(time.time()))
        self.login_button.pack(side="right", padx=6)

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.engine.tick(time.time())
        self.refresh()
        root.after(TICK_MS, self.tick)
        root.after(HEARTBEAT_MS, self.heartbeat)

    # -- behaviour -----------------------------------------------------------------

    def pressed(self, name):
        accepted, message = self.engine.press(name, time.time())
        self.last_var.set("Last Action:\n%s%s" % (name.upper(), "" if accepted else " (refused)"))
        if accepted:
            button = self.buttons[name]
            button.configure(relief="sunken")
            self.root.after(250, lambda: button.configure(relief="raised"))
        self.refresh()
        self.write_status()

    def tick(self):
        self.engine.tick(time.time())
        self.refresh()
        self.root.after(TICK_MS, self.tick)

    def refresh(self):
        engine, now = self.engine, time.time()
        for slot, (rect, text) in self.card_items.items():
            card = engine.shown.get(slot)
            if card:
                colour = "#c62828" if card[-1] in "HD" else "#111"
                self.table.itemconfigure(rect, fill="white", outline="#ddd")
                self.table.itemconfigure(text, text=card_text(card), fill=colour)
            else:
                self.table.itemconfigure(rect, fill="", outline="#2a8077")
                self.table.itemconfigure(text, text="")
        self.message_var.set(engine.message)
        remaining = engine.remaining(now)
        self.countdown_var.set("%d" % (remaining + 0.999)
                               if engine.phase in (BETTING, DECISION) else "")
        for name, button in self.buttons.items():
            open_now = engine.phase == OPEN_IN[name] and not engine.bets[name] and \
                (name == ANTE or engine.bets[ANTE])
            button.configure(state="normal" if open_now else "disabled",
                             background="#ffd54f" if open_now else "#555",
                             disabledforeground="#999")
        self.credits_var.set("TEST CREDITS %d   BET %d" % (
            engine.credits, sum(engine.bets.values())))
        self.round_var.set("ROUND %d   ANTE %d  BONUS %d  PLAY %d" % (
            engine.round, engine.counts[ANTE], engine.counts[BONUS], engine.counts[PLAY_BUTTON]))

    # -- what the controller reads ----------------------------------------------------

    def button_rects(self):
        rects = {}
        for name, button in self.buttons.items():
            x, y = button.winfo_rootx(), button.winfo_rooty()
            rects[name] = [x, y, x + button.winfo_width(), y + button.winfo_height()]
        return rects

    def centres(self):
        return {name: [(l + r) // 2, (t + b) // 2]
                for name, (l, t, r, b) in self.button_rects().items()}

    def write_status(self):
        now = time.time()
        status = self.engine.status(now)
        status.update({"title": self.config.test_window_title, "pid": os.getpid(),
                       "hwnd": int(self.root.wm_frame(), 16), "heartbeat": now,
                       "round": self.engine.round, "buttons": self.button_rects(),
                       "screen": "betting"})
        temp = self.config.status_file + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(status, handle)
        os.replace(temp, self.config.status_file)

    def heartbeat(self):
        try:
            self.write_status()
        except OSError:
            pass
        self.root.after(HEARTBEAT_MS, self.heartbeat)

    def close(self):
        try:
            os.remove(self.config.status_file)
        except OSError:
            pass
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=None)
    for phase, flag in ((NEXT_GAME, "--next-game"), (BETTING, "--betting"), (DEALING, "--dealing"),
                        (DECISION, "--decision"), (REVEAL, "--reveal"), (RESULT, "--result")):
        parser.add_argument(flag, type=float, default=None, dest=phase.lower(),
                            help="seconds of %s" % phase)
    parser.add_argument("--expire-round", type=int, default=None,
                        help="the session expires as this round would start")
    parser.add_argument("--expire-seconds", type=float, default=8.0,
                        help="log back in automatically after this long (0 = wait for the button)")
    parser.add_argument("--status-file", default=None,
                        help="heartbeat file (default: the controller's configured one)")
    args = parser.parse_args(argv)

    from calibration.calibrator import make_dpi_aware
    make_dpi_aware()

    durations = {phase: getattr(args, phase.lower()) for phase in PHASE_ORDER
                 if getattr(args, phase.lower()) is not None}
    engine = GameEngine(seed=args.seed, durations=durations,
                        expire_round=args.expire_round, expire_seconds=args.expire_seconds)
    config = load_mouse_config()
    if args.status_file:
        config.status_file = args.status_file
    root = tk.Tk()
    BettingScreen(root, config, engine)
    root.mainloop()


if __name__ == "__main__":
    main()
