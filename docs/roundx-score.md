# RoundX Score — Per-Player Game Rating

## Context

The tracker already records every action a player takes during a game and `app/core/analysis.py` aggregates these into per-player counters (aces, weak receives, side-outs, etc.) plus a per-point winner derivation. What's missing is a **single composite number per player per game** that captures "how well did this person play in this game?". A composite number makes it easy to compare teammates, spot the carrier vs the weak link in a loss, and headline the post-game results screen instead of a wall of stats.

The score is intentionally a **raw additive number, unbounded, signable** — it scales with engagement (long games yield bigger absolute numbers) and a negative score is a meaningful signal that a player actively hurt their team. It is **per-game only** (no season aggregate yet), reusing the existing event log with **no schema changes**.

## Design summary

- **Shape**: `score = Σ weight(event_i, point_outcome_i)` over every event credited to the player.
- **Range**: unbounded; in practice expect roughly **−25 to +60** for a 21-point game (see §6).
- **Centering**: not zero-centered. A high-engagement game on a winning team can sit at +30 even with average play; the calibration signal is *relative to teammates and opponents*, not against an absolute "average = 0".
- **Distribution**: scales linearly with the number of events the player was involved in. Two consequences:
  1. Long games inflate scores; OT games inflate more.
  2. A player who served all 8 rotation positions and received heavily will have more headroom (positive and negative) than a player who barely touched the ball.
  - This is the *cost* of "raw additive" — explicitly accepted over a per-rally rate (which would normalize away that asymmetry).
