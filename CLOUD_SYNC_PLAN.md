# Plan: Shared public database for Roundnet Stats Tracker

## Context

The app is an open-source PyQt6 desktop tool with a local SQLite database. We want anyone who clones and runs it to optionally contribute completed games to a **shared, central database**, so the app can publish **public leaderboards / community insights** back to all users. GCP is the preferred cloud.

Constraints already agreed:
- **Purpose**: push events; read public aggregates (leaderboards) back into the app.
- **Identity**: anonymous device-scoped UID via **Firebase Anonymous Auth** (no signup).
- **Player names**: shared as-typed (user owns the disclosure decision; opt-in).
- **Sync trigger**: auto-sync after each completed game.

The current app has no networking, no identity, and no UUIDs — everything lives in local SQLite at the repo root.

---

## Architecture

```
+----------------------+          +-----------------------------+
| Desktop client       |  HTTPS   | Cloud Run (FastAPI)         |
|  - Firebase Anon Auth| -------> |  - verify Firebase ID token |
|  - Sync worker       |          |  - validate schema (Pydantic)|
|  - Local SQLite      |          |  - rate-limit per UID       |
+----------------------+          |  - upsert Firestore doc     |
                                  +-------------+---------------+
                                                |
                                                v
                          Firestore: raw_games/{uid}/games/{game_uuid}
                                                |
                                  Cloud Scheduler (daily)
                                                |
                                                v
                          BigQuery (private analytics warehouse)
                                                |
                                  Scheduled query (daily)
                                                v
                          Firestore: public/leaderboards/* (read-only)
                                                ^
                                                |  HTTPS GET (cached)
+----------------------+                        |
| Desktop client       | -----------------------+
|  - Analysis screen   |
+----------------------+
```

