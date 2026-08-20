# Team hits visualization

This document describes how Milestone 3 turns persisted team game batting lines
into the first application page: a team's hits per game with a trailing rolling
average.

## 1. Data flow

```text
scripts/import_team_season.py  ──►  MLB Stats API  ──►  SQLite   (offline, CLI)

GET /?team_id=136&season=2025&window=15
        │
        ├─ list_available_team_seasons(session)   ──► selector options
        ├─ list_team_season(session, ...)         ──► list[TeamGameBattingLine]
        ├─ build_team_hits_analysis(games, ...)   ──► TeamHitsAnalysis
        ├─ build_team_hits_figure(analysis)       ──► plotly Figure
        └─ index.html                             ──► HTML
```

The web request reads SQLite only. It never calls
`get_team_game_batting_lines` or any other MLB path; `app/web/routes.py` does
not import `app.services.team_game_logs` at all. Getting new data into the page
is a separate, explicit CLI step.

## 2. Analytics layer

`app/analytics/team_hitting.py` holds every baseball calculation on this page.
It imports Pydantic schemas and nothing else from the application, so it has no
knowledge of FastAPI, Jinja, SQLAlchemy, Plotly, or the MLB API, and it does not
return figures.

```python
build_team_hits_analysis(
    games: Sequence[TeamGameBattingLine],
    *,
    rolling_window: int = 15,
) -> TeamHitsAnalysis
```

It orders games by `(game_date, game_number, game_pk)` — the same order the
repository uses — and assigns each one a `season_game_number` of 1, 2, 3, and so
on. That continuous index, not MLB's `game_number`, is the chart's x axis.
`game_number` exists to sequence doubleheaders and is carried onto each point
for reference.

Input the layer refuses, as `TeamHitsAnalysisError`:

- an empty list of games (the route renders a not-found state instead)
- games from more than one team or more than one season
- a `rolling_window` below one game

## 3. Rolling-average definition

The average is **trailing**, never centered. For game N it covers the
`rolling_window` most recent games *including* game N:

```text
window = 15

game 1   → average of game 1
game 2   → average of games 1-2
...
game 15  → average of games 1-15
game 16  → average of games 2-16
game 17  → average of games 3-17
```

A centered average would let future games influence an earlier point, which
misrepresents what a team's form looked like at the time.

## 4. Partial-window behavior

Early in a season there are not yet `rolling_window` games. Rather than leaving
the first fourteen points blank, each one averages every game played so far, so
game 1 is its own average. The line starts at the first game and is noisier at
the left edge, which honestly reflects how little data supports it.

## 5. Prior-window comparison

`vs Prior 15` compares the current window with the equal-sized window
immediately before it:

```text
current : games N-14 .. N
prior   : games N-29 .. N-15
change  : recent_average - prior_window_average
```

The comparison is only made when two **complete** windows exist, that is when
`games_played >= 2 * rolling_window`. Otherwise `prior_window_average` and
`change_vs_prior_window` are both `None`. Comparing a full window against a
partial one would report a difference caused by sample size rather than by
hitting.

> **Milestone 5 update.** The `vs Prior 15` card was replaced on this page by a
> `vs MLB` card. Both summary values above are still calculated, validated, and
> tested on `TeamHitsSummary`, and the strikeouts page still shows its own
> `vs Prior N` card with the `—` / "Not enough games" behaviour described here.
> See `docs/team-vs-mlb-comparison.md`.

Summary formulas:

| Value | Formula |
| --- | --- |
| `games_played` | number of completed games stored |
| `season_average` | total hits / completed games stored |
| `recent_average` | mean of the last `min(window, games_played)` games |
| `prior_window_average` | mean of the preceding full window, else `None` |
| `change_vs_prior_window` | `recent_average - prior_window_average`, else `None` |

Every value describes the games **currently stored**, which may be a season in
progress, a partial import, or an import that has not been refreshed. Nothing
on the page claims the database holds a complete season; the footer's "Data
through" date is what tells a reader how current the numbers are.

`TeamHitsSummary.season_average` is the only season average in the model. The
chart's reference line and the summary card both read it, so the two cannot
disagree.

Every calculation keeps ordinary floating-point precision. Rounding happens
only in `app/web/formatting.py`, for display.

## 6. Plotly figure construction

`app/web/charts.py` builds the figure from a `TeamHitsAnalysis`. It is separate
from the route so the figure contract can be tested without HTTP.

Three traces, in order:

| # | Name | Style | Purpose |
| --- | --- | --- | --- |
| 1 | `Game Hits` | thin grey line, small markers | game-to-game variation |
| 2 | `{window}-Game Average` | thick teal line | the trend, visually dominant |
| 3 | `Season Average` | dashed navy horizontal line | reference level |

Milestone 5 renamed this third trace to `Team Season Average` and added an
optional fourth trace, `MLB Average`, when the season has complete league
coverage. With two horizontal reference lines on one chart, "Season Average" no
longer said whose. See `docs/team-vs-mlb-comparison.md`.

The rolling average joins its points with straight segments
(`line.shape: "linear"`). Spline smoothing is deliberately not used: it bows
between games and would draw averages at positions where no average was
calculated.

Axes are titled `Season Game Number` and `Hits per Game`. The y axis uses
integer ticks with `rangemode: tozero` and no hardcoded maximum, so an unusually
high-scoring game still fits.

Both data traces share one hover template, so the same block appears wherever
the pointer is:

```text
May 18, 2025
vs Minnesota Twins
Hits: 11
15-Game Avg: 9.20
```

