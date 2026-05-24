"""
Import a previously-exported game CSV back into the DB.

Reads the format produced by `app.core.csv_export.export_game` (same column
order and event_type values) and creates a fresh game + events rows. The new
game appears in History and is fully analysable.

Some metadata isn't carried in the CSV (target_score, hard_cap, win_by), so
those fall back to the config defaults. end_reason is set to 'imported' and
the winner is inferred from the final score.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import DEFAULT_TARGET, DEFAULT_HARD_CAP
from app.core.rotation import rotation_index_for
from app.db import events_repo, games_repo


EXPECTED_HEADER = [
    "game_id", "point", "sequence_in_point", "timestamp", "event_type",
    "player_slot", "player_name", "fault_type", "serving_team",
    "receiving_team", "score_a", "score_b",
]


class CsvImportError(Exception):
    """Raised when the CSV can't be imported."""


def import_game_from_csv(file_path: str | Path) -> int:
    """Insert the CSV's events as a new game; return the new game_id."""
    path = Path(file_path)
    if not path.exists():
        raise CsvImportError(f"File not found: {path}")

    rows = _read_csv(path)
    if not rows:
        raise CsvImportError("CSV has no event rows.")

    names = _extract_names(rows)
    first_server, first_receiver = _infer_first_serve_pair(rows, names)
    rot_idx = rotation_index_for(first_server, first_receiver)
    if rot_idx == -1:
        rot_idx = 0

    final_a, final_b, winner = _infer_final_score_and_winner(rows)

    game_id = games_repo.create_game(
        target_score=DEFAULT_TARGET,
        hard_cap=DEFAULT_HARD_CAP,
        a1=names["A1"], a2=names["A2"],
        b1=names["B1"], b2=names["B2"],
        first_server_slot=first_server,
        first_receiver_slot=first_receiver,
        starting_rotation_idx=rot_idx,
        win_by=2,
    )
    games_repo.finalize_game(
        game_id=game_id,
        winner_team=winner,
        end_reason="imported",
        score_a=final_a,
        score_b=final_b,
    )

    for r in rows:
        events_repo.append(
            game_id=game_id,
            point=int(r["point"]),
            seq_in_point=int(r["sequence_in_point"]),
            timestamp=r["timestamp"] or datetime.utcnow().isoformat(),
            event_type=r["event_type"],
            player=r["player_slot"] or None,
            fault_type=r["fault_type"] or None,
            serving_team=r["serving_team"],
            receiving_team=r["receiving_team"],
            score_a=int(r["score_a"]),
            score_b=int(r["score_b"]),
        )

    return game_id


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise CsvImportError("CSV is empty.")
        if header != EXPECTED_HEADER:
            raise CsvImportError(
                "CSV header does not match the expected export format.\n"
                f"  expected: {EXPECTED_HEADER}\n"
                f"  found:    {header}"
            )
        rows = []
        for i, raw in enumerate(reader, start=2):
            if not raw or all(c.strip() == "" for c in raw):
                continue
            if len(raw) != len(EXPECTED_HEADER):
                raise CsvImportError(
                    f"Row {i} has {len(raw)} columns; expected "
                    f"{len(EXPECTED_HEADER)}."
                )
            rows.append(dict(zip(EXPECTED_HEADER, raw)))
        rows.sort(key=lambda r: (int(r["point"]), int(r["sequence_in_point"])))
        return rows


def _extract_names(rows: list[dict]) -> dict[str, str]:
    names: dict[str, str] = {}
    for r in rows:
        slot = r["player_slot"]
        name = r["player_name"]
        if slot and name and slot not in names:
            names[slot] = name
    # Fill in any missing slots with placeholders so the import doesn't break
    # on a CSV that never recorded a particular slot.
    for s in ("A1", "A2", "B1", "B2"):
        names.setdefault(s, s)
    return names


def _infer_first_serve_pair(rows: list[dict], names: dict) -> tuple[str, str]:
    """
    First-server slot comes from the first event of point 1 that names the
    server (an ace/fault/serve_fault_type or — if a clean first serve — the
    first event with serving_team set as a fallback).
    First-receiver comes from the first receive event of point 1.
    """
    first_point_rows = [r for r in rows if int(r["point"]) == 1]
    if not first_point_rows:
        first_point_rows = rows

    server: Optional[str] = None
    for r in first_point_rows:
        if r["event_type"] in ("serve_ace", "serve_fault",
                               "serve_double_fault", "serve_fault_type"):
            slot = r["player_slot"]
            if slot:
                server = slot
                break
    if server is None:
        srv_team = first_point_rows[0]["serving_team"]
        server = f"{srv_team}1"

    receiver: Optional[str] = None
    for r in first_point_rows:
        if r["event_type"] in ("receive", "weak_receive"):
            slot = r["player_slot"]
            if slot:
                receiver = slot
                break
    if receiver is None:
        rcv_team = first_point_rows[0]["receiving_team"]
        receiver = f"{rcv_team}1"

    return server, receiver


def _infer_final_score_and_winner(rows: list[dict]) -> tuple[int, int, Optional[str]]:
    """
    Walk points in order, computing the score after the last point. Final
    score = last event's score + 1 for the team that won the last point
    (determined by the structure: the last event before a point-end always
    has the pre-award score).
    """
    by_point: dict[int, list[dict]] = {}
    for r in rows:
        by_point.setdefault(int(r["point"]), []).append(r)
    sorted_pts = sorted(by_point.keys())

    final_a = 0
    final_b = 0
    for i, p in enumerate(sorted_pts):
        pt = by_point[p]
        last = pt[-1]
        before = (int(last["score_a"]), int(last["score_b"]))
        if i + 1 < len(sorted_pts):
            nxt = by_point[sorted_pts[i + 1]][0]
            after = (int(nxt["score_a"]), int(nxt["score_b"]))
        else:
            # Last point — infer winner from the last event's serving/receiving
            # team and the action: ace/error/point all decide who won.
            after = _guess_after_last(last, before)
        final_a, final_b = after

    if final_a > final_b:
        winner = "A"
    elif final_b > final_a:
        winner = "B"
    else:
        winner = None
    return final_a, final_b, winner


def _guess_after_last(last: dict, before: tuple[int, int]) -> tuple[int, int]:
    """Infer the post-award score for the final point in the CSV."""
    et = last["event_type"]
    srv = last["serving_team"]
    rcv = last["receiving_team"]
    a, b = before

    # Serving team wins
    if et == "serve_ace":
        return (a + 1, b) if srv == "A" else (a, b + 1)
    # Receiving team wins
    if et == "serve_double_fault":
        return (a + 1, b) if rcv == "A" else (a, b + 1)
    if et == "serve_fault_type":
        # Only a double-fault's annotation lands as the final event before a
        # point ends with that pattern, so attribute to the receiver.
        return (a + 1, b) if rcv == "A" else (a, b + 1)
    # point / error — the last_touch_team determines it, but we don't have
    # that field on the row. Best-effort guess: a generic "point" event leaves
    # the rally state ambiguous; we leave the score unchanged.
    return before
