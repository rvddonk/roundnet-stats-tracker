import csv
from datetime import datetime
from pathlib import Path

from app.config import EXPORTS_DIR
from app.db import events_repo, games_repo


def export_game(game_id: int) -> Path:
    """
    Export all events for a game to a CSV file in the exports directory.
    Returns the path to the created CSV.
    """
    game = games_repo.get_game(game_id)
    if not game:
        raise ValueError(f"Game {game_id} not found")

    # Build slot -> name lookup
    name_map = {
        "A1": game["a1_name"],
        "A2": game["a2_name"],
        "B1": game["b1_name"],
        "B2": game["b2_name"],
    }

    events = events_repo.list_for_game(game_id)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"game_{game_id}_{ts}.csv"
    output_path = EXPORTS_DIR / filename

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "game_id",
            "point",
            "sequence_in_point",
            "timestamp",
            "event_type",
            "player_slot",
            "player_name",
            "fault_type",
            "serving_team",
            "receiving_team",
            "score_a",
            "score_b",
        ])
        for ev in events:
            slot = ev["player"]
            name = name_map.get(slot, "") if slot else ""
            writer.writerow([
                ev["game_id"],
                ev["point"],
                ev["seq_in_point"],
                ev["timestamp"],
                ev["event_type"],
                slot or "",
                name,
                ev["fault_type"] or "",
                ev["serving_team"],
                ev["receiving_team"],
                ev["score_a"],
                ev["score_b"],
            ])

    return output_path
