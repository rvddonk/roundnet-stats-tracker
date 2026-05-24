# Roundnet Stats Tracker — Game Logic & Architecture Reference

This document is the authoritative reference for how the app works. It covers game rules, the serve rotation, rally auto-tracking, scoring, every button action, UI flow, database schema, and the CSV export format. It is intended to give a future AI assistant (or developer) enough context to make changes, fix bugs, or extend the app confidently without needing to re-read all source code.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Running the App](#2-running-the-app)
3. [Directory Structure](#3-directory-structure)
4. [Players & Slots](#4-players--slots)
5. [Serve Rotation](#5-serve-rotation)
6. [Rally Auto-Tracking](#6-rally-auto-tracking)
7. [Button Actions — Complete Reference](#7-button-actions--complete-reference)
8. [Scoring & Win Conditions](#8-scoring--win-conditions)
9. [Overtime](#9-overtime)
10. [Undo & Reset Point](#10-undo--reset-point)
11. [Game Segments & Button Availability](#11-game-segments--button-availability)
12. [UI Screen Flow](#12-ui-screen-flow)
13. [Database Schema](#13-database-schema)
14. [CSV Export Format](#14-csv-export-format)
15. [Key Invariants & Edge Cases](#15-key-invariants--edge-cases)

---

## 1. Project Overview

A desktop stats-tracking app for Roundnet (Spikeball) games. Runs on Windows via Python + PyQt6. All data is stored locally in a SQLite database (`roundnet.db`). At the end of every game a CSV is auto-exported to the `exports/` folder.

**Tech stack:** Python 3.11+, PyQt6, SQLite3 (stdlib), csv (stdlib).

---

## 2. Running the App

```
pip install -r requirements.txt
python main.py
```

Or double-click `run.bat` on Windows. The database and exports folder are created automatically on first run.

---

## 3. Directory Structure

```
roundnetstatstracker/
├── main.py                   Entry point
├── requirements.txt          PyQt6
├── run.bat                   Windows convenience launcher
├── roundnet.db               SQLite database (auto-created)
├── exports/                  CSV exports land here (auto-created)
└── app/
    ├── config.py             DB_PATH, EXPORTS_DIR, FAULT_TYPES, defaults
    ├── db/
    │   ├── schema.sql        CREATE TABLE statements
    │   ├── database.py       Database singleton (db)
    │   ├── players_repo.py   Player name CRUD
    │   ├── games_repo.py     Game CRUD
    │   └── events_repo.py    Event log CRUD
    ├── core/
    │   ├── models.py         GameConfig, GameState dataclasses
    │   ├── rotation.py       SERVE_ROTATION table + utilities
    │   ├── rally_tracker.py  Per-team alternation state machine
    │   ├── scoring.py        Win / overtime detection (pure functions)
    │   ├── game_engine.py    GameEngine — orchestrates everything
    │   └── csv_export.py     CSV file writer
    └── ui/
        ├── styles.qss        Dark-theme Qt stylesheet
        ├── main_window.py    QMainWindow + screen navigation
        ├── home_screen.py    Home screen
        ├── setup_screen.py   New game setup form
        ├── tracker_screen.py Live game tracking screen
        └── history_screen.py Game history viewer
```

---

## 4. Players & Slots

A game has exactly four players assigned to fixed **slots**:

| Slot | Meaning |
|------|---------|
| `A1` | Team A, Player 1 (serves first for Team A) |
| `A2` | Team A, Player 2 |
| `B1` | Team B, Player 1 (serves first for Team B) |
| `B2` | Team B, Player 2 |

All game logic operates on slots. Player names are stored separately in `GameState.players` (a `dict[str, str]`, e.g. `{"A1": "Anna", "A2": "Bob", "B1": "Carla", "B2": "Dan"}`). Names appear in the UI and in CSV exports; slots drive every calculation.

The partner of any slot is its teammate with the other number:
- `A1 ↔ A2`
- `B1 ↔ B2`

This is implemented in `rotation.partner_slot(slot)`.

---

## 5. Serve Rotation

### 5.1 The 8-Point Cycle

Serve rotation follows a fixed 8-point cycle defined in `app/core/rotation.py`. Each player serves exactly **twice** per cycle — once to each opponent.

```
Index  Server  Receiver
  0      A1      B1
  1      B2      A2
  2      B2      A1
  3      A2      B1
  4      A2      B2
  5      B1      A1
  6      B1      A2
  7      A1      B2
```

After index 7 the cycle wraps back to index 0. `rotation_index` in `GameState` tracks where we are (0–7). It is incremented by `next_rotation_index(current)` at the end of each point.

### 5.2 First Serve Selection

At game setup, the user picks who serves the very first point from a dropdown that lists all 8 valid server→receiver pairs (with actual player names substituted). The selected pair is looked up via `rotation_index_for(server, receiver)`, which returns its index (0–7). This becomes the starting `rotation_index`, so the cycle always starts at the correct position relative to the user's choice.

### 5.3 Reading the Rotation

For any given `rotation_index`, the current server and receiver are retrieved with `pair_at(rotation_index)` which returns a `ServePair(server, receiver)` namedtuple.

---

## 6. Rally Auto-Tracking

The app automatically assigns each button press to the correct player. No manual player selection is needed during a rally.

### 6.1 The Alternation Rule

Within a team, players **alternate** touching the ball. The `RallyTracker` class (`app/core/rally_tracker.py`) maintains `last_player: dict[str, str | None]` — the most recent player per team who touched the ball.

- At the **start of each point**: `last_player[serving_team] = server_slot`. The receiving team's pointer starts as `None`.
- On a **Receive** action: credits the designated receiver from the rotation (`_pending_receiver`), sets `last_player[receiving_team] = receiver_slot`.
- On any **play action**: the credited player is `partner_slot(last_player[acting_team])` — i.e. the other player from that team. The pointer is then updated to the credited player.
- If `last_player[acting_team]` is `None` (team has never touched this point): defaults to the `"1"` slot (`A1` or `B1`).

### 6.2 Soft Touch Exception

`soft_touch` and `weak_soft_touch` are the **only actions that do not advance the pointer**. The same player who last touched gets credited again, and `last_player` stays unchanged so the partner is next.

**Full example** (B2 serves, A1 receives):

```
Action          Acting team   last_player[A]  last_player[B]  Credited
──────────────────────────────────────────────────────────────────────
reset_for_point   —             None            B2              —
credit_receive    A             A1              B2              A1
Set               A             A2              B2              A2   (partner of A1)
Hit               A             A1              B2              A1   (partner of A2) → possession→B
Touch             B             A1              B1              B1   (partner of B2)
Soft Touch        B             A1              B1              B1   (same; pointer stays B1)
Hit               B             A1              B2              B2   (partner of B1) → possession→A
```

### 6.3 Possession Tracking

`GameState.possession_team` ("A" or "B") tracks who currently holds the ball mid-rally. It is set to the serving team at point start. It switches only on `hit` / `weak_hit`. All other play actions (`set`, `touch`, `soft_touch`, `receive`) leave possession unchanged.

`last_touch_team` and `last_touch_player` record the most recent action for use by the **Point** and **Error** buttons.

---

## 7. Button Actions — Complete Reference

### 7.1 Serve Segment (active when `segment == "serve"`)

These buttons are only enabled while the serve has not yet been returned (before any Receive/Ace/Fault resolves the serve).

| Button | Engine call | What happens |
|--------|------------|--------------|
| **Ace** | `on_serve_action("ace")` | Credits server. Point awarded to **serving team**. |
| **Fault** | `on_serve_action("fault")` | If `fault_count == 0`: records `serve_fault`, increments `fault_count` to 1, opens fault-type dialog (pocket / rim / high serve / foot fault), records `serve_fault_type`. Play resumes — serve continues. If `fault_count == 1`: records `serve_double_fault`. Point awarded to **receiving team**. |
| **Receive** | `on_serve_action("receive")` | Credits designated receiver. Resets `fault_count` to 0. Transitions to play segment. Possession → receiving team. |
| **Weak Receive** | `on_serve_action("weak_receive")` | Same as Receive, logged as `weak_receive`. |

**Fault reset rule:** A successful Ace or Receive resets `fault_count` to 0. A second fault in a row (without a successful serve in between) causes a double fault.

### 7.2 Play Segment (active when `segment == "play"`)

These buttons are only enabled once a Receive has been registered (the ball is in play). Player attribution is fully automatic via the rally tracker.

| Button | Engine call | Possession change | Event type logged |
|--------|------------|------------------|-------------------|
| **Set** | `on_play_action("set")` | No change | `set` |
| **Weak Set** | `on_play_action("weak_set")` | No change | `weak_set` |
| **Hit** | `on_play_action("hit")` | **Switches to other team** | `hit` |
| **Weak Hit** | `on_play_action("weak_hit")` | **Switches to other team** | `weak_hit` |
| **Touch** | `on_play_action("touch")` | No change | `touch` |
| **Weak Touch** | `on_play_action("weak_touch")` | No change | `weak_touch` |
| **Soft Touch** | `on_play_action("soft_touch")` | No change | `soft_touch` |
| **Weak Soft Touch** | `on_play_action("weak_soft_touch")` | No change | `weak_soft_touch` |

**"Weak" variants** mean a lower-quality execution of the same action. They are logged separately but have identical game-logic effects (same possession change, same alternation behaviour).

**Soft touch / Weak soft touch** credit the **same player** as the previous action (no alternation). See Section 6.2.

### 7.3 Other Segment (always available except when game has ended)

| Button | Engine call | What happens |
|--------|------------|--------------|
| **Point** | `on_other_action("point")` | Awards point to `last_touch_team`. If no touch has happened yet this point, awards to the serving team. |
| **Error** | `on_other_action("error")` | Awards point to the team **opposite** `last_touch_team`. If no touch yet, awards to the receiving team. |
| **Overtime** | `on_other_action("overtime")` | Manually triggers overtime mode (if not already in overtime). See Section 9. |
| **Game End** | `on_other_action("game_end")` | Ends the game immediately with `end_reason = "manual"`, no winner recorded. Exports CSV. |
| **↩ Undo** | `engine.undo()` | Removes the last action within the current point. See Section 10. |
| **↺ Reset Point** | `engine.reset_point()` | Clears **all** actions for the current point and restarts it from scratch. See Section 10. |

---

## 8. Scoring & Win Conditions

Scoring logic lives in `app/core/scoring.py` as pure functions.

### 8.1 Win Check (called after every point)

`check_game_end(score_a, score_b, config)` applies two checks **in this priority order**:

1. **Hard cap:** If either team's score `>= hard_cap` AND they lead by at least 1 → that team wins immediately. (`end_reason = "hard_cap"`)
2. **Standard win:** If either team's score `>= target_score` AND they lead by `>= win_by` (default 2) → that team wins. (`end_reason = "target_reached"`)

Returns `"A"`, `"B"`, or `None` (game continues).

### 8.2 Game Configuration (`GameConfig`)

Set by the user at game setup:

| Field | Default | Meaning |
|-------|---------|---------|
| `target_score` | 21 | Points needed to win (subject to win-by rule) |
| `hard_cap` | 25 | Absolute maximum — win by 1 at this score |
| `win_by` | 2 | Lead required to win (at or above target) |

### 8.3 Score Examples

| Score | target=21, hard_cap=25 | Result |
|-------|------------------------|--------|
| 21–19 | lead=2 ≥ win_by=2 | **A wins** |
| 21–20 | lead=1 < win_by → OT triggers | No winner yet |
| 22–20 | lead=2 ≥ win_by=2 | **A wins** |
| 25–24 | score_a=25 ≥ hard_cap | **A wins** |
| 24–25 | score_b=25 ≥ hard_cap | **B wins** |

---

## 9. Overtime

### 9.1 Auto-trigger Condition

`should_trigger_overtime(score_a, score_b, config, already_in_ot)` returns `True` when:
- `max(score_a, score_b) >= target_score` **AND**
- `abs(score_a - score_b) == 1` (exactly one point apart)
- AND the game is not already in overtime

This is checked **immediately after awarding each point**, before the win check. For a `target_score` of 21 this triggers at 21:20 or 20:21. For `target_score` of 15 it triggers at 15:14 or 14:15.

Overtime can also be triggered **manually** at any time with the Overtime button.

### 9.2 Overtime Serve Rules

Once `in_overtime = True`:

- **Servers alternate every single point.** The team that was serving when OT triggered serves first. On each subsequent point the other team serves.
- **Receivers do not rotate.** Each team's designated receiver (frozen at OT entry) stays the same for the rest of the game.
- The overtime serve counter (`overtime_serve_counter`) increments after each point. Even counter → first server team. Odd counter → other team.

The frozen server and receiver per team are stored in `GameState.overtime_servers` and `GameState.overtime_receivers` (both `dict[str, str]`, keyed by team `"A"` / `"B"`). They are set in `_enter_overtime()` by looking back through the rotation history to find the most recent server and receiver for each team.

### 9.3 What Does NOT Change in Overtime

- Player alternation within rallies is unchanged.
- `win_by` and `hard_cap` rules still apply.

---

## 10. Undo & Reset Point

Both operations are **scoped to the current point only**. Once a point ends and a new one begins, the previous point is sealed.

### 10.1 Undo (`engine.undo()`)

Removes the **single most recent action** in the current point:
1. Pops the last event ID from `_point_event_ids` and deletes it from the `events` table.
2. Pops the corresponding pre-action snapshot from `_point_snapshots` and restores `GameState` to it.
3. Calls `games_repo.update_score()` to keep the DB score consistent.
4. Clears `_awaiting_fault_type` if a fault dialog was pending.

The undo stack is built by `_save_snapshot()` which is called **before every action** via a `copy.deepcopy(state)`.

Undo is disabled (button greyed out) when there are no actions in the current point (`_point_event_ids` is empty).

### 10.2 Reset Point (`engine.reset_point()`)

Wipes all actions for the current point and starts it fresh:
1. Calls `events_repo.delete_point_events(game_id, point_number)` to remove all events for this point from the DB.
2. Clears both `_point_event_ids` and `_point_snapshots`.
3. Restores `GameState` from `_point_start_snapshot` (a deep copy taken at the beginning of the point, before any action).
4. Re-initialises the rally tracker.

The point number, score, rotation position, and server/receiver are all restored to exactly what they were at the start of the point.

---

## 11. Game Segments & Button Availability

`GameState.segment` is a computed property:

| Condition | Segment |
|-----------|---------|
| `state.ended == True` | `"ended"` |
| `state.point_in_progress == False` | `"serve"` |
| `state.point_in_progress == True` | `"play"` |

`point_in_progress` becomes `True` when a Receive or Weak Receive is registered, and resets to `False` at the start of each new point.

`engine.available_actions()` returns a `dict[str, bool]` used by the tracker screen to enable/disable every button:

| Action group | Enabled when |
|---|---|
| `ace`, `fault`, `receive`, `weak_receive` | `segment == "serve"` and not awaiting fault type |
| All 8 play actions | `segment == "play"` and not awaiting fault type |
| `point`, `error` | `segment` is `"serve"` or `"play"` and not awaiting fault type |
| `overtime` | Not already in overtime, game not ended, not awaiting fault type |
| `game_end` | Game not ended, not awaiting fault type |
| `undo` | `_point_event_ids` is non-empty and not awaiting fault type |
| `reset_point` | `_point_event_ids` is non-empty (or point is in progress) and not awaiting fault type |

**Awaiting fault type** (`_awaiting_fault_type = True`) is a blocking state that disables all buttons while the fault-type dialog is open. It clears automatically when the user selects a fault type or when Undo is called.

---

## 12. UI Screen Flow

```
HomeScreen
   ├── "New Game" → SetupScreen
   │                  └── "Start Game" → TrackerScreen
   │                                         └── game ends / "← Home" → HomeScreen
   └── "View History" → HistoryScreen
                            └── "← Back" → HomeScreen
```

### HomeScreen (`app/ui/home_screen.py`)
Two large buttons: **New Game** and **View Game History**.

### SetupScreen (`app/ui/setup_screen.py`)
- 4 player name fields (one per slot), each with a `QCompleter` autocomplete backed by `players_repo.all_names()` (most-used names first).
- Target score spinbox (default 21).
- Hard cap spinbox (default 25, must be > target).
- First-serve dropdown: shows all 8 valid server→receiver pairs from `SERVE_ROTATION` with actual player names substituted live as the user types.
- "Start Game" is disabled until all 4 names are filled, unique, and a first-serve option is selected.
- Emits `game_started(config, players_dict, first_server_slot, first_receiver_slot)`.

### TrackerScreen (`app/ui/tracker_screen.py`)
- **Score panel**: Large score labels for A and B; winning team's score turns green.
- **Serve/receive bar**: Shows `"Server: [name] ([slot]) → Receiver: [name] ([slot])"`. Appends `"⚠ 1 fault"` when `fault_count == 1`.
- **Possession indicator**: Coloured pill showing which team currently has the ball.
- **OVERTIME badge**: Shown in the top-right when `in_overtime == True`.
- **Button grid**: Three `QGroupBox` sections — Serve, Play, Other — with buttons coloured by action type.
- **Status bar**: Shows `"Next action credited to: [name] ([slot])"` during the play segment, or `"Point N — Awaiting serve"` during the serve segment.
- All buttons are enabled/disabled after every engine callback via `engine.available_actions()`.
- **Fault dialog** (`FaultTypeDialog`): Modal with radio buttons for Pocket / Rim / High serve / Foot fault.

### HistoryScreen (`app/ui/history_screen.py`)
- Left panel: list of all past games (newest first) showing date, final score, winner.
- Right panel: detail view for the selected game (players, duration, scores, end reason).
- "Export CSV" button re-exports the selected game on demand.

---

## 13. Database Schema

SQLite database at `roundnet.db` (project root). Created/migrated automatically by `db.initialize()` on every startup using `CREATE TABLE IF NOT EXISTS` statements from `app/db/schema.sql`.

### `players` table

Stores all player names ever used, for autocomplete.

| Column | Type | Notes |
|--------|------|-------|
| `name` | TEXT PK | Unique player name |
| `times_seen` | INTEGER | Incremented on each game that includes this player |
| `last_used` | TEXT | ISO8601 UTC timestamp of last use |

### `games` table

One row per game, created at setup and updated throughout.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `created_at` | TEXT | ISO8601 UTC |
| `ended_at` | TEXT | ISO8601 UTC; NULL until game ends |
| `target_score` | INTEGER | Win target |
| `hard_cap` | INTEGER | Absolute score cap |
| `win_by` | INTEGER | Default 2 |
| `a1_name` … `b2_name` | TEXT | Player names at time of game |
| `first_server_slot` | TEXT | E.g. `"A1"` |
| `first_receiver_slot` | TEXT | E.g. `"B1"` |
| `starting_rotation_idx` | INTEGER | 0–7 |
| `final_score_a` | INTEGER | Updated live during play |
| `final_score_b` | INTEGER | Updated live during play |
| `winner_team` | TEXT | `"A"`, `"B"`, or NULL (manual end / abandoned) |
| `end_reason` | TEXT | `"target_reached"`, `"hard_cap"`, or `"manual"` |

### `events` table

One row per button press. This is the complete audit log.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `game_id` | INTEGER FK → games | |
| `point` | INTEGER | 1-based point number |
| `seq_in_point` | INTEGER | 1-based sequence within the point |
| `timestamp` | TEXT | ISO8601 UTC |
| `event_type` | TEXT | See Section 13.1 |
| `player` | TEXT | Slot (`"A1"` etc.) or NULL |
| `fault_type` | TEXT | `"Pocket"`, `"Rim"`, `"High serve"`, `"Foot fault"` — only on `serve_fault_type` events |
| `serving_team` | TEXT | `"A"` or `"B"` — team serving at time of event |
| `receiving_team` | TEXT | `"A"` or `"B"` — team receiving at time of event |
| `score_a` | INTEGER | Score at the moment this event was recorded |
| `score_b` | INTEGER | Score at the moment this event was recorded |

### 13.1 Event Type Values

| `event_type` | Trigger |
|---|---|
| `serve_ace` | Ace button |
| `serve_fault` | Fault button (first fault) |
| `serve_fault_type` | Fault type selected from dialog (follow-up to `serve_fault`) |
| `serve_double_fault` | Fault button (second consecutive fault) |
| `receive` | Receive button |
| `weak_receive` | Weak Receive button |
| `set` | Set button |
| `weak_set` | Weak Set button |
| `hit` | Hit button |
| `weak_hit` | Weak Hit button |
| `touch` | Touch button |
| `weak_touch` | Weak Touch button |
| `soft_touch` | Soft Touch button |
| `weak_soft_touch` | Weak Soft Touch button |
| `point` | Point button |
| `error` | Error button |
| `overtime_triggered` | Overtime entered (auto or manual) |
| `game_end` | Game End button (manual end) |

**Note on fault events:** A fault always generates two consecutive events — `serve_fault` (with the player credited) followed immediately by `serve_fault_type` (with the `fault_type` column filled in). The `fault_type` column is NULL on all other event types.

---

## 14. CSV Export Format

Auto-exported to `exports/game_{id}_{timestamp}.csv` when the game ends. Can also be triggered manually from the history screen.

| Column | Source | Example |
|--------|--------|---------|
| `game_id` | `events.game_id` | `3` |
| `point` | `events.point` | `1` |
| `sequence_in_point` | `events.seq_in_point` | `2` |
| `timestamp` | `events.timestamp` | `2024-06-01T14:23:11.441234` |
| `event_type` | `events.event_type` | `receive` |
| `player_slot` | `events.player` | `B1` |
| `player_name` | Looked up from `games.b1_name` | `Carla` |
| `fault_type` | `events.fault_type` | `Rim` (or blank) |
| `serving_team` | `events.serving_team` | `A` |
| `receiving_team` | `events.receiving_team` | `B` |
| `score_a` | `events.score_a` | `5` |
| `score_b` | `events.score_b` | `4` |

Scores reflect the state **at the time the event was recorded** (before any point is awarded for that event). The full timeline of every button press, including who was credited for each touch, is reconstructable from this log.

---

## 15. Key Invariants & Edge Cases

**Undo scope:** Undo only operates within the current point. Once a new point begins (after any `_end_point()` call), the previous point's event stack is discarded and those events become permanent.

**Reset Point scope:** Same as Undo — scoped to the current point. It restores the full game state (score, rotation, possession) to exactly the snapshot taken at the start of that point.

**Fault count resets:** `fault_count` is reset to `0` on Receive, Weak Receive, or Ace. It persists between consecutive Fault presses. A double fault ends the point.

**First-touch defaults:** If a team's first touch in a rally is a play action (e.g. after a Hit switches possession) and their `last_player` pointer is `None`, the `RallyTracker` defaults to the `"1"` slot (`A1` or `B1`). This happens at the very start of the receiving team's play.

**Point / Error with no touches:** If Point is pressed before any Receive (i.e. immediately after serve, with no ball-in-play actions), the point goes to the serving team. If Error is pressed in the same situation, it goes to the receiving team.

**Overtime entry snapshot:** When `_enter_overtime()` runs, it walks back through the `SERVE_ROTATION` array (from the current index) to find the most recent server and receiver for each team. These are frozen as `overtime_servers` and `overtime_receivers`. In the rare case where a team has no history in the table, it defaults to the `"1"` slot.

**Hard cap takes precedence:** `check_game_end` checks the hard cap **first**, before the standard win condition. A score of `hard_cap : hard_cap - 1` ends the game immediately regardless of overtime state.

**Game end reasons stored in DB:**
- `"target_reached"` — standard win by 2 (or win_by)
- `"hard_cap"` — won at or above hard_cap
- `"manual"` — Game End button pressed; `winner_team` is NULL

**Player uniqueness:** The setup screen enforces that all four player names are distinct. Duplicate names would cause ambiguity in the CSV and autocomplete.

**DB path:** `roundnet.db` is always created at the project root (next to `main.py`), as defined by `app/config.DB_PATH`. The `exports/` folder is also at the project root.

**Scores in events are pre-point:** The `score_a` / `score_b` columns on every event row capture the score **before** the point is awarded. So a `serve_ace` event at 5–4 will show `score_a=5, score_b=4` even though that ace brings it to 6–4.
