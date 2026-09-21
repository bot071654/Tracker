"""The pre-round rule builder, now that it can also ask about the winner.

Two choosers were added to the existing window - who won the previous round,
and how many rounds in a row the player has won. What matters is that a rule
built here is the same shape as one built any other way, that it reaches
config/scenarios.json unchanged, and that rules saved before those choosers
existed still load and still read correctly in the list.
"""

import json
import os
import tempfile

import pytest

tk = pytest.importorskip("tkinter")

from poker import scenarios  # noqa: E402


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machine
        pytest.skip("no display available: %s" % exc)
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


def open_window(root, monkeypatch, tmp_path, starting_rules=None):
    """The real window, reading and writing a scratch scenarios.json.

    load() and save() bind the real path as a default argument, so redirecting
    them takes patching the functions themselves rather than the constant.
    """
    from ui import rule_windows

    path = str(tmp_path / "scenarios.json")
    data = starting_rules or {
        section: {"default": body["default"], "rules": []}
        for section, body in scenarios.DEFAULTS.items()
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4)

    monkeypatch.setattr(scenarios, "load", lambda *args: _load(path))
    monkeypatch.setattr(scenarios, "save", lambda data, *args: _save(data, path))

    built = rule_windows.PreRoundRules(root)
    root.update()
    built.scratch = path
    return built


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        stored_data = json.load(handle)
    return {section: {"default": stored_data[section]["default"],
                      "rules": list(stored_data[section]["rules"])}
            for section in scenarios.DEFAULTS}


def _save(data, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4)


@pytest.fixture()
def window(root, monkeypatch, tmp_path):
    built = open_window(root, monkeypatch, tmp_path)
    try:
        yield built
    finally:
        try:
            built.destroy()
        except tk.TclError:
            pass


def stored(window):
    with open(window.scratch, "r", encoding="utf-8") as handle:
        return json.load(handle)


# -- the two new choosers -----------------------------------------------------

def test_the_window_offers_a_winner_and_a_streak(window):
    assert window.winner_var.get() == scenarios.ANY
    assert window.streak_var.get() == scenarios.ANY


def test_a_winner_rule_can_be_built_and_saved(window):
    window.winner_var.set(scenarios.DEALER)
    window.action_var.set(scenarios.ACTION_LABELS[scenarios.SKIP])
    window.add_rule()

    rule = stored(window)["preround"]["rules"][-1]
    assert rule["previous_winner"] == scenarios.DEALER
    assert rule["action"] == scenarios.SKIP
    assert rule["player_win_streak"] == 0
    assert rule["previous_dealer_hand"] == scenarios.ANY


def test_a_streak_rule_can_be_built_and_saved(window):
    window.streak_var.set("2")
    window.action_var.set(scenarios.ACTION_LABELS[scenarios.ANTE])
    window.add_rule()

    rule = stored(window)["preround"]["rules"][-1]
    assert rule["player_win_streak"] == 2
    assert rule["action"] == scenarios.ANTE


def test_a_rule_built_here_matches_one_built_in_code(window):
    """Same shape either way, or the engine would treat them differently."""
    window.winner_var.set(scenarios.PLAYER)
    window.action_var.set(scenarios.ACTION_LABELS[scenarios.ANTE])
    window.add_rule()

    from_ui = stored(window)["preround"]["rules"][-1]
    in_code = scenarios.preround_rule(
        scenarios.ANY, scenarios.ANY, scenarios.ANTE,
        previous_winner=scenarios.PLAYER)
    assert set(from_ui) == set(in_code)
    assert {key: from_ui[key] for key in from_ui if key != "name"} == \
           {key: in_code[key] for key in in_code if key != "name"}


def test_a_rule_built_here_is_the_one_the_engine_applies(window):
    window.winner_var.set(scenarios.DEALER)
    window.action_var.set(scenarios.ACTION_LABELS[scenarios.SKIP])
    window.add_rule()

    action, _ = scenarios.decide_preround(
        window.scenarios, {"winner": scenarios.DEALER})
    assert action == scenarios.SKIP


