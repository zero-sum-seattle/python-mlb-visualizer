# Team batting strikeout visualization

This document describes how Milestone 3.5 extends the Milestone 3 team hits
page with a second metric: a team's **batting strikeouts** per game, with a
trailing rolling average.

It answers one question:

> Is this team's offense striking out more or less frequently per game as the
> season progresses?

Issue #23 later added a second question to the same page, documented in
section 13:

> How many times per game does this team's offense strike out compared with
> MLB overall?

Throughout this document and the UI, "strikeouts" means **batting** strikeouts —
times the team's own hitters struck out. Strikeouts recorded by the team's
pitchers are a different statistic and are not in scope.

## 1. Source data field

Batting strikeouts come from the team hitting `gameLog` split that Milestone 1
already retrieves.

| Layer | Name |
| --- | --- |
| MLB Stats API JSON | `strikeOuts` |
| `python-mlb-statsapi` model | `SimpleHittingSplit` |
| Python attribute | `split.stat.strikeouts` |
| Python type | `Optional[int]`, default `None` |

The attribute was verified against the installed package (`python-mlb-statsapi`
0.8.0) rather than guessed:

```python
from mlbstatsapi.models.stats.hitting import SimpleHittingSplit

SimpleHittingSplit.model_fields["strikeouts"].alias  # -> "strikeOuts"
```

`strike_outs` **does not exist** on the model; only `strikeouts` does. The
alias is what maps it to MLB's raw `strikeOuts` key.

## 2. Why no new MLB request was added

The team-season strategy is unchanged at three requests:

```text
get_team(team_id, season=...)            → the team's name for that season
get_team_stats(..., stats=["gameLog"],   → one split per played game:
               groups=["hitting"])          hits, runs, AND strikeOuts
get_schedule(...)                        → status, opponent, doubleheader, innings
```

Hits, runs, and batting strikeouts all arrive in the **same** hitting game-log
split. Strikeouts were already being fetched and discarded; Milestone 3.5 only
starts reading the field. Adding a request for them would have paid for data
already in hand.

`tests/test_team_season_ingestion.py::test_import_makes_exactly_three_mlb_requests`
pins this so a future change cannot quietly add a fourth call.

## 3. Domain model and normalization

`TeamGameBattingLine` gains:

```python
strikeouts: int | None = Field(default=None, ge=0)
```

The two layers apply deliberately different rules:

| Layer | Rule |
| --- | --- |
| Domain schema | `None` is permitted, so rows persisted before this milestone can be loaded |
| MLB normalization | `None` is **refused**; a fresh response must carry a real count |

`app/services/team_game_logs.py::_require_batting_strikeouts` raises
`TeamGameDataError` naming the `gamePk` and the field when the value is absent,
JSON `null`, not an integer, or negative. Nothing is substituted:

- a missing total never becomes `0`
- a game with missing data is never silently dropped from the import

A genuine `0` — a game in which nobody struck out — is a real value and is
stored as `0`.

## 4. Why the column is nullable

```sql
strikeouts INTEGER NULL
CHECK (strikeouts IS NULL OR strikeouts >= 0)
```

Rows written before this milestone were ingested without strikeouts. Their real
totals are **unknown**, not zero. A `DEFAULT 0` would have written a plausible
number into every historical row and made a fabricated season indistinguishable
from a real one.

So the column is nullable with no default, and `NULL` means exactly "this game's
batting strikeout total was never collected". The check constraint allows `NULL`
while rejecting negatives, so an unknown stays honest and a known value stays
sane.

### Migration mechanics

Revision `94dec6973c80`, following `166b6424e4f9`.

SQLite cannot attach a `CHECK` constraint with `ALTER TABLE`, so the table is
rebuilt with `op.batch_alter_table(..., recreate="always")`. The migration
passes `copy_from=` with the pre-revision table written out **in full**, because
the SQLite dialect does not reflect `CHECK` constraints — a rebuild driven by
reflection alone would have silently dropped the `hits >= 0`, `runs >= 0`, and
`home_away IN (...)` rules. `tests/test_migrations.py` asserts those constraints
still fire after the upgrade.

The migration preserves every existing row and the query index. `downgrade()`
drops the column and its constraint, returning to the pre-3.5 schema; recorded
strikeout values are lost, because the older schema has nowhere to keep them.

## 5. Backfill and re-import workflow

