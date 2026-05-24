CREATE TABLE IF NOT EXISTS players (
    name        TEXT PRIMARY KEY,
    times_seen  INTEGER NOT NULL DEFAULT 1,
    last_used   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at            TEXT NOT NULL,
    ended_at              TEXT,
    target_score          INTEGER NOT NULL,
    hard_cap              INTEGER NOT NULL,
    win_by                INTEGER NOT NULL DEFAULT 2,
    a1_name               TEXT NOT NULL,
    a2_name               TEXT NOT NULL,
    b1_name               TEXT NOT NULL,
    b2_name               TEXT NOT NULL,
    first_server_slot     TEXT NOT NULL,
    first_receiver_slot   TEXT NOT NULL,
    starting_rotation_idx INTEGER NOT NULL,
    final_score_a         INTEGER NOT NULL DEFAULT 0,
    final_score_b         INTEGER NOT NULL DEFAULT 0,
    winner_team           TEXT,
    end_reason            TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    point           INTEGER NOT NULL,
    seq_in_point    INTEGER NOT NULL,
    timestamp       TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,
    player          TEXT,
    fault_type      TEXT,
    serving_team    TEXT NOT NULL,
    receiving_team  TEXT NOT NULL,
    score_a         INTEGER NOT NULL,
    score_b         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_game ON events(game_id, point, seq_in_point);
CREATE INDEX IF NOT EXISTS idx_games_created ON games(created_at DESC);
