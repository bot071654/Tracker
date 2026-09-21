"""LOCAL TEST POKER TABLE - the only thing the Action Controller may click.

    python -m automation.test_poker_ui                 run the table
    python -m automation.test_poker_ui --write-config  also store its button
                                                       positions in
                                                       config/mouse_controller.json

It has real ANTE, BONUS and PLAY buttons and shows the last action and the round
(one round per ANTE). BONUS is there for the layout only: the controller never
clicks it, and only a person pressing it changes its count. While it runs it publishes a heartbeat file with its window
handle, title, button rectangles in screen pixels and click counts; the
controller clicks nothing unless that heartbeat is fresh and Windows confirms
this window is under the point, and it confirms each click from the counts.
"""

import argparse
import json
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation.mouse_controller import (  # noqa: E402
    ANTE, BONUS, PLAY_BUTTON, load_mouse_config, save_mouse_config,
)

HEARTBEAT_MS = 500


class TestPokerTable:
    __test__ = False                      # not a pytest test class

    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.round = 0
        self.counts = {ANTE: 0, BONUS: 0, PLAY_BUTTON: 0}
        self.history = []

        root.title(config.test_window_title)
        x, y = config.test_window_position
        root.geometry("+%d+%d" % (x, y))
        root.resizable(False, False)
        # The tracker window is always on top; so is this, or it could be covered.
        root.attributes("-topmost", True)

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="TEST POKER TABLE", font=("Segoe UI", 12, "bold")).pack()
        ttk.Label(frame, text="local test only - not a casino",
                  foreground="#b00020").pack(pady=(0, 8))
        self.buttons = {}
        for name, label, colour in ((ANTE, "ANTE", "#d7e8ff"), (BONUS, "BONUS", "#eeeeee"),
                                    (PLAY_BUTTON, "PLAY", "#d9f2d9")):
            button = tk.Button(frame, text=label, width=14, height=2, bg=colour,
                               activebackground="#ffe08a", font=("Segoe UI", 11, "bold"),
                               command=lambda n=name: self.pressed(n))
            button.pack(pady=4)
            self.buttons[name] = button
        self.last_var = tk.StringVar(value="Last Action:\nNONE")
        self.round_var = tk.StringVar(value="Round:\n0")
        self.counts_var = tk.StringVar(value=self._counts_text())
        ttk.Label(frame, textvariable=self.last_var, justify="center",
                  font=("Segoe UI", 10, "bold")).pack(pady=(8, 0))
        ttk.Label(frame, textvariable=self.round_var, justify="center").pack()
        ttk.Label(frame, textvariable=self.counts_var, font=("Consolas", 9)).pack(pady=(4, 0))

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self.heartbeat)

    def pressed(self, name):
        if name == ANTE:
            self.round += 1
        self.counts[name] += 1
        self.history.append((round(time.time(), 3), name, self.round))
        self.last_var.set("Last Action:\n%s" % name.upper())
        self.round_var.set("Round:\n%d" % self.round)
        self.counts_var.set(self._counts_text())
        # A brief highlight so a click is visible on screen.
        button = self.buttons[name]
        button.configure(relief="sunken")
        self.root.after(250, lambda: button.configure(relief="raised"))
        self.write_status()

    def _counts_text(self):
        return ("ANTE clicks: %d   BONUS clicks: %d   PLAY clicks: %d"
                % (self.counts[ANTE], self.counts[BONUS], self.counts[PLAY_BUTTON]))

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
        status = {"title": self.config.test_window_title, "pid": os.getpid(),
                  "hwnd": int(self.root.wm_frame(), 16), "heartbeat": time.time(),
                  "round": self.round, "last_action": (self.history[-1][1].upper()
                                                       if self.history else "NONE"),
                  "counts": dict(self.counts), "buttons": self.button_rects(),
                  "history": self.history[-50:]}
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
    parser.add_argument("--write-config", action="store_true",
                        help="store this table's button positions in config/mouse_controller.json")
    args = parser.parse_args(argv)

    # Same pixel coordinates as the tracker and PyAutoGUI.
    from calibration.calibrator import make_dpi_aware
    make_dpi_aware()

    config = load_mouse_config()
    root = tk.Tk()
    table = TestPokerTable(root, config)
    root.update()
    if args.write_config:
        centres = table.centres()
        config.ante_button, config.play_button = centres[ANTE], centres[PLAY_BUTTON]
        save_mouse_config(config)
        print("Stored TEST button positions: ANTE %s  PLAY %s (automation_enabled=%s)"
              % (config.ante_button, config.play_button, config.automation_enabled), flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
