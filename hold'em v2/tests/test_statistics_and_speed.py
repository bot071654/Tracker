"""Two things that were asked for together: how results are counted, and how
the tracker keeps up with cards that are only on screen for a moment.

The speed matters for the reading, not just the CPU. The community cards and
the dealer's are visible briefly, and the tracker used to spend 396ms of every
350ms poll recognising nine cards - so it ran slower than it meant to and a
short-lived card got two or three looks. Recognising only the cards whose
pixels have changed brings a still table down to about 11ms, which is where
the extra looks come from.

The statistics are history, not prediction: they count rounds that were
actually recorded, and a round is only recorded once all nine cards were read.
"""

import queue

import pytest

from database.db import running_win_percentages, summarise_results


# -- counting results ---------------------------------------------------------

def test_no_rounds_yet_does_not_divide_by_zero():
    stats = summarise_results({})
    assert stats["rounds"] == 0
    assert stats["player_percent"] == 0.0
    assert stats["dealer_percent"] == 0.0
    assert stats["tie_percent"] == 0.0


def test_the_percentages_come_from_the_counts():
    stats = summarise_results({"Player": 2, "Dealer": 1})
    assert stats["rounds"] == 3
    assert stats["player_percent"] == 66.7
    assert stats["dealer_percent"] == 33.3


def test_dealer_percentage():
    assert summarise_results({"Player": 1, "Dealer": 3})["dealer_percent"] == 75.0


def test_tie_percentage():
    stats = summarise_results({"Player": 1, "Dealer": 1, "Tie": 2})
    assert stats["tie_percent"] == 50.0
    assert stats["player_percent"] + stats["dealer_percent"] + stats["tie_percent"] \
        == pytest.approx(100.0)


def test_only_completed_rounds_are_counted():
    """A round that never finished has no winner, and must not become a tie."""
    rows = [{"winner": "Player"}, {"winner": None}, {"winner": "Dealer"},
            {"winner": ""}, {"winner": "Tie"}]
    running = running_win_percentages(rows)
    assert len(running) == 3, "an unfinished round was counted"
    assert running[-1]["round"] == 3


def test_the_running_percentage_is_the_history_after_each_round():
    rows = [{"winner": "Player"}, {"winner": "Dealer"}, {"winner": "Player"}]
    running = running_win_percentages(rows)
    assert [r["player_percent"] for r in running] == [100.0, 50.0, 66.7]
    assert [r["player_wins"] for r in running] == [1, 1, 2]


def test_the_running_percentage_of_no_rounds_is_empty():
    assert running_win_percentages([]) == []


# -- the statistics panel -----------------------------------------------------

def test_the_panel_says_so_when_there_is_nothing_to_show():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    root.withdraw()

    import app as app_module

    try:
        application = app_module.App.__new__(app_module.App)
        application.stats_var = tk.StringVar(master=root)
        application.show_statistics(summarise_results({}))
        assert "No completed rounds" in application.stats_var.get()

        application.show_statistics(summarise_results({"Player": 3, "Dealer": 1}))
        shown = application.stats_var.get()
        assert "Completed rounds : 4" in shown
        assert "75.0%" in shown
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


# -- reading only what changed ------------------------------------------------

@pytest.fixture()
def tracker():
    import tracker as tracker_module
    return tracker_module.Tracker({"monitor": 1}, queue.Queue())


def test_an_unchanged_slot_is_not_read_again(tracker, monkeypatch):
    """The saving that lets a short-lived card be sampled more often."""
    np = pytest.importorskip("numpy")
    import tracker as tracker_module

    image = np.zeros((40, 30, 3), dtype="uint8")
    calls = []

    def counting_read_table(config, images=None, regions=None):
        calls.append(sorted(images or {}))
        return ({slot: None for slot in tracker_module.CARD_SLOTS},
                {slot: {"present": False, "card": None, "confidence": 0.0,
                        "confident": False, "ratio": 0.0}
                 for slot in tracker_module.CARD_SLOTS})

    monkeypatch.setattr(tracker_module, "read_table", counting_read_table)

    images = {"player_1": image}
    tracker._read_changed(images)
    assert "player_1" in calls[0], "the first sight of a card must be read"

    tracker._read_changed({"player_1": image.copy()})
    assert "player_1" not in calls[1], "identical pixels were read a second time"


def test_a_changed_slot_is_read_again(tracker, monkeypatch):
    np = pytest.importorskip("numpy")
    import tracker as tracker_module

    calls = []

    def counting_read_table(config, images=None, regions=None):
        calls.append(sorted(images or {}))
        return ({slot: None for slot in tracker_module.CARD_SLOTS},
                {slot: {"present": False, "card": None, "confidence": 0.0,
                        "confident": False, "ratio": 0.0}
                 for slot in tracker_module.CARD_SLOTS})

    monkeypatch.setattr(tracker_module, "read_table", counting_read_table)

    tracker._read_changed({"player_1": np.zeros((40, 30, 3), dtype="uint8")})
    tracker._read_changed({"player_1": np.full((40, 30, 3), 200, dtype="uint8")})
    assert "player_1" in calls[1], "a card that changed was not read again"


def test_an_empty_slot_is_always_looked_at(tracker, monkeypatch):
    """"Nothing there" is cheap to establish, and a stale card must never
    outlive the card itself."""
    import tracker as tracker_module

    calls = []

    def counting_read_table(config, images=None, regions=None):
        calls.append(sorted(images or {}))
        return ({slot: None for slot in tracker_module.CARD_SLOTS},
                {slot: {"present": False, "card": None, "confidence": 0.0,
                        "confident": False, "ratio": 0.0}
                 for slot in tracker_module.CARD_SLOTS})

    monkeypatch.setattr(tracker_module, "read_table", counting_read_table)
    tracker._read_changed({"player_1": None})
    tracker._read_changed({"player_1": None})
    assert "player_1" in calls[0] and "player_1" in calls[1]


def test_the_cached_reading_is_the_one_reported(tracker, monkeypatch):
    """Skipping the work must not change the answer."""
    np = pytest.importorskip("numpy")
    import tracker as tracker_module

    image = np.zeros((40, 30, 3), dtype="uint8")
    read = {"present": True, "card": "8S", "confidence": 0.91,
            "confident": True, "ratio": 0.6}

    def once(config, images=None, regions=None):
        cards = {slot: None for slot in tracker_module.CARD_SLOTS}
        reads = {slot: {"present": False, "card": None, "confidence": 0.0,
                        "confident": False, "ratio": 0.0}
                 for slot in tracker_module.CARD_SLOTS}
        if "player_1" in (images or {}):
            cards["player_1"] = "8S"
            reads["player_1"] = read
        return cards, reads

    monkeypatch.setattr(tracker_module, "read_table", once)

    first_cards, _ = tracker._read_changed({"player_1": image})
    second_cards, second_reads = tracker._read_changed({"player_1": image.copy()})
    assert first_cards["player_1"] == "8S"
    assert second_cards["player_1"] == "8S", "the cached answer was lost"
    assert second_reads["player_1"]["confidence"] == 0.91
