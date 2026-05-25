"""Group finished games into tournament matches.

A "match" is two or more games that share the same (tournament, stage,
stage_part) tuple. A game missing any of those three fields, or one whose
key is unique in the input list, stays a standalone "single" entry.

Within a match the games are sorted oldest-first (chronological play order).
The match entry itself is placed at the position of its most-recent game in
the input ordering — preserving the caller's outer sort (newest-first by
created_at) so a recent match floats above older standalone games.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional


def match_key(game: dict) -> Optional[tuple[str, str, str]]:
    """Return the (tournament, stage, part) grouping key, or None if any
    of those fields is missing/empty."""
    tournament = (game.get("tournament") or "").strip()
    stage = (game.get("tournament_stage") or "").strip()
    part = (game.get("stage_part") or "").strip()
    if not (tournament and stage and part):
        return None
    return (tournament, stage, part)


def group_games_into_matches(games: list[dict]) -> list[dict]:
    """Group games by (tournament, stage, stage_part).

    Returns an ordered list of entries:
      {"type": "single", "game": <game>}
      {"type": "match", "key": (tournament, stage, part), "games": [<game>, ...]}
    """
    by_key: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    no_key: list[tuple[int, dict]] = []

    for idx, g in enumerate(games):
        k = match_key(g)
        if k is None:
            no_key.append((idx, g))
        else:
            by_key[k].append((idx, g))

    entries: list[tuple[int, dict]] = []

    for k, items in by_key.items():
        if len(items) < 2:
            # Unique-key games are still singles — no grouping.
            for (idx, g) in items:
                entries.append((idx, {"type": "single", "game": g}))
            continue
        anchor_idx = min(idx for (idx, _) in items)
        sorted_games = sorted(
            (g for (_, g) in items),
            key=lambda g: g.get("created_at") or "",
        )
        entries.append((anchor_idx, {
            "type": "match",
            "key": k,
            "games": sorted_games,
        }))

    for (idx, g) in no_key:
        entries.append((idx, {"type": "single", "game": g}))

    return [e for (_, e) in sorted(entries, key=lambda x: x[0])]


def canonical_team_maps(
    per_game_names: list[dict[str, str]],
) -> list[dict]:
    """Per-game canonical-team / canonical-slot mappings.

    Within a tournament match the two players on a team often swap onto the
    other side of the net between sets (e.g. PlayerX is `A1` in game 1 but
    `B1` in game 2). Aggregating by raw slot would then double-count one
    pairing as if it were two different teams. This helper resolves that by
    anchoring on the first game's roster:

      * Canonical "A" = the two players who were `A1` + `A2` in game 1.
      * Canonical "B" = the two players who were `B1` + `B2` in game 1.
      * Canonical slot `A1` = the human who was `A1` in game 1 (etc.).

    For each subsequent game whose roster matches the anchor — same set of
    four players, possibly with the two teams swapped between sides — the
    returned `team_map` and `slot_map` tell callers how to re-attribute that
    game's per-team and per-slot stats so the same person always lands in
    the same canonical bucket.

    When player composition differs from the anchor (e.g. a substitute came
    in), no remapping is possible and we fall back to identity — the caller
    will end up aggregating that game by its raw slots.

    Argument shape: each list element is a `{"A1": name, "A2": name,
    "B1": name, "B2": name}` dict (the four slots for one game).
    """
    if not per_game_names:
        return []
    anchor = per_game_names[0]
    canon_a = frozenset({anchor["A1"], anchor["A2"]})
    canon_b = frozenset({anchor["B1"], anchor["B2"]})
    canon_slot_for: dict[tuple[str, str], str] = {
        ("A", anchor["A1"]): "A1",
        ("A", anchor["A2"]): "A2",
        ("B", anchor["B1"]): "B1",
        ("B", anchor["B2"]): "B2",
    }
    identity = {
        "team_map": {"A": "A", "B": "B"},
        "slot_map": {"A1": "A1", "A2": "A2", "B1": "B1", "B2": "B2"},
    }

    result: list[dict] = []
    for names in per_game_names:
        a_set = frozenset({names["A1"], names["A2"]})
        b_set = frozenset({names["B1"], names["B2"]})
        if a_set == canon_a and b_set == canon_b:
            team_map = {"A": "A", "B": "B"}
        elif a_set == canon_b and b_set == canon_a:
            team_map = {"A": "B", "B": "A"}
        else:
            result.append({
                "team_map": dict(identity["team_map"]),
                "slot_map": dict(identity["slot_map"]),
            })
            continue
        slot_map: dict[str, str] = {}
        used: set[str] = set()
        for in_slot in ("A1", "A2", "B1", "B2"):
            canon_team = team_map[in_slot[0]]
            canon_slot = canon_slot_for.get((canon_team, names[in_slot]))
            if canon_slot and canon_slot not in used:
                slot_map[in_slot] = canon_slot
                used.add(canon_slot)
            else:
                # Should not occur when the team-sets matched, but stay safe.
                slot_map[in_slot] = in_slot
        result.append({"team_map": team_map, "slot_map": slot_map})
    return result


def _slot_names_from_game(game: dict) -> dict[str, str]:
    """Pull the `{"A1": name, ...}` slot map out of a DB-row-shaped game dict."""
    return {
        "A1": game["a1_name"], "A2": game["a2_name"],
        "B1": game["b1_name"], "B2": game["b2_name"],
    }


def canonical_winners(games: list[dict]) -> list[Optional[str]]:
    """Per-game canonical winner ('A', 'B', or None).

    `games[i].winner_team` is in raw in-game terms; this returns it after
    remapping each game's A/B onto the canonical match teams (anchored on
    the first game's roster).
    """
    maps = canonical_team_maps([_slot_names_from_game(g) for g in games])
    out: list[Optional[str]] = []
    for g, m in zip(games, maps):
        w = g.get("winner_team")
        if w is None:
            out.append(None)
        else:
            out.append(m["team_map"].get(w, w))
    return out


def match_series_score(games: list[dict]) -> tuple[int, int]:
    """(Canonical-team-A wins, canonical-team-B wins) across the match.

    Anchored on the first game's roster: when the two players swap sides
    between sets they're still credited to the same canonical team. See
    `canonical_team_maps` for the full rule.
    """
    a = b = 0
    for w in canonical_winners(games):
        if w == "A":
            a += 1
        elif w == "B":
            b += 1
    return a, b


def match_winner(games: list[dict]) -> Optional[str]:
    """Match-level winner ('A' or 'B'), or None on tie / no completed games.

    Uses canonical team identity — see `match_series_score`.
    """
    a, b = match_series_score(games)
    if a > b:
        return "A"
    if b > a:
        return "B"
    return None
