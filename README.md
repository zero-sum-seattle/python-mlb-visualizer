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
- Team Baserunners/Game trends (hits + walks + hit-by-pitch)
- Team run differential and Pythagorean expected record, from a league-wide import
- Team pitching: pitches per game, with ERA, WHIP, K/9 and BB/9
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
| `/baserunners` | Team Baserunners/Game |
| `/run-differential` | Team run differential and Pythagorean record |
| `/pitching` | Team pitches per game, with ERA and WHIP |
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

## Run with Docker

Build the image:

```bash
docker build -t mlb-visualizer .
```

Run it with a named volume so the SQLite database survives container
restarts and rebuilds:

```bash
docker volume create mlb-visualizer-data
docker run --rm -p 8000:8000 -v mlb-visualizer-data:/data mlb-visualizer
```

Open `http://127.0.0.1:8000`. Alembic migrations are applied automatically
before the container runs any command, whether that is the app itself or an
import script, so the schema is never out of date and no separate migration
step is needed.

The container starts with an empty database. Populate it by running an import
script in its own one-off container, mounting the same named volume so it
writes into the database the app container reads from:

```bash
docker run --rm -v mlb-visualizer-data:/data mlb-visualizer \
    poetry run python scripts/import_team_season.py --team-id 136 --season 2025

docker run --rm -v mlb-visualizer-data:/data mlb-visualizer \
    poetry run python scripts/import_league_season.py --season 2025
```

The app container does not need to be restarted afterward: each request reads
the SQLite file fresh, so a running app container picks up newly imported data
on the next page load.

Override configuration (for example a different port) with `-e`:

```bash
docker run --rm -p 8000:8000 -v mlb-visualizer-data:/data \
    -e APP_NAME=my-mlb-visualizer mlb-visualizer
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

### Run differential and runs allowed

Runs allowed is **not** a stored column and not a separate MLB request. Each
team-game row records the opponent's id alongside the team's own runs, so runs
allowed for a game is the opponent's own runs scored on their row for the same
`game_pk`, found by a self-join.

That makes `/run-differential` the one page needing a league-wide import. The
other pages read a single team's own rows, so a single-team import suits them;
this one has no opponent rows to pair with and refuses rather than treating an
unknown runs-allowed total as zero.

Win/loss is derived the same way. A completed MLB game cannot end tied, so the
team that outscored its opponent won, which gives an actual record with no W/L
column stored anywhere.

The page also shows the **Pythagorean expected record**:

```text
expected_win_pct = RS^1.83 / (RS^1.83 + RA^1.83)
```

using the exponent Baseball Reference publishes against, so the figure can be
checked against a public source. The gap between expected and actual describes
games already played; it is not a forecast.

There is no MLB average line on this page, which is not an omission:
league-wide run differential is exactly zero, because every run scored by one
team is a run allowed by another. The chart's zero line *is* the MLB average.

### Pitching

Pitching is a **separate MLB stat group in its own request**, so it is the first
feature here that increases import cost: a team-season is four requests rather
than three (the team lookup and the season schedule are shared between the two
game logs). Pitching lines live in their own table, `team_game_pitching_lines`.

A team-season imported before pitching was collected has no pitching rows at
all, and `/pitching` returns 409 asking for a re-import. Every pitching column
is `NOT NULL`, so unlike batting strikeouts and the baserunner components there
is no partially-known state.

#### Innings are stored as outs

MLB returns `inningsPitched` as a **string in baseball notation**, where
`'10.2'` means ten and two-thirds innings rather than 10.2 of them. Reading that
as a decimal silently corrupts every derived rate. The same split carries `outs`
as an exact integer, so that is the stored column:

```text
outs 32  ->  10.2 IP  ->  ER 9 * 27 / 32 = 7.59   (MLB's own era: '7.59')
```

Only raw components are persisted. ERA, WHIP, K/9 and BB/9 are derived on read,
so a stored rate cannot drift from the components it came from.

#### Counts and rates aggregate differently

Pitches per game is a **count**, so its season figure is the plain mean of the
per-game values, like every other per-game page here.

ERA, WHIP, K/9 and BB/9 are **rates**, and a rate over several games is the
ratio of the summed totals — not the mean of the per-game ratios. For the 2025
Mariners:

```text
season ERA (correct)          629 ER * 27 / 4388 outs  =  3.870
mean of the 162 game ERAs                              =  3.965
```

That 0.094 gap would match no published figure. The same rule applies to the
rolling window, which accumulates earned runs and outs rather than smoothing
game ERAs, and to the league context, whose rates are outs-weighted rather than
game-weighted.

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
- [Team baserunners visualization](docs/team-baserunners-visualization.md)
- [Team run differential visualization](docs/team-run-differential-visualization.md)
- [Team pitching visualization](docs/team-pitching-visualization.md)
- [Team vs MLB comparison](docs/team-vs-mlb-comparison.md)
- [Normalized hitting trends comparison](docs/team-hitting-trends-comparison.md)
- [League-season ingestion](docs/league-season-ingestion.md)
- [Team-season ingestion](docs/team-season-ingestion.md)
- [Team game data investigation](docs/team-game-data-spike.md)

## Disclaimer

This project is educational and is **not affiliated with Major League Baseball,
MLB Advanced Media, or any MLB club**. MLB names and marks belong to their
respective owners.
