"""The SCENARIO / DEALER display: what it shows for each state, that it shows
the tracker's "scenario" payload rather than working anything out, that a new
round never shows the last round's decision, and that unchanged polls do not
touch the widgets."""

import pytest

from poker import scenario_engine as se
from ui.scenario_panel import scenario_view


def slots(cards, status="CONFIRMED", readings=5):
    return {slot: {"card": card, "status": status, "readings": readings}
            for slot, card in cards.items()}


TRIPS = {"player_1": "7S", "player_2": "7D", "flop_1": "7H", "flop_2": "KC", "flop_3": "3S"}
NOTHING = {"player_1": "JS", "player_2": "9D", "flop_1": "7C", "flop_2": "5H", "flop_3": "2S"}


def view(scenario):
    decision, colour, details, qualification, dealer, dealer_colour = scenario_view(scenario)
    return {"decision": decision, "colour": colour, "details": details,
            "qualification": qualification, "dealer": dealer}


# -- the three decisions -------------------------------------------------------

def test_play_shows_decision_primary_cards_and_matches():
    scenario = se.evaluate_round(slots(TRIPS), round_id=1)
    assert scenario["matched_scenarios"] == [se.PLAYER_PAIR, se.PLAYER_PAIR_PLUS_FLOP_MATCH]
    shown = view(scenario)
    assert shown["decision"] == "Decision: PLAY"
    assert "Primary: THREE_OF_A_KIND" in shown["details"]
    assert "Player: 7S 7D" in shown["details"] and "Flop: 7H KC 3S" in shown["details"]
    assert "PLAYER_PAIR," in shown["details"]
    assert "PLAYER_PAIR_PLUS_FLOP_MATCH" in shown["details"]


def test_dont_play_is_shown_with_a_space_and_the_actual_hand():
    shown = view(se.evaluate_round(slots(NOTHING), round_id=1))
    assert shown["decision"] == "Decision: DON'T PLAY"
    assert "Primary: NONE - hand HIGH_CARD" in shown["details"]
    assert "Matched: none" in shown["details"]


def test_wait_shows_the_reason_and_which_cards_are_missing_and_why():
    """WAIT names the current round's cards holding it up (never another round's -
    see test_a_new_round_shows_wait_not_the_last_decision)."""
    waiting = slots(TRIPS, "CONFIRMING", 1)
    waiting["player_2"] = {"card": None, "status": "UNKNOWN", "readings": 0}
    shown = view(se.evaluate_round(waiting, round_id=1))
    assert shown["decision"] == "Decision: WAIT"
    assert shown["details"].startswith("Reason:\nWaiting for confirmed player + flop cards\n")
    assert "player_1: 7S is CONFIRMING" in shown["details"]
    assert "player_2: no card (UNKNOWN)" in shown["details"]
    assert "+2 more" in shown["details"]              # five reasons, three shown
    assert "Primary" not in shown["details"] and "Matched" not in shown["details"]


def test_card_cells_mark_each_cards_status():
    from app import card_cell

    assert card_cell("AC", "CONFIRMED") == "AC ✓"
    assert card_cell("8D", "HELD", held=True) == "8D ✓"
    assert card_cell("9H", "CONFIRMING") == "9H …"
    assert card_cell("7D", "AMBIGUOUS") == "7D ?"
    assert card_cell(None, "UNKNOWN") == "-- ✗"
    assert card_cell(None, "AMBIGUOUS") == "-- ?"
    assert card_cell(None, "EMPTY") == "--"
    assert card_cell("10D", "CONFIRMED") == "10D✓"     # fits the 5-character cell
    assert card_cell("AC", None, held=True) == "AC."         # an update without statuses


def test_no_payload_is_a_wait_too():
    shown = view(None)
    assert shown["decision"] == "Decision: WAIT"
    assert "PLAY" not in shown["details"]


def test_every_line_fits_beside_the_cards():
    """About 34 characters of Consolas 8 fit in the space beside the card table."""
    cards = {"player_1": "AS", "player_2": "7D", "flop_1": "AH", "flop_2": "AC",
             "flop_3": "7C", "turn": "10D", "river": "10S"}
    dealer = se.evaluate_dealer_qualification(["JC", "JD"], ["AH", "AC", "7C", "10D", "10S"])
    shown = view(se.evaluate_round(slots(cards), dealer=dealer))
    for line in (shown["details"] + "\n" + shown["dealer"]).splitlines():
        assert len(line) <= 34, line


# -- dealer, separate from the decision ---------------------------------------

def test_dealer_qualified_is_shown_separately():
    dealer = se.evaluate_dealer_qualification(["JC", "JD"], ["7H", "KC", "3S", "2D", "9C"])
    shown = view(se.evaluate_round(slots(NOTHING), dealer=dealer))
    assert shown["decision"] == "Decision: DON'T PLAY"      # the dealer changes nothing
    assert shown["dealer"] == "Cards: JC JD  Hand: PAIR OF Js"
    assert shown["qualification"] == "Qualification: QUALIFIED"


