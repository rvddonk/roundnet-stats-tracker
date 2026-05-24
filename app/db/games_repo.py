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
) -> int:
    now = datetime.utcnow().isoformat()
    db.execute(
        """INSERT INTO games
           (created_at, target_score, hard_cap, win_by,
            a1_name, a2_name, b1_name, b2_name,
            first_server_slot, first_receiver_slot, starting_rotation_idx,
            final_score_a, final_score_b)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)""",
        (now, target_score, hard_cap, win_by, a1, a2, b1, b2,
         first_server_slot, first_receiver_slot, starting_rotation_idx),
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
                  final_score_a, final_score_b, winner_team, end_reason
           FROM games ORDER BY created_at DESC"""
    )
    return [dict(r) for r in rows]


def get_game(game_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    return dict(row) if row else None
