"""tools/live_validation.py: the read-only recorder and the hand-by-hand report.

A scripted table drives the real Tracker (no screen), the recorder writes what
a live session would, and the report is checked against known cards.
"""

import json
import os
import queue
import sys

import numpy
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import live_validation as lv  # noqa: E402


@pytest.fixture
def session(tmp_path, monkeypatch):
    import tracker as tracker_module
    from config.settings import CARD_SLOTS
    from poker import scenario_engine as se

    screen = {}

    def fake_read_table(config, images=None):
        reads, seen = {}, {}
        for slot in CARD_SLOTS:
            card = screen.get(slot)
            reads[slot] = {"card": card, "present": bool(card), "confident": bool(card),
                           "confidence": 0.9 if card else 0.0, "suit_margin": 0.2}
            seen[slot] = card
        return seen, reads

    # A different picture every poll: the tracker skips re-reading unchanged pixels.
    polls = {"n": 0}

    def grab(monitor=1):
        polls["n"] += 1
        return numpy.full((768, 1366, 3), polls["n"] % 250, "uint8")

    monkeypatch.setattr(tracker_module, "grab_full_screen", grab)
    monkeypatch.setattr(tracker_module, "monitor_origin", lambda monitor=1: (0, 0))
    monkeypatch.setattr(tracker_module, "read_table", fake_read_table)
    monkeypatch.setattr(tracker_module.db, "insert_hand", lambda record: (False, None))
    monkeypatch.setattr(se, "load_engine_config", lambda: se.EngineConfig())
    monkeypatch.setattr(lv, "SESSIONS", str(tmp_path))
    monkeypatch.setattr(lv, "MIN_FRAME_GAP", 0.0)          # scripted polls take no time
    from automation import mouse_controller as mc
    monkeypatch.setattr(mc, "load_mouse_config", mc.load_mouse_config)   # undone after the test
    lv.force_read_only()

    folder = str(tmp_path / "s1")
    recorder = lv.Recorder(folder)
    undo = lv.install_tracker_hooks(recorder)
    config = {"monitor": 1, "clear_frames": 3, "regions": {
        slot: {"left": 500 + 80 * i, "top": 300, "width": 70, "height": 95}
        for i, slot in enumerate(CARD_SLOTS)}}
    tr = tracker_module.Tracker(config, queue.Queue())

    def run(n):
        for _ in range(n):
            tr._tick()

    yield tr, screen, run, recorder, folder
    undo()


def play_two_hands(screen, run):
    run(4)
    screen.update(player_1="7S", player_2="7D")
    run(6)
    screen.update(flop_1="7H", flop_2="KC", flop_3="3S")
    run(8)
    screen.update(turn="2D", river="9C", dealer_1="AS", dealer_2="AD")
    run(8)
    screen.clear()
    run(6)
    screen.update(player_1="JS", player_2="9D", flop_1="7C", flop_2="5H", flop_3="2S")
    run(10)
    screen.clear()
    run(6)


def test_recorder_writes_polls_and_frames_and_report_scores_hands(session, capsys):
    tr, screen, run, recorder, folder = session
    play_two_hands(screen, run)
    recorder.close()

    polls = lv.load_jsonl(os.path.join(folder, "polls.jsonl"))
    assert polls and all("round" in p and "status" in p for p in polls)
    assert os.listdir(os.path.join(folder, "frames")), "evidence frames were saved"

    hands = lv.split_hands(polls)
    assert len(hands) == 2
    first, second = hands[0][0], hands[1][0]
    truth = {
        str(first): {"player_1": "7S", "player_2": "7D", "flop_1": "7H", "flop_2": "KC",
                     "flop_3": "3S", "turn": "2D", "river": "9C", "dealer_1": "AS",
                     "dealer_2": "AH"},                       # tracker said AD -> wrong
        str(second): {"player_1": "JS", "player_2": lv.UNCLEAR, "flop_1": "7C",
                      "flop_2": "5H", "flop_3": "2S", "turn": None, "river": None,
                      "dealer_1": None, "dealer_2": None},
    }
    with open(os.path.join(folder, "ground_truth.json"), "w") as handle:
        json.dump(truth, handle)

    summary = lv.report("s1")
    out = capsys.readouterr().out
    by_round = {round_id: (info, verdicts) for round_id, info, verdicts, _, _ in summary}
    info, verdicts = by_round[first]
    assert info["decision"] == "PLAY"
    assert verdicts["dealer_2"] == "wrong" and verdicts["player_1"] == "correct"
    info, verdicts = by_round[second]
    assert info["decision"] == "DON'T_PLAY"
    assert verdicts["player_2"] == "unclear" and verdicts["turn"] == "not_dealt"
    assert info["slots"]["player_1"]["latency_ms"] is not None
    # scored: 9 in hand 1 + 4 in hand 2 (one UNCLEAR, four not dealt); one wrong
    assert "accuracy (correct / visible, unclear excluded): 12/13" in out
    assert os.path.exists(os.path.join(folder, "evidence", "hand_r%s.jpg" % first))


