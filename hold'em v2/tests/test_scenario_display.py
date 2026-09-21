"""What "Your scenarios say" puts on screen while a card is still arriving.

The window used to show a recommendation the moment five cards were in the
slots, whatever the tracker thought of them. On the live table that meant a
rule fired on a card sliding past its box, and the line changed a second
later once the card that belonged there was read. Now the panel says what it
is waiting for instead.
"""

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenarios  # noqa: E402

CONFIRMED, CONFIRMING = "CONFIRMED", "CONFIRMING"

# Round 37 of session 20260917_120408, at the poll the window first showed a
# recommendation: flop_3 read KD beside KS in flop_2 and looked like a paired
# flop. It was really the three of diamonds.
CARDS = {"player_1": "10H", "player_2": "KC", "flop_1": "9S",
         "flop_2": "KS", "flop_3": "KD",
         "turn": None, "river": None, "dealer_1": None, "dealer_2": None}
TRUTH = dict(CARDS, flop_3="3D")


@pytest.fixture(autouse=True)
def no_background_work(monkeypatch):
    from app import App

    monkeypatch.setattr(App, "_startup_checks", lambda self: None)
    monkeypatch.setattr(App, "_refresh_statistics", lambda self: None)
    monkeypatch.setattr(App, "_refresh_scenario_history", lambda self: None)


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:            # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:                   # pragma: no cover - already closed
        pass


@pytest.fixture
def app(root, tmp_path):
    from app import App
    from ui.window_geometry import WindowStateStore

    store = WindowStateStore(path=str(tmp_path / "window_state.json"),
                             node="test-machine")
    built = App(root, screen=(1920, 1080), work_area=(0, 0, 1920, 1080),
                window_state=store)
    built.scenarios = {section: {"default": body["default"], "rules": []}
                       for section, body in scenarios.DEFAULTS.items()}
    scenarios.add_standard_rules(built.scenarios)
    root.update()
    return built


def statuses(**overrides):
    every = {slot: CONFIRMED for slot in CARDS}
    every.update(overrides)
    return every


def test_a_settled_flop_shows_the_rule_that_matched(app):
    app._show_scenario(TRUTH, statuses())
    shown = app.scenario_var.get()
    assert "PLAY" in shown


def test_an_unsettled_card_shows_what_it_is_waiting_for(app):
    app._show_scenario(CARDS, statuses(flop_3=CONFIRMING))
    shown = app.scenario_var.get()
    assert "waiting for" in shown
    assert "Flop Card 3" in shown
    assert "PLAY" not in shown


def test_the_wrong_rule_is_never_put_on_screen(app):
    """It was: "If the flop is paired, play on", from a flop that was not."""
    app._show_scenario(CARDS, statuses(flop_3=CONFIRMING))
    assert "flop is paired" not in app.scenario_var.get()


def test_the_recommendation_appears_once_the_card_settles(app):
    app._show_scenario(CARDS, statuses(flop_3=CONFIRMING))
    assert "waiting for" in app.scenario_var.get()

    app._show_scenario(TRUTH, statuses())
    shown = app.scenario_var.get()
    assert "waiting for" not in shown
    assert "flop is paired" not in shown        # 9S KS 3D is not paired


def test_nothing_on_the_table_is_not_reported_as_waiting(app):
    """Before the flop there is nothing to wait for - the panel says so."""
    empty = {slot: None for slot in CARDS}
    app._show_scenario(empty, {slot: "EMPTY" for slot in CARDS})
    assert "waiting for" not in app.scenario_var.get()


def test_statuses_are_passed_through_from_the_tracker(app, monkeypatch):
    """The window must hand the panel the statuses the tracker sent it."""
    seen = {}
    monkeypatch.setattr(type(app), "_show_scenario",
                        lambda self, cards, statuses=None: seen.update(
                            cards=cards, statuses=statuses))
    app._show_reading({
        "state": "FLOP", "cards": CARDS, "seen": CARDS, "held": set(),
        "statuses": statuses(flop_3=CONFIRMING), "uncertain": [],
        "panels": {}, "scenario": None, "action": None,
    })
    assert seen["statuses"]["flop_3"] == CONFIRMING
