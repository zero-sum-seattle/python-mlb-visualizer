# MLB Stats Visualizer

Interactive MLB statistics visualization web application powered by
`python-mlb-statsapi`.

## Status

**Milestone 0 — Repository Foundation** is complete.

**Milestone 1 — Team game-level data feasibility spike** is complete.

Milestone 0 provides a FastAPI application with Jinja2 templates, Pydantic
Settings configuration, pytest coverage, Ruff linting/formatting, and GitHub
Actions CI.

Milestone 1 adds the first MLB data path: `python-mlb-statsapi` is now an
application dependency, and the project can retrieve **normalized team
game-level hitting data** for a selected team and season. Every completed
regular-season game becomes one typed `TeamGameBattingLine` record with the
game, team, opponent, home/away, hits, runs, and status. Persistence,
analytics, and charting are still deferred to later milestones — there is no
database and no new web page or API endpoint.

## Planned MVP

A local web application that:

- Retrieves MLB data through `python-mlb-statsapi`
- Presents interactive baseball statistics visualizations
- Runs with a clean Python web stack (FastAPI + Jinja2)

Later milestones will add persistence and visualization libraries.

## Technology stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Packaging | Poetry |
| Web framework | FastAPI |
| Templates | Jinja2 |
| MLB data | python-mlb-statsapi |
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
`app/services/team_game_logs.py` and `app/schemas/games.py`; the command-line
script is only a thin inspection wrapper around it.

```python
from app.services.team_game_logs import get_team_game_batting_lines

lines = get_team_game_batting_lines(team_id=136, season=2025)
```

### Inspection command

```bash
poetry run python scripts/inspect_game_logs.py --team-id 136 --season 2025
```

Options: `--team-id` and `--season` are required; `--limit N` shows only the
first N games and `--format json` emits Pydantic-serialized JSON on stdout.

Example output shape (values are illustrative):

```text
2025-03-27 | 778547 | Seattle Mariners vs Athletics | home | H: 5 | R: 4 | Final
2025-04-04 | 778444 | Seattle Mariners at San Francisco Giants | away | H: 15 | R: 9 | Final
2025-08-19 | 776691 (G1) | Chicago Cubs vs Milwaukee Brewers | home | H: 8 | R: 6 | Final

Team: Seattle Mariners
Season: 2025
Completed games: 162
Total hits: 1345
Average hits per game: 8.30
```

This script is the manual integration check for the data path. It calls the live
MLB Stats API, so it is not part of `poetry run pytest` and never runs in CI.

### Selected retrieval strategy

Team hitting `gameLog` splits joined on `gamePk` with a single team schedule
request:

| Call | Purpose |
| --- | --- |
| `Mlb.get_team(team_id, season=season)` | Confirm the id is an MLB team and read its name for that season |
| `Mlb.get_team_stats(stats=["gameLog"], groups=["hitting"], gameType="R")` | Per-game hits, runs, home/away, opponent id, date |
| `Mlb.get_schedule(gameTypes="R")` | Status, opponent name, game number, doubleheader flag, scheduled innings |

Three requests per team-season, no matter how many games were played. It was
chosen over *schedule + box score* and *schedule + linescore* because those need
one extra request per game (163 for a full season), and the box score exposes
team hits only through an untyped `team_stats["batting"]["hits"]` dictionary. The
`gameLog` splits alone are not enough — the package model drops the game number,
and the payload has no game status and no opponent name — which is why the
schedule half of the alternatives is kept. `schedule?hydrate=linescore` would be
a single request, but `ScheduleGames` has no `linescore` field and the package
discards it.

`docs/team-game-data-spike.md` records the full investigation, including the
measured request counts and field-by-field findings.

### Cross-source validation

The batting numbers come from the game log and the game context comes from the
schedule, so the service validates the values the two sources share for every
game instead of assuming they agree. A disagreement raises `TeamGameDataError`
naming the `gamePk`, the invariant, and both conflicting values:

- `split.team.id` matches the requested team id
- `split.date` matches `ScheduleGames.official_date`, compared as parsed dates
- `split.opponent.id` matches the opponent side of the schedule entry
- `split.is_home` matches the side of the schedule entry holding the team id
- `split.stat.runs` matches the selected side's schedule `score`, when that
  optional score is present

Team names are excluded from the comparison on purpose: the game log reports the
franchise's current name while the team lookup reports its name for the requested
season, so they legitimately differ for a renamed club.

A repeated `gamePk` in the game log is accepted only when both splits normalize
to identical records; conflicting duplicates raise `TeamGameDataError` rather
than silently preferring the first or last value.

These checks turn a future upstream field change or package-model change into a
loud failure instead of a silently wrong record.

### Edge cases and limitations

- A game counts as completed only when the schedule's `codedGameState` is `F`
  (Final) or `O` (Game Over), which includes rain-shortened *Completed Early*
  games. `abstractGameState` is not used: MLB reports it as `Final` for
  postponed and cancelled games too.
- Postponed, cancelled, suspended, and in-progress games are excluded.
- Both games of a doubleheader are kept as separate records. Records sort by
  date, then game number, then game id, because game ids do not always follow
  game numbers within a doubleheader.
- A postponed or suspended game keeps its `gamePk` when it is made up or
  resumed, so the same game can appear twice in one schedule. The completed
  entry wins and each game is emitted once.
- A suspended game that is resumed is reported once, dated its original official
  date. A suspended game that is never resumed is excluded; the 2025 season
  contained none, so that path is covered by fixture data only.
- Nothing assumes nine innings; `scheduled_innings` is carried through. The 2021
  Mariners return 5 seven-inning games alongside 157 nine-inning games.
- Team ids are the identity. Names are display values requested for the season
  under inspection, so team 133 reads `Oakland Athletics` for 2024 and
  `Athletics` for 2025.

## Testing

```bash
poetry run pytest
```

The suite is fully offline. MLB payload fixtures live in
`tests/fixtures/team_game_logs/` and the `mlbstatsapi.Mlb` client is replaced at
the service boundary, so no test calls the MLB Stats API.

## Lint and formatting

```bash
poetry run ruff check .
poetry run ruff format .
poetry run ruff format --check .
```

## Project structure

```text
.
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── games.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── team_game_logs.py
│   └── web/
│       ├── __init__.py
│       ├── routes.py
│       └── templates/
│           ├── base.html
│           └── index.html
├── scripts/
│   └── inspect_game_logs.py
├── docs/
│   └── team-game-data-spike.md
├── tests/
│   ├── __init__.py
│   ├── fixtures/
│   │   └── team_game_logs/
│   │       ├── cubs_2025_hitting_game_log.json
│   │       ├── cubs_2025_schedule.json
│   │       ├── edge_cases_hitting_game_log.json
│   │       └── edge_cases_schedule.json
│   ├── test_game_schemas.py
│   ├── test_team_game_logs.py
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

Persistence (database and migrations), analytics, and visualization libraries
will be added in later milestones. The project intentionally excludes those
dependencies for now.

## Disclaimer

This project is educational and is **not affiliated with Major League Baseball,
MLB Advanced Media, or any MLB club**. MLB names and marks belong to their
respective owners.
