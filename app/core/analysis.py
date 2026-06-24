"""
Stats computation over a single game's event log.

Pure / no UI / no DB writes. The public entry point is `compute_game_stats(game_id)`,
which loads the game + events via the repos and returns a fully-populated dict
that the Analysis screen renders.

Conventions (locked in by user during spec):
  * Total Touches numerator = touch + weak_touch + soft_touch + weak_soft_touch.
  * Total Touches denominator = opponent's hit + weak_hit events.
  * Side-out / hold / clean-side-out denominators = receive + weak_receive
    events for the team (i.e. points where the team actually received a
    served-in ball; aces-against are excluded).
  * Weak Touches = weak_touch + weak_soft_touch only.
  * Chain analysis is presented as conditional loss-rates vs. baseline.

Per-player attribution (for stats that are point-outcomes rather than per-event
actions): we derive each point's server slot and receiver slot — preferring the
slots named on the actual serve/receive events, falling back to walking the
serve rotation (and OT pair state) when a point has no receive event (ace /
double fault). Aced, side-outs, holds, breaks-for/against are then credited
to the specific slot(s) that owned the serve/receive.
"""

from collections import defaultdict
from typing import Optional

from app.core.match_grouping import canonical_team_maps
from app.core.rotation import SERVE_ROTATION, pair_at, next_rotation_index
from app.db import events_repo, games_repo


TOUCH_EVENTS = {"touch", "weak_touch", "soft_touch", "weak_soft_touch"}
WEAK_TOUCH_EVENTS = {"weak_touch", "weak_soft_touch"}
HIT_EVENTS = {"hit", "weak_hit"}
RECEIVE_EVENTS = {"receive", "weak_receive"}
SET_EVENTS = {"set", "weak_set"}


# ---------------------------------------------------------------------------
#  RoundX score — per-player game rating
# ---------------------------------------------------------------------------
# A single composite number per player per game built from a weighted sum
# over the event log. Normalized to a benchmark rally count so that a +40
# carries the same meaning across games of any length.
#
# Design notes (see PLAN_roundx_score.md):
#  * Aces and clean touches are the two highest single-event positives.
#  * Weak setup actions (receive / set / hit) double their penalty when the
#    point is lost — "doubly bad" when the failure is also the cause.
#  * All touch variants are net-positive: a defensive contact is always
#    worth attempting, even when sloppy.
#  * Breakdown items stay in raw units (the actual point swings of each
#    event). Only the final per-player total is scaled. This keeps the
#    "Key Plays" narrative readable while the headline score is comparable
#    across games.
ROUNDX_WEIGHTS = {
    "serve_ace":             5,
    "touch_base":            3,
    "touch_break_bonus":     3,   # additional on top of touch_base if team wins
    "receive_good":          1,
    "set_good":              1,
    "hit_good":              1,
    "point_finisher":        2,
    "weak_receive_base":    -1,
    "weak_receive_lost_mod":-2,   # additional if point lost
    "weak_set_base":        -1,
    "weak_set_lost_mod":    -2,
    "weak_hit_base":        -2,
    "weak_hit_lost_mod":    -2,
    "soft_touch":            2,
    "weak_touch":            1,
    "weak_soft_touch":       1,
    "serve_double_fault":   -5,
    "serve_fault_single":    0,
    "error":                -2,
    "aced_penalty":         -2,
}

# Normalization scaffolding.
#  * BENCHMARK_RALLIES = the rally count a "typical" game (21-point with
#    healthy side-out rates) lands at. The scalar pulls shorter games up
#    and longer games down toward this yardstick.
#  * SCALAR_CAP prevents tiny games from runaway amplification.
#  * RATED_MIN_RALLIES is the threshold below which the score is still
#    computed but marked unrated — the UI should signal that the number
#    isn't directly comparable to a full game.
ROUNDX_BENCHMARK_RALLIES = 40
ROUNDX_SCALAR_CAP = 2.0
ROUNDX_RATED_MIN_RALLIES = 20

# Only hits and sets are treated as error sources for analytics: receives and
# touches are setup/defensive contacts where "error" doesn't carve out a
# meaningful category (touches are net-positive by design — see the touch-
# weighting note — and a botched receive is already captured by `weak_receive`).
# The Error button itself stays available after any precursor so the point
# award still flows correctly; the analysis just doesn't credit those.
ERROR_SOURCE_EVENTS = HIT_EVENTS | SET_EVENTS

# Source event-type → per-action error counter name. Canonical and weak
# variants are tracked separately so the analysis can show
# `hit_errors / hits` and `weak_hit_errors / weak_hits`.
_ERROR_COUNTER_FOR_SOURCE = {
    "hit":      "hit_errors",
    "weak_hit": "weak_hit_errors",
    "set":      "set_errors",
    "weak_set": "weak_set_errors",
}


def compute_game_stats(game_id: int) -> dict:
    game = games_repo.get_game(game_id)
    if not game:
        raise ValueError(f"Game {game_id} not found")
    events = events_repo.list_for_game(game_id)
    return _compute(dict(game), events)


def compute_flow(game_id: int) -> list[dict]:
    """Return only the per-point flow list — for callers that need a
    lightweight score-progression view (e.g. title-card thumbnails) without
    paying for the full stats payload. Shape matches
    `compute_game_stats(...)["flow"]`."""
    game = games_repo.get_game(game_id)
    if not game:
        raise ValueError(f"Game {game_id} not found")
    game = dict(game)
    events = events_repo.list_for_game(game_id)
    by_point: dict[int, list[dict]] = defaultdict(list)
    for ev in events:
        by_point[ev["point"]].append(ev)
    for evs in by_point.values():
        evs.sort(key=lambda e: e["seq_in_point"])
    sorted_pts = sorted(by_point.keys())
    winners = _determine_point_winners(by_point, game)
    return _compute_flow(by_point, sorted_pts, winners)