There is no separate strikeout import. The existing command backfills naturally,
because the upsert compares the whole domain record:

```bash
poetry run alembic upgrade head
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

| State | Result |
| --- | --- |
| Before re-import | `strikeouts` is `NULL` for rows imported earlier |
| First re-import | rows counted as **updated**, `strikeouts` set to the MLB value |
| Second re-import (unchanged MLB data) | rows counted as **unchanged** |

Idempotency is unchanged from Milestone 2: strikeouts simply join the set of
fields the stored-vs-incoming comparison covers.

## 6. K/Game calculation

For one completed game, the plotted value is the raw count of batting
strikeouts. No denominator is applied.

The season average is:

```text
season_average = sum(strikeouts over stored completed games)
                 / number of stored completed games
```

`TeamStrikeoutsSummary.season_average` is the single authoritative value; the
chart's dashed reference line and the "Season Avg" card both read it from there
rather than recomputing it.

## 7. Rolling-average definition

Identical in shape to the hits page. The average is **trailing**, never
centered. For game N it covers the `window` most recent games *including* N:

```text
window = 15

game 1   → average of game 1
game 2   → average of games 1-2
...
game 15  → average of games 1-15
game 16  → average of games 2-16
```

Early-season points use every game played so far rather than leaving a gap. A
centered average would let later games influence an earlier point and
misrepresent what the offense looked like at the time.

Supported windows are 5, 10, 15, and 30. Full floating-point precision is kept
in the analytics layer; rounding happens only in `app/web/formatting.py` and in
the chart's hover template.

## 8. Prior-window comparison

> **Since issue #23 this comparison is calculated but no longer displayed.**
> The `vs Prior {window}` card was replaced by the `vs MLB` card so the row
> keeps four cards instead of growing a fifth. `prior_window_average` and
> `change_vs_prior_window` are still calculated, still validated, and still
> tested; the chart's rolling average shows the same trend the card described.
> The definition below is unchanged and still governs those fields.

The prior-window comparison measures the current complete window against the
**immediately preceding equal-sized complete window**:

```text
games_played >= 2 * window   → prior window = games [-2*window : -window]
games_played <  2 * window   → both fields are None
```

Two complete windows are required. Comparing a full window against a partial one
would report a change caused by sample size rather than by hitters.

### Direction is not labelled

A positive change means *more* batting strikeouts. It is **not** styled as good
or bad, and no positive/negative colouring is applied. Whether more strikeouts
matter depends on the question being asked and on what else the offense is
doing — a team may strike out more while also hitting for more power. The page
states the direction and leaves the judgement to the reader.

## 9. Page architecture

The dependency direction is the repository's existing one:

```text
scripts/import_team_season.py  ──►  MLB Stats API  ──►  SQLite   (offline, CLI)

GET /strikeouts?team_id=136&season=2025&window=15
        │
        ├─ list_available_team_seasons(session)        ──► selector options
        ├─ list_team_season(session, ...)              ──► list[TeamGameBattingLine]
        ├─ build_team_strikeouts_analysis(games, ...)  ──► TeamStrikeoutsAnalysis
        │
        ├─ get_league_season_ingestion(session, season=...)        ──► coverage
        ├─ supports_league_wide_strikeout_average(coverage)        ──► rule 1
        ├─ list_league_season(session, season=...)     ──► every stored record
        ├─ build_league_strikeouts_context(records)    ──► rule 2 + the formula
        ├─ compare_team_strikeouts_to_league(analysis, context)
        │
        ├─ build_team_strikeouts_figure(analysis, comparison)  ──► plotly Figure
        └─ strikeouts.html                             ──► HTML
```

The MLB-wide steps are the ones issue #23 added; section 13 describes them.

`/` remains the hits page and its URL is unchanged. Like `/`, `/strikeouts`
reads persisted SQLite only and never calls MLB during a browser request.

`app/analytics/team_strikeouts.py` imports Pydantic schemas and nothing else
from the application — no SQLAlchemy, FastAPI, Jinja, Plotly, or MLB client
models.

### Why hits and strikeouts are not shared code

The two metrics have the same visualization shape, and that was deliberately
**not** turned into a shared metric abstraction. `AGENTS.md` asks for a small
amount of obvious duplication over a premature abstraction, and these two
statistics are not interchangeable: they mean different things, they read
different source fields, one tolerates `NULL` history and the other does not,
and their labels must stay distinct. A `GenericMetricEngine` would have to
encode all of that as configuration.

What *is* shared is genuinely shared infrastructure that already existed: the
team-season selector (`app/web/selection.py`), the selector form partial
(`_selector_form.html`), figure rendering, and date/matchup formatting.

## 10. Legacy null-data behavior

If any stored game in the selected team-season has `strikeouts IS NULL`,
`build_team_strikeouts_analysis` raises `MissingStrikeoutDataError` and the page
renders an actionable state instead of a chart (HTTP 409).

It explicitly does **not**:

- chart unknown games as zero
- drop them and present the remaining games as a complete season
- calculate an average over a silently reduced set of games

The rendered guidance names the count of affected games and the exact command
for the **selected** team and season, not a hardcoded example:

```bash
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

