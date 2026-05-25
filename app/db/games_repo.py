from datetime import datetime

from app.db.database import db


def create_game(
    target_score: int,
    hard_cap: int,
    a1: str, a2: str, b1: str, b2: str,
    first_server_slot: str,
    first_receiver_slot: str,
    starting_rotation_idx: int,
    win_by: int = 2,
    tournament: str | None = None,
    tournament_stage: str | None = None,
    stage_part: str | None = None,
    bracket_type: str | None = None,
    match_format: str | None = None,
    division: str | None = None,
    division_tier: str | None = None,
    team_count: int | None = None,
) -> int:
    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO games
           (created_at, target_score, hard_cap, win_by,
            a1_name, a2_name, b1_name, b2_name,
            first_server_slot, first_receiver_slot, starting_rotation_idx,
            final_score_a, final_score_b,
            tournament, tournament_stage, stage_part, bracket_type, match_format,
            division, division_tier, team_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, target_score, hard_cap, win_by, a1, a2, b1, b2,
         first_server_slot, first_receiver_slot, starting_rotation_idx,
         tournament, tournament_stage, stage_part, bracket_type, match_format,
         division, division_tier, team_count),
    )
    return db.last_insert_id()


def update_score(game_id: int, score_a: int, score_b: int) -> None:
    db.execute(
        "UPDATE games SET final_score_a = ?, final_score_b = ? WHERE id = ?",
        (score_a, score_b, game_id),
    )


def finalize_game(
    game_id: int,
    winner_team: str | None,
    end_reason: str,
    score_a: int,
    score_b: int,
) -> None:
    now = datetime.utcnow().isoformat()
    db.execute(
        """UPDATE games SET ended_at = ?, winner_team = ?, end_reason = ?,
           final_score_a = ?, final_score_b = ?
           WHERE id = ?""",
        (now, winner_team, end_reason, score_a, score_b, game_id),
    )


def list_games() -> list[dict]:
    rows = db.query_all(
        """SELECT id, created_at, ended_at, target_score, hard_cap,
                  a1_name, a2_name, b1_name, b2_name,
                  final_score_a, final_score_b, winner_team, end_reason,
                  tournament, tournament_stage, stage_part, bracket_type, match_format,
                  division, division_tier, team_count, date_updated
           FROM games ORDER BY created_at DESC"""
    )
    return [dict(r) for r in rows]


def get_game(game_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    return dict(row) if row else None


def delete_game(game_id: int) -> None:
    db.execute("DELETE FROM games WHERE id = ?", (game_id,))


def update_metadata(
    game_id: int,
    a1_name: str,
    a2_name: str,
    b1_name: str,
    b2_name: str,
    tournament: str | None,
    tournament_stage: str | None,
    stage_part: str | None,
    bracket_type: str | None,
    match_format: str | None,
    division: str | None,
    division_tier: str | None,
    team_count: int | None,
) -> None:
    now = datetime.utcnow().isoformat()
    db.execute(
        """UPDATE games SET
              a1_name = ?, a2_name = ?, b1_name = ?, b2_name = ?,
              tournament = ?, tournament_stage = ?, stage_part = ?,
              bracket_type = ?, match_format = ?,
              division = ?, division_tier = ?, team_count = ?,
              date_updated = ?
           WHERE id = ?""",
        (a1_name, a2_name, b1_name, b2_name,
         tournament, tournament_stage, stage_part,
         bracket_type, match_format,
         division, division_tier, team_count,
         now, game_id),
    )


def list_tournaments() -> list[str]:
    """Distinct non-empty tournament names, most-recently-used first."""
    rows = db.query_all(
        """SELECT tournament, MAX(created_at) AS last_used
           FROM games
           WHERE tournament IS NOT NULL AND tournament <> ''
           GROUP BY tournament
           ORDER BY last_used DESC"""
    )
    return [row["tournament"] for row in rows]


def list_match_formats() -> list[str]:
    """Distinct non-empty match-format strings, most-recently-used first."""
    rows = db.query_all(
        """SELECT match_format, MAX(created_at) AS last_used
           FROM games
           WHERE match_format IS NOT NULL AND match_format <> ''
           GROUP BY match_format
           ORDER BY last_used DESC"""
    )
    return [row["match_format"] for row in rows]


def find_team_count(tournament: str, division: str | None) -> int | None:
    """Most-recent team_count recorded for this (tournament, division) pair.

    Used by the setup screen to autofill the team-count spinner when the
    user picks a tournament+division combination they've entered before.
    `division` may be None — matched literally against NULL rows.
    """
    if not tournament:
        return None
    if division is None:
        row = db.query_one(
            """SELECT team_count FROM games
               WHERE tournament = ?
                 AND division IS NULL
                 AND team_count IS NOT NULL
               ORDER BY created_at DESC LIMIT 1""",
            (tournament,),
        )
    else:
        row = db.query_one(
            """SELECT team_count FROM games
               WHERE tournament = ?
                 AND division = ?
                 AND team_count IS NOT NULL
               ORDER BY created_at DESC LIMIT 1""",
            (tournament, division),
        )
    return row["team_count"] if row else None
