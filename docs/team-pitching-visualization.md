# Team pitching visualization

The `/pitching` page charts how many pitches a team's staff threw per game, with
ERA, WHIP, K/9 and BB/9 as the rate statistics those pitches produced.

It is the first feature in this application that is not free. Everything before
it read the hitting `gameLog` split already being fetched, or derived new
figures from rows already stored. Pitching is a **separate MLB stat group in a
separate request**, landing in a **new table**.

## 1. Why a separate table

`team_game_pitching_lines` rather than more columns on `team_game_batting_lines`:

- The two are different stat groups from different requests. Half of each one's
  columns would be meaningless on the other row.
- A season imported before pitching existed simply has **no pitching rows**,
  rather than a batting row full of nulls. That means nothing here needs the
  nullable-until-backfilled treatment the batting strikeout and baserunner
  columns required, and every pitching column is `NOT NULL`.

Game context (opponent, status, game number, scheduled innings) is duplicated
onto the pitching row rather than joined. It comes from the same schedule
request at no extra cost, and it keeps a pitching row readable on its own.

## 2. Innings are stored as outs

This is the single most important decision on the page.

MLB returns `inningsPitched` as a **string in baseball notation**:

```text
inningsPitched = '10.2'   means ten and two-thirds innings
                          NOT 10.2 innings
```

Parsing that as a decimal silently corrupts every rate derived from it, and the
result stays plausible enough to go unnoticed. The same split carries `outs` as
an exact integer, so that is what the column holds:

```text
outs 32  ->  32 / 3 = 10.667 innings
         ->  ER 9 * 27 / 32 = 7.59
```

Checked against the API's own published value for that game, `era = '7.59'`.

`innings_pitched` and `innings_pitched_display` are properties on the domain
model. The first is a true fraction for calculation; the second reconstructs the
`10.2` form for display. Neither is stored, and the display string is never used
in a calculation — `tests/test_web_pitching.py` asserts that reading `'10.2'` as
a decimal would give 7.90 rather than 7.59.

## 3. Only components are stored

The table holds `outs`, `hits_allowed`, `runs_allowed`, `earned_runs`,
`pitching_base_on_balls`, `pitching_strikeouts`, `home_runs_allowed`,
`batters_faced`, `number_of_pitches`, and `strikes`.

ERA, WHIP, K/9 and BB/9 are **not** columns. They are derived on read, so a
stored rate cannot drift from the components it came from.

Balls are not stored either. MLB leaves that field empty on the team game log
even though it populates `strikes`, and balls are `number_of_pitches - strikes`,
so a column would only invite the two to disagree.

The walk and strikeout columns carry a `pitching_` prefix. The batting table has
identically named columns counting the opposite thing, and the prefix makes a
query unambiguous about which sense it means.

### Definitional constraints

Three check constraints encode facts that cannot be otherwise:

| Constraint | Why |
| --- | --- |
| `earned_runs <= runs_allowed` | An earned run is a run |
| `home_runs_allowed <= hits_allowed` | A home run is a hit |
| `batters_faced >= outs` | Every out is recorded against a batter faced |
| `strikes <= number_of_pitches` | A strike is a pitch |

All were verified against 648 real 2025 team-games across four clubs before
being encoded, rather than assumed.

## 4. Counts and rates aggregate differently

Every previous page here charts a **count** per game — hits, runs, batting
strikeouts, baserunners — and the mean of the per-game values is the right
season figure.

Pitches per game is also a count, so it follows that rule.

ERA, WHIP, K/9 and BB/9 are **rates**: ratios of two quantities that both vary
game to game. A rate over several games is the ratio of the summed totals, not
the mean of the per-game ratios. For the 2025 Mariners:

```text
season ERA (correct)   629 ER * 27 / 4388 outs   =  3.870
mean of 162 game ERAs                            =  3.965
```

The 0.094 gap would match no published source. The error grows with how uneven
the innings are, which is why the tests build seasons with unequal outs — with a
regulation nine innings in every game the two methods agree exactly, so a test
built that way would pass against a wrong implementation.

The same rule governs:

- **The rolling window**, which accumulates earned runs and outs and divides
  once per position rather than smoothing the game ERAs the markers show.
- **The league context**, whose rates are outs-weighted rather than
  game-weighted, so a club with more innings contributes proportionally more.

`_aggregate_rates` in `app/analytics/team_pitching.py` is the single place this
happens. Nothing in that module averages a rate.

## 5. Ingestion costs one extra request, not three

`get_team_game_lines` fetches both stat groups while sharing the team lookup and
the season schedule:

```text
get_team           1
get_schedule       1
get_team_stats     2   (hitting, then pitching)
                  ---
                   4   not 6
```

Over a 30-club league import that is 60 requests saved. Both game logs are
validated against the same schedule index.

Both persist in the **same transaction**, so a team-season can never end up with
batting rows stored and pitching rows missing because the second write failed —
a state that would look on this page exactly like a season imported before
pitching existed.

`ingest_team_season(..., include_pitching=False)` drops back to the original
three requests for callers that want batting only.

### The schedule as an independent check

The two logs are validated against **opposite sides of the score**. A hitting
split's runs must equal the selected team's scheduled score; a pitching split's
runs are runs *allowed*, so they must equal the opponent's. That makes the
schedule an independent check on which stat group a split really belongs to,
and it passed on all 162 games of the 2025 Mariners season.

## 6. Chart

Pitches per game, drawn like the other count-based pages: open markers per game,
a rolling trailing mean, and a dashed season average.

The y axis uses `rangemode="normal"` rather than `"tozero"`. A team throws
roughly 100 pitches at minimum, so anchoring at zero would waste the bottom
third of the plot.

There is **no MLB reference line**. A league-wide pitches-per-game figure needs
every club's pitching lines imported, which is a considerably larger import than
the batting-only one most stored seasons currently have. Rather than promise a
comparison that would usually be missing, the page shows none.

The league comparison machinery (`app/analytics/league_pitching.py`) is built and
tested, and `_load_league_pitching_comparison` is wired into the route — it
simply has no line on this chart yet. Its sign convention is worth noting when
it does surface: a **negative** ERA difference is the better direction, the
opposite of every other comparison in the application.

## 7. Missing-data state

A team-season with batting rows but no pitching rows returns **409** and names
the team re-import as the fix.

That differs from `/run-differential`, which asks for a *league* import: there
the team's own rows are fine and the opponents' rows are what is absent. Here
the team's own pitching was never fetched, so re-importing the team is exactly
the remedy.

There is no partially-known state to report. Every pitching column is `NOT
NULL`, so the rows either exist or they do not.

## 8. Where each responsibility lives

| Concern | Location |
| --- | --- |
| Fetching and normalizing both game logs | `app/services/team_game_logs.py` |
| Pitching domain model, outs/innings conversion | `app/schemas/games.py` |
| Table, constraints, ORM mapping | `app/database/models.py` |
| Migration | `alembic/versions/27a202039134_*.py` |
| Reading and upserting pitching rows | `app/database/repositories.py` |
| Rates, rolling ERA, pitch counts | `app/analytics/team_pitching.py` |
| MLB-wide pitching context | `app/analytics/league_pitching.py` |
| Analysis models and their consistency guards | `app/schemas/analytics.py` |
| Figure construction | `app/web/charts.py` |
| Cards, innings formatting, notes | `app/web/formatting.py` |
| Request handling and page state | `app/web/routes.py` (`/pitching`) |
| Page markup | `app/web/templates/pitching.html` |