def test_dealer_not_qualified():
    dealer = se.evaluate_dealer_qualification(["3S", "3H"], ["7C", "5H", "2S", "10D", "KC"])
    shown = view(se.evaluate_round(slots(NOTHING), dealer=dealer))
    assert shown["dealer"] == "Cards: 3S 3H  Hand: PAIR OF 3s"
    assert shown["qualification"] == "Qualification: NOT QUALIFIED"


def test_dealer_pending_before_the_showdown():
    shown = view(se.evaluate_round(slots(TRIPS)))
    assert shown["qualification"] == "Qualification: PENDING"
    assert shown["dealer"] == ""


# -- a new round ---------------------------------------------------------------

def test_a_new_round_shows_wait_not_the_last_decision():
    monitor = se.ScenarioMonitor(log=type("L", (), {"info": lambda *a: None})())
    dealer = ["JC", "JD"]
    full = dict(TRIPS, turn="2D", river="9C")
    played = view(monitor.observe(slots(full), 1, dealer_cards=dealer, board_ready=True))
    assert played["decision"] == "Decision: PLAY"
    assert played["qualification"] == "Qualification: QUALIFIED"

    new_round = {"player_1": {"card": "2C", "status": "CONFIRMING", "readings": 1}}
    shown = view(monitor.observe(new_round, 2))
    assert shown["decision"] == "Decision: WAIT"
    assert "7S" not in shown["details"] and "PLAYER_PAIR" not in shown["details"]
    assert shown["qualification"] == "Qualification: PENDING"
    assert shown["dealer"] == ""


# -- the Tk panel and the main window ------------------------------------------

@pytest.fixture(scope="module")
def root():
    """One Tk for the module: creating several in one process is what makes
    Tcl intermittently fail to find its own library on this machine. The
    main-window test destroys it, so it runs last."""
    tk = pytest.importorskip("tkinter")
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass                        # App.on_close already destroyed it


def test_the_panel_only_updates_when_the_payload_changes(root):
    from ui.scenario_panel import ScenarioPanel

    panel = ScenarioPanel(root)
    start = panel.updates
    scenario = se.evaluate_round(slots(TRIPS), round_id=1)
    assert panel.show(scenario) is True
    for _ in range(20):
        assert panel.show(dict(scenario)) is False       # same content, new dict
    assert panel.updates == start + 1
    assert panel.decision_var.get() == "Decision: PLAY"

    panel.reset()
    assert panel.decision_var.get() == "Decision: WAIT"
    assert "7S" not in panel.details_var.get()


def test_the_main_window_shows_the_trackers_scenario_payload(root, monkeypatch):
    """The window displays payload["scenario"] as given - it recalculates nothing."""
    import app as app_module
    from config.settings import CARD_SLOTS

    def fail(*args, **kwargs):
        raise AssertionError("the UI must not run the Scenario Engine")

    monkeypatch.setattr(se, "detect_player_decision", fail)
    monkeypatch.setattr(se, "evaluate_round", fail)

    window = app_module.App(root)
    # An update is only looked at while a tracking session is open, so that a
    # frame left in the queue by a stopped tracker cannot repaint the window.
    # start() is not called here because it would build a real Tracker and ask
    # the database; this flag is what _handle_event consults.
    window.tracking = True
    cards = {slot: TRIPS.get(slot) for slot in CARD_SLOTS}
    # Deliberately not what these cards would give, to prove it is only displayed.
    scenario = {"decision": se.DONT_PLAY, "primary_scenario": "FROM_THE_TRACKER",
                "player_cards": ["7S", "7D"], "flop_cards": ["7H", "KC", "3S"],
                "matched_scenarios": [], "detected_hand": "HIGH_CARD",
                "dealer_cards": [], "dealer_hand": None, "dealer_qualified": None}
    payload = {"state": "FLOP", "cards": cards, "seen": cards, "reads": {}, "held": set(),
               "panels": None, "diagnostics": None, "dealer": None, "timing": {},
               "uncertain": [], "scenario": scenario}
    window._handle_event("update", payload)
    assert window.scenario_panel.decision_var.get() == "Decision: DON'T PLAY"
    assert "FROM_THE_TRACKER" in window.scenario_panel.details_var.get()

    window._handle_event("update", dict(payload, scenario={
        "decision": se.WAIT, "reason": se.WAIT_REASON, "player_cards": [None, None],
        "flop_cards": [None] * 3, "matched_scenarios": []}))
    assert window.scenario_panel.decision_var.get() == "Decision: WAIT"
    assert "FROM_THE_TRACKER" not in window.scenario_panel.details_var.get()

    window.on_close()
