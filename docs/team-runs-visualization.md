# Team runs per game visualization

This document describes the `/runs` page added by issue #24: what it measures,
how the two averages on it are calculated, why the MLB average uses the
denominator it does, and when the application is allowed to call that average
"MLB" at all.

It answers one question:

> How many runs per game is this team scoring, how is that changing over the
> season, and how does its season average compare with MLB overall?

Related documents:

- [team-hits-visualization.md](team-hits-visualization.md) — the first metric
  page, whose layout and rolling-average behavior this one reuses
- [team-strikeouts-visualization.md](team-strikeouts-visualization.md) — the
  second metric page
- [team-vs-mlb-comparison.md](team-vs-mlb-comparison.md) — where the
  game-weighted league definition and the coverage rule were established
- [league-season-ingestion.md](league-season-ingestion.md) — the coverage state
  this page reads

Throughout this document and the UI, "runs" means **runs scored** by the
selected team. Runs allowed and run differential are different statistics and
are not calculated anywhere on this page.

## 1. Source data field

No new MLB request, no new column, and no migration were added for this page.

`TeamGameBattingLine.runs` has been persisted since Milestone 2, as a required
non-negative integer:

| Layer | Definition |
| --- | --- |
| Domain schema | `runs: int = Field(ge=0, ...)` |
| ORM column | `runs: Mapped[int] = mapped_column(Integer, nullable=False)` |
| Table constraint | `CHECK (runs >= 0)`, named `runs_nonnegative` |

Both migrations that have ever created the table (`166b6424e4f9` and the
`94dec6973c80` rebuild that added batting strikeouts) declare the column
`nullable=False` with that check constraint. There is therefore no historical
row holding an unknown run total, and this page needs none of the
nullable-data machinery batting strikeouts required.

That difference is worth stating plainly, because the two metrics look alike
and their data histories are not:

| | Hits | Batting strikeouts | Runs |
| --- | --- | --- | --- |
| Nullable on stored rows | no | **yes** (pre-3.5 rows) | no |
| Page can hit a backfill state | no | **yes** (HTTP 409) | no |
| League context can be refused for unknown values | no | **yes** | no |

## 2. Team Runs/Game formula

```text
Team Runs/Game = total runs scored by the team across its stored completed games
                 ──────────────────────────────────────────────────────────────
                 number of stored completed team-game records for that season
```

Implemented in `app/analytics/team_runs.py::build_team_runs_analysis`, which
returns a `TeamRunsAnalysis` carrying a `TeamRunsSummary`.

`TeamRunsSummary.season_average` is the **single authoritative** team average.
Three places on the page display it, and all three read this one field:

1. the **Season Avg** summary card
2. the chart's dashed **Team Season Average** reference line
3. the team side of the MLB comparison

`compare_team_runs_to_league` takes `analysis.summary.season_average` rather
than recalculating from the points, so the card and the chart cannot drift
apart. `TeamRunsAnalysis` also validates that `summary.games_played` equals the
number of chart points, so a summary describing a different set of games than
the chart cannot be constructed.

### What the denominator is

Stored completed team-game records for the selected team-season — not 162, and
not a scheduled-game count. A season still being played, or a partial import,
divides by the games actually held. The page says "the completed games
currently stored for this season" rather than implying a finished season, and
the footer carries a "Data through" date.

## 3. Rolling average

Unchanged from the hits and batting strikeout pages, deliberately. Issue #24
introduced no new smoothing method.

Games are ordered by `(game_date, game_number, game_pk)`, so both halves of a
doubleheader keep their real sequence and a tie on date and game number still
resolves deterministically. The chart's x axis is `season_game_number`, a
continuous 1-based index over that order.

For each game, the value plotted is the **trailing** mean of the `window` most
recent games up to and including that game:

```text
rolling_average[i] = mean(runs[max(0, i - window + 1) .. i])
```

Early-season points use every game played so far rather than disappearing until
`window` games exist, so game 1 of a season is its own average. The rolling
line joins calculated points with straight segments: **no spline
interpolation**, because a spline would draw averages between games that were
never calculated.

## 4. MLB Runs/Game formula

```text
MLB Runs/Game = total runs across all persisted team-game records for the season
                ───────────────────────────────────────────────────────────────
                total persisted team-game records for that season
```

Implemented in `app/analytics/league_runs.py::build_league_runs_context`,
returning a `LeagueRunsContext`:

| Field | Meaning |
| --- | --- |
| `season` | The season every counted record belongs to |
| `teams_represented` | Distinct `team_id`s with at least one stored game |
| `team_game_records` | Team-game batting lines counted |
| `total_runs` | Runs summed across those records |
| `runs_per_game` | `total_runs / team_game_records` |

