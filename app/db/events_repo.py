from app.db.database import db


def append(
    game_id: int,
    point: int,
    seq_in_point: int,
    timestamp: str,
    event_type: str,
    player: str | None,
    fault_type: str | None,
    serving_team: str,
    receiving_team: str,
    score_a: int,
    score_b: int,
) -> int:
    db.execute(
        """INSERT INTO events
           (game_id, point, seq_in_point, timestamp, event_type, player,
            fault_type, serving_team, receiving_team, score_a, score_b)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (game_id, point, seq_in_point, timestamp, event_type, player,
         fault_type, serving_team, receiving_team, score_a, score_b),
    )
    return db.last_insert_id()


def delete_event(event_id: int) -> None:
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))


def delete_point_events(game_id: int, point: int) -> None:
    db.execute(
        "DELETE FROM events WHERE game_id = ? AND point = ?",
        (game_id, point),
    )


def list_for_game(game_id: int) -> list[dict]:
    rows = db.query_all(
        """SELECT * FROM events WHERE game_id = ?
           ORDER BY point, seq_in_point""",
        (game_id,),
    )
    return [dict(r) for r in rows]