One unknown game is enough to trigger this: a partially backfilled season cannot
be described honestly as either complete or incomplete-but-fine.

The hits page is unaffected. `/` continues to work normally against rows with
null strikeouts, because hits were never missing.

## 11. Statistical limitations

**K/Game is a count, not a rate.** This is the most important limitation of the
page and it is stated on the page itself.

Games do not contain equal numbers of opportunities. Extra-inning games give
hitters more plate appearances; games shortened by weather give fewer; a long
inning inflates a single game's totals. A game with more opportunities can show
more strikeouts without hitters striking out any *more often*.

So K/Game legitimately supports:

- how many times this team's hitters struck out in each stored completed game
- whether that per-game count is trending up or down across the stored season

It does not support:

- how often hitters struck out *per opportunity*
- comparison against teams with different game-length profiles
- any claim about league context **unless** the season holds complete
  league-season coverage and a known strikeout total on every stored record,
  which is exactly what section 13 requires before an MLB average is shown

Every number describes the **completed games currently stored**, which may be a
season in progress or a partial import. The footer's "Data through" date shows
how current the stored games are.

## 12. Why K% is deferred

Strikeout rate:

```text
K% = strikeouts / plate appearances
```

is the opportunity-adjusted measure, and it is the better answer to "is this
offense striking out more *often*". It is deferred because **plate appearances
are not persisted**.

The hitting game-log split does carry `plateAppearances`, so the data is
reachable — but persisting it is a schema change, a migration, and a re-import
of its own, with the same nullable-history problem this milestone just solved
for strikeouts. Bundling it here would have widened Milestone 3.5 beyond its one
question.

K% is **not** estimated or approximated in the meantime. Dividing by innings, by
games, or by at-bats would produce a number that looks like K% and is not, which
is worse than not showing it. The page says plainly that K% is a better measure
for some questions and that it is deferred.

## 13. MLB batting K/Game context (issue #23)

Issue #23 gives this page the same MLB-wide context the hits page received in
Milestone 5, answering:

> How many times per game does this team's offense strike out compared with MLB
> overall?

This is **batting** strikeouts per game. It is not pitching strikeouts, not K%,
not a plate-appearance rate, and not a measure where a higher value is
automatically better or worse.

### 13.1 The formula

```text
MLB batting K/Game = total batting strikeouts across all persisted team-game
                     records for the selected season
                     ────────────────────────────────────────────────────────
                     total persisted team-game records for that season
```

Implemented in `app/analytics/league_strikeouts.py::build_league_strikeouts_context`,
returning a `LeagueStrikeoutsContext`:

| Field | Meaning |
| --- | --- |
| `season` | The season every counted record belongs to |
| `teams_represented` | Distinct `team_id`s with at least one stored game |
| `team_game_records` | Team-game batting lines counted |
| `total_strikeouts` | Batting strikeouts summed across those records |
| `strikeouts_per_game` | `total_strikeouts / team_game_records` |

The schema re-derives `strikeouts_per_game` from the two totals in a validator,
so a context holding an average nothing in it produced cannot be constructed.

One real MLB game produces **two** team batting lines, one per club, so the
denominator counts team-game records, not games. A full 30-team, 162-game
season is about 4,860 of them.

### 13.2 Why it is game-weighted

Every stored team-game record counts once, so a club that has played more games
contributes proportionally more. That is what "MLB overall" means here.

The alternative — averaging each club's own K/Game — answers a different
question and gives a different number:

```text
Team A: 10 K, 8 K   (2 games)
Team B:  6 K        (1 game)

game-weighted (implemented) : (10 + 8 + 6) / 3        = 8.0
mean of team averages       : ((10 + 8) / 2 + 6) / 2  = 7.5
```

