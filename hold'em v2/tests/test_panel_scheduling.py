"""Which thumbnails the panel tracker reads first, and what that does - and does not - change.

THE FINDING

A 10-minute practice session (9 rounds, 8 usable) confirmed 7 results from the panels. The eighth reached 5/5 player cards
but only 4/5 dealer cards before the panels went, 4.1 seconds after the round completed. The dealer's row is read after the
player's, at two new thumbnails a poll for the two rows together, while polls at the showdown take about 1.3 seconds: a new
five-thumbnail dealer row took four or five polls to confirm (4.4 and 6.8 s in two rounds), and the panels stay up a median of
4.8 s. The dealer's row now has a read budget of its own (DEALER_ROW_READS_PER_POLL = 5): it is read in the poll it first
appears and confirmed on the next.

WHAT THE CHANGE IS NOT

It is only a scheduling change: which unread thumbnails are read in a poll. Every thumbnail goes through the same reader
(read_thumbnail) with the same margins, so an uncertain card is refused exactly as before, a row with a refused thumbnail never
completes, and the lifecycle's confirmation rules, the stale-panel protection and the one-alert-per-round rule are untouched.
These tests drive the real PanelTracker on the real casino frame, with the dealer's row painted out and back to stand for the
panel arriving late or going early.
"""

import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import round_lifecycle as rl                                  # noqa: E402
import round_validation as rv                                 # noqa: E402
from poker import scenarios as scenario_rules                 # noqa: E402
from recognition import card_recognizer as cr                 # noqa: E402
from recognition import result_panel as rp                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOT = os.path.join(ROOT, "Screenshot 2026-09-02 152130.png")
pytestmark = pytest.mark.skipif(not os.path.exists(SCREENSHOT), reason="no casino screenshot in the project folder")

TABLE = {"dealer_1": "8D", "dealer_2": "QD", "flop_1": "2D", "flop_2": "QH", "flop_3": "3D", "turn": "3H", "river": "6S",
         "player_1": "8S", "player_2": "9C"}
DEALER_FIVE = ["QD", "QH", "3D", "3H", "8D"]
PLAYER_FIVE = ["3D", "3H", "QH", "9C", "8S"]
STEP = 1.3                                                    # seconds a poll takes at the showdown (measured)


@pytest.fixture(scope="module", autouse=True)
def templates():
    cr.load_templates(force=True)


@pytest.fixture(scope="module")
def frame():
    return cv2.imread(SCREENSHOT)


@pytest.fixture(scope="module")
def found(frame):
    return rp.locate(frame)


def without_dealer_row(frame, found):
    out = frame.copy()
    row = found["dealer"]
    x0, y0 = min(b[0] for b in row) - 12, min(b[1] for b in row) - 25
    x1, y1 = max(b[0] + b[2] for b in row) + 12, max(b[1] + b[3] for b in row) + 25
    out[y0:y1, x0:x1] = out[y0 - 4, x0 - 4]                    # the panel's own dark background
    return out


def with_dealer_thumbnail(frame, found, index, picture):
    out = frame.copy()
    x, y, w, h = found["dealer"][index]
    out[y:y + h, x:x + w] = cv2.resize(picture, (w, h))
    return out


def with_blurred_dealer_thumbnail(frame, found, index, sigma=3.0):
    """One dealer thumbnail smeared past what the reader will accept; the card face is still there, so the row is still found."""
    out = frame.copy()
    x, y, w, h = found["dealer"][index]
    out[y:y + h, x:x + w] = cv2.GaussianBlur(frame[y:y + h, x:x + w], (0, 0), sigma)
    return out


class Spy:
    """A PanelTracker that records, per poll, how many thumbnails of each row were actually read."""

    def __init__(self):
        self.tracker = rp.PanelTracker(full_search_interval=0.0)
        self.reads = []
        original = self.tracker._read_box
        spy = self

        def counting(side, index, frame, box, origin, budget):
            before = spy.tracker.read_count
            read = original(side, index, frame, box, origin, budget)
            spy.current[side] += spy.tracker.read_count - before
            return read

        self.tracker._read_box = counting
        self.current = {"player": 0, "dealer": 0}

    def poll(self, picture, cards=TABLE, generation=1):
        self.current = {"player": 0, "dealer": 0}
        snapshot = self.tracker.observe(picture, (0, 0), None, round_id=generation, cards=cards)
        self.reads.append(dict(self.current))
        return snapshot


def panels_for(snapshot, cards=TABLE):
    verdicts = {side: rp.verify(side, snapshot[side], cards) for side in rp.SIDES}
    return {"sides": snapshot, "verdicts": verdicts, "outcome": rp.outcome(snapshot)}


# -- scheduling -------------------------------------------------------------------------------------------------------------

def test_a_new_dealer_row_is_read_in_the_poll_it_appears_and_confirmed_on_the_next(frame, found):
    spy = Spy()
    hidden = without_dealer_row(frame, found)
    for _ in range(6):
        spy.poll(hidden)                                       # the player's row is up and settled; no dealer row yet
    first = spy.poll(frame)
    assert spy.reads[-1]["dealer"] == rp.DEALER_ROW_READS_PER_POLL
    assert [read["status"] for read in first["dealer"]["reads"]] == [rp.CONFIRMING] * 5      # all five read, none queued
    assert first["dealer"]["confirmed"] is None                 # one poll is still not enough to confirm
    second = spy.poll(frame)
    assert second["dealer"]["confirmed"] == DEALER_FIVE and second["dealer"]["complete"]
    assert spy.reads[-1]["dealer"] == 0                         # nothing read twice


def test_the_players_row_keeps_its_old_budget_even_while_the_dealer_row_is_read(frame):
    spy = Spy()
    for _ in range(6):
        spy.poll(frame)
    assert max(r["player"] for r in spy.reads) == rp.READS_PER_POLL
    assert spy.reads[0] == {"player": rp.READS_PER_POLL, "dealer": rp.DEALER_ROW_READS_PER_POLL}


def test_with_both_rows_new_the_dealer_row_confirms_by_the_second_poll_and_the_player_row_as_before(frame):
    spy = Spy()
    firsts = {"player": None, "dealer": None}
    for poll in range(1, 8):
        snapshot = spy.poll(frame)
        for side in rp.SIDES:
            if firsts[side] is None and snapshot[side]["complete"]:
                firsts[side] = poll
    assert firsts == {"player": 4, "dealer": 2}


def test_the_old_shared_budget_took_four_polls_to_confirm_a_late_dealer_row(frame, found, monkeypatch):
    """The negative control for the scheduling change: with the dealer's row on the old budget the late row needs 4 polls."""
    monkeypatch.setattr(rp, "DEALER_ROW_READS_PER_POLL", rp.READS_PER_POLL)
    spy = Spy()
    hidden = without_dealer_row(frame, found)
    for _ in range(6):
        spy.poll(hidden)
    polls = 0
    while polls < 10:
        polls += 1
        if spy.poll(frame)["dealer"]["confirmed"]:
            break
    assert polls == 4


