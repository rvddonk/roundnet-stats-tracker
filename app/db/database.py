import sqlite3
import threading
from pathlib import Path

from app.config import DB_PATH


class Database:
    def __init__(self):
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(DB_PATH),
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def initialize(self):
        schema_path = Path(__file__).resolve().parent / "schema.sql"
        sql = schema_path.read_text(encoding="utf-8")
        with self._lock:
            conn = self._get_conn()
            conn.executescript(sql)
            self._migrate(conn)
            conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Additive column migrations for older DBs created before a column existed."""
        games_additions = [
            ("tournament", "TEXT"),
            ("tournament_stage", "TEXT"),
            ("stage_part", "TEXT"),
            ("bracket_type", "TEXT"),
            ("match_format", "TEXT"),
            ("division", "TEXT"),
            ("division_tier", "TEXT"),
            ("team_count", "INTEGER"),
            ("date_updated", "TEXT"),
        ]
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(games)").fetchall()}
        for col, typ in games_additions:
            if col not in existing:
                conn.execute(f"ALTER TABLE games ADD COLUMN {col} {typ}")

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(sql, params)
            conn.commit()
            return cur

    def executemany(self, sql: str, params_list) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executemany(sql, params_list)
            conn.commit()

    def query_one(self, sql: str, params=()) -> sqlite3.Row | None:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(sql, params)
            return cur.fetchone()

    def query_all(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(sql, params)
            return cur.fetchall()

    def last_insert_id(self) -> int:
        with self._lock:
            conn = self._get_conn()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


db = Database()