Unequal game counts are the normal case for an in-progress season, and the
unweighted mean would silently give a club with 40 games the same weight as one
with 162. `test_unequal_team_game_counts_are_weighted_by_games_played` exists to
catch exactly that mistake.

Nothing assumes thirty clubs, 162 games, or 4,860 records. The denominator is
always counted from the records actually stored.

### 13.3 Two rules must both hold

Unlike hits, an MLB batting K/Game needs **two** conditions, because strikeouts
have a `NULL` history that hits never had.

**Rule 1 — complete league-season coverage.**
`supports_league_wide_strikeout_average` reuses the Milestone 5 rule unchanged:

```python
coverage is not None
and coverage.status is LeagueSeasonIngestionStatus.COMPLETE
```

Completeness is never inferred from a row count, a team count, thirty team ids,
162 games, or 4,860 records. `COMPLETE` describes the **latest league-wide
refresh** covering every discovered team — not that the baseball season is over.
The rule is deliberately not re-implemented here; two copies could drift and let
one page call a season MLB-wide while the other did not.

**Rule 2 — every counted record has a known strikeout total.**
Rows imported before Milestone 3.5 hold `strikeouts IS NULL`, meaning unknown.
`build_league_strikeouts_context` raises `MissingLeagueStrikeoutDataError` when
any record for the season is `NULL`. It does **not**:

- drop those rows and label the remainder MLB-wide
- read `NULL` as `0`
- average only the non-`NULL` subset

Complete coverage says every team was refreshed; it says nothing about whether
older rows were rewritten with strikeout totals, so a season can satisfy rule 1
and fail rule 2.

### 13.4 Behavior in each state

| State | MLB average | Chart | `vs MLB` card | Page |
| --- | --- | --- | --- | --- |
| `COMPLETE` + every record has a total | calculated and shown | MLB reference line added | signed number | works |
| `INCOMPLETE` | not calculated | no MLB line | `—` | works |
| `RUNNING` | not calculated | no MLB line | `—` | works |
| no coverage record | not calculated | no MLB line | `—` | works |
| `COMPLETE` + any league record is `NULL` | not calculated | no MLB line | `—` | works, with backfill guidance |
| selected team has `NULL` totals | not calculated | no chart (HTTP 409) | not rendered | existing re-import guidance, unchanged |

`RUNNING` is treated exactly like `INCOMPLETE`: a run that never finished left
its coverage unknown.

The first four unavailable states read:

```text
MLB comparison unavailable. A complete league-season import is required before
an MLB-wide batting strikeout average can be shown.
```

The `NULL`-records state is a different problem with a different remedy, so it
gets its own wording naming the gap and the league import:

```text
MLB comparison unavailable. 12 of the 4,860 team-game records stored for 2025
have no batting strikeout total, because they were imported before batting
strikeouts were persisted. They are not counted as zero and the rest are not
presented as MLB overall. Re-import the league season to backfill them:
poetry run python scripts/import_league_season.py --season 2025
```

The selected team's own behavior is untouched: a team-season with any `NULL`
total still returns HTTP 409 with the team re-import guidance from section 10,
rather than a partial analysis.

None of these states breaks an otherwise valid page. Everything Milestone 3.5
rendered still renders.

### 13.5 In-progress seasons

`COMPLETE` describes the refresh, not the season. Both of these are valid:

| Season | Coverage | Situation | Comparison |
| --- | --- | --- | --- |
| 2025 | `COMPLETE` | finished historical season | available |
| 2026 | `COMPLETE` | still being played, well under a full season | available |

The 2026 case works precisely because coverage is not a row-count rule. Forty
stored team-game records with complete coverage qualify exactly as 4,860 would.
What the MLB average then describes is MLB-wide batting strikeouts across the
**completed games currently stored**, which is what the page says in words.

### 13.6 The `vs MLB` difference

```text
difference_vs_mlb = team batting strikeouts per game
                    - MLB batting strikeouts per game
```

```text
Team K/Game: 7.80    MLB K/Game: 8.40    vs MLB: -0.60
```

A **negative** value means the team's hitters struck out fewer times per game
than MLB overall; a **positive** value means more.

