"""Round-by-round evidence about the result panels and the round lifecycle. Diagnostics only: it watches, it never acts.

WHY

The panel-recognition and round-lifecycle fixes were checked against saved crops and a replayed screenshot. What none of that
can say is what the casino does in time: how long after the dealer's cards turn the panels are readable, whether both rows
reach five confirmed cards before the panels disappear, and whether the next-round ANTE / SKIP alert follows. This module
answers that from the stream of updates the tracker already emits (`Tracker.events`), with nothing added to the tracker.

WHAT IT RECORDS, PER ROUND (a round is a lifecycle round id: the span from its first card to the table clearing)

  * the round id, the states it passed through, whether and when it reached COMPLETE;
  * for each panel row (player, dealer): when it first and last showed, how many cards were confirmed at most, when it
    completed, how many polls had thumbnails still QUEUED behind the reading budget, and the status of every thumbnail read;
  * the table's calculated winner against the winner the panel implies, and whether they agree;
  * the result status over time - unconfirmed, confirmed, conflict - when it became confirmed, and how many polls ignored a
    conflict from a partial row (the next deal);
  * seconds from completion until the panel confirmed the result, and until the panels disappeared;
  * every alert event about the round (ANTE / SKIP, RESULT PENDING, RESULT UNKNOWN) and how many times it fired.

THE ALERT

The alert logic lives in the window. `AlertProbe` drives the same `AnteAlertLogic` with the same readiness, key and decision
functions the window uses (`App._preround_ready`, `_preround_key`, `_preround_for`), headless: no banner, no sound, no voice.

READ-ONLY

Nothing here reads a screen, clicks, types or contacts a backend. It is given updates and returns dictionaries. The only
files it writes are the ones `write_evidence` is asked to, in the folder it is given.
"""

import json
import os
import statistics
import time
from types import SimpleNamespace

import round_lifecycle as rl

SIDES = ("player", "dealer")
THUMBNAIL_STATUSES = ("CONFIRMED", "CONFIRMING", "HELD", "QUEUED", "AMBIGUOUS", "UNKNOWN")
ALERT_ACTIONS = ("ante", "skip")


def _side_blank():
    return {"first_visible_s": None, "last_visible_s": None, "complete_at_s": None, "max_confirmed_cards": 0,
            "polls_visible": 0, "polls_with_queued_thumbnails": 0, "thumbnails_detected_max": 0,
            "thumbnail_status_counts": {name: 0 for name in THUMBNAIL_STATUSES}, "last_verdict": None, "verdicts_seen": [],
            "last_confirmed": None, "refused_cards": []}


def _rounded(value):
    return None if value is None else round(value, 3)


