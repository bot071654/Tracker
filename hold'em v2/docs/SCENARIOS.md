# Scenarios

What the tracker decides, and why. Every rule here was explicitly confirmed;
nothing was inferred from general poker strategy. Where a question was left
open it is written down as open rather than answered with a guess.

This document is the source of truth for the scenario system. If the code and
this file disagree, one of them is a bug.

**Nothing in the scenario system presses a button.** It recognises situations,
classifies hands, and shows a recommendation. There is no betting automation
and none is to be added.

---

## 1. The two rule systems

There are two separate systems, and they answer different questions. Only one
of them decides what the tracker shows you; the other is still live, but it is
used by the backtest and by Teach / Correct rather than displayed.

| System | Produces | Lives in | Shown where |
|---|---|---|---|
| Pre-round rules | `ANTE` / `SKIP` | `config/scenarios.json` | **the green banner** |
| Scenario Engine | `PLAY` / `DON'T PLAY` / `WAIT` | `config/scenario_engine.json` | **the green banner** and the SCENARIO panel |
| Saved flop rules | `Play on` / `Fold` | `config/scenarios.json` | **not displayed** — used by Teach / Correct and the backtest |

### The green banner

* `ANTE` and `SKIP` come from the **pre-round rules** (section 2), via
  `ui/ante_alert.py`.
* `PLAY`, `DON'T PLAY` and `WAIT` come from the **Scenario Engine**
  (section 4), via `app.py`.

**The Scenario Engine is the only source of the green PLAY / DON'T PLAY
decision.** The saved flop rules do not control the banner.

### One scenario area

The main window shows the Scenario Engine's decision in **one** place, the
`SCENARIO` panel beside the cards:

```
SCENARIO   Decision: PLAY
Scenario: PAIR
Player: 7S 7D  Flop: KC 9H 2S
Matched: PLAYER_PAIR
Reason: matched PLAYER_PAIR
```

`Scenario:` is the primary scenario, `Matched:` every structural scenario that
fired, and `Reason:` the engine's own sentence — this panel displays those
values, it does not word them. A `WAIT` shows its reason and which cards are
holding it up instead.

Note that `Scenario:` names the primary scenario, which for anything below a
straight is the **group** — `PAIR`, `TWO_PAIR`, `THREE_OF_A_KIND` — while
`Matched:` names the individual scenarios. See section 7.

---

## 2. Pre-round rules

About the round that just finished, deciding the one about to start. Read in
order; the first rule that matches wins. If none match, the default applies.

| Order | When | Says | In plain words |
|---|---|---|---|
| 1 | The player won the last **2** rounds | `ANTE` | "I won twice in a row — increase the bet and play." |
| 2 | The dealer won the last round | `SKIP` | "The dealer won last round — sit this one out." |
| 3 | The player won the last round | `ANTE` | "I won last round — play." |
| — | Nothing above matched | `ANTE` | "Otherwise, ante." |

**The order matters.** After two wins in a row, rules 1 and 3 both match, and
rule 1 wins because it comes first. Listed the other way round, rule 1 could
never fire — two wins in a row is also "the player won the last round".

**A tie matches nothing.** It is not a dealer win, so rule 2 does not fire, and
it is not a player win, so rules 1 and 3 do not either. A tie falls through to
the default.

"Increase the bet" is carried in the rule's name and nowhere else. The
application has no betting subsystem, and none was added for this.

---

## 3. Saved flop rules

Your own rules about the flop. Read in order, first match wins.

**These are not displayed in the main tracker window.** They remain fully
functional and are still used by:

* **Teach / Correct Scenario** — which rule matched, and what correcting it
  would change;
* **`tools/backtest.py`** — scoring a rule against the hands already recorded;
* the **rule builder** windows, which create and edit them.

They are stored in `config/scenarios.json` as they always were. What changed is
only that the tracker window no longer shows their recommendation.

| Order | Hand | Condition | Says | In plain words |
|---|---|---|---|---|
| 1 | Any | the flop is paired | `Play on` | "Two flop cards share a rank — play on." |
| 2 | High Card | one of my two cards is A, K or Q | `Play on` | "I have nothing made, but I hold an ace, king or queen — play on." |
| — | — | default | `Play on` | "Otherwise, play on." |

**Rules 1 and 2 can never both match.** If the flop is paired then the board
pair plays, so the made hand is a Pair and not High Card — which rule 2
requires. The order between them therefore does not matter.

### The A/K/Q rule asks about your own two cards

Rule 2 asks whether the higher of **your two hole cards** is an ace, king or
queen. It does not count the flop. An ace on the flop is the dealer's ace as
well and plays the same for both seats, so a hand can be "ace high" with
nothing in it.

