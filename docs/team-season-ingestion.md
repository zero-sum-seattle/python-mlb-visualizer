# Team-season ingestion

This document describes how Milestone 2 persists normalized team game batting
lines in SQLite and how team-season ingestion behaves.

## 1. Database architecture

The application uses synchronous SQLAlchemy 2 against a local SQLite database.
Schema changes are applied only through Alembic (`poetry run alembic upgrade head`).
The FastAPI app and import CLI do not call `Base.metadata.create_all()` at startup.

Engine and session construction live in `app/database/engine.py` as explicit
factory functions so tests and scripts can target isolated database URLs without
creating a global engine at import time.

## 2. ORM and Pydantic separation

| Layer | Type | Role |
| --- | --- | --- |
| MLB API | python-mlb-statsapi models | Remote payloads |
| Domain | `TeamGameBattingLine` | Normalized, validated game line |
| Persistence | `TeamGameBattingLineRecord` | SQLite row |

The repository converts explicitly between ORM rows and Pydantic models. Public
read paths return `TeamGameBattingLine`, not ORM instances.

## 3. Table schema

Table: `team_game_batting_lines`

Columns: `id`, `game_pk`, `game_date`, `season`, `team_id`, `team_name`,
`opponent_id`, `opponent_name`, `home_away`, `hits`, `runs`, `status`,
`game_number`, `doubleheader`, `scheduled_innings`, `created_at`, `updated_at`.

Team and opponent names are historical display snapshots for the season under
ingestion. They are not normalized into a separate teams table in this milestone.

## 4. Unique key rationale

Unique constraint: `(team_id, game_pk)` (`uq_team_game_batting_lines_team_id_game_pk`).

`game_pk` is stable across postponement and resumption, but a completed game will
eventually produce one row per team in league-wide storage. The pair `(team_id,
game_pk)` is the natural identity for one selected team's batting line in one game.

## 5. Query index rationale

Index `ix_team_game_batting_lines_team_season_order` on
`(team_id, season, game_date, game_number, game_pk)` supports future chart queries
that need a team's season in game order, including doubleheaders (same date,
different `game_number`).

Repository reads order by `game_date`, `game_number`, `game_pk`.

## 6. Upsert comparison behavior

`upsert_team_season` loads all existing rows for the team-season in one query,
indexes them by `(team_id, game_pk)`, then for each incoming `TeamGameBattingLine`:

- **Insert** when no row exists for the pair.
- **Unchanged** when the stored baseball fields match the domain record (via
  explicit domain comparison).
- **Update** when any persisted baseball field differs: `game_date`, `season`,
  `team_name`, `opponent_id`, `opponent_name`, `home_away`, `hits`, `runs`,
  `status`, `game_number`, `doubleheader`, `scheduled_innings`.

Identity fields `team_id` and `game_pk` must not drift; conflicts raise
`TeamGamePersistenceError`.

`created_at` is preserved on updates. `updated_at` changes only on meaningful
updates, not on unchanged rows.

## 7. Transaction boundary

`ingest_team_season` completes all MLB retrieval and normalization through
`get_team_game_batting_lines` **before** opening the database transaction. The
write path is a single transaction: load existing rows, compare, insert/update,
commit.

The repository does not commit or roll back; the ingestion service owns the
transaction via `session.begin()`.

## 8. Failure and rollback behavior

- `TeamNotFoundError`, `TeamGameDataError`, and other Milestone 1 errors propagate
  without writing partial data.
- SQLAlchemy failures during the write transaction are wrapped in
  `TeamSeasonIngestionError`, preserving the original exception, and the
  transaction rolls back.

## 9. No-deletion decision

Ingestion uses **insert + update + unchanged**. Rows already stored but missing
from the latest MLB response are **not** deleted.

Reasons:

- An incomplete upstream response must not erase good local data.
- Milestone 1 noted that an empty stat block can disappear before normalization.
- Full reconciliation needs stronger completeness guarantees and belongs to a
  later milestone.

## 10. Alembic workflow

```bash
poetry run alembic upgrade head
```

Alembic reads the database URL from `Settings.database_url` unless
`sqlalchemy.url` is set on the Alembic `Config` (used in tests).

Downgrade:

```bash
poetry run alembic downgrade base
```

## 11. Manual import workflow

```bash
poetry run alembic upgrade head
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

First run (empty table): all fetched lines are inserted.

Second identical run: all lines are unchanged; no duplicates.

JSON output:

```bash
poetry run python scripts/import_team_season.py \
  --team-id 136 --season 2025 --format json
```

## 12. Recommendation for Milestone 3

- Expose persisted team-season data through the web layer (read-only charts or
  tables) using `list_team_season` and the chart-order index.
- Keep ingestion CLI-oriented for bulk refresh; defer background workers until
  league-wide or scheduled ingestion is required.
- When adding league-wide ingestion, retain `(team_id, game_pk)` identity and
  extend reconciliation only after completeness checks are defined.

See also `docs/team-game-data-spike.md` for the Milestone 1 data path.
