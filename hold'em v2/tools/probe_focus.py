"""Find out what to put in config["game_window_title"].

The tracker pauses by comparing the foreground window's TITLE against that
setting. It cannot enumerate Chrome's tabs - nobody can, from outside the
browser - so the string to configure is whatever this prints while the real
poker page is the one in front. See window_focus.py for what that can and
cannot promise.

Read-only. It only asks Windows which window is in front; it does not click,
focus or move anything.

Run:  python tools/probe_focus.py
      python tools/probe_focus.py --seconds 60

Then, while it runs, switch to the poker game, to something else, and back.
Every line is a change - the one printed while the game was in front is the
string (or a distinctive part of it) to put in config/config.json.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import window_focus as wf  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="how long to watch (default 30)")
    args = parser.parse_args()

    if wf._user32() is None:
        print("This platform has no foreground-window API (not Windows).")
        print("window_focus.foreground_title() will return None here, which")
        print("the tracker reads as 'cannot tell' - it will never pause.")
        return

    print("Watching the foreground window for %.0f seconds." % args.seconds)
    print("Switch to the poker game, then somewhere else, then back.")
    print("-" * 70)

    last = object()
    start = time.time()
    end = start + args.seconds
    while time.time() < end:
        title = wf.foreground_title()
        if title != last:
            # Exactly what GetWindowTextW returned. repr() so nothing is
            # normalized, shortened or invented - trailing spaces, odd
            # casing and non-ASCII characters all show up as they really are.
            print("[FOCUS] foreground=%r  (t=%.1fs)" % (title, time.time() - start))
            last = title
        time.sleep(0.3)

    print("-" * 70)
    print("Put the distinctive part of the poker game's line into")
    print('config/config.json, as "game_window_title". Matching is a')
    print("case-insensitive substring, so the exact casing does not matter.")


if __name__ == "__main__":
    main()
