"""The diagnostics-only round recorder (round_validation.py) and the harness that feeds it (tools/validate_rounds.py).

The recorder is driven here through the real Tracker with scripted readings and scripted panel snapshots, with the clock in the
updates set by the test so the timings it reports can be checked exactly. The harness is checked for what it must never do.
"""

import json
import os
import subprocess
import sys

import pytest

import round_lifecycle as rl
import round_validation as rv
from poker import scenarios as scenario_rules

from test_round_lifecycle import (FLOP, FULL, NEXT_FLOP, NEXT_PLAYER, PLAYER, RIVER, TURN,   # noqa: F401 - fixtures too
                                  panel_snapshot, run, settle)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_UP = {**PLAYER, **FLOP, **TURN, **RIVER}
OTHER = {**NEXT_PLAYER, **NEXT_FLOP, "turn": "QH", "river": "3C", "dealer_1": "AD", "dealer_2": "KC"}
OTHER_BUILD_UP = {k: v for k, v in OTHER.items() if not k.startswith("dealer")}
STEP = 0.5                                                      # seconds between polls in the scripted clock


class Watch:
    """Drives the tracker, stamps each update with a scripted clock, and feeds the recorder and the alert probe."""

    def __init__(self, run):
        self.run, self.clock = run, 100.0
        self.recorder, self.probe = rv.RoundRecorder(), rv.AlertProbe(session=1)

    def poll(self, table, mode, polls=1, step=STEP, unreadable=()):
        self.run.panel_mode = mode
        payload = None
        for _ in range(polls):
            self.clock += step
            payload = self.run.poll(table, unreadable=unreadable)
            payload["emitted_mono"] = self.clock
            self.recorder.observe(payload)
            self.recorder.observe_alert(self.probe.observe(payload), payload)
        return payload

    def rounds(self):
        return self.recorder.finish()


@pytest.fixture
def watch(run):
    return Watch(run)


# -- what is recorded for one round ----------------------------------------------------------------------------------------

def test_a_round_whose_panel_arrives_late_records_when_it_was_confirmed(watch):
    watch.poll(BUILD_UP, "missing", polls=4)
    watch.poll(FULL, "missing", polls=3)                      # complete; the panels are not readable yet ...
    watch.poll(FULL, "agree", polls=3)                        # ... then both rows read
    watch.poll({}, "missing", polls=6)
    [record] = [r for r in watch.rounds() if r["completed"]]
    assert record["result_timeline"] == [[record["first_seen_s"], "none"], [record["completed_at_s"], "unconfirmed"],
                                         [record["completed_at_s"] + 3 * STEP, "confirmed"]]
    assert record["seconds_complete_to_confirmed"] == pytest.approx(3 * STEP)
    assert record["result_status"] == rl.RESULT_CONFIRMED
    assert record["table_result"] == record["panel_winner"] and record["winners_agree"] is True
    for side in ("player", "dealer"):
        row = record["panel"][side]
        assert row["max_confirmed_cards"] == 5 and row["complete_vs_completion_s"] == pytest.approx(3 * STEP)
        assert "consistent" in row["verdicts_seen"] and row["polls_visible"] >= 3
    assert record["ante_skip_alerts"] == 1 and record["pending_notices"] == 0 and record["unknown_notices"] == 0


def test_a_round_whose_panel_vanishes_unread_is_recorded_as_pending_and_never_alerts(watch):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "partial", polls=4)                      # the dealer's row only ever partly read
    watch.poll(FULL, "missing", polls=2)                      # and then the panels disappear
    watch.poll({}, "missing", polls=6)
    watch.poll({}, "missing", polls=1, step=30.0)             # past the alert's timeout
    [record] = [r for r in watch.rounds() if r["completed"]]
    assert record["result_status"] == rl.RESULT_UNCONFIRMED and record["confirmed_at_s"] is None
    assert record["panel"]["player"]["max_confirmed_cards"] == 5 and record["panel"]["dealer"]["max_confirmed_cards"] < 5
    assert record["seconds_complete_to_panel_gone"] == pytest.approx(4 * STEP)
    assert record["panel_gone_before_confirmation"] is True
    assert record["ante_skip_alerts"] == 0 and record["pending_notices"] == 1 and record["unknown_notices"] == 1


