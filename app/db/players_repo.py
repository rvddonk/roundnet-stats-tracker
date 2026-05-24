from datetime import datetime

from app.db.database import db


def add_or_bump(name: str) -> None:
    """Insert the player name if new, or increment times_seen if existing."""
    name = name.strip()
    if not name:
        return
    now = datetime.utcnow().isoformat()
    existing = db.query_one("SELECT name FROM players WHERE name = ?", (name,))
    if existing:
        db.execute(
            "UPDATE players SET times_seen = times_seen + 1, last_used = ? WHERE name = ?",
            (now, name),
        )
    else:
        db.execute(
            "INSERT INTO players (name, times_seen, last_used) VALUES (?, 1, ?)",
            (name, now),
        )


def all_names() -> list[str]:
    """Return all known player names, most-used first."""
    rows = db.query_all(
        "SELECT name FROM players ORDER BY times_seen DESC, name ASC"
    )
    return [row["name"] for row in rows]
