# MLB Stats Visualizer

Interactive MLB statistics visualization web application powered by
`python-mlb-statsapi`.

## Status

**Milestone 0 — Repository Foundation** is complete.

**Milestone 1 — Team game-level data feasibility spike** is complete.

**Milestone 2 — Database persistence and team-season ingestion** is complete.

Milestone 0 provides a FastAPI application with Jinja2 templates, Pydantic
Settings configuration, pytest coverage, Ruff linting/formatting, and GitHub
Actions CI.

Milestone 1 adds the first MLB data path: normalized `TeamGameBattingLine`
records from the live MLB Stats API (or fixtures in tests).

Milestone 2 adds SQLite persistence with SQLAlchemy 2 and Alembic, a
team-season ingestion service, and an import CLI. Charting and web database
integration remain deferred.

## Planned MVP

A local web application that:

- Retrieves MLB data through `python-mlb-statsapi`
- Presents interactive baseball statistics visualizations
- Runs with a clean Python web stack (FastAPI + Jinja2)

Later milestones will add visualization libraries and web integration for
persisted data.

## Technology stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Packaging | Poetry |
| Web framework | FastAPI |
| Templates | Jinja2 |
| MLB data | python-mlb-statsapi |
| Database | SQLite |
| ORM | SQLAlchemy 2 |
| Migrations | Alembic |
| Configuration | Pydantic Settings |
| Testing | pytest, httpx |
| Lint / format | Ruff |
| CI | GitHub Actions |

## Requirements

- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)

## Installation

Install Poetry if you do not already have it:

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

Clone the repository and install dependencies:

```bash
git clone https://github.com/zero-sum-seattle/python-mlb-visualizer.git
cd python-mlb-visualizer
poetry install
```

Optionally copy the example environment file (defaults work without it):

```bash
cp .env.example .env
```

### Database configuration

Default database URL (no `.env` required):

```text
sqlite:///./mlb_visualizer.db
```

Override with `DATABASE_URL` in `.env` or the environment. Alembic and the
application read the same `Settings.database_url` value.

Apply the schema:

```bash
poetry run alembic upgrade head
```

The database file is created relative to the project root when first used.

## Local development

Start the development server:

```bash
poetry run uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

- `/` — foundation HTML page
- `/health` — JSON health check

## Team game-level hitting data

Milestone 1 retrieves one normalized batting line per completed regular-season
game for a selected team and season. The reusable code lives in
`app/services/team_game_logs.py` and `app/schemas/games.py`.

```python
from app.services.team_game_logs import get_team_game_batting_lines

lines = get_team_game_batting_lines(team_id=136, season=2025)
```

### Inspection command (no persistence)

```bash
poetry run python scripts/inspect_game_logs.py --team-id 136 --season 2025
```

Options: `--team-id` and `--season` are required; `--limit N` shows only the
first N games and `--format json` emits Pydantic-serialized JSON on stdout.

This script calls the live MLB Stats API and is not part of `poetry run pytest`.

### Team-season import (Milestone 2)

Persist one team-season after migrations:

```bash
poetry run alembic upgrade head
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

First run on an empty table inserts every fetched game:

```text
Team: Seattle Mariners
Season: 2025
Fetched: 162
Inserted: 162
Updated: 0
Unchanged: 0
```

A second identical run is idempotent:

```text
Fetched: 162
Inserted: 0
Updated: 0
Unchanged: 162
```

JSON output:

```bash
poetry run python scripts/import_team_season.py \
  --team-id 136 --season 2025 --format json
```

**Unique key:** `(team_id, game_pk)` — one row per team per game.

**Upsert behavior:** compare persisted baseball fields; insert new rows, update
changed rows, leave identical rows untouched (`updated_at` does not change on
unchanged rows).

**No automatic deletion:** rows already stored but missing from the latest MLB
response are kept. See `docs/team-season-ingestion.md`.

### Selected retrieval strategy

Team hitting `gameLog` splits joined on `gamePk` with a single team schedule
request. See `docs/team-game-data-spike.md` for the full investigation.

### Cross-source validation

The batting numbers come from the game log and the game context comes from the
schedule. Disagreements raise `TeamGameDataError`.

### Edge cases and limitations

Same as Milestone 1 (completed states, doubleheaders, postponements, etc.).

## Testing

```bash
poetry run pytest
```

The suite is fully offline. Migration and repository tests use temporary SQLite
files and real Alembic upgrades.

## Lint and formatting

```bash
poetry run ruff check .
poetry run ruff format .
poetry run ruff format --check .
```

## Project structure

```text
.
├── alembic/
│   ├── versions/
│   │   └── 166b6424e4f9_create_team_game_batting_lines.py
│   ├── env.py
│   └── script.py.mako
├── alembic.ini
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── engine.py
│   │   ├── models.py
│   │   └── repositories.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── games.py
│   │   └── ingestion.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── team_game_logs.py
│   │   └── team_season_ingestion.py
│   └── web/
│       ├── __init__.py
│       ├── routes.py
│       └── templates/
│           ├── base.html
│           └── index.html
├── scripts/
│   ├── inspect_game_logs.py
│   └── import_team_season.py
├── docs/
│   ├── team-game-data-spike.md
│   └── team-season-ingestion.md
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── team_game_logs/
│   ├── test_game_schemas.py
│   ├── test_import_team_season.py
│   ├── test_migrations.py
│   ├── test_repositories.py
│   ├── test_team_game_logs.py
│   ├── test_team_season_ingestion.py
│   └── test_web.py
├── .github/
│   └── workflows/
│       └── test.yml
├── .env.example
├── .gitignore
├── poetry.lock
├── pyproject.toml
└── README.md
```

## Later milestones

Visualization libraries, league-wide ingestion, and web pages backed by the
database will be added in later milestones.

## Disclaimer

This project is educational and is **not affiliated with Major League Baseball,
MLB Advanced Media, or any MLB club**. MLB names and marks belong to their
respective owners.
