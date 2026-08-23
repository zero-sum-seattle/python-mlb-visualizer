# Architecture Deep Dive

A from-scratch walkthrough of how python-mlb-visualizer is built, for
picking the project back up after time away. Written 2026-08-21 against
`main` at commit `088da5e` (991 tests passing, 98% coverage, ruff clean).

## The shape of the system

```
MLB Stats API                     SQLite                        Browser
     │                              │                               │
     │  scripts/import_*.py         │                               │
     ├──────────────────────────────►                               │
     │  (network, offline-unsafe)   │  app/database/                │
     │                              │  (source of truth)            │
     │                              │        │                      │
     │                              │        │ app/analytics/       │
     │                              │        │ (pure functions)     │
     │                              │        │        │             │
     │                              │        │        │ app/web/    │
     │                              │        │        │ charts.py + │
     │                              │        │        │ routes.py   │
     │                              │        │        │             ├──►
     │                              │        │        │  Jinja +    │
     │                              │        │        │  Plotly     │
```

The rule that makes everything else fall into place: **the running web
server never calls the MLB API.** Only the CLI scripts in `scripts/` (via
`app/services/`) touch the network. Every page render reads SQLite only.
This is why the test suite runs in ~80 seconds fully offline — nothing
needs mocking at the HTTP layer, just the `MlbGameDataClient`/
`MlbTeamDirectoryClient` protocol objects.

## 1. Ingestion — getting MLB data into SQLite

### Single team-season: `app/services/team_season_ingestion.py`

```python
def ingest_team_season(*, session, team_id, season, client=None):
    lines = get_team_game_batting_lines(team_id, season, client=client)  # network
    with session.begin():                                                # DB txn
        persistence = upsert_team_season(session, lines=lines)
```

Two-phase, in that order, always: **fetch everything from MLB first, then
persist in one transaction.** If the fetch fails, nothing touches the
database. If persistence fails, the whole team-season rolls back — no
half-imported season.

`upsert_team_season` (`app/database/repositories.py:153`) is the piece
worth understanding in detail, since every re-import goes through it:

- Loads every existing row for `(team_id, season)` once, indexed both by
  `(team_id, game_pk)` and by bare `game_pk`.
- For each incoming line: if the `(team_id, game_pk)` key exists, compare
  the full row via `to_domain() == line` — if equal, count as
  **unchanged** (no write); if different, mutate the ORM row in place and
  bump `updated_at`.
- If the key doesn't exist, it inserts — but first checks whether that
  `game_pk` is already claimed by a *different* team, which would mean
  upstream sent conflicting data (`TeamGamePersistenceError`).
- Rows present in the DB but absent from the new fetch are **never
  deleted**. MLB dropping a game from a response (rain-out
  reclassification, API hiccup) doesn't erase history.

This is why the README can promise "imports are idempotent" — re-running
the same import is a no-op at the DB level (all `unchanged`), and
importing a season mid-year then re-importing later only touches games
that actually changed.

### League-wide season: `app/services/league_season_ingestion.py`

This orchestrates ~30 calls to the same `ingest_team_season`, plus a
bookkeeping row (`LeagueSeasonIngestionRecord`) that answers one specific
question: *did the most recent league-wide import successfully cover
every team MLB reports for this season?* That answer is what gates the
"MLB average" comparison lines on every chart — see `supports_league_wide_average` and friends in `app/analytics/league_*.py`.

Sequence, explicitly documented in the module docstring:

```
discover teams                     network only, no transaction
record RUNNING                     short transaction, committed
for each team:
    fetch team-season from MLB     network only, no transaction
    persist team-season            short transaction, committed  (own txn, via ingest_team_season)
build + validate the result        no transaction
record COMPLETE / INCOMPLETE       short transaction, committed
```

Key design decisions, and why:

- **No long-running transaction wraps the whole run.** Each team commits
  independently. If team #14 of 30 fails, teams 1–13 stay persisted; the
  season is marked `INCOMPLETE`, and a rerun re-attempts all 30 (cheap,
  because the upsert makes 1–13 all "unchanged").
- **A crash mid-run leaves the row `RUNNING` forever** — there's no
  heartbeat or lease. That's deliberate: a `RUNNING` row is a true
  statement ("the last run didn't finish, coverage unknown"), and the next
  invocation just overwrites it. No cleanup job is needed because full
  reruns are idempotent and cheap.
- **The result object is built and validated before the coverage row is
  written.** `LeagueSeasonIngestionResult` (a Pydantic model) can reject
  an inconsistent result before it's recorded — so `COMPLETE` can never be
  persisted for a run the domain model itself considers broken. If
  validation throws, the row is left `RUNNING` (honest) rather than
  something misleadingly stable.
- **Team discovery is season-aware**, not a hardcoded list of 30 ids
  (`app/services/league_teams.py`). It calls
  `Mlb.get_teams(sport_id=1, season=<year>)`, filters out All-Star squads
  (`all_star_status == "Y"`, since they carry the MLB sport id but play no
  real season), and hard-fails on a duplicate team id rather than silently
  deduping — a repeat id means upstream identity is unreliable, which is a
  data-integrity problem worth surfacing, not smoothing over.

