"""A completed hand is saved off the polling thread (B1).

THE FAULT

When a hand finished, the polling loop itself sent it to the backend and waited for the answer. Over the network that is seconds
(the live log shows `other=2634 ms` and `other=4040 ms` on the ticks that saved a hand: a 3.5 s and a 5.2 s tick), and for that
long the tracker took no new look at the table. The save now runs on one background thread, with exactly the rules it always
had: one hand at a time, the same fingerprint / round guards so a hand is stored once, the same back-off after a failed save
(the remote backend has already put that hand in its on-disk outbox), the same events.
"""

import queue
import threading
import time

import pytest

import tracker as tracker_module
from config.settings import CARD_SLOTS
from database import db
from tracker import Tracker

HAND = {"dealer_1": "KH", "dealer_2": "6C", "flop_1": "8D", "flop_2": "10D", "flop_3": "6S",
        "turn": "4S", "river": "AD", "player_1": "9H", "player_2": "JH"}
SAVE_SECONDS = 0.6


class Backend:
    """A stand-in for database.db.insert_hand that records its calls and can be slow, fail or refuse as a duplicate."""

    def __init__(self, monkeypatch, seconds=0.0, outcomes=None):
        self.seconds, self.calls, self.outcomes = seconds, [], list(outcomes or [])
        self.threads = []
        monkeypatch.setattr(db, "insert_hand", self.insert_hand)
        monkeypatch.setattr(tracker_module.excel_export, "append_hand", lambda row: None)

    def insert_hand(self, record):
        self.calls.append(record["hand_fingerprint"])
        self.threads.append(threading.current_thread().name)
        time.sleep(self.seconds)
        outcome = self.outcomes.pop(0) if self.outcomes else "stored"
        if outcome == "fail":
            raise db.DatabaseError("backend unavailable")
        if outcome == "crash":
            raise RuntimeError("something unexpected")
        if outcome == "duplicate":
            return False, None
        return True, {"id": len(self.calls), "hand_fingerprint": record["hand_fingerprint"]}


@pytest.fixture
def make(monkeypatch):
    made = []

    def build(asynchronous=True):
        t = Tracker({"monitor": 1, "regions": {}, "game_window_title": "", "async_save": asynchronous}, queue.Queue())
        t._verify_against_panels = lambda record: ("unavailable", "no result panel in this test")
        for slot in CARD_SLOTS:                                       # memory as it is when a hand has just completed
            t.memory.cards[slot] = HAND[slot]
        made.append(t)
        return t

    yield build
    for t in made:
        t.stop()


def events(t):
    out = []
    while not t.events.empty():
        out.append(t.events.get_nowait())
    return out