This was decided on recorded data: over 197 hands, High Card hands with an
A/K/Q in the hole won 39.1% (18 of 46), while those where the big card was only
on the board won 19.2% (10 of 52).

Both readings of the question stay available to the rule builder — "highest
card showing" and "one of my two cards" — but the saved rule uses the
hole-card one.

### The dealer is deliberately not part of this rule

An earlier phrasing of this requirement read *"player and dealer are High
Card"*. **The dealer clause was considered and explicitly dropped.** Rule 2 is
final as written: the player's hand and the player's own two cards, nothing
else.

No flop rule looks at the dealer, and no dealer-dependent flop logic is to be
added. This is recorded here so the missing dealer clause is not mistaken for
an oversight and "restored" later.

### There is no Fold rule

`Fold` exists as an action the rule model can express, but **no Fold rule is
configured and none is to be invented.**

### Why these are no longer shown — a note on the history

The tracker window used to carry a second area, "Your scenarios say", showing
what these rules made of the table. It was removed so there is one
authoritative scenario area rather than two.

The reason it was confusing is worth recording, because the underlying fact has
not changed: **these rules and the Scenario Engine disagree by design.** Their
defaults point opposite ways.

* The saved flop rules **default to "play on"**, so this path never says fold.
* The Scenario Engine **defaults to `DON'T PLAY`** when no structural scenario
  matches.

So on any hand the engine rejects, these rules still say "play on (default)" —
for instance a flush with no A/K/Q in the hole, or a High Card hand where the
big card is only on the board. The two used to sit side by side on screen
saying opposite things.

Removing the display did not change that; it only stopped showing it. The
**green banner remains the decision**, and these rules remain what the backtest
and Teach / Correct reason about.

---

## 4. Scenario Engine rules

Structural checks on your two cards and the three flop cards. They do not ask
what the hand is called; they ask where each pair came from, because "Two Pair"
alone cannot say whether you hold any of it.

All ten are active. **If any one matches, the decision is `PLAY`. If none
match, it is `DON'T PLAY`.**

### Pair

| Scenario | In plain words |
|---|---|
| `PLAYER_PAIR` | My two cards are the same rank. |
| `COMBINED_PAIR` | My two cards are different, and one of them makes a pair with a flop card. |
| `FLOP_PAIR` | My two cards are different, and two flop cards have the same rank. |

### Two pair

| Scenario | In plain words |
|---|---|
| `PLAYER_PAIR_PLUS_FLOP_PAIR` | I hold a pair, and the flop holds a pair of a **different** rank. |
| `COMBINED_TWO_PAIR` | My two cards are different, and **each** of them pairs a flop card. |
| `PLAYER_PAIR_CARD_PLUS_FLOP_PAIR` | My cards are different, exactly one of them pairs the flop, and the flop holds another pair. |

### Three of a kind

| Scenario | In plain words |
|---|---|
| `PLAYER_PAIR_PLUS_FLOP_MATCH` | I hold a pair, and a flop card matches it. |
| `PLAYER_CARD_PLUS_FLOP_PAIR` | One of my cards matches **two** flop cards. |

### Straight

| Scenario | In plain words |
|---|---|
| `STRAIGHT` | Five ranks in a row across my two cards and the flop. |

Suits do not matter. **A-2-3-4-5 counts as a straight**, with the five as its
high card. The ace does not wrap around: Q-K-A-2-3 is not a straight.

### High card

| Scenario | In plain words |
|---|---|
| `HIGH_CARD_AKQ` | At least one of my two cards is an ace, king or queen. |

**This one does not check what the hand is.** It asks only about your two
cards. So a hand that is already something better — a flush, for instance —
still matches this scenario if you hold an A/K/Q, and therefore plays.

That is deliberate and was confirmed. It is **not** a flush rule: the flush is
incidental, and the ace is what plays. A flush with no A/K/Q in the hole is
`DON'T PLAY`.

---

## 5. WAIT — when the tracker will not decide

**No PLAY or DON'T PLAY is ever made from incomplete card information.**

A decision is made only when all five decision cards — your two and the three
flop cards — are settled. Until then the answer is `WAIT`.

A card counts as settled when the tracker has at least **two matching
readings** of it and its current status is `CONFIRMED`, or `HELD`.

| Situation | Result |
|---|---|
| Card not recognised (`UNKNOWN`) | `WAIT` |
| Card could be one of several (`AMBIGUOUS`) | `WAIT` |
| Card seen once, not yet settled (`CONFIRMING`) | `WAIT` |
| Card missing entirely | `WAIT` |
| The same card read into two different places | `WAIT` |
| Card settled, then temporarily covered (`HELD`) | **still counts as settled** |