def test_a_rule_matching_every_round_is_still_refused(window, monkeypatch):
    """The existing guard, now aware that a winner or streak is enough."""
    warned = []
    monkeypatch.setattr("ui.rule_windows.messagebox.showwarning",
                        lambda *args, **kwargs: warned.append(args))
    window.add_rule()                       # everything left at Any
    assert warned, "a rule with no condition at all was accepted"
    assert window.body()["rules"] == []


def test_the_streak_chooser_maps_to_and_from_the_stored_number():
    assert scenarios.streak_from_choice(scenarios.ANY) == 0
    assert scenarios.streak_from_choice("2") == 2
    assert scenarios.streak_from_choice("nonsense") == 0
    assert scenarios.streak_choice(0) == scenarios.ANY
    assert scenarios.streak_choice(2) == "2"


# -- what was already saved ---------------------------------------------------

def test_rules_saved_before_the_choosers_existed_still_show(root, monkeypatch,
                                                            tmp_path):
    """scenarios.json on disk predates previous_winner and player_win_streak."""
    old = {
        "preround": {"default": scenarios.ANTE, "rules": [
            {"previous_dealer_hand": "Pair",
             "previous_player_hand": scenarios.ANY,
             "action": scenarios.SKIP, "name": "an old rule"},
            # A rule hand-edited into the file with no name at all.
            {"previous_dealer_hand": scenarios.ANY,
             "previous_player_hand": "Two Pair",
             "action": scenarios.SKIP}]},
        "flop": {"default": scenarios.PLAY, "rules": []},
    }
    built = open_window(root, monkeypatch, tmp_path, old)
    try:
        listed = [built.listbox.get(index)
                  for index in range(built.listbox.size())]
        # Every rule is described from its own conditions, named or not.
        assert any("dealer had Pair" in line for line in listed), listed
        assert any("player had Two Pair" in line for line in listed), listed
    finally:
        built.destroy()


def test_an_old_rule_survives_a_trip_through_the_window(root, monkeypatch,
                                                        tmp_path):
    """Adding a new rule must not rewrite or drop the ones already saved."""
    old = {
        "preround": {"default": scenarios.ANTE, "rules": [
            {"previous_dealer_hand": "Pair",
             "previous_player_hand": scenarios.ANY,
             "action": scenarios.SKIP, "name": "an old rule"}]},
        "flop": {"default": scenarios.PLAY, "rules": []},
    }
    built = open_window(root, monkeypatch, tmp_path, old)
    try:
        built.winner_var.set(scenarios.PLAYER)
        built.add_rule()
        saved = stored(built)["preround"]["rules"]
        assert saved[0] == old["preround"]["rules"][0], "the old rule changed"
        assert saved[1]["previous_winner"] == scenarios.PLAYER
    finally:
        built.destroy()


def test_the_standard_rules_read_correctly_in_the_list(root, monkeypatch,
                                                       tmp_path):
    """The five installed rules must be legible, not blank or mangled."""
    fresh = {section: {"default": body["default"], "rules": []}
             for section, body in scenarios.DEFAULTS.items()}
    scenarios.add_standard_rules(fresh)

    built = open_window(root, monkeypatch, tmp_path, fresh)
    try:
        listed = " ".join(built.listbox.get(index)
                          for index in range(built.listbox.size()))
        assert "the player won the last 2 rounds" in listed
        assert "dealer won" in listed
        assert "player won" in listed
    finally:
        built.destroy()


def test_a_rule_built_here_reads_the_same_in_the_list(window):
    """A rule the user builds is named after its own conditions."""
    window.winner_var.set(scenarios.DEALER)
    window.action_var.set(scenarios.ACTION_LABELS[scenarios.SKIP])
    window.add_rule()

    rule = window.body()["rules"][-1]
    assert window.describe(rule) == scenarios.describe_preround(rule)