def test_classify():
    info = {"final": "AS", "refusals": {"AMBIGUOUS": 0, "UNKNOWN": 0}}
    assert lv.classify("AS", info) == "correct"
    assert lv.classify("AH", info) == "wrong"
    assert lv.classify(lv.UNCLEAR, info) == "unclear"
    assert lv.classify(None, info) == "phantom"
    empty = {"final": None, "refusals": {"AMBIGUOUS": 2, "UNKNOWN": 0}}
    assert lv.classify("AS", empty) == "ambiguous"
    assert lv.classify("AS", dict(empty, refusals={"AMBIGUOUS": 0, "UNKNOWN": 3})) == "unread"
    assert lv.classify(None, empty) == "not_dealt"


def test_leakage_is_detected():
    def row(t, cards, present):
        return {"t": t, "round": 2, "cards": cards, "present": present,
                "status": {s: None for s in lv.SLOTS}, "scenario": {}}

    blank = {s: None for s in lv.SLOTS}
    rows = [row(1.0, dict(blank, player_1="KS"), dict({s: False for s in lv.SLOTS}))]
    info = lv.analyse_hand(rows, previous_final=dict(blank, player_1="KS"))
    assert info["slots"]["player_1"]["leaks"] == 1


def test_the_same_card_in_the_same_place_two_hands_running_is_not_a_leak():
    blank = {s: None for s in lv.SLOTS}
    absent = {s: False for s in lv.SLOTS}
    seen = dict(row := {"t": 1.0, "round": 2, "cards": dict(blank, flop_2="QC"),
                        "present": dict(absent, flop_2=True), "conf": dict(blank, flop_2=0.95),
                        "read_as": dict(blank, flop_2="QC"),
                        "status": {s: None for s in lv.SLOTS}, "scenario": {}})
    held = dict(row, t=2.0, present=absent, read_as=blank)
    info = lv.analyse_hand([seen, held], previous_final=dict(blank, flop_2="QC"))
    assert info["slots"]["flop_2"]["leaks"] == 0


def test_revision_after_confirmation_is_a_false_confirmation():
    def row(t, card, status):
        return {"t": t, "round": 3, "cards": dict({s: None for s in lv.SLOTS}, flop_1=card),
                "present": dict({s: False for s in lv.SLOTS}, flop_1=True),
                "status": dict({s: None for s in lv.SLOTS}, flop_1=status), "scenario": {}}

    info = lv.analyse_hand([row(1.0, "7D", "CONFIRMING"), row(1.2, "7D", "CONFIRMED"),
                            row(1.4, "7H", "CONFIRMED")])
    assert info["slots"]["flop_1"]["revisions"] == ["7H"]
    assert info["slots"]["flop_1"]["latency_ms"] == 200


def test_read_only_mode_forces_automation_off_and_never_loads_pyautogui():
    import subprocess

    code = ("import sys; sys.path.insert(0, 'tools'); import live_validation as lv; "
            "lv.force_read_only(); from automation import mouse_controller as mc; "
            "import queue, tracker; t = tracker.Tracker({'monitor': 1}, queue.Queue()); "
            "print(mc.load_mouse_config().automation_enabled, "
            "t._run_action_controller('WAITING', {}, None), 'pyautogui' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.stdout.strip().splitlines()[-1] == "False None False", out.stderr[-800:]