def test_at_the_showdown_pace_the_new_row_confirms_in_about_two_and_a_half_seconds_not_five(frame, found, monkeypatch):
    def seconds(budget):
        monkeypatch.setattr(rp, "DEALER_ROW_READS_PER_POLL", budget)
        spy = Spy()
        hidden = without_dealer_row(frame, found)
        for _ in range(6):
            spy.poll(hidden)
        polls = 0
        while polls < 10:
            polls += 1
            if spy.poll(frame)["dealer"]["confirmed"]:
                return polls * STEP
    assert seconds(rp.DEALER_ROW_READS_PER_POLL) == pytest.approx(2 * STEP)
    assert seconds(rp.READS_PER_POLL) == pytest.approx(4 * STEP)


# -- the cache -----------------------------------------------------------------------------------------------------------------

def test_a_read_dealer_row_is_never_read_again_while_the_pictures_are_the_same_or_within_the_noise(frame):
    spy = Spy()
    rng = np.random.RandomState(5)
    boxes = [b for row in rp.locate(frame).values() for b in row]
    for poll in range(8):
        picture = frame.copy()
        for x, y, w, h in boxes:                                # the live noise: a few pixels a grey level or two off
            for spot in rng.choice(w * h, size=40, replace=False):
                picture[y + spot // w, x + spot % w] = np.clip(picture[y + spot // w, x + spot % w].astype(int) + 2, 0, 255)
        spy.poll(picture)
    assert sum(r["dealer"] for r in spy.reads) == rp.DEALER_ROW_READS_PER_POLL      # once, in the first poll
    assert sum(r["player"] for r in spy.reads[4:]) == 0


def test_one_thumbnail_that_really_changes_is_the_only_one_read_again(frame, found):
    spy = Spy()
    for _ in range(6):
        spy.poll(frame)
    x, y, w, h = found["player"][0]
    other = cv2.resize(frame[found["player"][3][1]:found["player"][3][1] + found["player"][3][3],
                             found["player"][3][0]:found["player"][3][0] + found["player"][3][2]], (w, h))
    changed = frame.copy()
    changed[y:y + h, x:x + w] = other
    spy.poll(changed)
    assert spy.reads[-1] == {"player": 1, "dealer": 0}


# -- incomplete rows -------------------------------------------------------------------------------------------------------------

def test_a_dealer_row_with_a_thumbnail_the_reader_will_not_accept_never_completes(frame, found):
    """Reading the row in full does not make an unreadable card readable: one thumbnail covered, the row stays partial."""
    covered = with_blurred_dealer_thumbnail(frame, found, 2)
    spy = Spy()
    for _ in range(10):
        snapshot = spy.poll(covered)
    assert snapshot["dealer"]["confirmed"] is None and not snapshot["dealer"]["complete"]
    assert snapshot["dealer"]["state"] == rp.VISIBLE_PARTIAL
    assert [read["status"] for read in snapshot["dealer"]["reads"]].count(rp.CONFIRMING) == 4
    assert sum(r["dealer"] for r in spy.reads[1:]) == 0                         # the refusal is cached too: no repeated work
    assert rl.panel_evidence(panels_for(snapshot), "Dealer") == rl.PANEL_UNCONFIRMED


def test_a_row_still_filling_in_is_not_confirmed_early(frame, found):
    spy = Spy()
    partial = frame.copy()
    for index in (3, 4):                                         # the casino has not drawn the last two thumbnails yet
        x, y, w, h = found["dealer"][index]
        partial[y:y + h, x:x + w] = partial[y - 4, x - 4]
    for _ in range(6):
        snapshot = spy.poll(partial)
    assert snapshot["dealer"]["confirmed"] is None or len(snapshot["dealer"]["confirmed"]) < 5
    assert not snapshot["dealer"]["complete"]


# -- the panel disappearing --------------------------------------------------------------------------------------------------------

def test_a_dealer_row_that_is_up_for_only_two_polls_is_now_confirmed_where_before_it_never_was(frame, found):
    spy = Spy()
    hidden = without_dealer_row(frame, found)
    for _ in range(6):
        spy.poll(hidden)
    snapshots = [spy.poll(frame), spy.poll(frame)]
    assert snapshots[-1]["dealer"]["complete"] and snapshots[-1]["dealer"]["confirmed"] == DEALER_FIVE
    for _ in range(rp.VANISH_FRAMES + 1):
        gone = spy.poll(hidden)
    assert gone["dealer"]["state"] == rp.NOT_VISIBLE and gone["dealer"]["confirmed"] is None


def test_a_dealer_row_up_for_a_single_poll_is_never_confirmed_and_nothing_is_guessed(frame, found):
    spy = Spy()
    hidden = without_dealer_row(frame, found)
    for _ in range(6):
        spy.poll(hidden)
    one = spy.poll(frame)
    assert one["dealer"]["confirmed"] is None and not one["dealer"]["complete"]
    for _ in range(rp.VANISH_FRAMES + 1):
        gone = spy.poll(hidden)
    assert gone["dealer"]["confirmed"] is None and rp.outcome(gone)["winner"] is None


def test_the_next_rounds_dealer_row_still_starts_from_nothing(frame, found):
    """The stale-panel protection is untouched: the old dealer row on screen in the next round is ignored, not re-read as new."""
    spy = Spy()
    for _ in range(6):
        spy.poll(frame, generation=1)
    nxt = {"player_1": "2C", "player_2": "5D", "flop_1": "9S", "flop_2": "8S", "flop_3": "7D", "turn": "QH", "river": "3C",
           "dealer_1": "AD", "dealer_2": "KC"}
    snapshot = None
    for _ in range(4):
        snapshot = spy.poll(frame, cards=nxt, generation=2)      # the old panel still showing, a new round on the table
    assert snapshot["dealer"]["stale"] and not snapshot["dealer"]["complete"]
    assert rp.verify("dealer", snapshot["dealer"], nxt)[0] == "unavailable"


# -- one alert per confirmed round, end to end ---------------------------------------------------------------------------------

def play_round(frames, generation=1):
    """The real PanelTracker, RoundLifecycle and the window's alert logic over a sequence of frames; returns the alert events."""
    spy, life, probe = Spy(), rl.RoundLifecycle(), rv.AlertProbe(scenarios={"preround": {"default": scenario_rules.ANTE, "rules": []}})
    events, clock = [], 100.0
    for picture in frames:
        clock += STEP
        snapshot = spy.poll(picture, generation=generation)
        panels = panels_for(snapshot)
        lifecycle = life.observe(generation, rl.COMPLETE, TABLE, panels)
        payload = {"state": "COMPLETE", "seen": dict(TABLE), "lifecycle": lifecycle, "emitted_mono": clock, "panels": panels}
        events.append(probe.observe(payload))
    for step in range(8):                                        # the table clears: a new, empty round
        clock += STEP if step < 6 else 30.0
        lifecycle = life.observe(generation + 1 + step // 4, rl.WAITING, {}, None)
        payload = {"state": "WAITING", "seen": {}, "lifecycle": lifecycle, "emitted_mono": clock}
        events.append(probe.observe(payload))
    return [e for e in events if e], life


def test_a_late_dealer_row_still_confirms_and_the_next_round_alert_fires_exactly_once(frame, found):
    hidden = without_dealer_row(frame, found)
    events, _ = play_round([hidden] * 4 + [frame] * 3)
    alerts = [e for e in events if e[0] == "show" and e[1] in rv.ALERT_ACTIONS]
    assert len(alerts) == 1
    assert [e for e in events if e[0] == "show" and e[1] in (rl.RESULT_PENDING_KEY, rl.RESULT_UNKNOWN_KEY)] == []


def test_a_dealer_row_seen_for_one_poll_gives_no_alert_only_pending_then_unknown(frame, found):
    hidden = without_dealer_row(frame, found)
    events, _ = play_round([hidden] * 4 + [frame] + [hidden] * 4)
    shown = [e[1] for e in events if e[0] == "show"]
    assert shown == [rl.RESULT_PENDING_KEY, rl.RESULT_UNKNOWN_KEY]


# -- negative control: uncertain cards are still refused -----------------------------------------------------------------------------

def test_an_uncertain_thumbnail_in_the_dealer_row_is_refused_and_keeps_the_row_unconfirmed(frame, found):
    """A smeared thumbnail the reader cannot be sure of: reading the row in full does not change what is accepted."""
    faded = with_blurred_dealer_thumbnail(frame, found, 1)
    spy = Spy()
    for _ in range(8):
        snapshot = spy.poll(faded)
    row = snapshot["dealer"]
    assert row["confirmed"] is None and not row["complete"]
    assert row["reads"][1]["status"] in (rp.AMBIGUOUS, rp.UNKNOWN) and not row["reads"][1]["confident"]
    assert [read["status"] for read in row["reads"]].count(rp.CONFIRMING) == 4          # the other four read, and are only that


def test_the_thresholds_and_the_reader_are_exactly_what_they_were():
    assert (cr.SUIT_MARGIN, cr.RANK_MARGIN, cr.CORROBORATED_SUIT_MARGIN) == (0.05, 0.10, 0.02)
    assert (rp.SIZE_TOLERANCE, rp.CONFIRM_FRAMES, rp.VANISH_FRAMES, rp.PanelTracker.CACHE_TOLERANCE) == (0.25, 2, 3, 4)
    assert rp.PanelTracker().__init__.__defaults__[0] is rp.read_thumbnail
    assert rp.DEALER_ROW_READS_PER_POLL == rp.MAX_PANEL_CARDS and rp.READS_PER_POLL == 2


def test_the_outline_still_needs_to_agree_with_the_template_when_the_row_is_read_in_full(frame, found, monkeypatch):
    """An ambiguous spade the outline does not corroborate is refused in the burst exactly as when read one at a time."""
    spade = cv2.imread(os.path.join(ROOT, "tests", "fixtures", "panel_thumbnails",
                                    sorted(f for f in os.listdir(os.path.join(ROOT, "tests", "fixtures", "panel_thumbnails"))
                                           if f.endswith(".png"))[0]))
    monkeypatch.setattr(cr, "pip_shape", lambda glyph: (None, 0.3))             # the outline has no opinion
    stalled = with_dealer_thumbnail(frame, found, 0, spade)
    spy = Spy()
    for _ in range(8):
        snapshot = spy.poll(stalled)
    assert snapshot["dealer"]["confirmed"] is None
    assert not snapshot["dealer"]["reads"][0]["confident"]