### The "unknown ≠ zero" rule

Runs through the whole ingestion story: `strikeouts` is a **nullable**
column (`app/database/models.py:77`), because rows persisted before batting
strikeouts were tracked have a genuinely unknown total — treating that as
0 would be a lie, not a default. Anywhere strikeouts feed an average or a
chart, the code explicitly checks for `None` and raises
`MissingStrikeoutDataError` / `MissingLeagueStrikeoutDataError` rather than
computing a misleading number. `runs`, by contrast, has no such nullable
case — it's required on every persisted record, so runs code never needs
this branch.

## 2. Storage — `app/database/models.py`

Two tables, intentionally minimal:

- **`team_game_batting_lines`** — one row per team-game. Uniqueness is
  `(team_id, game_pk)`. CHECK constraints enforce basic sanity at the DB
  level (positive ids, non-negative counts, `home_away IN ('home',
  'away')`) so bad data can't slip in even from a careless script.
- **`league_season_ingestions`** — one row *per season* (not an attempt
  log). A rerun overwrites the row, which is what makes an incomplete run
  retryable without accumulating history nobody reads. The CHECK
  constraints are worth noting because they enforce invariants that would
  otherwise only live in application code:
  - `(status = 'RUNNING') = (completed_at IS NULL)` — you cannot have a
    finished run with no timestamp, or a running one with one.
  - `status <> 'COMPLETE' OR (failed_team_count = 0 AND
    expected_team_count > 0)` — the database itself refuses to let a
    partial import call itself `COMPLETE`, even if application code were
    later changed carelessly.

Both models have `to_domain()` / `from_domain()` / `apply_domain()`
methods that convert to/from the Pydantic schema types
(`app/schemas/games.py`, `app/schemas/ingestion.py`). This is the seam that
keeps SQLAlchemy out of the analytics and web layers entirely.

Migrations are Alembic (`alembic/versions/`); three so far, matching the
project's milestones (create batting lines table → add league ingestion
state → add batting strikeouts column).

## 3. Analytics — `app/analytics/`

Pure functions: given `Sequence[TeamGameBattingLine]` in, a typed
`*Analysis` Pydantic object out. No FastAPI, SQLAlchemy, Jinja, or Plotly
import anywhere in this package — enforced by convention, not tooling, but
consistently followed. This is what makes 98% coverage achievable without
spinning up a DB or an HTTP client for most tests.

The pattern, using `team_hitting.py` as the template all four analytics
modules (`team_hitting`, `team_strikeouts`, `team_runs`,
`team_hitting_comparison`) follow:

1. **Order games correctly.** Sort by `(game_date, game_number, game_pk)`
   — not just date — so both halves of a doubleheader land in true
   chronological order. The chart's x-axis is `season_game_number`, a
   synthetic 1-based index over this order (not the MLB-assigned game
   number, which can repeat across doubleheaders).
2. **Validate invariants.** Raises if games mix teams/seasons, or if
   `rolling_window < 1`, or (for strikeouts) if any game has a `None`
   total.
3. **Trailing rolling average**, computed with a running-sum trick
   (`_trailing_averages`, `app/analytics/team_hitting.py:85`) rather than
   recomputing a sum per window — O(n) instead of O(n·window). Early
   games average over however many games exist so far rather than leaving
   a gap (game 1 is its own one-game average).
4. **Season summary** compares the most recent `rolling_window` games to
   the *prior* `rolling_window` games — but only when at least
   `2 × rolling_window` games have been played, specifically to avoid
   comparing a partial window against a full one and reporting a change
   that's really just a sample-size artifact.

`team_hitting_comparison.py` is one level up: it takes an already-built
`TeamHitsAnalysis` + `TeamStrikeoutsAnalysis` plus league-wide baselines,
and produces normalized indexes (`team_rate / mlb_rate * 100`). It
explicitly rejects a baseline of zero (`InvalidComparisonBaselineError`)
rather than dividing by it.

The `league_*.py` modules mirror the team-level ones but compute the
MLB-wide average from every stored row for a season, and expose the
`supports_league_wide_*` predicate functions that routes use to decide
whether the "MLB average" line is trustworthy enough to draw at all.

## 4. Charts — `app/web/charts.py`

One explicit builder function per chart (`build_team_hits_figure`,
`build_team_strikeouts_figure`, `build_team_runs_figure`,
`build_team_hitting_comparison_figure`) rather than one parameterized
builder — the module docstring explains why: a single generic builder
would have to encode which labels/colors/axis semantics belong to which
statistic, which wound up harder to read than four builders that look
almost identical. This is a real, considered tradeoff, not unconsidered
duplication — worth knowing before you reflexively refactor it into one.

Details that show up as small functions/constants rather than being
scattered inline:

