"""The window's event pump: how often it drains the tracker's updates, and that draining stays ordered, complete and single.

Every update the tracker sends waits in a queue until the window's next drain, so the drain interval is the longest the screen can lag
behind a poll. It was 200 ms and is now EVENT_POLL_MS (50). Nothing about WHAT is drained changed: these tests pin that the payloads still
arrive in the order they were queued, none is lost, a bad one still does not stop the rest, and the recurring drain is scheduled exactly once.
"""

import logging
import time

import pytest

pytest.importorskip("tkinter")

import app as app_module                                                 # noqa: E402
from test_decision_voice import application, engine, root                # noqa: E402,F401 - the real window, with the database and the screen stubbed


def pending_pumps(application):
    """How many drains are waiting to run in Tk's timer queue right now."""
    tk = application.root.tk
    count = 0
    for ident in tk.splitlist(tk.call("after", "info")):
        script = tk.call("after", "info", ident)[0]
        if "_poll_events" in str(script):
            count += 1
    return count


def watch_reschedules(application, monkeypatch):
    scheduled = []
    real_after = application.root.after

    def watched(delay, *args):
        if args and getattr(args[0], "__name__", "") == "_poll_events":
            scheduled.append((delay, args[0]))
        return real_after(delay, *args)

    monkeypatch.setattr(application.root, "after", watched)
    return scheduled


def test_the_drain_interval_is_fifty_milliseconds():
    assert app_module.EVENT_POLL_MS == 50


def test_each_drain_schedules_the_next_one_exactly_once_at_that_interval(application, monkeypatch):
    scheduled = watch_reschedules(application, monkeypatch)
    application._poll_events()
    assert [delay for delay, _ in scheduled] == [app_module.EVENT_POLL_MS]
    assert scheduled[0][1] == application._poll_events
    application._poll_events()
    application._poll_events()
    assert len(scheduled) == 3                                           # one per drain, never two


def test_the_window_has_exactly_one_recurring_drain_after_it_is_built_and_while_it_runs(application):
    assert pending_pumps(application) == 1
    deadline = time.time() + 0.6
    while time.time() < deadline:                                        # the real loop, several intervals long
        application.root.update()
        time.sleep(0.01)
        assert pending_pumps(application) <= 1
    assert pending_pumps(application) == 1


def test_queued_events_are_delivered_in_order_and_none_is_lost(application, monkeypatch):
    seen = []
    monkeypatch.setattr(application, "_handle_event", lambda kind, payload: seen.append((kind, payload)))
    sent = [("update" if number % 3 == 0 else "saved" if number % 3 == 1 else "error", {"n": number}) for number in range(60)]
    for event in sent:
        application.events.put(event)
    application._poll_events()
    assert seen == sent
    assert application.events.empty()


def test_events_queued_between_drains_are_delivered_in_arrival_order(application, monkeypatch):
    seen = []
    monkeypatch.setattr(application, "_handle_event", lambda kind, payload: seen.append(payload["n"]))
    for number in range(5):
        application.events.put(("update", {"n": number}))
    application._poll_events()
    for number in range(5, 12):
        application.events.put(("update", {"n": number}))
    application._poll_events()
    application._poll_events()                                           # an empty drain delivers nothing and loses nothing
    assert seen == list(range(12))


def test_a_bad_event_in_the_middle_is_logged_and_the_rest_still_arrive_in_order(application, monkeypatch, caplog):
    seen = []

    def handle(kind, payload):
        if payload["n"] == 4:
            raise ValueError("boom")
        seen.append(payload["n"])

    monkeypatch.setattr(application, "_handle_event", handle)
    for number in range(10):
        application.events.put(("update", {"n": number}))
    with caplog.at_level(logging.ERROR):
        application._poll_events()
    assert seen == [0, 1, 2, 3, 5, 6, 7, 8, 9]
    assert any("Could not handle" in record.getMessage() for record in caplog.records)
    assert application.events.empty()


def test_the_running_loop_delivers_a_queued_event_on_its_own_within_a_second(application, monkeypatch):
    seen = []
    monkeypatch.setattr(application, "_handle_event", lambda kind, payload: seen.append(payload["n"]))
    application.events.put(("update", {"n": 1}))
    application.events.put(("update", {"n": 2}))
    deadline = time.time() + 1.0
    while time.time() < deadline and len(seen) < 2:
        application.root.update()
        time.sleep(0.005)
    assert seen == [1, 2]
    assert pending_pumps(application) == 1
