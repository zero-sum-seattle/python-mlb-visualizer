# MLB Stats Visualizer

Interactive MLB statistics visualization web application powered by
`python-mlb-statsapi`.

## Status

**Milestone 0 — Repository Foundation** is complete.

**Milestone 1 — Team game-level data feasibility spike** is complete.

**Milestone 2 — Database persistence and team-season ingestion** is complete.

**Milestone 3 — Team hits web visualization** is complete.

**Milestone 3.5 — Team batting strikeouts over time** is complete.

Milestone 0 provides a FastAPI application with Jinja2 templates, Pydantic
Settings configuration, pytest coverage, Ruff linting/formatting, and GitHub
Actions CI.

Milestone 1 adds the first MLB data path: normalized `TeamGameBattingLine`
records from the live MLB Stats API (or fixtures in tests).

Milestone 2 adds SQLite persistence with SQLAlchemy 2 and Alembic, a
team-season ingestion service, and an import CLI.

Milestone 3 adds the first visualization page: a team's hits per game with a
trailing rolling average, drawn with Plotly from data already in SQLite. See
[docs/team-hits-visualization.md](docs/team-hits-visualization.md).

Milestone 3.5 adds a second metric page on the same foundation: a team's
batting strikeouts per game. Batting strikeouts were already present in the
hitting game log Milestone 1 retrieves, so no new MLB request was added. See
[docs/team-strikeouts-visualization.md](docs/team-strikeouts-visualization.md).

## Planned MVP

A local web application that:

- Retrieves MLB data through `python-mlb-statsapi`
- Presents interactive baseball statistics visualizations
- Runs with a clean Python web stack (FastAPI + Jinja2)

## Technology stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Packaging | Poetry |
| Web framework | FastAPI |
| Templates | Jinja2 |
| MLB data | python-mlb-statsapi |
| Charts | Plotly |
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

Apply migrations, import at least one team-season, then start the server:

```bash
poetry run alembic upgrade head
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
poetry run uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

- `/` — team hitting trends (hits per game)
- `/strikeouts` — team batting strikeout trends
- `/health` — JSON health check

## Team hitting trends page

The homepage charts one team's hits per game for one season, with a trailing
rolling average and the team's season average.

```text
http://127.0.0.1:8000/?team_id=136&season=2025&window=15
```

| Parameter | Meaning | Default |
| --- | --- | --- |
| `team_id` | MLB team id that has been imported | Seattle (136) when stored, otherwise the first team alphabetically |
| `season` | season imported for that team | the most recent stored season |
| `window` | rolling window: `5`, `10`, `15`, or `30` | `15` |

Submitting the controls produces a shareable URL, so a chart can be linked
directly. Choosing a different team updates the season selector in the browser
to that team's stored seasons, so the form cannot submit a combination that has
no data. The route validates the pair on every request regardless.

**The page reads SQLite only.** No web request touches the MLB Stats API;
importing data is always the explicit CLI step above. Selectors list only the
team-seasons that are actually stored locally.

**Rolling average.** Trailing, not centered: the value at game N averages the
`window` most recent games including game N. Early-season games average every
game played so far rather than showing a gap. The line joins the calculated
points with straight segments, so it never implies an average between games.

**Every number describes the stored games.** That may be a season in progress
or a partial import, so the dashed reference line is the team's average across
the completed games currently stored, not a guaranteed full season. The
footer's "Data through" date shows how current the numbers are.

**No MLB-wide average.** The database holds only explicitly imported
team-seasons, so a league average calculated from it would describe whichever
teams happen to be stored rather than the league. The third series is the
team's own stored-season average. League comparison is deferred until
league-wide ingestion is defined.

If the database is empty, the page explains how to import a team-season. If
migrations have not been applied, it asks for `poetry run alembic upgrade head`
instead of failing with a traceback.

## Team batting strikeout trends page

`/strikeouts` charts one team's **batting** strikeouts per game for one season,
with a trailing rolling average and the team's season average.

```text
http://127.0.0.1:8000/strikeouts?team_id=136&season=2025&window=15
```

It takes the same `team_id`, `season`, and `window` parameters as the hits page,
with the same defaults, and the navigation carries the current selection between
the two pages. `/` is unchanged and still serves hits.

**Batting, not pitching.** Every label says "Batting Strikeouts": these are
times the team's own hitters struck out, not strikeouts recorded by its
pitchers.

**Direction is not labelled good or bad.** A positive "vs Prior {window}" value
means more batting strikeouts. Whether that is bad depends on the question and
on what else the offense is doing, so no positive/negative colouring is applied.

**K/Game is a count, not a rate.** Games contain different numbers of plate
appearances, so a game with more opportunities can show more strikeouts without
hitters striking out any more often. K% (strikeouts per plate appearance) is the
opportunity-adjusted measure and is deferred until plate appearances are
persisted; it is not estimated in the meantime.

### Re-importing for batting strikeouts

Batting strikeouts were added in Milestone 3.5, so team-seasons imported before
it have no stored strikeout totals. Those totals are **unknown, not zero**, so
the migration leaves them `NULL` rather than defaulting them.

If a selected team-season still has unimported strikeouts, `/strikeouts`
explains that and shows the command for that exact team and season instead of
charting anything:

```bash
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

The same import command as always — there is no separate strikeout import. The
first re-import counts those rows as **updated**; running it again against
unchanged MLB data counts them as **unchanged**.

The hits page keeps working normally throughout, before and after the backfill.

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
│   ├── analytics/
│   │   ├── __init__.py
│   │   └── team_hitting.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── engine.py
│   │   ├── models.py
│   │   └── repositories.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── analytics.py
│   │   ├── catalog.py
│   │   ├── games.py
│   │   └── ingestion.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── team_game_logs.py
│   │   └── team_season_ingestion.py
│   └── web/
│       ├── __init__.py
│       ├── charts.py
│       ├── dependencies.py
│       ├── errors.py
│       ├── formatting.py
│       ├── routes.py
│       ├── selection.py
│       ├── static/
│       │   ├── css/
│       │   │   └── app.css
│       │   └── js/
│       │       └── season-selector.js
│       └── templates/
│           ├── base.html
│           ├── error.html
│           └── index.html
├── scripts/
│   ├── inspect_game_logs.py
│   └── import_team_season.py
├── docs/
│   ├── team-game-data-spike.md
│   ├── team-hits-visualization.md
│   └── team-season-ingestion.md
├── tests/
│   ├── conftest.py
│   ├── factories.py
│   ├── fixtures/
│   │   └── team_game_logs/
│   ├── test_analytics_schemas.py
│   ├── test_analytics_team_hitting.py
│   ├── test_charts.py
│   ├── test_formatting.py
│   ├── test_game_schemas.py
│   ├── test_import_team_season.py
│   ├── test_migrations.py
│   ├── test_repositories.py
│   ├── test_repositories_catalog.py
│   ├── test_selection.py
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

League-wide ingestion is the recommended next step, because an honest MLB
average comparison depends on it. See the recommendation in
[docs/team-hits-visualization.md](docs/team-hits-visualization.md).

## Disclaimer

This project is educational and is **not affiliated with Major League Baseball,
MLB Advanced Media, or any MLB club**. MLB names and marks belong to their
respective owners.