def compute_roundx_scores(game_id: int) -> dict:
    """Return only the RoundX block — for callers that don't need the full
    stats payload (e.g. the end-game dialog). Result shape matches
    `compute_game_stats(...)["roundx"]`."""
    game = games_repo.get_game(game_id)
    if not game:
        raise ValueError(f"Game {game_id} not found")
    game = dict(game)
    events = events_repo.list_for_game(game_id)
    by_point: dict[int, list[dict]] = defaultdict(list)
    for ev in events:
        by_point[ev["point"]].append(ev)
    for evs in by_point.values():
        evs.sort(key=lambda e: e["seq_in_point"])
    sorted_pts = sorted(by_point.keys())
    winners = _determine_point_winners(by_point, game)
    point_pairs = _derive_point_pairs(game, by_point, sorted_pts)
    return _compute_roundx_from_events(
        game, by_point, sorted_pts, winners, point_pairs
    )


def _aggregate_roundx(
    per_game: list[dict], maps: list[dict] | None = None
) -> dict:
    """Combine multiple games' RoundX dicts into a single per-slot dict.
    Raw scores and counted rallies sum across games; the scalar is re-derived
    from the combined rally count so a longer match is still benchmarked
    against the same 40-rally reference point.

    `maps` is the canonical-team / canonical-slot mapping from
    `match_grouping.canonical_team_maps`, one entry per game. When omitted
    we derive it from the per-game `name` fields so the same player ends up
    in one canonical bucket even if they swap sides between sets. Pass
    explicit maps when the caller already has them — avoids recomputing.
    """
    slots = ["A1", "A2", "B1", "B2"]
    if not per_game:
        return {}

    if maps is None:
        per_game_names = [{s: rx[s]["name"] for s in slots} for rx in per_game]
        maps = canonical_team_maps(per_game_names)

    # Canonical names — anchored on the first game (which always maps
    # identity, so its A1/A2/B1/B2 are the canonical roster).
    name = {s: per_game[0][s]["name"] for s in slots}
    total_rallies = 0
    raw: dict[str, int] = {s: 0 for s in slots}
    breakdowns: dict[str, list[tuple[str, int]]] = {s: [] for s in slots}

    for rx, m in zip(per_game, maps):
        sample = next(iter(rx.values()))
        total_rallies += sample.get("counted_rallies", 0)
        slot_map = m["slot_map"]
        for in_slot in slots:
            canon_slot = slot_map.get(in_slot, in_slot)
            raw[canon_slot] += rx[in_slot]["raw_score"]
            breakdowns[canon_slot].extend(rx[in_slot]["breakdown"])

    if total_rallies > 0:
        scalar = min(
            ROUNDX_BENCHMARK_RALLIES / total_rallies, ROUNDX_SCALAR_CAP
        )
    else:
        scalar = 1.0
    rated = total_rallies >= ROUNDX_RATED_MIN_RALLIES

    normalized = {s: round(raw[s] * scalar) for s in slots}
    avg = round(sum(normalized.values()) / 4) if normalized else 0

    return {
        s: {
            "slot": s,
            "name": name[s],
            "score": normalized[s],
            "raw_score": raw[s],
            "scalar": round(scalar, 3),
            "rated": rated,
            "counted_rallies": total_rallies,
            "relative_to_avg": normalized[s] - avg,
            "breakdown": sorted(breakdowns[s], key=lambda x: -abs(x[1])),
        }
        for s in slots
    }


def compute_match_roundx(game_ids: list[int]) -> dict:
    """Lightweight per-match RoundX — used for the picker title card so the
    grid doesn't have to load the full per-game stats payload."""
    per_game = [compute_roundx_scores(gid) for gid in game_ids]
    return _aggregate_roundx(per_game)


def _sum_counters_into(target: dict, src: dict) -> None:
    """Sum int counters and merge per-type fault dicts. Derived rates are
    skipped — caller is expected to re-run `_finalize` on the result."""
    fault_dict_keys = ("single_faults_by_type", "double_faults_by_type")
    for k, v in src.items():
        if k in fault_dict_keys:
            for fk, fv in (v or {}).items():
                target[k][fk] += fv
        elif isinstance(v, int) and k in target and isinstance(target[k], int):
            target[k] += v