class RoundRecorder:
    """Folds in tracker updates and keeps one record per round."""

    def __init__(self, dealer_watch=None, read_watch=None, poll_watch=None, panel_watch=None, machine_watch=None):
        self.dealer_watch = dealer_watch      # dealer_diagnostics.DealerWatch, or None for no dealer-box evidence
        self.read_watch = read_watch          # read_diagnostics.ReadWatch, or None for no per-slot reading evidence
        self.poll_watch = poll_watch          # poll_diagnostics.PollWatch, or None for no timing / load / crop-change / table-loss evidence
        self.panel_watch = panel_watch        # panel_diagnostics.PanelWatch, or None for no panel-thumbnail measurements (opt-in)
        self.machine_watch = machine_watch    # machine_diagnostics.MachineWatch, or None for no machine-state clues (opt-in)
        self.t0 = None
        self.rounds = {}             # round id -> record, in the order first seen
        self._open = None            # the record of the round being watched, or None while the table is empty
        self._cleared = None         # the last completed round, while its table is empty and the next round has not begun
        self.polls = []              # a compact line per poll, for the evidence folder

    # -- updates ---------------------------------------------------------------------------------------------------------

    def observe(self, payload, extras=None):
        """Fold in one tracker update. Returns the compact poll line that was kept.

        `extras` (optional, from the harness) carries what the update does not: {"frame": the frame this poll read,
        "layout_source", "regions" (the slots the tracker read from this poll), "region_sources"}. With it, and a dealer
        watch, every poll line also records dealer-box presence and the slot-assignment diagnostics. With it, and a read
        watch, every poll line also records the dealer slots' raw readings against the card memory ("slot_reads"), and
        extras["frame"] / ["origin"] / ["fresh"] let the watch save the pixels behind a reading that disagrees with it.
        """
        now = payload.get("emitted_mono")
        now = time.monotonic() if now is None else now
        if self.t0 is None:
            self.t0 = now
        t = now - self.t0
        lifecycle = payload.get("lifecycle") or {}
        panels = payload.get("panels") or {}
        sides = panels.get("sides") or {}
        state = lifecycle.get("state")

        record = None
        if state in (rl.IN_PROGRESS, rl.COMPLETE):
            round_id = lifecycle.get("round_id")
            record = self.rounds.get(round_id)
            if record is None:
                record = self.rounds[round_id] = self._new_record(round_id, t)
            self._open, self._cleared = record, None
            self._fold(record, t, payload, lifecycle, panels, sides)
        else:
            if self._open is not None:
                self._open["ended_s"] = _rounded(self._open["last_seen_s"])
                self._open["table_cleared_at_s"] = _rounded(t)
                self._cleared = self._open if self._open["completed"] else None
                self._open = None
            # The panels may outlast the cards: watch for them going until the next round begins.
            if (self._cleared is not None and self._cleared["panel_gone_at_s"] is None
                    and all((sides.get(side) or {}).get("state") in (None, "NOT_VISIBLE") for side in SIDES)):
                self._cleared["panel_gone_at_s"] = _rounded(t)
                self._cleared["panel_gone_after_table_cleared"] = True

        line = {"t": _rounded(t), "round": lifecycle.get("round_id"), "state": payload.get("state"), "lifecycle": state,
                "status": lifecycle.get("result_status"), "ignored": bool(lifecycle.get("panel_conflict_ignored"))}
        for side in SIDES:
            data = sides.get(side) or {}
            line[side] = {"panel": data.get("state"), "confirmed": len(data.get("confirmed") or ()),
                          "detected": data.get("detected"), "complete": bool(data.get("complete")),
                          "stale": bool(data.get("stale")),
                          "thumbnails": _count([read.get("status") for read in data.get("reads") or ()])}
        if self.dealer_watch is not None and extras is not None:
            reads = payload.get("reads") or {}
            state_now = {"layout_source": extras.get("layout_source"), "regions": extras.get("regions"),
                         "region_sources": extras.get("region_sources"),
                         "seen": [(payload.get("seen") or {}).get(slot) for slot in ("dealer_1", "dealer_2")],
                         "present": [round((reads.get(slot) or {}).get("ratio") or 0.0, 2) for slot in ("dealer_1", "dealer_2")]}
            # NOT "dealer": that key already holds the dealer's result-panel row.
            line["dealer_table"] = self.dealer_watch.observe(
                t, lifecycle.get("round_id"), payload.get("state"),
                {side: (sides.get(side) or {}).get("state") not in (None, "NOT_VISIBLE") for side in SIDES},
                state_now, extras.get("frame"), record)
        if self.read_watch is not None and extras is not None:
            try:
                line["slot_reads"] = self.read_watch.observe(t, lifecycle.get("round_id"), payload, extras)
            except Exception as exc:  # noqa: BLE001 - the watch guards itself; this is the second guard
                line["slot_reads"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        if self.poll_watch is not None and extras is not None:
            try:
                line["perf"] = self.poll_watch.observe(t, lifecycle.get("round_id"), payload, extras, line)
            except Exception as exc:  # noqa: BLE001 - the watch guards each part; this is the second guard
                line["perf"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        if self.panel_watch is not None and extras is not None:
            try:
                line["panel_thumbs"] = self.panel_watch.observe(t, lifecycle.get("round_id"), payload, extras)
            except Exception as exc:  # noqa: BLE001 - the watch guards itself; this is the second guard
                line["panel_thumbs"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        if self.machine_watch is not None and extras is not None:
            try:
                line["machine"] = self.machine_watch.observe(t, lifecycle.get("round_id"), payload, extras)
            except Exception as exc:  # noqa: BLE001 - the watch guards itself; this is the second guard
                line["machine"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        self.polls.append(line)
        return line

    def _new_record(self, round_id, t):
        return {"round_id": round_id, "first_seen_s": _rounded(t), "last_seen_s": t, "ended_s": None, "states_seen": [],
                "completed": False, "completed_at_s": None, "table_result": None, "table_fingerprint": None,
                "panel_winner": None, "winners_agree": None,
                "result_status": rl.RESULT_NONE, "result_timeline": [], "confirmed_at_s": None, "conflict_at_s": None,
                "seconds_complete_to_confirmed": None, "seconds_complete_to_panel_gone": None,
                "panel_gone_at_s": None, "panel_gone_before_confirmation": None, "panel_gone_after_table_cleared": False,
                "table_cleared_at_s": None, "seconds_complete_to_table_cleared": None,
                "polls_conflict_ignored": 0, "polls_complete": 0,
                "panel": {side: _side_blank() for side in SIDES},
                "alerts": [], "ante_skip_alerts": 0, "pending_notices": 0, "unknown_notices": 0,
                "final_status_when_table_cleared": None}

    def _fold(self, record, t, payload, lifecycle, panels, sides):
        record["last_seen_s"] = t
        state = payload.get("state")
        if state and state not in record["states_seen"]:
            record["states_seen"].append(state)
        if lifecycle.get("state") == rl.COMPLETE:
            record["polls_complete"] += 1
            if not record["completed"]:
                record["completed"], record["completed_at_s"] = True, _rounded(t)
            record["table_result"] = lifecycle.get("table_result") or record["table_result"]
            record["table_fingerprint"] = lifecycle.get("fingerprint") or record["table_fingerprint"]
        if lifecycle.get("panel_conflict_ignored"):
            record["polls_conflict_ignored"] += 1

        status = lifecycle.get("result_status") or rl.RESULT_NONE
        record["result_status"] = status
        if not record["result_timeline"] or record["result_timeline"][-1][1] != status:
            record["result_timeline"].append([_rounded(t), status])
        if status == rl.RESULT_CONFIRMED and record["confirmed_at_s"] is None:
            record["confirmed_at_s"] = _rounded(t)
        if status == rl.RESULT_CONFLICT and record["conflict_at_s"] is None:
            record["conflict_at_s"] = _rounded(t)

        outcome_winner = (panels.get("outcome") or {}).get("winner")
        if outcome_winner:
            record["panel_winner"] = outcome_winner
        if record["panel_winner"] and record["table_result"]:
            record["winners_agree"] = record["panel_winner"] == record["table_result"]

        verdicts = panels.get("verdicts") or {}
        for side in SIDES:
            data = sides.get(side) or {}
            row = record["panel"][side]
            visible = data.get("state") not in (None, "NOT_VISIBLE")
            if visible:
                row["polls_visible"] += 1
                row["first_visible_s"] = row["first_visible_s"] if row["first_visible_s"] is not None else _rounded(t)
                row["last_visible_s"] = _rounded(t)
            confirmed = list(data.get("confirmed") or ())
            row["max_confirmed_cards"] = max(row["max_confirmed_cards"], len(confirmed))
            if confirmed:
                row["last_confirmed"] = confirmed
            if data.get("complete") and row["complete_at_s"] is None:
                row["complete_at_s"] = _rounded(t)
            row["thumbnails_detected_max"] = max(row["thumbnails_detected_max"], data.get("detected") or 0)
            statuses = [read.get("status") for read in data.get("reads") or ()]
            for name in statuses:
                if name in row["thumbnail_status_counts"]:
                    row["thumbnail_status_counts"][name] += 1
            if "QUEUED" in statuses:
                row["polls_with_queued_thumbnails"] += 1
            for read in data.get("reads") or ():
                if read.get("status") in ("AMBIGUOUS", "UNKNOWN") and read.get("card"):
                    note = "%s %s" % (read["card"], read["status"])
                    if note not in row["refused_cards"]:
                        row["refused_cards"].append(note)
            verdict = verdicts.get(side)
            row["last_verdict"] = verdict[0] if verdict else row["last_verdict"]
            if verdict and verdict[0] not in row["verdicts_seen"]:
                row["verdicts_seen"].append(verdict[0])

        # Both rows gone, after the round completed: the panels have disappeared.
        gone = all((sides.get(side) or {}).get("state") in (None, "NOT_VISIBLE") for side in SIDES)
        if record["completed"] and gone and record["panel_gone_at_s"] is None:
            record["panel_gone_at_s"] = _rounded(t)

    # -- alerts ----------------------------------------------------------------------------------------------------------

    def observe_alert(self, event, payload):
        """Attribute an alert event to the finished round it is about (the lifecycle's `previous`)."""
        if not event:
            return
        lifecycle = payload.get("lifecycle") or {}
        about = (lifecycle.get("previous") or {}).get("round_id")
        record = self.rounds.get(about)
        if record is None:
            return
        now = payload.get("emitted_mono")
        t = (time.monotonic() if now is None else now) - (self.t0 or 0.0)
        kind = event[0]
        entry = {"t": _rounded(t), "event": kind}
        if kind == "show":
            entry["shown"] = event[1]
            entry["reason"] = event[2]
            if event[1] in ALERT_ACTIONS:
                record["ante_skip_alerts"] += 1
            elif event[1] == rl.RESULT_PENDING_KEY:
                record["pending_notices"] += 1
            elif event[1] == rl.RESULT_UNKNOWN_KEY:
                record["unknown_notices"] += 1
        else:
            entry["why"] = event[1]
        record["alerts"].append(entry)

    # -- results ---------------------------------------------------------------------------------------------------------

    def finish(self):
        """The round records, with the derived timings filled in. Safe to call repeatedly."""
        out = []
        for record in self.rounds.values():
            done = dict(record)
            done["last_seen_s"] = _rounded(record["last_seen_s"])
            start = record["completed_at_s"]
            if start is not None:
                if record["table_cleared_at_s"] is not None:
                    done["seconds_complete_to_table_cleared"] = _rounded(record["table_cleared_at_s"] - start)
                if record["confirmed_at_s"] is not None:
                    done["seconds_complete_to_confirmed"] = _rounded(record["confirmed_at_s"] - start)
                if record["panel_gone_at_s"] is not None:
                    done["seconds_complete_to_panel_gone"] = _rounded(record["panel_gone_at_s"] - start)
                    done["panel_gone_before_confirmation"] = (record["confirmed_at_s"] is None
                                                              or record["panel_gone_at_s"] < record["confirmed_at_s"])
                done["panel"] = dict(done["panel"])
                for side in SIDES:
                    row = record["panel"][side]
                    done["panel"][side] = dict(row, first_visible_vs_completion_s=_vs(row["first_visible_s"], start),
                                               complete_vs_completion_s=_vs(row["complete_at_s"], start),
                                               last_visible_vs_completion_s=_vs(row["last_visible_s"], start))
            done["final_status_when_table_cleared"] = record["result_status"]
            out.append(done)
        return out


def _vs(value, start):
    return None if value is None else _rounded(value - start)


def _count(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


# -- the alert, headless -----------------------------------------------------------------------------------------------------

class AlertProbe:
    """The window's next-round alert, driven the way the window drives it, with nothing to show or say."""

    def __init__(self, scenarios=None, session=1, empty_polls=1, timeout=25.0):
        from app import App
        from poker import scenarios as scenario_rules
        from ui.ante_alert import AnteAlertLogic
        self._app = App
        self.logic = AnteAlertLogic(empty_polls=empty_polls, timeout=timeout)
        self.window = SimpleNamespace(scenarios=scenarios if scenarios is not None else scenario_rules.load(),
                                      history=[], last_record=None, teach_session=session)

    def observe(self, payload):
        """The alert's answer to one update: None, ("show", action, reason) or ("hide", why)."""
        window, app = self.window, self._app
        now = payload.get("emitted_mono")
        return self.logic.observe(
            payload.get("state"), payload.get("seen"),
            lambda: app._preround_for(window, payload),
            now=now, ready=lambda: app._preround_ready(window, payload), key=app._preround_key(window, payload))


# -- summary and evidence -----------------------------------------------------------------------------------------------------

def _merged_counts(counters):
    merged = {}
    for counter in counters:
        for key, number in counter.items():
            merged[key] = merged.get(key, 0) + number
    return merged


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def summarize(rounds):
    """Numbers across the rounds: the questions a short live observation would be run to answer."""
    completed = [r for r in rounds if r["completed"]]
    by_status = _count([r["result_status"] for r in completed])
    summary = {
        "rounds_seen": len(rounds), "rounds_completed": len(completed), "final_result_status": by_status,
        "rounds_confirmed": by_status.get(rl.RESULT_CONFIRMED, 0),
        "rounds_unconfirmed": by_status.get(rl.RESULT_UNCONFIRMED, 0), "rounds_conflict": by_status.get(rl.RESULT_CONFLICT, 0),
        "seconds_complete_to_confirmed_median": _median([r["seconds_complete_to_confirmed"] for r in completed]),
        "seconds_complete_to_panel_gone_median": _median([r["seconds_complete_to_panel_gone"] for r in completed]),
        "seconds_complete_to_table_cleared_median": _median([r["seconds_complete_to_table_cleared"] for r in completed]),
        "panels_still_showing_when_the_next_round_began": sum(1 for r in completed if r["panel_gone_at_s"] is None
                                                              and r["table_cleared_at_s"] is not None),
        "panel_gone_before_confirmation": sum(1 for r in completed if r["panel_gone_before_confirmation"]),
        "winners_agree": sum(1 for r in completed if r["winners_agree"] is True),
        "winners_disagree": sum(1 for r in completed if r["winners_agree"] is False),
        "rounds_with_ante_skip_alert": sum(1 for r in completed if r["ante_skip_alerts"] >= 1),
        "most_ante_skip_alerts_for_one_round": max([r["ante_skip_alerts"] for r in rounds] or [0]),
        "pending_notices": sum(r["pending_notices"] for r in rounds), "unknown_notices": sum(r["unknown_notices"] for r in rounds),
        "polls_conflict_ignored": sum(r["polls_conflict_ignored"] for r in rounds),
        "alert_without_confirmed_result": sum(1 for r in rounds if r["ante_skip_alerts"] and r["result_status"] != rl.RESULT_CONFIRMED),
    }
    watched = [r for r in rounds if r.get("dealer_boxes")]
    if watched:
        summary["dealer_boxes"] = {
            "rounds_watched": len(watched),
            "rounds_with_no_dealer_box_in_any_poll": sum(1 for r in watched if not r["dealer_boxes"]["polls_with_any_box"]),
            "rounds_with_dealer_row_visible_and_no_box_for_3s": sum(
                1 for r in watched if r["dealer_boxes"]["longest_absence_with_dealer_row_visible_s"] >= 3.0),
            "frames_saved": [f["name"] for r in watched for f in r["dealer_boxes"]["frames"]],
            "rounds_where_the_finder_saw_a_dealer_shaped_card_but_assigned_no_slot": sum(
                1 for r in watched if r["dealer_boxes"]["zone_cards_max"] and not r["dealer_boxes"]["polls_with_fresh_dealer_slots"]),
            # Fusion-recovered pairs and how paper-white their halves were (diagnostic only; see dealer_diagnostics.paper_white_share).
            "recovered_polls": sum(r["dealer_boxes"].get("recovered_polls", 0) for r in watched),
            "recovered_paper_white_min": min([r["dealer_boxes"]["recovered_paper_white_min"] for r in watched
                                              if r["dealer_boxes"].get("recovered_paper_white_min") is not None] or [None]),
            "recovered_paper_white_max": max([r["dealer_boxes"]["recovered_paper_white_max"] for r in watched
                                              if r["dealer_boxes"].get("recovered_paper_white_max") is not None] or [None]),
            # Geometrically valid fused pairs the paper-white guard REFUSED (face-down backs, a half that could not be measured).
            "fusion_refused_polls": sum(r["dealer_boxes"].get("refused_polls", 0) for r in watched),
            "fusion_refused_paper_white_min": min([r["dealer_boxes"]["refused_paper_white_min"] for r in watched
                                                   if r["dealer_boxes"].get("refused_paper_white_min") is not None] or [None]),
            "fusion_refused_paper_white_max": max([r["dealer_boxes"]["refused_paper_white_max"] for r in watched
                                                   if r["dealer_boxes"].get("refused_paper_white_max") is not None] or [None]),
            "fusion_refusal_reasons": _merged_counts([r["dealer_boxes"].get("refusal_reasons") or {} for r in watched]),
            "fusion_refusals_dropped": sum(r["dealer_boxes"].get("refusals_dropped", 0) for r in watched),
        }
    for side in SIDES:
        rows = [r["panel"][side] for r in completed]
        summary[side] = {
            "rounds_with_row_visible": sum(1 for row in rows if row["polls_visible"]),
            "rounds_with_row_complete": sum(1 for row in rows if row["complete_at_s"] is not None),
            "max_confirmed_cards_median": _median([row["max_confirmed_cards"] for row in rows]),
            "polls_with_queued_thumbnails": sum(row["polls_with_queued_thumbnails"] for row in rows),
            "thumbnail_status_counts": {name: sum(row["thumbnail_status_counts"][name] for row in rows)
                                        for name in THUMBNAIL_STATUSES},
            "refused_cards": sorted({card for row in rows for card in row["refused_cards"]}),
        }
    return summary


def write_evidence(folder, recorder, extra=None):
    """rounds.json, polls.jsonl and summary.json in `folder` (created if needed). Nothing else is written."""
    os.makedirs(folder, exist_ok=True)
    rounds = recorder.finish()
    summary = summarize(rounds)
    if extra:
        summary["run"] = extra
    if recorder.read_watch is not None:
        summary["read_evidence"] = recorder.read_watch.summary()
    if recorder.poll_watch is not None:
        summary["poll_diagnostics"] = recorder.poll_watch.summary()
    if recorder.panel_watch is not None:
        summary["panel_diagnostics"] = recorder.panel_watch.summary()
    if recorder.machine_watch is not None:
        summary["machine_diagnostics"] = recorder.machine_watch.summary()
    with open(os.path.join(folder, "rounds.json"), "w", encoding="utf-8") as handle:
        json.dump(rounds, handle, indent=1)
    with open(os.path.join(folder, "polls.jsonl"), "w", encoding="utf-8") as handle:
        for line in recorder.polls:
            handle.write(json.dumps(line, default=str) +"\n")
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1)
    return rounds, summary