- `_trailing_averages`-driven rolling line, a raw-value scatter with
  **open-circle markers** (deliberately, so 162 overlapping season points
  don't merge into a solid blob), and up to two horizontal dashed
  reference lines (team season average in navy, MLB average in amber
  dotted) — drawn with distinct hue *and* dash pattern so they're
  distinguishable in greyscale.
- `_season_game_ticks` picks ~10 evenly spaced x-axis ticks and always
  force-includes the final game, labeling each with both the game number
  and its date — a bare index doesn't tell you *when* in the season a
  stretch happened.
- Only one reference line ever gets a text label (`_label_reference_line`)
  — when both team-average and MLB-average lines are present, only the
  MLB one is labeled, because they can sit within a tenth of a point of
  each other and two overlapping labels would be worse than one.
- `plotly.js` itself is **not bundled into the page**. `render_figure_html`
  renders with `include_plotlyjs=False`, and a separate route
  (`/vendor/plotly.min.js`) serves the bundle from the installed `plotly`
  package via `get_plotlyjs()`, cached with `@lru_cache`. This keeps
  multi-megabyte JS out of the git repo while still working with zero
  internet access at request time.

## 5. Web routes — `app/web/routes.py`

Four page routes (`/`, `/strikeouts`, `/runs`, `/comparison`) share one
skeleton, repeated per route rather than factored into a shared helper
(again, a deliberate legibility tradeoff — each route's docstring and
error branches read as one linear story):

```
list_available_team_seasons(session)   → DatabaseSchemaMissingError → 503 page telling you to run alembic
build_team_options(available)          → empty?  render "no data yet" state
select_team(teams, team_id)            → not found? → 404 page naming stored teams
select_season(selected_team, season)   → not found? → 404 page naming stored seasons
list_team_season(session, ...)         → rows for the chosen team-season
build_team_*_analysis(games, window)   → pure analytics call
_load_league_*_comparison(session, …)  → None unless coverage is COMPLETE
build_team_*_figure(analysis, league)  → Plotly figure
render + return TemplateResponse
```

Every branch point returns a real, styled page (via
`app/web/templates/error.html` or the page's own template with a `state`
context flag: `empty` / `not_found` / `missing_strikeouts` / `unavailable`
/ `ok`) — never a bare exception. The comparison route is the most layered
of the four because it has the most ways to be legitimately unavailable:
missing strikeout data on the team's own games, incomplete league
coverage, missing strikeout data in the league-wide rows, or a zero
baseline — each gets its own message and (where relevant) the exact
re-import command to fix it.

Selection state (`team_id`, `season`, `window`) round-trips through query
parameters, which is what makes every chart URL shareable
(`app/web/selection.py`, `app/web/navigation.py` build the option lists
and the nav links that carry the current selection across pages).

## 6. Configuration — `app/config.py`

Trivial by design: a `pydantic_settings.BaseSettings` with four fields
(`app_name`, `environment`, `debug`, `database_url`), loaded from `.env`,
cached via `@lru_cache`. `app/main.py`'s `lifespan` builds the DB engine
from settings *at app startup*, not at import time, specifically so tests
and scripts can point at their own database without fighting a
module-level singleton.

## 7. Testing strategy — `tests/`

- `tests/conftest.py` gives every DB-touching test a **real, file-backed
  SQLite database migrated to Alembic head** (`migrated_session` fixture)
  — not an in-memory mock of SQLAlchemy. Migrations run for real per test,
  which means the test suite doubles as continuous verification that the
  Alembic chain actually works.
- `tests/factories.py` provides `make_batting_line` / `make_season`
  builders that default `strikeouts=None` (matching a pre-Milestone-3.5
  row) and require callers to opt in to real strikeout values —
  reinforcing the "unknown ≠ zero" rule at the test-data level too, so a
  test can't accidentally rely on strikeouts being present without saying
  so.
- Almost a thousand tests, ~80s, 98% coverage, all offline — the MLB
  client is always a protocol-typed stub/fake, never the real
  `mlbstatsapi.Mlb`.

## Open threads (as of this writing)

No open PRs. Seven open issues, all additive feature/infra work, nothing
reads as a bug fix or debt cleanup:

- **#30** Design UI architecture for team and player analytics
- **#31** Upgrade to python-mlb-statsapi 1.1.0 and adopt async support
- **#26** Compare one team across two seasons in one chart
- **#29** Add game result context and W/L markers to team charts
- **#32** Explore hits per pitch as a team metric
- **#20** Add a Dockerfile for local app container
- **#17** CI — add optional live MLB integration smoke workflow

Given the layering described above, most of these slot in cleanly: a new
stat (#32, #29) means a new `app/analytics/*.py` + chart builder + route,
following the exact template `team_hitting.py`/`team_strikeouts.py`
already established. #31 (async) is the one with the widest blast radius,
since `ingest_league_season`'s whole transaction-boundary story
(sequential, one commit per team) is written around a synchronous client.
