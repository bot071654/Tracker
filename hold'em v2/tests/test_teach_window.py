"""The Teach / Correct Scenario window, driven the way a person drives it.

Every test here points the window's `save_path` at a temporary file, so running
the suite can never write the real config/scenarios.json, and passes `load_rows`
so the backtest never needs the database.

The logic these tests drive lives in poker/teaching.py and is tested on its own
in test_teaching.py; what is checked here is the window: that it shows what was
captured, that it refuses a scenario that has moved on, and that nothing is
written before Activate.
"""

import json
import os

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenario_engine as se        # noqa: E402
from poker import scenarios as sr              # noqa: E402
from poker import teaching                     # noqa: E402
from ui import rule_windows                    # noqa: E402


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:                  # pragma: no cover - headless
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """Catch every message box, so no test can open a real modal and hang.

    Not a convenience: a modal opened by a test blocks the whole run with
    nothing on screen to click, and which tests reach one depends on what the
    window refuses. Every box is recorded here instead, and the tests that care
    read the list.
    """
    seen = []

    def record(kind):
        def catch(title, message, **_kwargs):
            seen.append((kind, title, message))
        return catch

    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(rule_windows.messagebox, name, record(name))
    return seen


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    """Point scenarios.load/save at a scratch file.

    load() and save() bind the real path as a default argument, so redirecting
    them means patching the functions rather than the constant - the same trick
    test_preround_rule_window.py uses, for the same reason.
    """
    path = str(tmp_path / "scenarios.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rules(), handle, indent=4)
    monkeypatch.setattr(sr, "load", lambda *args, **kw: _load(path))
    monkeypatch.setattr(sr, "save", lambda data, *args, **kw: _save(data, path))
    return path


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle)
    return {section: {"default": stored[section]["default"],
                      "rules": list(stored[section]["rules"])}
            for section in sr.DEFAULTS}


def _save(data, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4)

SETTLED = ["player_1", "player_2", "flop_1", "flop_2", "flop_3"]
CARDS = {"player_1": "AS", "player_2": "7D",
         "flop_1": "KC", "flop_2": "7C", "flop_3": "2H"}

PAIR_FOLD = sr.flop_rule("Pair", sr.ANY, sr.FOLD, name="Pair -> DON'T PLAY")
HIGH_PLAY = sr.flop_rule("High Card", sr.ANY, sr.PLAY, name="High Card -> PLAY")


def rules():
    return {"preround": {"default": sr.ANTE, "rules": []},
            "flop": {"default": sr.PLAY,
                     "rules": [dict(PAIR_FOLD), dict(HIGH_PLAY)]}}


def payload(cards=None, round_id=123, state="FLOP"):
    return {"state": state, "round_id": round_id,
            "cards": dict(CARDS if cards is None else cards),
            "statuses": {slot: "CONFIRMED" for slot in SETTLED},
            "scenario": {"decision": se.DONT_PLAY, "reason": "nothing matched"}}


def hand_row(identifier, player, flop, winner="Dealer"):
    return {"id": identifier, "winner": winner,
            "player_card_1": player[0], "player_card_2": player[1],
            "flop_card_1": flop[0], "flop_card_2": flop[1],
            "flop_card_3": flop[2], "turn_card": "3D", "river_card": "4S",
            "dealer_card_1": "QH", "dealer_card_2": "JD"}


ROWS = [
    hand_row(1, ["AS", "7D"], ["KC", "7C", "2H"], "Player"),   # Pair
    hand_row(2, ["9S", "9D"], ["KC", "8C", "2H"], "Dealer"),   # Pair
    hand_row(3, ["AS", "4D"], ["KC", "9C", "2H"], "Dealer"),   # High Card
]


class Live:
    """Stands in for the application: what it would report about the tracker."""

    def __init__(self, session=4, round_id=123, running=True):
        self.session = session
        self.round_id = round_id
        self.running = running

    def __call__(self):
        return self.snapshot.is_current(self.session, self.round_id, self.running)


@pytest.fixture
def live():
    return Live()


@pytest.fixture
def snapshot():
    taken, why = teaching.capture(payload(), rules(), session=4)
    assert taken is not None, why
    return taken


@pytest.fixture
def window(root, snapshot, live, tmp_path):
    live.snapshot = snapshot
    made = rule_windows.TeachScenarioWindow(
        root, snapshot, rules=rules(), is_current=live,
        load_rows=lambda: ROWS, save_path=str(tmp_path / "scenarios.json"))
    yield made
    made.destroy()


def text(widget):
    return widget.get("1.0", "end")


def choose(window, root, key=None, action=None, position=None):
    """Pick a scope, a decision and a position, the way the widgets do."""
    if action is not None:
        window.action_var.set(action)
    if key is not None:
        index = [scope.key for scope in window.scopes].index(key)
        window.scope_list.selection_clear(0, "end")
        window.scope_list.selection_set(index)
    if key is not None or action is not None:
        window.refresh()
    if position is not None:
        window.position_var.set(str(position))
        window.refresh(keep_position=True)
    root.update()


# -- 11. the window opens and shows the captured scenario ---------------------

def test_the_window_opens(window, root):
    root.update()
    assert window.winfo_exists()
    assert window.title() == "Teach / Correct Scenario"


def labels(widget):
    """Every piece of label text in a window, wherever it is nested."""
    found = []
    for child in widget.winfo_children():
        try:
            found.append(str(child.cget("text")))
        except tk.TclError:                     # frames, listboxes, text boxes
            pass
        found.extend(labels(child))
    return found


def test_it_shows_the_captured_round_stage_and_cards(window, root):
    root.update()
    shown = labels(window)
    assert "Round:" in shown and "123" in shown
    assert "Stage:" in shown and "FLOP" in shown
    assert "Player Cards:" in shown and "A♠  7♦" in shown
    assert "Flop:" in shown and "K♣  7♣  2♥" in shown
    assert "Turn:" in shown and "River:" in shown
    assert shown.count("-") == 2                # an empty turn and river
    assert "Hand Type:" in shown and "Pair" in shown


def test_it_shows_the_decision_and_the_matched_rule(window):
    assert window.snapshot.matched_name() == "Pair -> DON'T PLAY"
    assert window.snapshot.priority() == "#1"
    assert "FOLD" in window._decision_text()


def test_the_engines_word_is_shown_when_it_differs_from_the_rules(root, tmp_path):
    """The banner may have been showing the engine. Both are named, so nobody
    has to guess which decision the rule is about to change."""
    empty = {"preround": {"default": sr.ANTE, "rules": []},
             "flop": {"default": sr.PLAY, "rules": []}}
    taken, _ = teaching.capture(payload(), empty, session=1)
    made = rule_windows.TeachScenarioWindow(
        root, taken, rules=empty, load_rows=lambda: ROWS,
        save_path=str(tmp_path / "s.json"))
    try:
        assert taken.action == sr.PLAY               # the flop default
        assert "Scenario Engine says DON'T_PLAY" in made._decision_text()
    finally:
        made.destroy()


# -- 12. choosing the correct decision ----------------------------------------

def test_only_the_sections_own_actions_are_offered(window):
    assert window.snapshot.actions() == sr.FLOP_ACTIONS


def test_wait_is_not_offered(window, root):
    root.update()
    assert se.WAIT not in window.snapshot.actions()


def test_choosing_a_decision_changes_the_rule_the_window_would_build(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    assert window.rule()["action"] == sr.PLAY
    choose(window, root, action=sr.FOLD)
    assert window.rule()["action"] == sr.FOLD


# -- 13. the preview ----------------------------------------------------------

def test_the_preview_shows_the_stage_the_hand_and_the_decision(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    shown = text(window.preview)
    assert "Stage = FLOP" in shown
    assert "AND Hand type = Pair" in shown
    assert "PLAY ON" in shown


def test_the_preview_names_the_current_rule_and_the_new_one(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    shown = text(window.preview)
    assert "CURRENT RULE:" in shown and "Pair -> DON'T PLAY" in shown
    assert "NEW RULE:" in shown and window.rule()["name"] in shown


def test_the_preview_shows_the_scope_not_a_wider_one(window, root):
    """A narrow scope must be visible as a narrow scope before it is saved."""
    choose(window, root, key="hand+no draw", action=sr.PLAY)
    shown = text(window.preview)
    assert "AND On the table: no draw" in shown
    assert window.rule()["condition"] == "no draw"


def test_the_preview_shows_exactly_what_gets_stored(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    shown = text(window.preview)
    for key, value in window.rule().items():
        if key == "name":
            continue
        assert "%r" % value in shown


# -- 17/18. conflicts and priority --------------------------------------------

def test_a_conflicting_rule_is_named_in_the_conflict_pane(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    shown = text(window.conflicts)
    assert "CONFLICT" in shown
    assert "Pair -> DON'T PLAY" in shown


def test_the_conflict_pane_explains_first_match_wins(window, root):
    choose(window, root, key="hand", action=sr.PLAY, position=3)
    shown = text(window.conflicts)
    assert "SHADOWED" in shown
    assert "never run" in shown
    assert "first match wins" in shown


def test_the_suggested_position_is_offered_and_is_above_the_conflict(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    assert window.position_var.get() == "1"
    assert "Suggested position: #1" in text(window.conflicts)


def test_the_resulting_order_is_shown_with_the_new_rule_marked(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    shown = text(window.conflicts)
    assert "[NEW]" in shown
    assert "1. %s  [NEW]" % window.rule()["name"] in shown
    assert "2. Pair -> DON'T PLAY" in shown
    assert "3. High Card -> PLAY" in shown


def test_the_conflict_pane_promises_nothing_is_deleted(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    assert "No existing rule is deleted or rewritten" in text(window.conflicts)


def test_the_position_chooser_offers_every_position(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    assert list(window.position.cget("values")) == ["1", "2", "3"]


def test_choosing_a_position_does_not_reset_it(window, root):
    """It used to: the combobox called refresh, which put the suggestion back."""
    choose(window, root, key="hand", action=sr.PLAY, position=3)
    assert window.position_var.get() == "3"
    assert window.index() == 2


def test_changing_the_scope_offers_the_suggestion_again(window, root):
    choose(window, root, key="hand", action=sr.PLAY, position=3)
    choose(window, root, key="hand+no draw")
    assert window.position_var.get() == "1"


# -- 16. duplicates -----------------------------------------------------------

def test_an_exact_duplicate_blocks_save_and_test(window, root):
    choose(window, root, key="hand", action=sr.FOLD)     # identical to rule #1
    assert "DUPLICATE" in text(window.conflicts)
    assert str(window.test_button.cget("state")) == "disabled"


def test_a_duplicate_never_creates_a_second_copy(window, root, dialogs):
    choose(window, root, key="hand", action=sr.FOLD)
    window.save_and_test()
    root.update()
    assert dialogs and "already exists" in dialogs[0][2]
    assert window.proposed is None
    assert not os.path.exists(window.save_path)


def test_a_conflicting_answer_is_allowed_through(window, root):
    """Answering an existing question differently is the whole point."""
    choose(window, root, key="hand", action=sr.PLAY)
    assert "CONFLICT" in text(window.conflicts)
    assert str(window.test_button.cget("state")) == "normal"


# -- 23. save and test --------------------------------------------------------

def test_save_and_test_writes_nothing(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    root.update()
    assert window.tested is True
    assert not os.path.exists(window.save_path)


def test_save_and_test_says_so_in_as_many_words(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    root.update()
    assert "nothing has been written yet" in text(window.result).lower()


def test_save_and_test_reports_the_change_to_the_captured_hand(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    root.update()
    shown = text(window.result)
    assert "CURRENT SCENARIO" in shown
    assert "FOLD  ->  PLAY ON" in shown


def test_save_and_test_reports_real_recorded_hand_numbers(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    root.update()
    shown = text(window.result)
    assert "Hands on record:     3" in shown
    assert "This rule matches:   2" in shown
    assert "Hands affected:      2" in shown


def test_activate_is_disabled_until_save_and_test(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    assert str(window.activate_button.cget("state")) == "disabled"
    window.save_and_test()
    root.update()
    assert str(window.activate_button.cget("state")) == "normal"


def test_changing_anything_after_testing_disables_activate_again(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    root.update()
    choose(window, root, key="hand+no draw")
    assert window.tested is False
    assert str(window.activate_button.cget("state")) == "disabled"


def test_activating_without_testing_does_nothing(window, root, dialogs):
    choose(window, root, key="hand", action=sr.PLAY)
    window.activate()
    root.update()
    assert dialogs and "Save & Test first" in dialogs[0][2]
    assert not os.path.exists(window.save_path)


# -- 25/26. activation and persistence ----------------------------------------

def test_activate_writes_the_rule_at_the_approved_position(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    window.activate()
    root.update()
    saved = sr.load(window.save_path)
    assert [rule["name"] for rule in saved["flop"]["rules"]] == [
        window.proposed_rule["name"], "Pair -> DON'T PLAY", "High Card -> PLAY"]


def test_activate_says_what_it_did(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    window.activate()
    root.update()
    shown = text(window.result)
    assert "Rule activated successfully." in shown
    assert "Priority:  #1" in shown
    assert "Decision:  PLAY ON" in shown


def test_an_activated_rule_carries_the_note_about_where_it_came_from(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    window.activate()
    root.update()
    note = sr.load(window.save_path)["flop"]["rules"][0]["taught"]
    assert note["round_id"] == 123
    assert note["original_decision"] == sr.FOLD
    assert note["corrected_decision"] == sr.PLAY


def test_an_activated_rule_decides_the_next_matching_hand(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    window.activate()
    root.update()
    reloaded = sr.load(window.save_path)         # as if the app had restarted
    assert sr.decide_flop(reloaded, window.snapshot.features)[0] == sr.PLAY


def test_activation_tells_the_application_to_reload(root, snapshot, live, tmp_path):
    live.snapshot = snapshot
    reloaded = []
    made = rule_windows.TeachScenarioWindow(
        root, snapshot, rules=rules(), is_current=live, load_rows=lambda: ROWS,
        on_change=lambda: reloaded.append(True),
        save_path=str(tmp_path / "scenarios.json"))
    try:
        choose(made, root, key="hand", action=sr.PLAY)
        made.save_and_test()
        made.activate()
        root.update()
        assert reloaded == [True]
    finally:
        made.destroy()


def test_activating_twice_does_not_add_the_rule_twice(window, root):
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    window.activate()
    window.activate()
    root.update()
    assert len(sr.load(window.save_path)["flop"]["rules"]) == 3


def test_existing_rules_survive_activation_unchanged(window, root):
    before = rules()["flop"]["rules"]
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    window.activate()
    root.update()
    assert sr.load(window.save_path)["flop"]["rules"][1:] == before


# -- 27/28/29. stale state ----------------------------------------------------

def test_a_new_round_cancels_the_teaching(window, root, live):
    choose(window, root, key="hand", action=sr.PLAY)
    live.round_id = 124
    window.save_and_test()
    root.update()
    shown = text(window.result)
    assert "round has changed" in shown
    assert "Nothing was saved" in shown
    assert window.tested is False
    assert not os.path.exists(window.save_path)


def test_a_stopped_tracker_cancels_the_teaching(window, root, live):
    choose(window, root, key="hand", action=sr.PLAY)
    live.running = False
    window.save_and_test()
    root.update()
    assert "tracker has stopped" in text(window.result)
    assert not os.path.exists(window.save_path)


def test_a_restarted_tracker_cancels_the_teaching(window, root, live):
    choose(window, root, key="hand", action=sr.PLAY)
    live.session = 5
    window.save_and_test()
    root.update()
    assert "restarted" in text(window.result)
    assert not os.path.exists(window.save_path)


def test_the_round_changing_between_test_and_activate_cancels_it(window, root, live):
    """The dangerous one: tested against the right hand, activated against the
    wrong one."""
    choose(window, root, key="hand", action=sr.PLAY)
    window.save_and_test()
    root.update()
    assert window.tested is True

    live.round_id = 999
    window.activate()
    root.update()
    assert "round has changed" in text(window.result)
    assert not os.path.exists(window.save_path)


def test_a_cancelled_teaching_cannot_be_resumed(window, root, live):
    choose(window, root, key="hand", action=sr.PLAY)
    live.running = False
    window.save_and_test()
    root.update()
    assert str(window.test_button.cget("state")) == "disabled"
    assert str(window.activate_button.cget("state")) == "disabled"


# -- 30. the manual rule builders still work ----------------------------------

def test_the_preround_rule_window_still_opens(root, scratch):
    made = rule_windows.PreRoundRules(root)
    try:
        root.update()
        assert made.winfo_exists()
        assert made.listbox.size() >= 1
    finally:
        made.destroy()


def test_the_flop_rule_window_still_opens_and_lists_its_rules(root, scratch):
    made = rule_windows.FlopRules(root)
    try:
        root.update()
        assert [made.listbox.get(index) for index in range(made.listbox.size())] == [
            "  1.  If Pair, fold", "  2.  If High Card, play on"]
    finally:
        made.destroy()


def test_add_move_and_remove_still_work(root, scratch):
    made = rule_windows.FlopRules(root)
    try:
        made.hand_var.set("Flush")
        made.condition_var.set(sr.ANY)
        made.action_var.set(sr.ACTION_LABELS[sr.PLAY])
        made.add_rule()
        root.update()
        assert made.listbox.size() == 3
        assert made.body()["rules"][2]["hand"] == "Flush"

        made.listbox.selection_set(2)
        made.move(-1)
        root.update()
        assert made.body()["rules"][1]["hand"] == "Flush"

        made.listbox.selection_clear(0, "end")
        made.listbox.selection_set(1)
        made.remove_rule()
        root.update()
        assert made.listbox.size() == 2
        assert [rule["name"] for rule in made.body()["rules"]] == [
            "Pair -> DON'T PLAY", "High Card -> PLAY"]
        assert _load(scratch)["flop"]["rules"] == made.body()["rules"]
    finally:
        made.destroy()


def test_the_default_chooser_still_works(root, scratch):
    made = rule_windows.FlopRules(root)
    try:
        made.default_var.set(sr.ACTION_LABELS[sr.FOLD])
        made.change_default()
        root.update()
        assert _load(scratch)["flop"]["default"] == sr.FOLD
    finally:
        made.destroy()


def test_the_manual_builder_still_refuses_a_duplicate(root, scratch, dialogs):
    made = rule_windows.FlopRules(root)
    try:
        made.hand_var.set("Pair")
        made.condition_var.set(sr.ANY)
        made.action_var.set(sr.ACTION_LABELS[sr.PLAY])
        made.add_rule()
        root.update()
        assert dialogs                               # refused
        assert made.body()["rules"] == rules()["flop"]["rules"]
    finally:
        made.destroy()


def test_a_taught_rule_is_listed_by_the_manual_builder_like_any_other(
        root, scratch, snapshot):
    """One rule store: a correction shows up in the window that was there
    before, described by the same describer, movable by the same buttons."""
    taught = teaching.stamp(
        teaching.available_scopes(snapshot)[0].build(sr.PLAY), snapshot, sr.PLAY)
    stored = rules()
    stored["flop"]["rules"].insert(0, taught)
    _save(stored, scratch)

    made = rule_windows.FlopRules(root)
    try:
        root.update()
        assert made.listbox.size() == 3
        assert sr.describe_flop_rule(taught) in made.listbox.get(0)

        made.listbox.selection_set(0)
        made.move(1)                                 # movable like any other
        root.update()
        assert made.body()["rules"][1]["name"] == taught["name"]
        assert _load(scratch)["flop"]["rules"][1]["taught"] == taught["taught"]
    finally:
        made.destroy()


def test_the_manual_builder_refuses_a_duplicate_of_a_taught_rule(
        root, scratch, snapshot, dialogs):
    """The reason the teaching note is in METADATA_KEYS: without it, this rule
    and the identical hand-made one would look like two different rules."""
    scope = [s for s in teaching.available_scopes(snapshot) if s.key == "hand"][0]
    taught = teaching.stamp(scope.build(sr.PLAY), snapshot, sr.PLAY)
    stored = rules()
    stored["flop"]["rules"] = [taught]
    _save(stored, scratch)

    made = rule_windows.FlopRules(root)
    try:
        made.hand_var.set(scope.fields["hand"])
        made.condition_var.set(scope.fields["condition"])
        made.action_var.set(sr.ACTION_LABELS[sr.PLAY])
        made.add_rule()
        root.update()
        assert dialogs
        assert len(made.body()["rules"]) == 1
    finally:
        made.destroy()