def test_a_contradicted_round_is_recorded_as_a_conflict(watch):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "disagree", polls=4)
    watch.poll({}, "disagree", polls=6)
    [record] = [r for r in watch.rounds() if r["completed"]]
    assert record["result_status"] == rl.RESULT_CONFLICT and record["conflict_at_s"] is not None
    assert record["confirmed_at_s"] is None and record["seconds_complete_to_confirmed"] is None
    assert record["ante_skip_alerts"] == 0 and record["unknown_notices"] == 1


def test_a_partial_next_deal_row_is_counted_as_ignored_not_as_a_conflict(watch):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "agree", polls=3)
    watch.poll(FULL, "next_deal", polls=4)
    [record] = [r for r in watch.rounds() if r["completed"]]
    assert record["result_status"] == rl.RESULT_CONFIRMED and record["conflict_at_s"] is None
    assert record["polls_conflict_ignored"] == 4


def test_the_table_state_and_when_it_cleared_are_recorded(watch):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "agree", polls=3)
    watch.poll({}, "agree", polls=6)
    [record] = [r for r in watch.rounds() if r["completed"]]
    assert "RIVER" in record["states_seen"] and "COMPLETE" in record["states_seen"]
    assert record["seconds_complete_to_table_cleared"] is not None and record["seconds_complete_to_table_cleared"] > 0


def test_a_panel_that_outlasts_the_cards_is_noticed_going_after_the_table_cleared(watch):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "agree", polls=3)
    generation = watch.run.memory.generation
    watch.run._observe_panels = lambda cards: panel_snapshot("agree", FULL, generation)    # the panels, whatever the table shows
    watch.poll({}, "agree", polls=5)                          # the table is empty but the panels are still up
    watch.run._observe_panels = lambda cards: panel_snapshot("missing", cards, 0)
    watch.poll({}, "missing", polls=2)                        # then they go
    [record] = [r for r in watch.rounds() if r["completed"]]
    assert record["panel_gone_after_table_cleared"] is True and record["panel_gone_at_s"] > record["table_cleared_at_s"]
    assert record["panel_gone_before_confirmation"] is False


def test_a_round_that_never_completes_is_still_recorded_and_has_no_timings(watch):
    watch.poll(BUILD_UP, "missing", polls=4)
    watch.poll({}, "missing", polls=8)
    [record] = watch.rounds()
    assert record["completed"] is False and record["completed_at_s"] is None
    assert record["seconds_complete_to_confirmed"] is None and record["seconds_complete_to_panel_gone"] is None
    assert record["pending_notices"] == 1 and record["ante_skip_alerts"] == 0


# -- rounds, alerts and the count of how often the alert fired ---------------------------------------------------------------

def test_two_rounds_are_two_records_and_each_alerts_once_however_the_table_flickers(watch):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "agree", polls=3)
    watch.poll({}, "missing", polls=6)
    watch.poll({}, "missing", polls=1, step=30.0)             # the alert's banner has timed out
    watch.poll({"player_1": "2C"}, "missing", polls=1)       # a stray card read between rounds re-arms nothing
    watch.poll({}, "missing", polls=6, step=30.0)
    watch.poll(OTHER_BUILD_UP, "missing", polls=3)
    watch.poll(OTHER, "agree", polls=3)
    watch.poll({}, "missing", polls=6)
    done = [r for r in watch.rounds() if r["completed"]]
    assert len(done) == 2 and done[0]["round_id"] != done[1]["round_id"]
    assert [r["ante_skip_alerts"] for r in done] == [1, 1]
    assert all(a["shown"] in rv.ALERT_ACTIONS for r in done for a in r["alerts"] if a["event"] == "show")
    summary = rv.summarize(watch.rounds())
    assert summary["rounds_completed"] == 2 and summary["rounds_confirmed"] == 2
    assert summary["rounds_with_ante_skip_alert"] == 2 and summary["most_ante_skip_alerts_for_one_round"] == 1
    assert summary["alert_without_confirmed_result"] == 0


def test_the_summary_counts_each_outcome_and_the_dealer_rows_queueing(watch):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "partial", polls=3)
    watch.poll({}, "missing", polls=8)
    summary = rv.summarize(watch.rounds())
    assert summary["rounds_completed"] == 1 and summary["rounds_unconfirmed"] == 1 and summary["rounds_confirmed"] == 0
    assert summary["player"]["rounds_with_row_complete"] == 1 and summary["dealer"]["rounds_with_row_complete"] == 0
    assert summary["dealer"]["max_confirmed_cards_median"] < 5