def wait_until(condition, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def saved_events(t):
    return [kind for kind, _ in events(t) if kind == "saved"]


# -- the polling thread no longer waits -----------------------------------------------------------------------------

def test_storing_a_hand_returns_at_once_while_the_save_takes_its_time(make, monkeypatch):
    backend = Backend(monkeypatch, seconds=SAVE_SECONDS)
    t = make()
    started = time.perf_counter()
    t._store(dict(HAND))
    assert time.perf_counter() - started < SAVE_SECONDS / 2, "the polling thread waited for the save"
    assert t._saving is not None
    assert wait_until(lambda: t._saving is None)
    assert backend.threads == ["tracker-save"]                      # the call was made on the save thread
    assert t._last_saved_fingerprint == backend.calls[0]


def test_the_same_save_run_inline_does_block_this_is_the_fault_being_fixed(make, monkeypatch):
    Backend(monkeypatch, seconds=SAVE_SECONDS)
    t = make(asynchronous=False)
    started = time.perf_counter()
    t._store(dict(HAND))
    assert time.perf_counter() - started >= SAVE_SECONDS


def test_polls_carry_on_while_a_hand_is_being_saved(make, monkeypatch):
    from test_stale_scenario import read
    Backend(monkeypatch, seconds=SAVE_SECONDS)
    t = make()
    t._record_tick = lambda *a, **k: None
    t.memory.cards.clear()
    stamps = []

    def poll():
        cards = dict(HAND)
        reads = {slot: read(card) for slot, card in cards.items()}
        t._read_table = lambda: (cards, reads)
        t._tick()
        stamps.append(time.perf_counter())

    start = time.perf_counter()
    while len(stamps) < 40 and (not stamps or t._saving is not None or time.perf_counter() - start < 0.2):
        poll()
        time.sleep(0.02)
    during = [stamp for stamp in stamps if stamp - start < SAVE_SECONDS]
    assert len(during) >= 5, "only %d polls ran while the save was in flight" % len(during)
    assert wait_until(lambda: t._saving is None)


# -- the rules a save always had ---------------------------------------------------------------------------------

def test_a_hand_is_stored_once_however_often_it_is_offered(make, monkeypatch):
    backend = Backend(monkeypatch, seconds=0.3)
    t = make()
    for _ in range(6):                                                # offered again on every poll, during and after the save
        t._store(dict(HAND))
        time.sleep(0.1)
    assert wait_until(lambda: t._saving is None)
    for _ in range(3):
        t._store(dict(HAND))
    time.sleep(0.2)
    assert len(backend.calls) == 1
    assert len(saved_events(t)) == 1


def test_only_one_save_is_in_flight_at_a_time(make, monkeypatch):
    backend = Backend(monkeypatch, seconds=0.4)
    t = make()
    t._store(dict(HAND))
    other = dict(HAND, river="2D")                                    # a different hand while the first is still being saved
    t._store(other)
    assert wait_until(lambda: t._saving is None)
    assert len(backend.calls) == 1


def test_a_failed_save_backs_off_and_is_tried_again_and_then_stored_once(make, monkeypatch):
    backend = Backend(monkeypatch, outcomes=["fail", "stored"])
    t = make()
    t._store(dict(HAND))
    assert wait_until(lambda: t._saving is None and t._pending is not None)
    assert [kind for kind, _ in events(t)] == ["error"]
    t._store(dict(HAND))                                              # still inside the back-off: not retried
    time.sleep(0.2)
    assert len(backend.calls) == 1
    fingerprint, _ = t._pending
    t._pending = (fingerprint, time.time() - 1)                       # the back-off has passed
    t._store(dict(HAND))
    assert wait_until(lambda: t._saving is None and t._pending is None)
    assert len(backend.calls) == 2 and len(saved_events(t)) == 1
    assert t._last_saved_fingerprint == fingerprint


def test_a_duplicate_is_reported_and_remembered(make, monkeypatch):
    backend = Backend(monkeypatch, outcomes=["duplicate"])
    t = make()
    t._store(dict(HAND))
    assert wait_until(lambda: t._saving is None)
    assert [kind for kind, _ in events(t)] == ["duplicate"]
    t._store(dict(HAND))
    time.sleep(0.2)
    assert len(backend.calls) == 1


def test_an_unexpected_error_is_a_failed_save_not_a_dead_worker(make, monkeypatch):
    backend = Backend(monkeypatch, outcomes=["crash", "stored"])
    t = make()
    t._store(dict(HAND))
    assert wait_until(lambda: t._saving is None and t._pending is not None)
    assert [kind for kind, _ in events(t)] == ["error"]
    fingerprint, _ = t._pending
    t._pending = (fingerprint, time.time() - 1)
    t._store(dict(HAND))
    assert wait_until(lambda: t._saving is None and t._pending is None)
    assert len(saved_events(t)) == 1                                   # the same worker took the second job


def test_stopping_waits_for_a_save_in_flight(make, monkeypatch):
    backend = Backend(monkeypatch, seconds=0.5)
    t = make()
    t._store(dict(HAND))
    t.stop()
    assert t._saving is None and t._last_saved_fingerprint == backend.calls[0]
    assert len(saved_events(t)) == 1


def test_the_saved_event_carries_the_record_and_the_row_as_before(make, monkeypatch):
    Backend(monkeypatch)
    t = make()
    t._store(dict(HAND))
    assert wait_until(lambda: t._saving is None)
    kind, payload = [e for e in events(t) if e[0] == "saved"][0]
    assert set(payload) == {"record", "row", "excel_error"} and payload["excel_error"] is None
    assert payload["record"]["hand_fingerprint"] == payload["row"]["hand_fingerprint"]