That is all it means. It is plain subtraction of two descriptive averages: not
normalized, not ranked, not a percentile, not tested for significance, and not a
claim about cause. Neither direction is labelled good or bad, and the card
carries no positive/negative styling. Striking out less often is not
automatically better hitting — a club may strike out more while also hitting for
more power — so the page states the direction and leaves the judgement to the
reader.

The team side is `TeamStrikeoutsAnalysis.summary.season_average`, the same value
the chart's team reference line and the `Season Avg` card read, so the page
cannot show two different team averages.

`K/Game is still not K%.` MLB context does not adjust for opportunity; section
11 and section 12 still apply, and section 12's reasons for deferring K% are
unchanged.

### 13.7 Chart

| # | Name | Style | Purpose |
| --- | --- | --- | --- |
| 1 | `Game Strikeouts` | thin grey line, open markers | game-to-game variation |
| 2 | `{window}-Game Average` | thick teal line | the trend |
| 3 | `Team Season Average` | dashed navy horizontal line | the team's own level |
| 4 | `MLB Average` | dotted amber horizontal line | MLB overall, when both rules hold |

The team reference line was renamed from `Season Average` to
`Team Season Average`, matching the hits chart: with two horizontal lines on one
chart, "Season Average" no longer says whose. The two lines differ in both hue
and dash pattern so they stay apart in greyscale and for a colour-blind reader.
Both are straight lines with `hoverinfo: skip`, no spline is used anywhere, and
no existing trace changed meaning. Without a comparison the figure is exactly
the three-trace chart Milestone 3.5 built.

Only one horizontal line is annotated with its value — the MLB line when it is
drawn, the team line otherwise — because the two can sit a tenth of a strikeout
apart, where two labels would collide.

### 13.8 Summary cards

```text
Recent 15-Game Avg   Season Avg   vs MLB   Games Played
       8.53             7.80       -0.60        40
```

The `vs MLB` card replaced the `vs Prior 15` card rather than being added beside
it, so the row keeps four cards on the existing grid (see section 8). Without a
comparison it reads `—` / "Comparison unavailable"; it never shows `0.00` for
unavailable, because `0.00` is a real value meaning the team matched MLB
exactly. Rounding to two decimals happens only in `app/web/formatting.py`.

### 13.9 The browser stays database-only

Normal browser requests remain database-only. The comparison reads two
repository queries — the recorded coverage state and the season's stored records
— and nothing else. No MLB request was added to `/strikeouts` or to any other
page request, and no new persistence column was needed.

Getting league data into the database is still an explicit CLI step:

```bash
poetry run python scripts/import_league_season.py --season 2025
```

Tests assert this directly by making `requests.Session.request`,
`mlbstatsapi.Mlb.__init__`, `get_team_game_batting_lines`, `discover_mlb_teams`,
and `ingest_league_season` raise, then loading the page with complete coverage
and asserting the MLB line is still drawn.

### 13.10 Where each responsibility lives

| Layer | Holds |
| --- | --- |
| `app/database/repositories.py` | `list_league_season` — persistence and querying only |
| `app/analytics/league_strikeouts.py` | the formula, the difference, the `NULL` rule |
| `app/analytics/league_hitting.py` | the shared `COMPLETE` coverage rule |
| `app/schemas/analytics.py` | `LeagueStrikeoutsContext`, `TeamStrikeoutsLeagueComparison` and their invariants |
| `app/web/routes.py` | wiring, in `_load_league_strikeouts_comparison` |
| `app/web/charts.py` | the MLB reference trace |
| `app/web/formatting.py` | rounding and the wording |

`app/analytics/league_strikeouts.py` is a focused module, not a generic metric
framework. Hits and batting strikeouts keep separate league implementations for
the same reasons section 9 gives for their team implementations, and issue #23
added one more: hits have no `NULL` history and strikeouts do, so the two
league calculations do not have the same preconditions.

### 13.11 Limitations

- The comparison covers hits and batting strikeouts. Runs have no league
  context yet.
- `teams_represented` counts clubs with stored games for the season. Under
  complete coverage that is the league; it is reported for transparency, not
  used as a completeness rule.
- Coverage is per season. Complete 2025 coverage says nothing about 2026, and
  the page treats them independently.
- The `NULL` check scans the season's stored records on each page request.
  A full season is roughly 4,860 domain objects, which is immediate; if that
  ever stops being true, the change belongs with the measurement that motivated
  it.
- No live MLB validation was performed for this change; every automated test is
  offline, seeding rows and coverage states directly.