def compute_match_stats(game_ids: list[int]) -> dict:
    """Aggregate per-game stats into a single match-level stats dict.

    Output shape matches `compute_game_stats` so the analysis UI can render
    it unchanged, with two additions on the `game` block: `series_score`
    (A-wins, B-wins) and `game_results` (per-game summary list). The
    aggregate `flow` is empty — per-game flow visualisations don't compose
    meaningfully across separate games, so the title-card strip is skipped
    on the match view.

    Aggregation is keyed on **canonical team identity**, not raw in-game
    slot. The first game's roster establishes the canonical sides; for
    each later game the players are mapped back onto those canonical
    sides via `match_grouping.canonical_team_maps`. This is what makes a
    pairing that swaps `A1`↔`B1` between sets still get tallied as the
    same team in series score, per-team stats, per-player stats, chain
    analysis, and RoundX.
    """
    if not game_ids:
        raise ValueError("compute_match_stats requires at least one game id")

    per_game = [compute_game_stats(gid) for gid in game_ids]

    # Canonical roster — anchored on game 1. Same four people across the
    # match, but we don't assume they stay on the same side.
    first_game = per_game[0]["game"]
    names = first_game["names"]

    maps = canonical_team_maps([g["game"]["names"] for g in per_game])

    teams = {"A": _empty_counters(), "B": _empty_counters()}
    players = {s: _empty_counters() for s in ("A1", "A2", "B1", "B2")}
    for s, ps in players.items():
        ps["name"] = names[s]
        ps["slot"] = s

    chain_rows_combined: dict[str, list[dict]] = {"A": [], "B": []}
    total_points = 0

    for g, m in zip(per_game, maps):
        team_map = m["team_map"]
        slot_map = m["slot_map"]
        for in_team in ("A", "B"):
            canon_team = team_map[in_team]
            _sum_counters_into(teams[canon_team], g["teams"][in_team])
            # Chain rows carry slot strings ("A1" etc.) inside their
            # weak_*_players lists — those must be remapped to canonical
            # slots before being merged, otherwise per-player chain stats
            # will attribute a weak action to the wrong human.
            for row in g.get("_chain_rows", {}).get(in_team, []):
                chain_rows_combined[canon_team].append({
                    **row,
                    "weak_receive_players": [
                        slot_map.get(s, s) for s in row["weak_receive_players"]
                    ],
                    "weak_set_players": [
                        slot_map.get(s, s) for s in row["weak_set_players"]
                    ],
                    "weak_hit_players": [
                        slot_map.get(s, s) for s in row["weak_hit_players"]
                    ],
                })
        for in_slot in ("A1", "A2", "B1", "B2"):
            canon_slot = slot_map.get(in_slot, in_slot)
            _sum_counters_into(players[canon_slot], g["players"][in_slot])
        total_points += g["game"]["total_points"]

    for ts in teams.values():
        _finalize(ts)
    for ps in players.values():
        _finalize(ps)
    for slot, ps in players.items():
        ps["opponent_hits"] = teams[slot[0]]["opponent_hits"]

    chains = _compute_chains(chain_rows_combined, names)
    roundx = _aggregate_roundx([g["roundx"] for g in per_game], maps)

    for s in ("A1", "A2", "B1", "B2"):
        rx = roundx[s]
        players[s]["roundx_score"] = rx["score"]
        players[s]["roundx_raw_score"] = rx["raw_score"]
        players[s]["roundx_relative_to_avg"] = rx["relative_to_avg"]
        players[s]["roundx_rated"] = rx["rated"]
        players[s]["roundx_breakdown"] = rx["breakdown"]

    a_wins = b_wins = 0
    for g, m in zip(per_game, maps):
        w = g["game"]["winner"]
        if w is None:
            continue
        canon_w = m["team_map"].get(w, w)
        if canon_w == "A":
            a_wins += 1
        elif canon_w == "B":
            b_wins += 1
    if a_wins > b_wins:
        match_winner_ = "A"
    elif b_wins > a_wins:
        match_winner_ = "B"
    else:
        match_winner_ = None

    # Per-game results in canonical orientation — final_score_a is always
    # the canonical-A team's score, final_score_b the canonical-B team's,
    # regardless of which side they played on that game.
    game_results = []
    canon_total_a = canon_total_b = 0
    for g, m in zip(per_game, maps):
        gg = g["game"]
        sa = gg["final_score_a"]
        sb = gg["final_score_b"]
        if m["team_map"].get("A") == "B":
            canon_sa, canon_sb = sb, sa
        else:
            canon_sa, canon_sb = sa, sb
        canon_total_a += canon_sa
        canon_total_b += canon_sb
        w = gg["winner"]
        canon_w = m["team_map"].get(w, w) if w else None
        game_results.append({
            "id": gg["id"],
            "final_score_a": canon_sa,
            "final_score_b": canon_sb,
            "winner": canon_w,
            "total_points": gg["total_points"],
        })

    return {
        "game": {
            "id": None,
            "created_at": first_game["created_at"],
            "ended_at": per_game[-1]["game"].get("ended_at"),
            "names": names,
            "team_a_players": first_game["team_a_players"],
            "team_b_players": first_game["team_b_players"],
            "final_score_a": canon_total_a,
            "final_score_b": canon_total_b,
            "winner": match_winner_,
            "end_reason": None,
            "target_score": first_game["target_score"],
            "hard_cap": first_game["hard_cap"],
            "total_points": total_points,
            "is_match": True,
            "series_score": (a_wins, b_wins),
            "game_results": game_results,
        },
        "teams": teams,
        "players": players,
        "chains": chains,
        "flow": [],
        "roundx": roundx,
    }


def _team_of(slot: Optional[str]) -> Optional[str]:
    return slot[0] if slot else None


def _opp(team: str) -> str:
    return "B" if team == "A" else "A"


