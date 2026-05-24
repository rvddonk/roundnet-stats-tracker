# Roundnet Stats Tracker

A PyQt6 desktop app for tracking [Roundnet](https://en.wikipedia.org/wiki/Roundnet) (Spikeball) games event-by-event — every serve, receive, set, hit, touch, and fault — then turning the recorded log into per-player stats, chain analysis, and a single composite performance rating called **RoundX**.

Built for self-hosted local use: one SQLite file at the repo root, no accounts, no cloud.

## What it does

- **Live tracking** during a game: tap the action that just happened (`Hit`, `Weak Set`, `Touch`, `Double Fault`, …) and the engine handles slot rotation, scoring, side-outs, overtime, undo, and reset-point automatically.
- **Per-team and per-player stats**: aces, weak receives, side-out rate, hold rate, break rate, touch %, weak-action chains, error attribution, fault breakdowns by type.
- **RoundX rating**: one composite number per player per game, normalized so a `+40` carries the same meaning in any game length. See [`docs/roundx-score.md`](docs/roundx-score.md) for the full weight table and design notes.
- **CSV export / import**: every game's event log can be exported and re-imported, so games tracked on different machines can be merged into one history.

## Requirements

- Python 3.10+
- PyQt6 (installed via `requirements.txt`)
- Windows, macOS, or Linux (developed on Windows; the SQLite + PyQt stack is cross-platform)

## Install and run

```bash
git clone https://github.com/AleksandrsJuska/roundnet-stats-tracker.git
cd roundnet-stats-tracker
pip install -r requirements.txt
python main.py
```

Windows users can also double-click `run.bat`.

The SQLite database (`roundnet.db`) and CSV exports (`exports/`) are created automatically in the project root on first launch. Both are gitignored so your game data never enters the repo.

## How a game flows

1. **Home** → "Start new game". Pick player names, target score, hard cap, and the first server / receiver.
2. **Tracker screen**. Tap the action that just happened on each rally. The engine credits the right player automatically (rally tracker alternates between the two slots on each team, with one carved-out exception for soft touches). Undo and reset-point are available until the point is awarded; once a point is sealed, earlier points are no longer reachable.
3. **Game ends** automatically when the target / hard-cap is reached, or manually via "End game". A summary dialog shows each player's RoundX score, their delta vs. the game average, and their biggest plays.
4. **History** lists past games with a one-line RoundX summary per game; **Analysis** opens a full per-player / per-team / chain breakdown for any game.

## Architecture

Three layers — full design notes in [`GAME_LOGIC.md`](GAME_LOGIC.md):

- **UI** (`app/ui/`): PyQt6 screens (`HomeScreen`, `SetupScreen`, `TrackerScreen`, `HistoryScreen`, `AnalysisScreen`) wired together by `MainWindow` via `pyqtSignal`. Styling lives in `app/ui/styles.qss`.
- **Game engine** (`app/core/game_engine.py`): single source of truth during a live game. Models the game as fixed slots (`A1`, `A2`, `B1`, `B2`), owns the serve rotation, scoring rules, and undo/reset semantics. UI talks to it via four callbacks; the engine never imports from `app.ui`.
- **DB** (`app/db/`): one shared `sqlite3.Connection` behind a singleton, with per-table repo modules. Schema is in `app/db/schema.sql` and is applied with `CREATE TABLE IF NOT EXISTS` on every startup, so the file is self-upgrading.

## RoundX in one paragraph

Every event in a game contributes a small positive or negative weight to the responsible player (`Ace +5`, `Touch +3`, `Touch → break +3 bonus`, `Weak hit (lost) -4`, `Aced -2`, …). Touches and aces are the two highest single-event positives; all defensive touch variants are net-positive on the principle that attempting defense is always worth rewarding. The raw additive score is then scaled by `min(40 / counted_rallies, 2.0)` so a player's RoundX is comparable across games of any length, with games under 20 rallies flagged as **unrated**. The displayed score uses the scaled total; the "Key Plays" breakdown stays in raw units so individual contributions read naturally.

## License

This repo currently ships without an explicit license, which on GitHub defaults to *all rights reserved*. If you'd like to use, fork, or contribute, please open an issue and a license can be added.