- **Credit attribution**: action-based only. No "team bonus" for breaks or side-outs — credit flows through the individual action weights (receive + set + hit + finisher = a clean side-out's reward).

## Weights

| Event | Base | If point lost by player's team | Notes |
|---|---:|---:|---|
| `serve_ace` | +5 | — | Server only; auto-ends point as a win. |
| `touch` (defensive touch off opponent's hit) | +3 | — | Break-generating action. |
| `touch` → break converted (team wins the point) | **+3 bonus on top** | — | Rewards defensive plays that actually mattered. |
| `receive` (good) | +1 | — | Table-setter; chains into a side-out. |
| `set` (good) | +1 | — | |
| `hit` (good) | +1 | — | |
| `point` event (engine credits `last_touch_player` on rally win) | +2 | — | "Finisher" bonus — landing the decisive ball. |
| `weak_receive` | −1 | −3 | |
| `weak_set` | −1 | −3 | |
| `weak_hit` | −2 | −4 | Higher stakes — hit is the finishing action. |
| `soft_touch` (clean, same-player repeat) | +2 | +2 | Still a defensive touch; slightly less valuable than alternating coverage but net positive for the team. |
| `weak_touch` | +1 | +1 | Even a sloppy defensive touch generates a break attempt — net positive. |
| `weak_soft_touch` | +1 | +1 | Same rationale as `weak_touch`. |
| `serve_double_fault` | −5 | — | Auto-loses point. |
| `serve_fault` (single) | 0 | 0 | Excluded per design — pressure event but no outcome. |
| `error` after a `hit` / `weak_hit` (hit error) | −3 | — | Whiffing the finishing ball; stacks on top of `weak_hit`'s already-negative base. Mirrors `players[slot]["hit_errors"]`. |
| `error` after a `set` / `weak_set` (set error) | −2 | — | Botched setup that ended the point. Mirrors `players[slot]["set_errors"]`. |
| `error` after a `receive` / `touch` / `soft_touch` (any variant) | 0 | 0 | **Excluded** — receives and touches are setup/defensive contacts; the analysis module deliberately doesn't classify these as errors (touches are net-positive by design — see `project_touch_weighting`, and a bad receive is already captured by `weak_receive`). The point still flips to the opponents via `_end_point`, but no individual RoundX penalty applies. Mirrors `analysis.ERROR_SOURCE_EVENTS`. |
| "Aced" (lost a point to `serve_ace`) | −2 | — | Penalty on the would-be-receiver, derived from rotation via `_derive_point_pairs`. |

**Why these numbers**: a clean side-out (receive + set + hit + point credit) sums to +5 distributed across 3 actions ≈ same magnitude as one ace. A weak finishing hit that loses the point is −4, almost wiping out one ace. A double fault is −5, exactly cancels one ace. The explicit priorities are honored: aces and touches are the two highest single-event positives; weak actions in the *setup phase* (receive/set/hit) hurt more when they lose the point.

**Why errors are split by source action**: the analysis module distinguishes `hit_errors` from `set_errors` because the cost differs — fumbling the finishing hit ends a point your team had control of, whereas a botched set is one rung earlier in the chain and slightly less damning. Receive- and touch-sourced errors are intentionally excluded (the Error button stays usable so the rally winner is awarded correctly, but no per-player error stat is computed for them). See `app/core/analysis.py:ERROR_SOURCE_EVENTS`.

**Why touch variants are all positive**: a defensive touch — even a weak or same-player one — still generates a break attempt, which is the highest-leverage outcome in the game. Penalizing a sloppy touch would punish the player for *attempting* defense; we'd rather reward the attempt and let the conversion bonus (`touch → break`) separate the great touches from the merely-okay ones. Note: the `touch → break` conversion bonus (+3) currently only applies to clean `touch` events; the same idea could be extended to soft/weak touches later if the spread feels off.

## Implementation

### New function: `compute_roundx_scores(game_id)` in `app/core/analysis.py`

Returns `{slot: {"score": int, "name": str, "breakdown": [(label, delta), ...]}}`. The breakdown is built during the single pass and powers the "top contributors" display in the end-game dialog. Also wire `roundx_score` into `compute_game_stats`'s per-player block so the analysis & history screens reuse one computation.

Place it in `analysis.py` (not a sibling file): it needs `_team_of`, `_derive_point_pairs`, `_determine_point_winners`, and `by_point` grouping that already live there.

Single-pass algorithm:

1. Build `by_point` and `winners = _determine_point_winners(by_point, game)` (reuse existing helpers).
2. Use `point_pairs = _derive_point_pairs(...)` to get `(server_slot, receiver_slot)` per point — needed for "aced" attribution when no `receive` event was recorded.
3. For each point `p` with `winner = winners[p]` (skip abandoned points where winner is None):
   - For each event in `by_point[p]`, lookup the weight and `lost = (winner is not None and winner != event_slot[0])`. Apply to the slot's running total and append `(label, delta)` to its breakdown.
   - `serve_ace` additionally adds `−2` to `point_pairs[p][1]` (the receiver) as "Aced".
   - `touch` additionally adds `+3` if `winner == event_slot[0]` ("Touch → break"). `weak_touch` / `soft_touch` / `weak_soft_touch` do **not** get the conversion bonus — they already award a small positive base regardless of outcome.
   - `error` is a follow-up event: walk back to the immediately-preceding action in the same point (same logic `analysis.py` uses for the per-action error counters). If that prior action is a `hit` / `weak_hit`, apply −3 to the prior-action player as "Hit error"; if it's a `set` / `weak_set`, apply −2 as "Set error". Any other source (receive/touch/soft_touch/etc.) → no RoundX delta. The error event's own `player` field is **not** used directly — it always equals `last_touch_player`, which is what the walk-back resolves to anyway, but anchoring on the source action keeps the breakdown label honest.

### UI wire-ups

1. **End-of-game dialog** — `app/ui/tracker_screen.py:829-874` (`on_game_ended`). Replace the `QMessageBox` with a small `QDialog` containing the final score header + a 4-row `QTableWidget` of `Slot | Name | RoundX | Top 3 contributors` (sorted by `|delta|` from the breakdown), and a "Continue" button. Keep CSV export untouched.
2. **Analysis screen** — `app/ui/analysis_screen.py` `_stat_rows()`. Insert a `RoundX` row at the top of the per-player stats table, reading `players[slot]["roundx_score"]`. Sits above the existing `Hit Errors` / `Set Errors` rows that were added with the per-action error breakdown — those two rows give the qualitative "why" behind the negative RoundX deltas (a low RoundX with a high `set_errors_total` tells you setup is the leak, not finishing).
3. **History screen** — `app/ui/history_screen.py:_on_select`. After loading the game, append a `RoundX:  A1 +12, A2 +9, B1 +4, B2 −3` line to the detail panel. Cache by `game_id` to avoid recomputing on every selection.

CSV export is deliberately not modified (per design).

### How RoundX lines up with the existing analysis metrics

Every weight either *mirrors* an existing counter in `compute_game_stats` (so a player can cross-reference) or applies a derivation that already exists in `analysis.py`. The mapping:

| RoundX weight | Backing analysis counter / derivation |
|---|---|
| `+5` ace | `players[slot]["aces"]` |
| `+1` receive / set / hit | `good_receives`, `sets − weak_sets`, `hits − weak_hits` |
| `+3` touch (+`+3` on conversion) | `total_touches`; conversion mirrors `breaks_for` when the toucher's team won the point |
| `+1..+2` soft / weak touch variants | `weak_touches` (and `total_touches` membership) |
| `−1 / −3` weak receive / set, `−2 / −4` weak hit | `weak_receives`, `weak_sets`, `weak_hits` |
| `+2` finisher (`point` event) | derived from `point` events crediting `last_touch_player` |
| `−5` double fault | `double_faults_total` |
| `−3` hit error | `hit_errors_total` (= `hit_errors + weak_hit_errors`) |
| `−2` set error | `set_errors_total` (= `set_errors + weak_set_errors`) |
| `−2` aced | `players[slot]["aced"]` |

This is the consistency contract: a player's RoundX equals `Σ (counter × weight)` over the mapped counters, plus the touch-conversion bonus. If a future change in `_compute` reweights or adds a category, the RoundX table is the single place that has to follow.

### Edge cases to handle

- **Abandoned points** (`winners[p] is None`) — skip the entire point's events from RoundX (matches existing `counted_points` behaviour in analysis.py).
- **Imported CSV games** where `player` is `None` on some events — guard with `if slot:` before applying any delta.
- **Very short games / no events** — every slot scores 0. Dialog should render "0", not blank.
- **Overtime points** — `_derive_point_pairs` already handles OT receiver derivation; no special-case needed.
- **`serve_ace` + would-be-receiver** — receiver comes from `point_pairs[p][1]`, which back-derives from rotation when no `receive` event exists. No new rotation walk required.

## Verification

1. **Unit-style sanity check**: pick the most recent game in `roundnet.db`, run `compute_roundx_scores(game_id)` from a Python REPL, and verify:
   - All four slots return scores.
   - Sum of breakdown deltas equals the score field per slot (no missed events).
   - The only events that contribute nothing to the breakdown are `serve_fault` (single, weight 0), `serve_fault_type` annotations, and `error` events whose preceding action is a receive / touch / soft-touch variant (excluded — see weights table). Every other event type appears with its weight.
   - For every player, the count of "Hit error" breakdown entries equals `players[slot]["hit_errors_total"]` from `compute_game_stats`, and "Set error" entries equal `set_errors_total`. If they diverge, the walk-back logic in `compute_roundx_scores` has drifted from `analysis._compute`'s.
2. **Distribution sanity**: across the games in the current DB, the spread of RoundX scores should sit roughly in **−25 to +60**. If any slot exceeds ~+80 or drops below ~−40 in a non-blowout game, the `touch_break_bonus` or `weak_hit_lost` weight needs retuning.
3. **Manual UI test**: `python main.py` → play a quick game to completion → confirm:
   - End-of-game dialog shows the four RoundX scores and top contributors.
   - Returning home, opening History → selecting the just-played game shows the four scores in the detail panel.
   - Opening Analysis for the same game shows the RoundX row in the per-player view.
4. **Cross-screen consistency**: scores in the end-game dialog, history detail, and analysis screen must match exactly (they share one underlying computation).

## Open follow-ups (not in this plan)

- Season aggregation across games (currently per-game only; players are stable by name and could roll up later).
- ELO-style persistent rating across games.
- Tunable weights via a config file (currently hardcoded constants in `analysis.py`).
- CSV export of RoundX summary (deliberately excluded for now).
- Extending the `touch → break` conversion bonus to weak/soft touch variants if score distribution feels off.

### Critical files
- `app/core/analysis.py` — add `compute_roundx_scores` + weight constants; inject `roundx_score` into `compute_game_stats` output.
- `app/ui/tracker_screen.py` — replace `QMessageBox` in `on_game_ended` with end-game dialog.
- `app/ui/analysis_screen.py` — add RoundX row in `_stat_rows`.
- `app/ui/history_screen.py` — append RoundX line in `_on_select`.