Away games read `at Minnesota Twins`. The season-average trace sets
`hoverinfo: skip` so it does not add a third box.

`render_figure_html` emits a bare div with `include_plotlyjs=False`. The
library itself is served by the application at `/vendor/plotly.min.js`, read out
of the installed plotly package, which keeps a multi-megabyte file out of the
repository while letting the page render with no internet access. The script tag
sits in `<head>` because the div's bootstrap script runs during body parsing.

The mode bar is disabled and Plotly branding is off. Export is not implemented
in this milestone.

## 7. Web and database session lifecycle

```text
FastAPI lifespan
    └─ build_engine(settings.database_url)
        └─ build_session_factory(engine)
            └─ app.state.session_factory
                └─ Depends(get_db_session) → Session per request
```

No engine is created at module import time, and startup never calls
`Base.metadata.create_all()`. Alembic remains the only thing that creates
schema.

Tests replace `get_db_session` through `app.dependency_overrides` so each test
runs against its own migrated temporary SQLite file. One test exercises the real
lifespan with `DATABASE_URL` pointed at a temporary database.

If the database is reachable but unmigrated, `list_available_team_seasons`
raises `DatabaseSchemaMissingError` and the page answers **503** with:

```bash
poetry run alembic upgrade head
```

Tables are never created automatically.

Only a genuinely absent `team_game_batting_lines` is translated that way. A
locked database, an unreadable file, or any other `OperationalError` keeps its
own exception, because telling someone to run a migration would send them down
the wrong path.

## 8. Team and season selection

`app/web/selection.py` turns the persisted catalog into selector options. Team
options are grouped by `team_id`, labelled with the name from that team's most
recent stored season, and sorted alphabetically. A franchise that was renamed
keeps the correct historical name against each season.

Defaults when a query parameter is absent:

| Parameter | Default |
| --- | --- |
| `team_id` | Seattle (136) when stored, otherwise the first team alphabetically |
| `season` | the most recent season stored for the selected team |
| `window` | 15 |

Handling of values that are present but unusable:

| Input | Result |
| --- | --- |
| `window` outside 5/10/15/30 | 422; a readable HTML page for browsers, JSON for API clients |
| `team_id=-1`, `season=banana` | 422, same readable page |
| `team_id` not stored | 404 with the selectors still usable |
| `season` not stored for that team | 404 listing the seasons that are stored |

Nothing renders a traceback, and an unstored selection is never silently
replaced with different data.

### Keeping the season selector in step with the team

Teams do not all have the same stored seasons. With a plain GET form, picking a
different team while the season selector still shows the previous team's season
would submit a pair that does not exist and land on the 404 state, even though
the newly chosen team has perfectly good data.

The page therefore embeds its own catalog next to the form:

```html
<script type="application/json" id="team-seasons-data">{"112":[2024],"136":[2025,2024]}</script>
```

`build_team_seasons_catalog` produces that mapping from the same `TeamOption`
list the selectors are built from, and `app/web/static/js/season-selector.js`
rebuilds the season options on `change`, selecting the newest season for the
newly chosen team. The rolling window is untouched. No request is made to
populate the selector, and no combination is hardcoded.

This is a convenience for the normal click-through path only. The route still
validates the pair on every request, so a hand-typed
`/?team_id=112&season=2025` for a team that only has 2024 continues to render
the 404 state. The script degrades to the previous behaviour if JavaScript is
unavailable.

## 9. Empty database behavior

A migrated but empty database renders the page normally at **200** with:

```text
No team data has been imported yet
```

and the import command. The page does not fetch anything from MLB to fill
itself in.

## 10. Why the MLB average was deferred

> **Milestone 5 update.** The MLB Average line now exists, under exactly the
> condition this section asked for: it is drawn only when the selected season
> has `COMPLETE` league-season coverage recorded by Milestone 4, and it is
> omitted otherwise. The reasoning below is why it did not exist in Milestone 3
> and is left as written. See `docs/team-vs-mlb-comparison.md`.


The original mockup included an MLB Average line. It is deliberately not
implemented.

The database contains only team-seasons someone explicitly imported. A "league
average" computed from that would be the average of whichever teams happen to be
stored — one team, in the common case — presented as though it described the
league. That is worse than showing nothing, because it looks authoritative.

The third series is the team's own **Season Average** instead, which is exactly
as trustworthy as the data behind it. League comparison belongs after
league-wide ingestion is defined and completeness can be checked.

## 11. Recommendation for Milestone 4

> **Update.** Milestone 4 delivered the league-wide ingestion and coverage
> state recommended here (`docs/league-season-ingestion.md`), and Milestone 5
> delivered the MLB-average trace it unblocked
> (`docs/team-vs-mlb-comparison.md`). League rank remains unimplemented.


Define **league-wide ingestion** next, because it unblocks the most requested
missing feature on this page and nothing else can honestly deliver it.

Concretely: a CLI that ingests all thirty teams for a season, a way to record
that a season is complete rather than inferring it from row counts, and only
then an MLB-average trace and league rank on this chart. The existing
`(team_id, game_pk)` identity and the upsert path already support one row per
team per game, so this is an ingestion and completeness problem, not a schema
one.

Two smaller follow-ups worth considering once that lands:

- HTMX for chart updates, so changing a selector does not reload the whole page
  and cannot request a season the newly selected team lacks.
- Additional hitting metrics (runs, on-base events) reusing the same analytics
  and chart shape, since `TeamGameBattingLine` already stores runs.

See also `docs/team-season-ingestion.md` for the Milestone 2 persistence path
and `docs/team-game-data-spike.md` for the Milestone 1 data path.
