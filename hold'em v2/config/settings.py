"""Configuration loading/saving and logging setup.

Configuration lives in config/config.json. Screen coordinates are never
hard-coded: they are written there by the calibration tool.
"""

import json
import logging
import os
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "config.json")
LOG_PATH = os.path.join(ROOT, "logs", "tracker.log")
TEMPLATE_DIR = os.path.join(ROOT, "recognition", "templates")
EXCEL_PATH = os.path.join(ROOT, "data", "poker_hands.xlsx")

# The nine card slots, in the order the calibrator asks for them.
CARD_SLOTS = [
    "dealer_1",
    "dealer_2",
    "flop_1",
    "flop_2",
    "flop_3",
    "turn",
    "river",
    "player_1",
    "player_2",
]

SLOT_LABELS = {
    "dealer_1": "Dealer Card 1",
    "dealer_2": "Dealer Card 2",
    "flop_1": "Flop Card 1",
    "flop_2": "Flop Card 2",
    "flop_3": "Flop Card 3",
    "turn": "Turn",
    "river": "River",
    "player_1": "Player Card 1",
    "player_2": "Player Card 2",
}

DEFAULT_CONFIG = {
    "regions": {},                    # slot -> {left, top, width, height}
    "monitor": 1,                     # MSS monitor index used at calibration time
    "screen_size": None,              # [w, h] recorded at calibration time
    # How often the tracker samples the screen. The community cards and the
    # dealer's are on screen briefly, so the sampling rate decides how much
    # evidence a card gets before it goes. A poll costs about 11ms when the
    # table has not changed and about 350ms on the poll where a whole new
    # table appears, so 0.2s is roughly 5% of one core at rest - fast enough
    # to see a short-lived card several times without running hot.
    "poll_interval_seconds": 0.20,
    # Pause tracking while the game window is not in front. Part of the
    # foreground window's title, matched case-insensitively - "Chrome", or
    # something from the casino page's own title. Empty means never pause,
    # which is how the tracker behaved before the setting existed. It matches
    # a WINDOW title, not a browser tab; see window_focus.py.
    "game_window_title": "",
    "confidence_threshold": 0.62,     # minimum template-match score to accept a card
    # The dealer's cards are held to a higher standard than the rest, because
    # they are only face up for a second or two at the showdown. Every other
    # card is seen 40-60 times and settles; the dealer's are seen about twice,
    # so a single marginal frame would otherwise become the stored answer - and
    # a wrong dealer card changes the winner. Measured over 200 recorded hands,
    # a fifth of dealer readings sit below 0.75, which is where the misreads are.
    "dealer_min_confidence": 0.80,    # the best single look must be this good
    "dealer_min_total": 1.50,         # and the readings together must add to this
    # ...or one look good enough to stand on its own. The dealer's cards are
    # sometimes face up for a single poll before the table clears, and a clear
    # reading at this level was being thrown away for want of a second look:
    # a jack and a queen of clubs at 0.86 and 0.88 were refused on exactly that
    # ground. Set from the recorded hands, where it admits those and still
    # refuses the readings that have been wrong (0.70, 0.75).
    "dealer_strong_confidence": 0.85,

    "presence_threshold": 0.35,       # min fraction of card-coloured pixels for "card present"
    "stable_frames": 2,               # identical reads required before a hand is saved
    "latch_cards": True,              # keep a card once read, through hands passing over it
    "clear_frames": 10,               # empty polls before the remembered cards are dropped
    "change_confirm_frames": 2,       # polls of a different player pair that mean a new deal
    # Save the crop of any card that was seen but refused, so an intermittent
    # failure leaves evidence instead of a dash in a log. Off by default: this
    # writes a file per failure and is meant for diagnosing, not for running.
    "debug_save_failures": False,
    # Live recognition diagnostics: whenever what is read changes, save the game
    # area with every card box drawn and labelled, each card's crop and the rank
    # and suit glyphs it was matched on, to debug/live and debug/card_crops.
    # Off by default; the Live Recognition Debug window switches it on.
    "live_diagnostics": False,
    # Dealer-card diagnostics, on while the dealer's slow or missing cards are
    # being investigated: logs DEALER_TIMING while the dealer's cards are in
    # play and DEALER_LATENCY when each is shown, and saves every changed
    # dealer crop with its box and scores to debug/live/dealer_*.png. Needs no
    # checkbox, so a live session cannot end without the evidence.
    "dealer_debug": True,
    # Cut each dealer card to its own face before reading it. The dealer's
    # boxes are wider than the card, and the felt they take in was being read
    # as part of the rank. Dealer slots only; see recognition/dealer_crop.py.
    "dealer_face_cut": True,
    "validate_with_result_boxes": False,
    "result_boxes": {},               # optional: {"player": {...}, "dealer": {...}}

    # Spoken announcements of what the tracker has already worked out - the
    # cards as they settle, the player's hand, and the result. Read-only: the
    # voice never interacts with the game, and it runs on its own thread so it
    # cannot hold up the recognition loop. See voice/announcer.py.
    #
    # With voice_enabled false nothing is imported, no thread is started and
    # no speech engine is created; the tracker behaves exactly as it did
    # before the feature existed.
    "voice_enabled": True,
    "voice_rate": 180,                # words per minute; pyttsx3's default is 200
    "voice_volume": 1.0,              # 0.0 - 1.0
    # Part of an installed voice's name, matched case-insensitively, e.g.
    # "zira" or "david". Empty means the system default. A name that matches
    # nothing logs a warning and falls back rather than failing.
    "voice_name": "",
    # Read the table out as well: your two cards, the flop, the turn, the
    # river and the hand you are holding, then who won. Off, because the
    # voice's job is the decision on the green banner and a running commentary
    # talks over it. Set true to hear the cards again.
    "voice_announce_cards": False,
}


def load_config():
    """Load config.json, filling in any missing keys with defaults."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                config.update(json.load(handle))
        except (OSError, json.JSONDecodeError) as exc:
            logging.getLogger(__name__).error("Could not read config.json: %s", exc)
    return config


def save_config(config):
    """Write config.json, creating the directory if needed."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=4)
    logging.getLogger(__name__).info("Configuration saved to %s", CONFIG_PATH)


def is_calibrated(config):
    """True when every one of the nine card regions has been calibrated."""
    regions = config.get("regions") or {}
    return all(slot in regions for slot in CARD_SLOTS)


def setup_logging(level=logging.INFO):
    """Configure logging to logs/tracker.log plus the console. Idempotent."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_poker_tracker_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-22s %(message)s")

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    root._poker_tracker_configured = True