def _safe_pct(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return num / den


# ---------------------------------------------------------------------------
#  Point-winner derivation
# ---------------------------------------------------------------------------

def _determine_point_winners(by_point, game) -> dict:
    """
    Each event records the score BEFORE the point it belongs to was awarded.
    So the winner of point P = the team whose score is incremented between
    the last event of P and the first event of P+1. The final point compares
    against games.final_score_*.

    Returns {point_number: 'A' | 'B' | None}. None means the point was
    abandoned (e.g. manual game_end mid-rally) — those are excluded from
    side-out / break / chain calculations.
    """
    sorted_pts = sorted(by_point.keys())
    winners = {}
    for i, p in enumerate(sorted_pts):
        last_ev = by_point[p][-1]
        before = (last_ev["score_a"], last_ev["score_b"])
        if i + 1 < len(sorted_pts):
            nxt_first = by_point[sorted_pts[i + 1]][0]
            after = (nxt_first["score_a"], nxt_first["score_b"])
        else:
            after = (game["final_score_a"], game["final_score_b"])
        if after[0] > before[0]:
            winners[p] = "A"
        elif after[1] > before[1]:
            winners[p] = "B"
        else:
            winners[p] = None
    return winners


# ---------------------------------------------------------------------------
#  Per-point server / receiver derivation
# ---------------------------------------------------------------------------

def _derive_ot_setup(rotation_idx: int) -> tuple[str, dict, dict]:
    """Mirror `game_engine._enter_overtime`: from the rotation index at the
    moment OT was triggered, derive (first_server_team, ot_servers,
    ot_receivers) — both dicts keyed by team.
    """
    current_pair = SERVE_ROTATION[rotation_idx % len(SERVE_ROTATION)]
    srv_team = current_pair.server[0]
    rcv_team = current_pair.receiver[0]
    ot_servers = {srv_team: current_pair.server}
    ot_receivers = {rcv_team: current_pair.receiver}

    partner_srv_team = "B" if srv_team == "A" else "A"
    partner_rcv_team = "B" if rcv_team == "A" else "A"
    for offset in range(1, len(SERVE_ROTATION)):
        idx = (rotation_idx - offset) % len(SERVE_ROTATION)
        pair = SERVE_ROTATION[idx]
        if pair.server[0] == partner_srv_team and partner_srv_team not in ot_servers:
            ot_servers[partner_srv_team] = pair.server
        if pair.receiver[0] == partner_rcv_team and partner_rcv_team not in ot_receivers:
            ot_receivers[partner_rcv_team] = pair.receiver
        if len(ot_servers) == 2 and len(ot_receivers) == 2:
            break

    for t in ("A", "B"):
        ot_servers.setdefault(t, f"{t}1")
        ot_receivers.setdefault(t, f"{t}1")

    return srv_team, ot_servers, ot_receivers


def _derive_point_pairs(
    game: dict, by_point: dict, sorted_pts: list[int]
) -> dict[int, tuple[Optional[str], Optional[str]]]:
    """Return {point_number: (server_slot, receiver_slot)}.

    For each point we prefer event-derived slots (a serve event names the
    server, a receive event names the receiver). When a point has no receive
    event (ace or double fault) the receiver is back-derived from the rotation
    cycle / OT pair state — exactly mirroring the engine's bookkeeping.
    """
    rotation_idx = game.get("starting_rotation_idx") or 0
    in_ot = False
    ot_first_server_team: Optional[str] = None
    ot_servers: dict[str, str] = {}
    ot_receivers: dict[str, str] = {}
    ot_counter = 0

    result: dict[int, tuple[Optional[str], Optional[str]]] = {}

    for p in sorted_pts:
        events = by_point[p]
        if not events:
            continue

        # Slot from rotation / OT state (fallback).
        if in_ot and ot_first_server_team:
            srv_team = (
                ot_first_server_team
                if ot_counter % 2 == 0
                else ("B" if ot_first_server_team == "A" else "A")
            )
            rcv_team = "B" if srv_team == "A" else "A"
            derived_server = ot_servers.get(srv_team, f"{srv_team}1")
            derived_receiver = ot_receivers.get(rcv_team, f"{rcv_team}1")
        else:
            pair = pair_at(rotation_idx)
            derived_server = pair.server
            derived_receiver = pair.receiver

        # Prefer event ground truth.
        server_slot: Optional[str] = derived_server
        receiver_slot: Optional[str] = derived_receiver
        for ev in events:
            if ev["event_type"] in (
                "serve_ace", "serve_fault", "serve_double_fault",
            ):
                if ev["player"]:
                    server_slot = ev["player"]
                break
        for ev in events:
            if ev["event_type"] in ("receive", "weak_receive"):
                if ev["player"]:
                    receiver_slot = ev["player"]
                break

        result[p] = (server_slot, receiver_slot)

        # Advance state for the next point — matches the engine in _end_point:
        # if the point was played under OT, the OT serve counter advances; the
        # rotation index advances otherwise. The OT-trigger point itself is
        # counted as a normal point (rotation advances) and then OT begins.
        triggered_ot = any(
            e["event_type"] == "overtime_triggered" for e in events
        )
        if in_ot:
            ot_counter += 1
        else:
            if triggered_ot:
                ot_first_server_team, ot_servers, ot_receivers = (
                    _derive_ot_setup(rotation_idx)
                )
                ot_counter = 0
                in_ot = True
            rotation_idx = next_rotation_index(rotation_idx)

    return result


# ---------------------------------------------------------------------------
#  RoundX score computation
# ---------------------------------------------------------------------------

def _compute_roundx_from_events(
    game: dict,
    by_point: dict,
    sorted_pts: list[int],
    winners: dict,
    point_pairs: dict,
) -> dict:
    """Single-pass RoundX computation. Returns {slot: {...}} dict.

    Shares by_point / winners / point_pairs with `_compute` so the analysis
    screen and the end-game dialog both pay only one pass over the event log.
    """
    slots = ["A1", "A2", "B1", "B2"]
    names = {
        "A1": game["a1_name"], "A2": game["a2_name"],
        "B1": game["b1_name"], "B2": game["b2_name"],
    }

    raw_scores: dict[str, int] = {s: 0 for s in slots}
    breakdowns: dict[str, list[tuple[str, int]]] = {s: [] for s in slots}

    def add(target_slot: Optional[str], label: str, delta: int) -> None:
        if not target_slot or delta == 0:
            return
        raw_scores[target_slot] += delta
        breakdowns[target_slot].append((label, delta))

    counted_rallies = 0
    for p in sorted_pts:
        winner = winners.get(p)
        if winner is None:
            continue
        counted_rallies += 1
        server_slot, receiver_slot = point_pairs.get(p, (None, None))

        for ev in by_point[p]:
            et = ev["event_type"]
            slot = ev["player"]
            team = _team_of(slot)
            lost = team is not None and team != winner

            if et == "serve_ace":
                add(slot, "Ace", ROUNDX_WEIGHTS["serve_ace"])
                # Penalize the would-be receiver (derived from rotation).
                add(receiver_slot, "Aced", ROUNDX_WEIGHTS["aced_penalty"])
            elif et == "serve_double_fault":
                add(slot, "Double fault", ROUNDX_WEIGHTS["serve_double_fault"])
            elif et == "serve_fault":
                # Single fault is intentionally weightless (pressure event,
                # no outcome). Skipped so the breakdown doesn't list +0 rows.
                pass
            elif et == "receive":
                add(slot, "Receive", ROUNDX_WEIGHTS["receive_good"])
            elif et == "weak_receive":
                delta = ROUNDX_WEIGHTS["weak_receive_base"]
                lbl = "Weak receive"
                if lost:
                    delta += ROUNDX_WEIGHTS["weak_receive_lost_mod"]
                    lbl = "Weak receive (lost)"
                add(slot, lbl, delta)
            elif et == "set":
                add(slot, "Set", ROUNDX_WEIGHTS["set_good"])
            elif et == "weak_set":
                delta = ROUNDX_WEIGHTS["weak_set_base"]
                lbl = "Weak set"
                if lost:
                    delta += ROUNDX_WEIGHTS["weak_set_lost_mod"]
                    lbl = "Weak set (lost)"
                add(slot, lbl, delta)
            elif et == "hit":
                add(slot, "Hit", ROUNDX_WEIGHTS["hit_good"])
            elif et == "weak_hit":
                delta = ROUNDX_WEIGHTS["weak_hit_base"]
                lbl = "Weak hit"
                if lost:
                    delta += ROUNDX_WEIGHTS["weak_hit_lost_mod"]
                    lbl = "Weak hit (lost)"
                add(slot, lbl, delta)
            elif et == "touch":
                add(slot, "Touch", ROUNDX_WEIGHTS["touch_base"])
                if team == winner:
                    add(slot, "Touch → break",
                        ROUNDX_WEIGHTS["touch_break_bonus"])
            elif et == "soft_touch":
                add(slot, "Soft touch", ROUNDX_WEIGHTS["soft_touch"])
            elif et == "weak_touch":
                add(slot, "Weak touch", ROUNDX_WEIGHTS["weak_touch"])
            elif et == "weak_soft_touch":
                add(slot, "Weak soft touch", ROUNDX_WEIGHTS["weak_soft_touch"])
            elif et == "point":
                add(slot, "Finisher", ROUNDX_WEIGHTS["point_finisher"])
            elif et == "error":
                add(slot, "Error", ROUNDX_WEIGHTS["error"])
            # serve_fault_type / lost / overtime_triggered / game_end → 0

    if counted_rallies > 0:
        scalar = min(
            ROUNDX_BENCHMARK_RALLIES / counted_rallies, ROUNDX_SCALAR_CAP
        )
    else:
        scalar = 1.0
    rated = counted_rallies >= ROUNDX_RATED_MIN_RALLIES

    normalized = {s: round(raw_scores[s] * scalar) for s in slots}
    avg = round(sum(normalized.values()) / 4) if normalized else 0

    result: dict[str, dict] = {}
    for s in slots:
        # Biggest swings first — the "Key Plays" narrative.
        bd_sorted = sorted(breakdowns[s], key=lambda x: -abs(x[1]))
        result[s] = {
            "slot": s,
            "name": names[s],
            "score": normalized[s],
            "raw_score": raw_scores[s],
            "scalar": round(scalar, 3),
            "rated": rated,
            "counted_rallies": counted_rallies,
            "relative_to_avg": normalized[s] - avg,
            "breakdown": bd_sorted,
        }
    return result


# ---------------------------------------------------------------------------
#  Counter scaffolding
# ---------------------------------------------------------------------------

def _empty_counters() -> dict:
    return {
        "aces": 0,
        "aced": 0,
        "good_receives": 0,
        "weak_receives": 0,
        "total_receives": 0,
        "clean_side_outs": 0,
        "side_outs": 0,
        "holds": 0,
        "points_served": 0,
        "points_received": 0,
        "breaks_for": 0,
        "breaks_against": 0,
        "total_touches": 0,
        "weak_touches": 0,
        "opponent_hits": 0,
        "sets": 0,
        "weak_sets": 0,
        "hits": 0,
        "weak_hits": 0,
        "finish_hits": 0,
        "single_faults_total": 0,
        "double_faults_total": 0,
        "single_faults_by_type": defaultdict(int),
        "double_faults_by_type": defaultdict(int),
        "total_serves": 0,
        # Errors — each error event is attributed to the player who performed
        # the immediately-preceding hit or set. Receive- and touch-sourced
        # errors are intentionally excluded (see ERROR_SOURCE_EVENTS).
        "total_errors": 0,
        "hit_errors": 0,
        "weak_hit_errors": 0,
        "set_errors": 0,
        "weak_set_errors": 0,
    }


def _finalize(stats: dict) -> dict:
    """Add derived percentages; convert defaultdicts to plain dicts."""
    stats["single_faults_by_type"] = dict(stats["single_faults_by_type"])
    stats["double_faults_by_type"] = dict(stats["double_faults_by_type"])
    all_f: dict = defaultdict(int)
    for k, v in stats["single_faults_by_type"].items():
        all_f[k] += v
    for k, v in stats["double_faults_by_type"].items():
        all_f[k] += v
    stats["all_faults_by_type"] = dict(all_f)
    stats["faults_total"] = (
        stats["single_faults_total"] + stats["double_faults_total"]
    )

    stats["clean_side_out_pct"] = _safe_pct(
        stats["clean_side_outs"], stats["total_receives"]
    )
    stats["side_out_pct"] = _safe_pct(
        stats["side_outs"], stats["total_receives"]
    )
    stats["hold_pct"] = _safe_pct(
        stats["holds"], stats["total_receives"]
    )
    stats["total_touches_pct"] = _safe_pct(
        stats["total_touches"], stats["opponent_hits"]
    )
    stats["weak_touch_ratio"] = _safe_pct(
        stats["weak_touches"], stats["total_touches"]
    )
    stats["weak_set_ratio"] = _safe_pct(stats["weak_sets"], stats["sets"])
    stats["weak_hit_ratio"] = _safe_pct(stats["weak_hits"], stats["hits"])
    stats["finish_hit_ratio"] = _safe_pct(stats["finish_hits"], stats["hits"])
    stats["weak_receive_ratio"] = _safe_pct(
        stats["weak_receives"], stats["total_receives"]
    )
    stats["fault_rate"] = _safe_pct(stats["faults_total"], stats["total_serves"])

    # Per-action error totals (canonical + weak variant) and rates against the
    # matching action count.
    hit_err = stats["hit_errors"] + stats["weak_hit_errors"]
    set_err = stats["set_errors"] + stats["weak_set_errors"]
    stats["hit_errors_total"] = hit_err
    stats["set_errors_total"] = set_err
    stats["hit_error_rate"] = _safe_pct(hit_err, stats["hits"])
    stats["set_error_rate"] = _safe_pct(set_err, stats["sets"])
    return stats


# ---------------------------------------------------------------------------
#  Core computation
# ---------------------------------------------------------------------------

def _compute(game: dict, events: list[dict]) -> dict:
    by_point: dict[int, list[dict]] = defaultdict(list)
    for ev in events:
        by_point[ev["point"]].append(ev)
    for evs in by_point.values():
        evs.sort(key=lambda e: e["seq_in_point"])

    winners = _determine_point_winners(by_point, game)

    slots = ["A1", "A2", "B1", "B2"]
    names = {
        "A1": game["a1_name"], "A2": game["a2_name"],
        "B1": game["b1_name"], "B2": game["b2_name"],
    }

    teams = {"A": _empty_counters(), "B": _empty_counters()}
    players = {s: _empty_counters() for s in slots}
    for s in slots:
        players[s]["name"] = names[s]
        players[s]["slot"] = s

    # Per-team chain inputs, one entry per scored point.
    chain_rows: dict[str, list[dict]] = {"A": [], "B": []}

    sorted_pts = sorted(by_point.keys())
    point_pairs = _derive_point_pairs(game, by_point, sorted_pts)

    counted_points = 0
    for p in sorted_pts:
        pt_events = by_point[p]
        if not pt_events:
            continue
        first = pt_events[0]
        srv_team = first["serving_team"]
        rcv_team = first["receiving_team"]
        winner = winners.get(p)
        if winner is None:
            # Abandoned — skip entirely.
            continue
        counted_points += 1

        server_slot, receiver_slot = point_pairs.get(p, (None, None))

        teams[srv_team]["points_served"] += 1
        teams[rcv_team]["points_received"] += 1
        if server_slot:
            players[server_slot]["points_served"] += 1

        rcv_team_received = False
        srv_team_touched_during_rally = False
        srv_team_hit = False

        weak_in_point = {
            "A": {"receive": [], "set": [], "hit": []},
            "B": {"receive": [], "set": [], "hit": []},
        }

        for j, ev in enumerate(pt_events):
            et = ev["event_type"]
            slot = ev["player"]
            slot_team = _team_of(slot)

            if et == "serve_ace":
                teams[srv_team]["aces"] += 1
                teams[rcv_team]["aced"] += 1
                teams[srv_team]["total_serves"] += 1
                if slot:
                    players[slot]["aces"] += 1
                    players[slot]["total_serves"] += 1
                if receiver_slot:
                    players[receiver_slot]["aced"] += 1

            elif et == "serve_fault":
                teams[srv_team]["single_faults_total"] += 1
                teams[srv_team]["total_serves"] += 1
                if slot:
                    players[slot]["single_faults_total"] += 1
                    players[slot]["total_serves"] += 1

            elif et == "serve_double_fault":
                teams[srv_team]["double_faults_total"] += 1
                teams[srv_team]["total_serves"] += 1
                if slot:
                    players[slot]["double_faults_total"] += 1
                    players[slot]["total_serves"] += 1

            elif et == "serve_fault_type":
                prev = pt_events[j - 1] if j > 0 else None
                ft = ev["fault_type"] or "Unknown"
                if prev and prev["event_type"] == "serve_fault":
                    teams[srv_team]["single_faults_by_type"][ft] += 1
                    if slot:
                        players[slot]["single_faults_by_type"][ft] += 1
                elif prev and prev["event_type"] == "serve_double_fault":
                    teams[srv_team]["double_faults_by_type"][ft] += 1
                    if slot:
                        players[slot]["double_faults_by_type"][ft] += 1

            elif et in RECEIVE_EVENTS:
                rcv_team_received = True
                teams[rcv_team]["total_receives"] += 1
                teams[srv_team]["total_serves"] += 1
                if et == "receive":
                    teams[rcv_team]["good_receives"] += 1
                else:
                    teams[rcv_team]["weak_receives"] += 1
                if slot:
                    players[slot]["total_receives"] += 1
                    if et == "receive":
                        players[slot]["good_receives"] += 1
                    else:
                        players[slot]["weak_receives"] += 1
                        weak_in_point[rcv_team]["receive"].append(slot)
                # Credit the server's per-player serve count — previously
                # only ace / fault events bumped it, so per-player fault rate
                # was overestimated.
                if server_slot:
                    players[server_slot]["total_serves"] += 1

            elif et in SET_EVENTS:
                if slot_team is not None:
                    teams[slot_team]["sets"] += 1
                    if et == "weak_set":
                        teams[slot_team]["weak_sets"] += 1
                if slot:
                    players[slot]["sets"] += 1
                    if et == "weak_set":
                        players[slot]["weak_sets"] += 1
                        weak_in_point[slot_team]["set"].append(slot)

            elif et in HIT_EVENTS:
                if slot_team is not None:
                    teams[slot_team]["hits"] += 1
                    teams[_opp(slot_team)]["opponent_hits"] += 1
                    if et == "weak_hit":
                        teams[slot_team]["weak_hits"] += 1
                if slot:
                    players[slot]["hits"] += 1
                    if et == "weak_hit":
                        players[slot]["weak_hits"] += 1
                        weak_in_point[slot_team]["hit"].append(slot)
                if slot_team == srv_team:
                    srv_team_hit = True

            elif et in TOUCH_EVENTS:
                if slot_team is not None:
                    teams[slot_team]["total_touches"] += 1
                    if et in WEAK_TOUCH_EVENTS:
                        teams[slot_team]["weak_touches"] += 1
                if slot:
                    players[slot]["total_touches"] += 1
                    if et in WEAK_TOUCH_EVENTS:
                        players[slot]["weak_touches"] += 1
                if slot_team == srv_team:
                    srv_team_touched_during_rally = True

            elif et == "error":
                # An Error event means "the previous play action lost the
                # point". Walk back to find the immediately-preceding action
                # in the point and credit the error there. Annotations like
                # `serve_fault_type` are skipped so we land on the real
                # action that caused the error.
                src = None
                for k in range(j - 1, -1, -1):
                    cand = pt_events[k]
                    if cand["event_type"] in ERROR_SOURCE_EVENTS:
                        src = cand
                        break
                if src is not None:
                    counter_key = _ERROR_COUNTER_FOR_SOURCE.get(
                        src["event_type"]
                    )
                    src_slot = src["player"]
                    src_team = _team_of(src_slot)
                    if src_team is not None and counter_key:
                        teams[src_team][counter_key] += 1
                        teams[src_team]["total_errors"] += 1
                    if src_slot and counter_key:
                        players[src_slot][counter_key] += 1
                        players[src_slot]["total_errors"] += 1

        # Side-out / hold classification (only when a receive actually happened).
        if rcv_team_received and winner == rcv_team:
            teams[rcv_team]["holds"] += 1
            if receiver_slot:
                players[receiver_slot]["holds"] += 1
            if not srv_team_hit:
                teams[rcv_team]["side_outs"] += 1
                if receiver_slot:
                    players[receiver_slot]["side_outs"] += 1
                if not srv_team_touched_during_rally:
                    teams[rcv_team]["clean_side_outs"] += 1
                    if receiver_slot:
                        players[receiver_slot]["clean_side_outs"] += 1

        # Breaks: any point the serving team wins (per the user's glossary)
        # — including aces. Credited to the server (break-for) and the
        # designated receiver (break-against).
        if winner == srv_team:
            teams[srv_team]["breaks_for"] += 1
            teams[rcv_team]["breaks_against"] += 1
            if server_slot:
                players[server_slot]["breaks_for"] += 1
            if receiver_slot:
                players[receiver_slot]["breaks_against"] += 1

        # Chain row per team
        for team in ("A", "B"):
            wp = weak_in_point[team]
            chain_rows[team].append({
                "weak_receive_players": wp["receive"],
                "weak_set_players": wp["set"],
                "weak_hit_players": wp["hit"],
                "has_weak_receive": bool(wp["receive"]),
                "has_weak_set": bool(wp["set"]),
                "has_weak_hit": bool(wp["hit"]),
                "lost": winner != team,
                "won": winner == team,
            })

        # Finish hits (per hit event): count a hit when the hitter's team wins
        # the point and the opponent never records a hit after that hit.
        #
        # Two accepted endings:
        #   1) Direct point (no opponent defensive touch, then a `point` event).
        #   2) Opponent gets a defensive touch, but still no opponent hit after.
        #
        # This intentionally compares against `hits` as denominator (which
        # includes both `hit` and `weak_hit`).
        for j, ev in enumerate(pt_events):
            if ev["event_type"] not in HIT_EVENTS:
                continue
            slot = ev["player"]
            if not slot:
                continue
            hit_team = _team_of(slot)
            if hit_team is None or hit_team != winner:
                continue
            opp = _opp(hit_team)
            tail = pt_events[j + 1:]
            opp_hit_after = any(
                e["event_type"] in HIT_EVENTS and _team_of(e["player"]) == opp
                for e in tail
            )
            if opp_hit_after:
                continue

            opp_touch_after = any(
                e["event_type"] in TOUCH_EVENTS and _team_of(e["player"]) == opp
                for e in tail
            )
            if not opp_touch_after:
                # No defensive touch path qualifies only as a direct point.
                is_direct_point = any(e["event_type"] == "point" for e in tail)
                if not is_direct_point:
                    continue

            teams[hit_team]["finish_hits"] += 1
            players[slot]["finish_hits"] += 1

    # Per-player Total Touches % uses the opposing TEAM's hits as its
    # denominator, so total_touches_pct sums sensibly across the two
    # team-mates (sum of per-player == team's total_touches_pct).
    for slot, ps in players.items():
        ps["opponent_hits"] = teams[slot[0]]["opponent_hits"]

    # Derived metrics
    for ts in teams.values():
        _finalize(ts)
    for ps in players.values():
        _finalize(ps)

    chains = _compute_chains(chain_rows, names)

    flow = _compute_flow(by_point, sorted_pts, winners)

    roundx = _compute_roundx_from_events(
        game, by_point, sorted_pts, winners, point_pairs
    )
    # Inject the per-player RoundX fields so the analysis screen can read
    # them off the same `players[slot]` dict it already iterates.
    for s in slots:
        rx = roundx[s]
        players[s]["roundx_score"] = rx["score"]
        players[s]["roundx_raw_score"] = rx["raw_score"]
        players[s]["roundx_relative_to_avg"] = rx["relative_to_avg"]
        players[s]["roundx_rated"] = rx["rated"]
        players[s]["roundx_breakdown"] = rx["breakdown"]

    return {
        "game": {
            "id": game["id"],
            "created_at": game["created_at"],
            "ended_at": game.get("ended_at"),
            "names": names,
            "team_a_players": [game["a1_name"], game["a2_name"]],
            "team_b_players": [game["b1_name"], game["b2_name"]],
            "final_score_a": game["final_score_a"],
            "final_score_b": game["final_score_b"],
            "winner": game.get("winner_team"),
            "end_reason": game.get("end_reason"),
            "target_score": game["target_score"],
            "hard_cap": game["hard_cap"],
            "total_points": counted_points,
        },
        "teams": teams,
        "players": players,
        "chains": chains,
        "flow": flow,
        "roundx": roundx,
        # Exposed for match-level aggregation — same shape as the input to
        # `_compute_chains`. Not consumed by the analysis UI directly.
        "_chain_rows": chain_rows,
    }


# ---------------------------------------------------------------------------
#  Game flow — per-point running score with break / OT annotations
# ---------------------------------------------------------------------------

def _compute_flow(by_point: dict, sorted_pts: list[int], winners: dict) -> list[dict]:
    """One row per scored point. The visualization layer walks this list to
    render the running score, mark breaks (serving team won), and tint OT
    points. Abandoned points are excluded — they don't change the score.

    `triggers_ot` is True for the point that fired `overtime_triggered`; the
    point itself was played under normal rotation and isn't marked `in_ot`,
    but the next point onwards is.
    """
    flow: list[dict] = []
    score_a = 0
    score_b = 0
    seq = 0
    in_ot = False
    for p in sorted_pts:
        events = by_point[p]
        if not events:
            continue
        winner = winners.get(p)
        if winner is None:
            continue
        srv_team = events[0]["serving_team"]
        seq += 1
        if winner == "A":
            score_a += 1
        else:
            score_b += 1
        triggered = any(e["event_type"] == "overtime_triggered" for e in events)
        flow.append({
            "point": seq,
            "score_a": score_a,
            "score_b": score_b,
            "winner": winner,
            "serving_team": srv_team,
            "is_break": winner == srv_team,
            "in_ot": in_ot,
            "triggers_ot": triggered,
        })
        if triggered:
            in_ot = True
    return flow


# ---------------------------------------------------------------------------
#  Chain analysis
# ---------------------------------------------------------------------------

def _pattern_row(label: str, rows: list[dict], pred, baseline: float) -> dict:
    count = sum(1 for r in rows if pred(r))
    losses = sum(1 for r in rows if pred(r) and r["lost"])
    lr = _safe_pct(losses, count)
    vs = (lr - baseline) if lr is not None else None
    return {
        "pattern": label,
        "count": count,
        "losses": losses,
        "loss_rate": lr,
        "vs_baseline": vs,
    }


def _compute_chains(chain_rows: dict[str, list[dict]], names: dict) -> dict:
    out = {}
    for team in ("A", "B"):
        rows = chain_rows[team]
        n_points = len(rows)
        n_lost = sum(1 for r in rows if r["lost"])
        baseline = n_lost / n_points if n_points else 0.0

        team_patterns = [
            _pattern_row("Any weak action",
                         rows,
                         lambda r: r["has_weak_receive"] or r["has_weak_set"]
                                   or r["has_weak_hit"],
                         baseline),
            _pattern_row("Weak Receive",
                         rows,
                         lambda r: r["has_weak_receive"], baseline),
            _pattern_row("Weak Set",
                         rows,
                         lambda r: r["has_weak_set"], baseline),
            _pattern_row("Weak Hit",
                         rows,
                         lambda r: r["has_weak_hit"], baseline),
            _pattern_row("Weak Receive → Weak Set",
                         rows,
                         lambda r: r["has_weak_receive"] and r["has_weak_set"],
                         baseline),
            _pattern_row("Weak Receive → Weak Hit",
                         rows,
                         lambda r: r["has_weak_receive"] and r["has_weak_hit"],
                         baseline),
            _pattern_row("Weak Set → Weak Hit",
                         rows,
                         lambda r: r["has_weak_set"] and r["has_weak_hit"],
                         baseline),
            _pattern_row("Weak Receive → Weak Set → Weak Hit",
                         rows,
                         lambda r: (r["has_weak_receive"] and r["has_weak_set"]
                                    and r["has_weak_hit"]),
                         baseline),
        ]

        per_player = {}
        for slot in (f"{team}1", f"{team}2"):
            per_player[slot] = {
                "name": names[slot],
                "slot": slot,
                "patterns": [
                    _pattern_row("Weak Receive",
                                 rows,
                                 lambda r, s=slot: s in r["weak_receive_players"],
                                 baseline),
                    _pattern_row("Weak Set",
                                 rows,
                                 lambda r, s=slot: s in r["weak_set_players"],
                                 baseline),
                    _pattern_row("Weak Hit",
                                 rows,
                                 lambda r, s=slot: s in r["weak_hit_players"],
                                 baseline),
                    _pattern_row(
                        "Any weak action",
                        rows,
                        lambda r, s=slot: (s in r["weak_receive_players"]
                                           or s in r["weak_set_players"]
                                           or s in r["weak_hit_players"]),
                        baseline,
                    ),
                ],
            }

        out[team] = {
            "baseline_loss_rate": baseline,
            "total_points": n_points,
            "total_losses": n_lost,
            "team_patterns": team_patterns,
            "per_player": per_player,
        }
    return out