def test_the_alert_probe_uses_the_windows_own_functions(watch, monkeypatch):
    from app import App
    seen = []
    original = App._preround_ready
    monkeypatch.setattr(App, "_preround_ready", staticmethod(lambda window, payload: seen.append(1) or original(window, payload)))
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "agree", polls=3)
    watch.poll({}, "missing", polls=6)
    assert seen, "the probe did not ask the window's readiness rule"


# -- the evidence folder -----------------------------------------------------------------------------------------------------

def test_write_evidence_writes_three_files_and_nothing_else(watch, tmp_path):
    watch.poll(BUILD_UP, "missing", polls=3)
    watch.poll(FULL, "agree", polls=3)
    watch.poll({}, "missing", polls=6)
    folder = tmp_path / "evidence"
    rounds, summary = rv.write_evidence(str(folder), watch.recorder, {"mode": "test"})
    assert sorted(os.listdir(folder)) == ["polls.jsonl", "rounds.json", "summary.json"]
    assert json.load(open(folder / "rounds.json")) == json.loads(json.dumps(rounds))
    assert json.load(open(folder / "summary.json"))["run"] == {"mode": "test"}
    lines = [json.loads(line) for line in open(folder / "polls.jsonl")]
    assert len(lines) == len(watch.recorder.polls) and {"t", "round", "lifecycle", "status", "player", "dealer"} <= set(lines[0])


# -- read-only: what the recorder and the harness must never do ----------------------------------------------------------------

FORBIDDEN = ("pyautogui", "mouse_event", "SendInput", "keybd_event", "SetCursorPos", "pynput", "click(", "requests.", "urlopen",
             "psycopg", "sqlite3", "smtplib", "webbrowser")


@pytest.mark.parametrize("path", ["round_validation.py", os.path.join("tools", "validate_rounds.py")])
def test_nothing_that_clicks_types_or_phones_home_is_in_the_diagnostics(path):
    source = open(os.path.join(ROOT, path), encoding="utf-8").read()
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    for word in FORBIDDEN:
        assert word not in code.replace('"""', ""), "%s mentions %r" % (path, word)


def test_the_harness_keeps_every_output_in_its_evidence_folder_and_stubs_the_saves():
    source = open(os.path.join(ROOT, "tools", "validate_rounds.py"), encoding="utf-8").read()
    assert 'os.environ["POKER_TRACKER_HOME"] = os.path.join(evidence, "home")' in source
    assert 'os.environ.pop("POKER_TRACKER_API_URL", None)' in source
    assert "T.db = types.SimpleNamespace(insert_hand=slow_insert" in source and "T.excel_export = types.SimpleNamespace" in source
    assert "installed app's windows are on screen" in source                 # it refuses to run over the installed app
    assert "open(config_path" in source and '"w"' not in source.split("open(config_path")[1].split("\n")[0]   # config is only read


# -- the harness, end to end, with no casino ---------------------------------------------------------------------------------

SCREENSHOT = os.path.join(ROOT, "Screenshot 2026-09-02 152130.png")


@pytest.mark.skipif(not os.path.exists(SCREENSHOT), reason="no casino screenshot in the project folder")
def test_the_harness_replay_records_two_confirmed_rounds_one_alert_each(tmp_path):
    out = tmp_path / "evidence"
    result = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "validate_rounds.py"), "--replay", SCREENSHOT,
                             "--out", str(out)], capture_output=True, text=True, cwd=ROOT, timeout=240)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    assert {"rounds.json", "polls.jsonl", "summary.json", "report.md", "run.log", "home"} <= set(os.listdir(out))
    summary = json.load(open(out / "summary.json"))
    assert summary["rounds_completed"] == 2 and summary["rounds_confirmed"] == 2
    assert summary["rounds_with_ante_skip_alert"] == 2 and summary["most_ante_skip_alerts_for_one_round"] == 1
    assert summary["alert_without_confirmed_result"] == 0 and summary["winners_disagree"] == 0
    assert summary["run"]["mode"] == "replay" and summary["run"]["suit_margin"] == 0.05 and summary["run"]["rank_margin"] == 0.10
    assert summary["player"]["rounds_with_row_complete"] == 2 and summary["dealer"]["rounds_with_row_complete"] == 2
