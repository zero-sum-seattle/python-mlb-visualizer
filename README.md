# MLB Stats Visualizer

A local-first MLB statistics visualization application built with FastAPI,
Jinja2, Plotly, and `python-mlb-statsapi`.

> **Work in progress:** this project is under active development. The data model,
> visualizations, navigation, and deployment approach are still evolving. Expect
> features and UI details to change as the project grows.

The application imports MLB data explicitly, stores normalized team-game records
in SQLite, and renders interactive charts entirely from the local database.
Normal browser requests do **not** call the MLB Stats API.

## What it does today

- Team Hits/Game trends with rolling and season averages
- Team batting Strikeouts/Game trends
- Team Runs/Game trends
- MLB-wide per-game comparisons when league coverage is trustworthy
- Normalized Hits vs batting Strikeouts comparison with MLB average = 100
- Team, season, and rolling-window selectors with shareable URLs
- League-wide season ingestion with persisted completeness state
- Offline deterministic test suite and GitHub Actions CI

Player visualizations, additional team metrics, multi-season overlays, and a more
organized Team/Player UI are planned but not implemented yet.

## Screenshots

The UI is still being refined. Current desktop and mobile screenshots will live
here as the design stabilizes.

<!--
Add real application screenshots here, for example:

![Team hits chart](docs/screenshots/team-hits.png)

![Normalized hits vs strikeouts comparison](docs/screenshots/team-comparison.png)

![Mobile layout](docs/screenshots/mobile.png)
-->

## Technology stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12+ |
| Packaging | Poetry |
| Web framework | FastAPI |
| Templates | Jinja2 |
| MLB data | python-mlb-statsapi |
| Charts | Plotly |
| Database | SQLite |
| ORM | SQLAlchemy 2 |
| Migrations | Alembic |
| Configuration | Pydantic Settings |
| Testing | pytest |
| Lint / format | Ruff |
| CI | GitHub Actions |

## Installation

Requirements:

- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)

Clone the repository and install dependencies:

```bash
git clone https://github.com/zero-sum-seattle/python-mlb-visualizer.git
cd python-mlb-visualizer
poetry install
```

Optionally create a local environment file:

```bash
cp .env.example .env
```

The default database is:

```text
sqlite:///./mlb_visualizer.db
```

Apply migrations:

```bash
poetry run alembic upgrade head
```

## Import data

The web application reads SQLite only. MLB network access happens through
explicit import commands.

Import one team-season:

```bash
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

Import an entire MLB season:

```bash
poetry run python scripts/import_league_season.py --season 2025
```

League imports record whether every discovered team was refreshed successfully.
MLB-wide comparison statistics are only presented when the persisted coverage
state supports describing the stored data as league-wide.

Imports are idempotent: unchanged rows stay unchanged, changed rows are updated,
and already-stored rows are not deleted simply because a later upstream response
omits them.

## Run locally

```bash
poetry run uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

Current routes:

| Route | Visualization |
| --- | --- |
| `/` | Team Hits/Game |
| `/strikeouts` | Team batting Strikeouts/Game |
| `/runs` | Team Runs/Game |
| `/comparison` | Normalized Hits vs batting Strikeouts |
| `/health` | JSON health check |

The chart routes support:

```text
team_id=<MLB team id>
season=<season>
window=5|10|15|30
```

Example:

```text
http://127.0.0.1:8000/comparison?team_id=136&season=2025&window=15
```

## Statistics and interpretation

### Rolling averages

Rolling values are trailing averages that include the current game. Before a
full window exists, the application uses every completed game available so far.

### MLB comparisons

League averages are game-weighted over persisted team-game records, not an
unweighted mean of team averages. League statistics are shown only when the
stored league-season ingestion state indicates complete coverage.

A `COMPLETE` refresh does **not** mean the baseball season has ended. It means
every discovered club was successfully refreshed for that import run.

### Batting strikeouts

Strikeouts shown by this application are **batting strikeouts** by the selected
team's hitters, not pitching strikeouts.

Batting K/Game is a per-game count, not K%. Plate appearances are not currently
persisted, so the application does not estimate K%.

### Normalized comparison

The comparison page puts two different statistics on a common scale:

```text
Hits Index = rolling team Hits/Game / MLB Hits/Game * 100

Batting Strikeout Index = rolling team batting K/Game
                          / MLB batting K/Game
                          * 100
```

`100` means MLB average for that metric. Above 100 means more of the named
statistic than MLB average; it does not automatically mean better performance.

The displayed Trend Gap is:

```text
recent Hits Index - recent Batting Strikeout Index
```

It is descriptive arithmetic, not a validated overall offensive-performance
metric, ranking, percentile, significance test, or causal claim.

## Architecture

```text
MLB Stats API
    ↓
python-mlb-statsapi
    ↓
services / normalization
    ↓
domain schemas
    ↓
repositories / SQLite
    ↓
analytics
    ↓
FastAPI + Jinja2 + Plotly
    ↓
browser
```

Key rules:

- Browser requests are database-only.
- MLB network access belongs to explicit ingestion workflows.
- Baseball calculations live in analytics, not routes or repositories.
- Routes stay thin and server-rendered.
- Missing data is treated as unknown rather than silently converted to zero.
- League completeness is persisted explicitly rather than inferred from row counts.

See [`AGENTS.md`](AGENTS.md) for the project architecture and contribution rules.

## Project layout

```text
app/
├── analytics/        # baseball calculations
├── database/         # SQLAlchemy models, repositories, engine
├── schemas/          # typed domain and analytics contracts
├── services/         # MLB retrieval, normalization, ingestion
└── web/              # FastAPI routes, Plotly figures, templates, static assets

scripts/              # operational import / inspection commands
docs/                 # design and statistical documentation
alembic/              # database migrations
tests/                # offline deterministic tests
```

## Testing

Run the full suite:

```bash
poetry run pytest
```

The automated test suite is designed to run without live MLB network access.

Lint and formatting:

```bash
poetry run ruff check .
poetry run ruff format .
poetry run ruff format --check .
```

## Documentation

More detailed implementation and statistical notes live in [`docs/`](docs/),
including:

- [Team hits visualization](docs/team-hits-visualization.md)
- [Team batting strikeouts visualization](docs/team-strikeouts-visualization.md)
- [Team runs visualization](docs/team-runs-visualization.md)
- [Team vs MLB comparison](docs/team-vs-mlb-comparison.md)
- [Normalized hitting trends comparison](docs/team-hitting-trends-comparison.md)
- [League-season ingestion](docs/league-season-ingestion.md)
- [Team-season ingestion](docs/team-season-ingestion.md)
- [Team game data investigation](docs/team-game-data-spike.md)

## Disclaimer

This project is educational and is **not affiliated with Major League Baseball,
MLB Advanced Media, or any MLB club**. MLB names and marks belong to their
respective owners.
