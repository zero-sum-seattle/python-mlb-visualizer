# Team batting strikeout visualization

This document describes how Milestone 3.5 extends the Milestone 3 team hits
page with a second metric: a team's **batting strikeouts** per game, with a
trailing rolling average.

It answers one question:

> Is this team's offense striking out more or less frequently per game as the
> season progresses?

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

The "vs Prior {window}" card compares the current complete window against the
**immediately preceding equal-sized complete window**:

```text
games_played >= 2 * window   → prior window = games [-2*window : -window]
games_played <  2 * window   → None, rendered as "—" / "Not enough games"
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
        ├─ build_team_strikeouts_figure(analysis)      ──► plotly Figure
        └─ strikeouts.html                             ──► HTML
```

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
- any claim about league context — the database holds only explicitly imported
  team-seasons, so a league average computed from it would describe whichever
  teams happen to be stored (the same reasoning as the hits page)

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
