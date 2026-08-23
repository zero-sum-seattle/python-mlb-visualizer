# Team baserunners visualization

This document describes the `/baserunners` page added by issue #37: what it
measures, where the two new source fields come from, why they are nullable,
and how the MLB-wide comparison is gated.

It answers one question:

> How often is this team putting a runner on base per game, how is that
> changing over the season, and how does its season average compare with MLB
> overall?

Related documents:

- [team-strikeouts-visualization.md](team-strikeouts-visualization.md) — the
  first metric to need a nullable, backfillable column; the migration
  mechanics, the legacy-null-data 409 state, and the two-rule league gate this
  page reuses were all established there
- [team-runs-visualization.md](team-runs-visualization.md) — the rolling
  average and chart layout this page otherwise mirrors
- [team-vs-mlb-comparison.md](team-vs-mlb-comparison.md) — where the
  game-weighted league definition and the coverage rule were established

Throughout this document and the UI, "baserunners" means **hits + walks +
hit-by-pitch**: times a batter reached base by one of those three means. This
is the standard on-base-percentage numerator, excluding reached-on-error and
fielder's choice, neither of which is persisted.

## 1. Source data fields

No new MLB request was added. `base_on_balls` (MLB's `baseOnBalls`) and
`hit_by_pitch` (MLB's `hitByPitch`) arrive in the same team hitting `gameLog`
split that already supplies hits, runs, and batting strikeouts:

| Layer | Name |
| --- | --- |
| MLB Stats API JSON | `baseOnBalls`, `hitByPitch` |
| `python-mlb-statsapi` model | `SimpleHittingSplit` |
| Python attribute | `split.stat.base_on_balls`, `split.stat.hit_by_pitch` |
| Python type | `Optional[int]`, default `None` |

Both fields were already present in every fixture payload this application
uses for testing; nothing upstream needed to change to read them.

## 2. Why both columns are nullable

```sql
base_on_balls INTEGER NULL CHECK (base_on_balls IS NULL OR base_on_balls >= 0)
hit_by_pitch  INTEGER NULL CHECK (hit_by_pitch  IS NULL OR hit_by_pitch  >= 0)
```

Rows written before this migration were ingested without these two fields.
Their real totals are **unknown**, not zero. A `DEFAULT 0` would have written
a plausible number into every historical row and made a fabricated
walk-and-HBP-free game indistinguishable from a real one — the same reasoning
`team-strikeouts-visualization.md` section 4 gives for the `strikeouts`
column, applied twice.

`app/services/team_game_logs.py::_require_nonnegative_stat` is the shared
validator both fields go through when normalizing a fresh MLB response: it
refuses `None`, non-integers, and negative values, naming the `gamePk` and the
field in the error. A genuine `0` — a game with no walks, or no hit batters —
is a real value and is stored as `0`.

### Migration mechanics

Revision `2efdbec9b07e`, following `7f2c4b8e91d3`. Built the same way revision
`94dec6973c80` added `strikeouts`: SQLite cannot attach a `CHECK` constraint
with `ALTER TABLE`, so the table is rebuilt with
`op.batch_alter_table(..., recreate="always")`, and `copy_from=` describes the
pre-revision table (now including the `strikeouts` column and its constraint)
in full, because SQLite reflection does not return `CHECK` constraints.
`tests/test_migrations.py` asserts every existing constraint still fires after
the upgrade, and that `downgrade()` returns the schema to its prior shape
without touching any other column or row.

## 3. Backfill and re-import workflow

There is no separate baserunners import. The existing commands backfill
naturally, because the upsert compares the whole domain record:

```bash
poetry run alembic upgrade head
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

| State | Result |
| --- | --- |
| Before re-import | `base_on_balls` and `hit_by_pitch` are `NULL` for rows imported earlier |
| First re-import | rows counted as **updated**, both columns set to the MLB value |
| Second re-import (unchanged MLB data) | rows counted as **unchanged** |

## 4. Baserunners/Game calculation

For one completed game:

```text
baserunners = hits + base_on_balls + hit_by_pitch
```

calculated in `app/analytics/team_baserunners.py`, never stored as its own
column — the same "derive it, don't persist it" choice every other computed
number in this application follows. The season average is:

```text
season_average = sum(baserunners over stored completed games)
                 / number of stored completed games
```

`TeamBaserunnersSummary.season_average` is the single authoritative value; the
chart's dashed reference line and the "Season Avg" card both read it from
there.

## 5. Rolling-average definition

Identical in shape to the runs and batting-strikeout pages: a **trailing**
mean covering the `window` most recent games including the current one, with
early-season points using every game played so far rather than leaving a gap.
Supported windows are 5, 10, 15, and 30.

## 6. Legacy null-data behavior

If any stored game in the selected team-season has `base_on_balls IS NULL` or
`hit_by_pitch IS NULL`, `build_team_baserunners_analysis` raises
`MissingBaserunnerDataError` and the page renders an actionable state instead
of a chart (HTTP 409) — either missing component is enough, since a partial
sum is not a real baserunners total.

It explicitly does **not**:

- chart unknown games as zero
- drop them and present the remaining games as a complete season
- calculate an average over a silently reduced set of games

The rendered guidance names the count of affected games and the exact
re-import command for the **selected** team and season. The hits, batting
strikeouts, and runs pages are unaffected, because none of them reads these
two columns.

## 7. MLB Baserunners/Game context

Two rules must both hold, mirroring the batting strikeouts precedent exactly:

**Rule 1 — complete league-season coverage.**
`supports_league_wide_baserunners_average` delegates to the unchanged
Milestone 5 rule: the latest league-wide refresh reached `COMPLETE`, covering
every discovered team. Never a row count, a team count, or a game count.

**Rule 2 — every counted record has known walk and hit-by-pitch totals.**
`build_league_baserunners_context` raises `MissingLeagueBaserunnerDataError`
when any stored record for the season is missing either total. It never drops
those rows, reads `NULL` as `0`, or averages only the known subset.

The formula is game-weighted, exactly as every other league average in this
application is:

```text
MLB Baserunners/Game = total baserunners across all persisted team-game
                       records for the season
                       ─────────────────────────────────────────────────
                       total persisted team-game records for that season
```

### Behavior in each state

| State | MLB average | Chart | `vs MLB` card |
| --- | --- | --- | --- |
| `COMPLETE` + every record has both totals | calculated and shown | MLB reference line added | signed number |
| `INCOMPLETE` / `RUNNING` / no coverage record | not calculated | no MLB line | `—` |
| `COMPLETE` + any league record missing a total | not calculated | no MLB line | `—`, with backfill guidance |
| selected team has missing totals | not calculated | no chart (HTTP 409) | not rendered |

### The `vs MLB` difference

```text
difference_vs_mlb = team Baserunners/Game - MLB Baserunners/Game
```

Descriptive subtraction only: not normalized, not ranked, not tested for
significance, and neither direction is labelled good or bad. Putting more
runners on base than MLB overall is not automatically better than a club
doing other things well.

## 8. Chart

| # | Name | Style | Purpose |
| --- | --- | --- | --- |
| 1 | `Game Baserunners` | thin grey line, open markers | game-to-game variation |
| 2 | `{window}-Game Average` | thick teal line | the trend |
| 3 | `Team Season Average` | dashed navy horizontal line | the team's own level |
| 4 | `MLB Average` | dotted amber horizontal line | MLB overall, when both rules hold |

Only one horizontal line is annotated with its value — the MLB line when it is
drawn, the team line otherwise — matching every other metric chart.

## 9. Summary cards

```text
Recent 15-Game Avg   Season Avg   vs MLB   Games Played
       9.10             8.75       +0.35        40
```

Without a comparison the third card reads `—` / "Comparison unavailable"; it
never shows `0.00`, which is a real value meaning the team matched MLB
exactly.

## 10. What Baserunners/Game does not tell you

**It is a count, not a rate.** It is not the same statistic as on-base
percentage, which divides the same numerator by plate appearances. Plate
appearances are not persisted, so OBP is not estimated here — the same
deferral `team-strikeouts-visualization.md` section 12 explains for K%, for
the same reason.

**No context is adjusted.** Opposing pitching, park, and sequencing all count
the same as any other game.

**Every number describes stored games.** For an in-progress season or a
partial import, both the team and MLB averages move as more games are
imported.

## 11. Where each responsibility lives

```text
app/schemas/analytics.py            TeamBaserunnersPoint, TeamBaserunnersSummary,
                                     TeamBaserunnersAnalysis, LeagueBaserunnersContext,
                                     TeamBaserunnersLeagueComparison
app/analytics/team_baserunners.py   the team trend, the season average, the formula
app/analytics/league_baserunners.py the MLB average, the coverage rule, the comparison
app/web/charts.py                   build_team_baserunners_figure
app/web/formatting.py               build_baserunners_summary_cards, the wording
app/web/routes.py                   the thin /baserunners route
app/web/templates/baserunners.html  the page
alembic/versions/2efdbec9b07e_*.py  the migration
```

Pure analytics, no FastAPI, no Jinja, no SQLAlchemy, no Plotly, no MLB client,
no network — the same shape every other analytics module in this application
follows, and for the same reason: hits, batting strikeouts, runs, and
baserunners are not interchangeable statistics, and a shared
`GenericMetricAnalysis` would have to encode which labels, nullability rules,
and error states belong to which one.