`LeagueRunsContext` re-derives `runs_per_game` from the two totals in a
validator, so a context holding an average nothing in it produced cannot be
constructed — by this code or by any future caller.

### Why the denominator is team-game records

One real MLB game produces **two** team batting lines, one per club. The
denominator counts those lines, not games. Both sides of the comparison are
therefore per-team per-game numbers, which is what makes the subtraction in
section 6 meaningful: a team's own average is also per-team per-game.

Because each club's runs appear once, as that club's own total and never as its
opponent's, nothing here implies a run differential.

### Why it is game-weighted, and not the mean of team averages

Every stored team-game record counts once, so a club that has played more games
contributes proportionally more. That is what "MLB overall" means.

The worked example from the issue:

```text
Team A: 5 runs, 3 runs
Team B: 2 runs

game-weighted     : (5 + 3 + 2) / 3     == 3.333...   <- correct
mean of averages  : ((5 + 3) / 2 + 2)/2 == 3.0        <- wrong
```

The two agree only when every club has played the same number of games. During
a season they never do — off days, doubleheaders, and postponements guarantee
it — and a partially imported season can differ far more. Averaging each club's
own average would silently give a team with 40 games the same weight as a team
with 162, which answers a different question.

`tests/test_analytics_league_runs.py::test_unequal_team_game_counts_are_weighted_by_games_played`
pins this exact example, and
`test_unequal_game_counts_are_weighted_on_the_page` pins it end to end through
the rendered HTML.

### No hardcoded league shape

Nothing in the calculation assumes 30 teams, 162 games, 2,430 MLB games, or
4,860 team-game records. Every number comes from the records actually stored.
An in-progress season with 40 stored records divides by 40.

## 5. Coverage semantics

The league average is shown only when `LeagueSeasonIngestionStatus.COMPLETE` is
recorded for the season. `supports_league_wide_runs_average` delegates to
`app.analytics.league_hitting.supports_league_wide_average` rather than
re-implementing the rule, so the three metric pages cannot disagree about
whether a season qualifies.

| Coverage state | MLB Average line | `vs MLB` card | Team chart |
| --- | --- | --- | --- |
| `COMPLETE` | drawn | signed number | renders |
| `INCOMPLETE` | not drawn | `—` | renders |
| `RUNNING` | not drawn | `—` | renders |
| no coverage record | not drawn | `—` | renders |

Two rules matter here and are easy to reverse by accident:

**Completeness is never inferred from record counts.** A row count cannot tell
a full season from a season missing a club, and it certainly cannot tell either
from a season still being played. Only the recorded coverage state counts.

**`COMPLETE` describes the refresh, not the season.** It means the latest
league-wide run discovered every MLB team for that season and successfully
ingested all of them. It does **not** mean the baseball season has ended.

### In-progress seasons

It follows that an in-progress season — 2026, say — with `COMPLETE` coverage
**is** allowed to show an MLB Runs/Game comparison, calculated from the
completed games currently persisted. That is the intended behavior, not a
loophole.

What the number describes in that case is "MLB across the games stored so far",
and the page's wording says exactly that: it names the record count and the
team count behind the average, says "currently stored", and states that
complete league coverage means every team was refreshed, "not that the season
has finished being played."

### The team page never depends on the comparison

A missing or untrusted league context yields `None` and nothing more. The
chart, the rolling average, the season average, and the other three cards are
unaffected. League-comparison unavailability is never an error state for this
page.

## 6. What `vs MLB` means

```text
difference_vs_mlb = team season Runs/Game - MLB Runs/Game
```

For example, a team at 4.75 against an MLB average of 4.42 reads `+0.33`.

- **Positive** — the selected team scored more runs per game than MLB overall
  across the stored season.
- **Negative** — fewer.
- **`+0.00`** — matched MLB exactly. This is a real result and is deliberately
  distinct from unavailable, which renders as `—`. The card never shows `0.00`
  to mean "no data".

This is descriptive subtraction and nothing more. It is deliberately **not**:

- a rank or a percentile
- a test of significance
- park-adjusted or opponent-adjusted
- an expected-runs or run-creation model
- a run differential
- a correlation, or any claim about cause

Any of those would need its own definition, its own data, and its own
milestone. `TeamRunsLeagueComparison` re-derives the difference from its two
inputs in a validator, so no caller can hand the page a number the subtraction
did not produce.

## 7. The page renders from the database only