The dealer's cards are **not** part of this gate. They are not needed for the
player's decision.

---

## 6. Dealer qualification

Worked out from the **dealer's two cards plus the board**. The dealer qualifies
with a **pair of fours or better**; anything stronger than that qualifying pair
also qualifies.

It is calculated only once the whole board — flop, turn and river — is
confirmed, which is the showdown.

**Dealer qualification is shown on its own and never becomes a PLAY decision.**
It says how the round pays. It is not advice about whether to play, and no rule
consults it.

---

## 7. The hand you have, and the scenario that describes it

These are two different things and are shown separately.

**`detected_hand`** is the actual poker hand — what the five cards really make.

**`primary_scenario`** is the main structural scenario chosen for display.

* A straight, full house, four of a kind or straight flush always uses the
  player's cards, so for those the actual hand is also the primary scenario.
* Otherwise the strongest matched scenario group is used — not the hand's
  name. Three of a kind lying entirely on the flop is `FLOP_PAIR`, because
  none of it is yours.
* If nothing matched, the primary scenario is `NONE`.

**Not every hand the tracker can name is a reason to play.** A hand being
detected and a hand triggering a decision are separate questions.

---

## 8. Flush

A flush is detected and reported as the hand you have.

**There is no flush → PLAY rule, and none is to be added.** A flush is
deliberately left out of the hands that become the primary scenario.

A flush plays only when `HIGH_CARD_AKQ` matches on its own — that is, when you
hold an ace, king or queen. A flush without one is `DON'T PLAY`.

---

## 9. Condition vocabulary — descriptions, not decisions

These terms describe a situation. **None of them is a rule.** They are
available when building a rule, and the statistics score them, but on their own
they decide nothing.

| Term | In plain words |
|---|---|
| `four to a flush` | Four of the five cards share a suit. |
| `four to a straight (open)` | One card short of a straight, open at both ends. |
| `four to a straight (gutshot)` | One card short of a straight, needing one particular rank. |
| `no draw` | Neither of the above. |
| `no overcards` | Neither of my cards is higher than every board card. |
| `one overcard` | One of my cards is higher than every board card. |
| `two overcards` | Both of my cards are higher than every board card. |
| `pair only on the board` | The pair is on the board and shared with the dealer. |
| `pair uses one of my cards` | The pair is made with a card I hold. |
| `pocket pair` | My two cards are the same rank. |
| `the flop is paired` | Two flop cards have the same rank. |
| `highest card showing is A, K or Q` | Counting my cards and the board together. |
| `one of my two cards is A, K or Q` | Counting only my own two cards. |

Only two of these are used by a saved rule: *the flop is paired* and *one of my
two cards is A, K or Q*. **The rest are descriptions only and must not be
turned into betting rules without explicit confirmation.**

---

## 10. "One Card Required" — not implemented

A scenario called **"One Card Required"** exists in this project's history. It
was never implemented, because the term was never defined.

It remains unimplemented, and deliberately so:

* there is no rule for it,
* there is no condition for it,
* there is no placeholder or stand-in for it,
* and its meaning has not been guessed.

Defining it needs answers to: what the term means, which cards it refers to, at
what stage it applies, what condition makes it fire, what decision it should
produce, and whether it is a pre-round rule or a flop rule. Until those are
answered it stays out.

---

## 11. Deliberately not here

None of the following exists in the scenario system, and none is to be added
without explicit confirmation:

* flush → PLAY
* any draw → PLAY
* one overcard or two overcards → PLAY
* full house, four of a kind or straight flush as PLAY rules in their own right
* any Fold strategy
* any dealer-dependent flop logic
* any standard Casino Hold'em strategy
* any rule derived from general poker knowledge rather than from a confirmed
  requirement

The absence of a rule here is a decision, not an omission. If a scenario seems
to be missing, it is either in section 10 or in this list.

---

## 12. Where these live

| What | File |
|---|---|
| Pre-round rules, saved flop rules | `config/scenarios.json` |
| Which structural scenarios decide, and the A/K/Q settings | `config/scenario_engine.json` |
| How the saved rules are matched | `poker/scenarios.py` |
| The structural scenarios, WAIT gate, dealer qualification | `poker/scenario_engine.py` |
| The condition vocabulary | `poker/board_features.py` |
| Hand evaluation and the qualifying pair | `poker/hand_evaluator.py` |
| Turning a live hand into a rule | `poker/teaching.py` |

Changing a rule means changing the JSON, or using the rule builder or the
Teach / Correct window. It does not mean editing the Python.