**Why this stack over alternatives**:
- **Cloud Run over direct-to-Firestore from client**: schema validation, cross-field invariants ("final score must match max event score"), and rate-limiting are trivial in FastAPI/Pydantic but unmaintainable in Firestore security rules. A leaked Firebase API key (open-source — it *will* be public, and that's by design) means a Cloud Run gateway is needed to enforce abuse limits the client cannot bypass.
- **Firestore (per-game doc) over BigQuery streaming**: one doc per game is one free write (~5–50 KB holds metadata + events array). BQ streaming costs and doesn't free-tier; BQ **batch loads are free**. Firestore also makes GDPR delete trivial (delete the doc).
- **BQ for analytics, Firestore for serving aggregates**: BQ does the heavy aggregation cheaply (nightly scheduled query), result is materialized to a tiny Firestore `public/leaderboards/*` doc that the read endpoint serves for ~free.
- **Anonymous Auth**: stable per-install UID without signup friction; ID tokens are signed by Google → server-verifiable; no embedded secrets in the client (Firebase Web API key is public by design).

**Cost profile**: realistically $0–10/month at hobby scale. Cloud Run scales to zero, Firestore writes free up to 20K/day, BQ free for 1 TB queried/month + free batch loads, Firebase Anon Auth free to 50K MAU. Billing alert + Cloud Run `--max-instances=3` cap the worst case.

---

## Security & threat model

| Threat | Mitigation |
|---|---|
| Spam writes from script | Require Firebase ID token (server-verified via Admin SDK). Per-UID rate limit (Firestore counter, transactional) — e.g. 20 games/hr, 500 events/game. Cloud Run `max-instances=3` hard ceiling. |
| Fake / impossible games | Pydantic schema: score monotonicity, final == last event score, event_type whitelist, bounded target/hard_cap, max 500 events/game, max 60 points. Reject with 422. |
| Replay / duplicate | Client-generated `game_uuid` is the Firestore doc ID. Server uses `set()` → idempotent. |
| Scraping others' events | Raw events never exposed via any read endpoint. Only `public/leaderboards/*` (aggregates) is readable. |
| Cost-blowup attack | `max-instances=3`, `concurrency=80`, billing alert at $5/$20, Firestore daily quota alert, Cloud Armor rate-limit rule. |
| Secrets in repo | Only Firebase Web config in client repo (project ID, API key, auth domain — all public by design). Admin SDK uses Cloud Run's ambient service account; no JSON key files. |
| Name PII | README + settings copy disclose what's shared. "Delete my data" endpoint wipes Firestore + tombstones to BQ. |

---

## Local schema changes

Use a **`PRAGMA user_version` migrator** because the current `CREATE TABLE IF NOT EXISTS` approach can't handle additive changes to existing installs.

Migration 0→1 (in new `app/db/migrations.py`, run by `Database.initialize()`):

```sql
ALTER TABLE games ADD COLUMN game_uuid TEXT;
ALTER TABLE games ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'not_synced';
ALTER TABLE games ADD COLUMN synced_at TEXT;
ALTER TABLE games ADD COLUMN sync_error TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_games_uuid ON games(game_uuid);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- holds: cloud_sync_enabled, firebase_refresh_token, last_sync_at
```

Python-side: backfill `game_uuid = uuid4().hex` for existing rows in the same transaction. Update `app/db/schema.sql` so fresh installs include the new columns from the start.

**`sync_status`** values: `not_synced` (default, opt-in disabled), `pending`, `synced`, `rejected` (4xx — don't retry), `failed` (5xx/network — retry with backoff).

---

## Implementation chunks (shippable in order)

### Chunk A — Local schema & sync state (no network)
Ship-alone: makes the DB ready, adds `game_uuid` to every new game, no behavior change for users.
- `app/db/migrations.py` (new) — version-tracked migrator.
- `app/db/database.py` — call migrator after `executescript(schema.sql)`.
- `app/db/schema.sql` — add new columns for fresh installs.
- `app/db/games_repo.py` — generate `game_uuid` in `create_game`; add `list_unsynced`, `mark_pending`, `mark_synced(uuid, ts)`, `mark_failed(uuid, error)`.
- `app/db/settings_repo.py` (new) — k/v wrapper for `app_settings`.

### Chunk B — Settings UI (still no network)
- `app/ui/widgets/settings_dialog.py` (new) — opt-in toggle with disclosure copy ("Your game data including player names will be shared..."), "Delete my shared data" button (wired in Chunk E).
- `app/ui/main_window.py` — menu entry / settings button on home screen.
- `app/config.py` — add `CLOUD_API_URL` and `FIREBASE_WEB_CONFIG` constants (public values, committed to repo).

### Chunk C — Firebase Anonymous Auth
- `requirements.txt` — add `requests` (Firebase Auth via REST API — keeps deps minimal; ~40 LOC vs pulling in `pyrebase4`).
- `app/sync/auth.py` (new) — `get_or_refresh_id_token()`. First call → `POST signupNewUser` (anonymous), persist `refresh_token` in `app_settings`. Subsequent calls → exchange refresh token at `https://securetoken.googleapis.com/v1/token`.

### Chunk D — Sync worker
- `app/sync/serializer.py` (new) — `build_game_payload(game_id) → dict`. Mirrors the shape of `app/core/csv_export.py` (events list + game metadata) — reuse joins.
- `app/sync/client.py` (new) — `post_game(payload, id_token)`. Maps HTTP status → `synced`/`rejected`/`failed`.
- `app/sync/worker.py` (new) — `QThread` driven by `QTimer`. Triggers: app start, game finish, every 5 min. Drains `list_unsynced()` with exponential backoff on `failed` rows.
- `app/core/game_engine.py` — at end of `_finish_game`, if `cloud_sync_enabled`, call `games_repo.mark_pending(game_id)` and signal the worker.

### Chunk E — Public leaderboards in app
- `app/sync/leaderboards.py` (new) — `GET /v1/leaderboards/{kind}` with 1-hour local cache.
- `app/ui/analysis_screen.py` — add a "Global" tab showing top players / win rates / event-type distributions vs. community.
- Wire "Delete my shared data" button → `DELETE /v1/me/data`.

### Chunk F — Server (sibling repo `roundnetstatstracker-server`)
Separate repo because the deploy lifecycle, dependencies (FastAPI/firebase-admin), and audience (only the maintainer) all differ from the client.

- `server/app/main.py` — FastAPI app.
- `server/app/auth.py` — `verify_firebase_token()` via `firebase-admin`.
- `server/app/schemas.py` — Pydantic models with strict validation rules (see threat table).
- `server/app/routes/games.py` — `POST /v1/games` (idempotent upsert), `DELETE /v1/me/data`.
- `server/app/routes/leaderboards.py` — `GET /v1/leaderboards/*` reads `public/leaderboards/*` doc.
- `server/app/rate_limit.py` — Firestore-backed transactional counter.
- `server/app/firestore_client.py` — Admin SDK singleton.
- `server/Dockerfile` + `server/cloudbuild.yaml` — deploy to Cloud Run with `max-instances=3`, dedicated service account (`roles/datastore.user` only).
- `server/scheduled/load_to_bq.py` — Cloud Run Job: list Firestore games modified in last 25 h, `MERGE` into BQ keyed on `(uid, game_uuid)`.
- `server/scheduled/build_leaderboards.py` — BQ scheduled query → write `public/leaderboards/*` docs.
- Cloud Scheduler crons: 03:00 (load), 03:15 (leaderboard build).

### Chunk G — Polish
- README privacy section: what's shared, how to opt out, link to delete-my-data.
- Apache 2.0 LICENSE for server repo.

---

## Critical files in this repo

- `app/db/database.py` — wire migrator.
- `app/db/schema.sql` — fresh-install schema additions.
- `app/db/games_repo.py` — `game_uuid` generation + sync-state methods.
- `app/core/game_engine.py` — trigger sync at game end.
- `app/config.py` — public Firebase config + API URL.
- `app/core/csv_export.py` — **reuse** its event-joining logic in `app/sync/serializer.py`; do not re-implement.
- `app/ui/main_window.py` — add settings entry; lazy-import the new dialog (project convention).
- `app/ui/analysis_screen.py` — Global tab.
- `requirements.txt` — add `requests`.

New modules: `app/sync/{auth.py, client.py, worker.py, serializer.py, leaderboards.py}`, `app/db/{migrations.py, settings_repo.py}`, `app/ui/widgets/settings_dialog.py`.

---

## Verification

**Local end-to-end (Firebase emulator)**:
- `firebase emulators:start --only auth,firestore` in the server repo.
- Run server locally with `FIREBASE_AUTH_EMULATOR_HOST` + `FIRESTORE_EMULATOR_HOST` env vars.
- Run client with `CLOUD_API_URL=http://localhost:8080`.
- Play a full game → confirm `roundnet.db` shows `sync_status='synced'` for that game and the Firestore emulator UI shows the doc.

**Staging GCP project**:
- Separate `roundnet-stats-staging` project. Cloud Build trigger deploys on push to `main` of the server repo.
- Client supports `--staging` flag flipping `CLOUD_API_URL` + `FIREBASE_WEB_CONFIG` to staging. Validates real Firebase ID token verification + the daily BQ load + leaderboard materialization end-to-end.

**Idempotency**:
- Kill the app between `mark_pending` and `mark_synced`. Restart. Confirm exactly one Firestore doc and exactly one BQ row after the next scheduled load.

**Hostile-client integration test** (CI on server repo):
- Script using only the published Firebase config attempts (a) duplicate `game_uuid`, (b) score=999, (c) 100 games/min. Each must be rejected or throttled. Asserts on response codes + Firestore state.

**Delete-my-data**:
- Trigger from app → confirm all `raw_games/{uid}/games/*` removed → next scheduled BQ load includes a tombstone pass that DELETEs from `games` and `events` for that UID.