`/runs` reads SQLite and nothing else. It loads the selected team-season with
`list_team_season`, and — only when coverage permits — the whole season with
`list_league_season`. Both are existing repository functions; no new query was
added, and no Runs/Game formula lives in the repository layer.

The MLB Stats API is reached exclusively from the import CLI. Automated tests
assert this directly: `test_the_runs_page_never_calls_the_mlb_api` and
`test_the_comparison_never_reaches_the_mlb_api` monkeypatch the HTTP session,
the MLB client constructor, and the ingestion services to raise, then render
the page.

The one network dependency in the rendered HTML is decorative: club and league
marks are fetched by the browser from MLB's public logo host. Every page names
its team in text and the layout holds when those images do not load.

## 8. Architecture

```text
app/schemas/analytics.py     TeamRunsPoint, TeamRunsSummary, TeamRunsAnalysis,
                             LeagueRunsContext, TeamRunsLeagueComparison
app/analytics/team_runs.py   the team trend and season average
app/analytics/league_runs.py the MLB average, the coverage rule, the comparison
app/web/charts.py            build_team_runs_figure
app/web/formatting.py        build_runs_summary_cards, format_league_runs_note
app/web/routes.py            the thin /runs route
app/web/templates/runs.html  the page
```

Both analytics modules are pure: no FastAPI, no Jinja, no SQLAlchemy, no
Plotly, no MLB client, no network. They take normalized domain records and
return typed results, and they are testable without a web server or a database.

### Why three implementations instead of one framework

Hits, batting strikeouts, and runs are now three near-identical modules. That
duplication is intentional and is called out in `AGENTS.md`: a shared
`GenericMetricAnalysis`/`MetricConfig` layer would have to encode which labels,
axis semantics, colours, nullability rules, and error states belong to which
statistic, and would be harder to read than three explicit implementations.

The three are not interchangeable. Batting strikeouts can be unknown on a
stored row and runs cannot. More hits is not the same kind of fact as more
batting strikeouts. The right time to abstract is after a repeated pattern has
proven stable in real use, not at the third occurrence.

Two things *are* shared, because sharing them prevents a real bug rather than
saving typing: the coverage rule (`supports_league_wide_average`) and the chart
rendering helpers. One coverage rule means the pages cannot disagree about
whether a season is MLB-wide.

## 9. Chart

| Trace | Style | Meaning |
| --- | --- | --- |
| Game Runs | thin line, open circle markers | runs scored in each completed game |
| *N*-Game Average | thick teal, linear segments | the rolling trend |
| Team Season Average | navy dashed horizontal | this club's stored-season average |
| MLB Average | amber dotted horizontal | MLB overall, only when coverage is `COMPLETE` |

The MLB treatment — amber, dotted — matches the hits and batting strikeout
pages, so the three read as one application. Only one of the two horizontal
lines carries a value label: they can sit within a tenth of a run of each
other, where two labels would overlap. When MLB context is present it is the
labelled one, since that is the line a reader is comparing against.

The y axis starts at zero, grows with the data, and has no hardcoded maximum —
a 20-run blowout must still fit.

## 10. Summary cards

The same four-card row as the other metric pages:

| Card | Example | Source |
| --- | --- | --- |
| Recent *N*-Game Avg | `4.87` | `summary.recent_average` |
| Season Avg | `4.62` | `summary.season_average` |
| vs MLB | `+0.21`, or `—` | `comparison.difference_vs_mlb` |
| Games Played | `162` | `summary.games_played` |

`TeamRunsSummary` still calculates the prior-window comparison; it is not given
a card, so the row keeps four cards rather than growing a fifth.

## 11. Limitations

**Runs are a team outcome, not a measure of hitting alone.** A run requires
getting on base and being driven in, and it is shaped by opposing pitching and
defense, by ballpark, by sequencing, and by luck. Runs/Game describes what the
scoreboard said, not how well the offense hit.

**No context is adjusted.** Coors Field and a marine-layer night in Seattle
count the same. Strength of schedule counts the same. A `+0.33` difference does
not mean the club's offense is 0.33 runs per game better than average in any
adjusted sense.

**Extra innings inflate a game's runs.** Runs/Game is a per-game count, not a
per-inning or per-opportunity rate, and a 14-inning game had more chances to
score than a rain-shortened one.

**Every number describes stored games.** For an in-progress season or a partial
import, that is a subset of the season, and both the team and MLB averages move
as more games are imported.

**The comparison says nothing about run prevention.** A club can score more
runs per game than MLB and still be outscored. Run differential is a different
statistic and is not shown.

**No significance is claimed.** Two clubs 0.05 runs per game apart are not
meaningfully distinguished by this page, and it does not pretend otherwise.
