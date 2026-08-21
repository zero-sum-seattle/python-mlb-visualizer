# Team vs MLB comparison

This document describes how Milestone 5 adds MLB-wide context to the team hits
page, what the MLB average means mathematically, and the coverage rule that
decides whether it may be shown at all.

It answers one question:

> How many hits per game does the selected team average compared with MLB
> overall?

See `docs/team-hits-visualization.md` for the team page this extends and
`docs/league-season-ingestion.md` for the coverage state it depends on.

## 1. MLB Hits/Game formula

```text
MLB Hits/Game = total hits across all persisted team-game batting lines
                for the selected season
                ─────────────────────────────────────────────────────────
                total persisted team-game batting lines for that season
```

Implemented in `app/analytics/league_hitting.py::build_league_hits_context`,
returning a `LeagueHitsContext`:

| Field | Meaning |
| --- | --- |
| `season` | The season every counted record belongs to |
| `teams_represented` | Distinct `team_id`s with at least one stored game |
| `team_game_records` | Team-game batting lines counted |
| `total_hits` | Hits summed across those records |
| `hits_per_game` | `total_hits / team_game_records` |

`LeagueHitsContext` re-derives `hits_per_game` from the two totals in a
validator, so a context holding an average nothing in it produced cannot be
constructed — by this code or by any future caller.

### Why the denominator is team-game records

One real MLB game produces **two** team batting lines, one per club. The
denominator counts those lines, not games. A full 30-team, 162-game season is
about 4,860 team-game records. Both the schema field name and this document say
"team-game records" everywhere for that reason.

### Why it is game-weighted, and not the mean of team averages

Every stored team-game record counts once. A club that has played more games
therefore contributes proportionally more, which is what "MLB overall" means.

The alternative — calculating each club's own hits per game and averaging the
results — answers a different question and gives a different number:

```text
Team A: 10 hits, 10 hits   (2 games)
Team B:  4 hits            (1 game)

game-weighted (implemented) : 24 / 3          = 8.0
mean of team averages       : (10 + 4) / 2    = 7.0
```

The unweighted mean silently gives a club with 40 games the same weight as a
club with 162. That is wrong for this question and is the specific mistake
`test_unequal_team_game_counts_are_weighted_by_games_played` exists to catch.

The application does **not** assume equal games per club, thirty clubs, 162
games, or 4,860 records anywhere. The denominator is always counted from the
records actually stored.

## 2. When an MLB-wide average may be shown

`app/analytics/league_hitting.py::supports_league_wide_average` holds the whole
rule:

```python
coverage is not None
and coverage.status is LeagueSeasonIngestionStatus.COMPLETE
```

That is the coverage state Milestone 4 records for a league-wide ingestion run,
read back with `get_league_season_ingestion`. Nothing else counts as evidence.

Completeness is **never** inferred from:

- how many rows the season holds
- thirty team ids being present
- 4,860 team-game records existing
- 162 games per club

A row count cannot tell a full season from a season missing a club, and it
certainly cannot tell either from a season still being played. Averaging
whichever teams happen to be stored and labelling the result "MLB" would look
authoritative while being wrong, which is worse than showing nothing.

### Behavior in each state

| Coverage | MLB average | Chart | Cards | Page |
| --- | --- | --- | --- | --- |
| `COMPLETE` | calculated and shown | MLB reference line added | `vs MLB` shows a signed number | works |
| `INCOMPLETE` | not calculated | no MLB line | `vs MLB` reads `—` | works |
| `RUNNING` | not calculated | no MLB line | `vs MLB` reads `—` | works |
| no record | not calculated | no MLB line | `vs MLB` reads `—` | works |

`RUNNING` is treated exactly like `INCOMPLETE`: a run that never finished left
its coverage unknown, so it cannot support an MLB-wide claim.

In every unavailable case the page reads:

```text
MLB comparison unavailable. A complete league-season import is required
before an MLB-wide average can be shown.
```

A missing or incomplete league comparison never breaks an otherwise valid team
hits page. Everything Milestone 3 rendered still renders.

## 3. In-progress seasons

`COMPLETE` describes the **refresh**, not the season. It means every team
discovered for that season was successfully ingested by one league-wide run,
and — through the per-game check inside the team-season path — that each of
those clubs had every completed scheduled game represented.

It does **not** mean the regular season has ended. Nothing in the UI or in this
document calls `COMPLETE` "season complete".

Both of these are valid and supported:

| Season | Coverage | Situation | Comparison |
| --- | --- | --- | --- |
| 2025 | `COMPLETE` | finished historical season | available |
| 2026 | `COMPLETE` | still being played, well under a full season of games | available |

The 2026 case is available precisely because coverage is not a row-count rule.
Forty stored team-game records with complete coverage qualify exactly as 4,860
would. What the MLB average then describes is MLB-wide performance across the
completed games currently stored by the latest complete league refresh, which
the page says in those words.

## 4. Team-vs-MLB difference

```text
difference_vs_mlb = selected_team_hits_per_game - mlb_hits_per_game
```

`compare_team_hits_to_league` produces a `TeamHitsLeagueComparison`, which
carries the team identity, the season, the team's average, the full
`LeagueHitsContext`, and the difference. Like the context, it re-derives the
difference in a validator and refuses a league context from a different season.

The team side is `TeamHitsAnalysis.summary.season_average` — the same value the
chart's team reference line and the `Season Avg` card read. There is one team
average on the page, so the card, the line, and the comparison cannot disagree.

```text
Team H/G: 8.70    MLB H/G: 8.20    vs MLB: +0.50 H/G
Team H/G: 7.95    MLB H/G: 8.20    vs MLB: -0.25 H/G
```

### What it does and does not mean

A **positive** difference means the selected team averaged more hits per game
than MLB overall across the stored season. A **negative** difference means
fewer.

That is all. The difference is plain subtraction of two descriptive averages.
It is not normalized, not ranked, not tested for significance, and says nothing
about why the two numbers differ, whether the gap will persist, or what will
happen next. The page states the direction and leaves interpretation to the
reader.

League rank, percentiles, and normalized indexes were deliberately left out.
They need a definition and a completeness story of their own, and the basic
comparison had to be trustworthy first.

## 5. Where each responsibility lives

```text
GET /?team_id=136&season=2025&window=15
        │
        ├─ list_team_season(session, ...)          ──► team games
        ├─ build_team_hits_analysis(games, ...)    ──► TeamHitsAnalysis
        │
        ├─ get_league_season_ingestion(session, season=...)   ──► coverage
        ├─ supports_league_wide_average(coverage)             ──► the rule
        ├─ list_league_season(session, season=...)   ──► every stored record
        ├─ build_league_hits_context(records)        ──► LeagueHitsContext
        ├─ compare_team_hits_to_league(analysis, …)  ──► TeamHitsLeagueComparison
        │
        ├─ build_team_hits_figure(analysis, comparison)
        └─ index.html
```

| Layer | Holds |
| --- | --- |
| `app/database/repositories.py` | `list_league_season` — persistence and querying only |
| `app/analytics/league_hitting.py` | the formula, the difference, the coverage rule |
| `app/schemas/analytics.py` | `LeagueHitsContext`, `TeamHitsLeagueComparison` and their invariants |
| `app/web/routes.py` | wiring, in `_load_league_comparison` |
| `app/web/charts.py` | the MLB reference trace |
| `app/web/formatting.py` | rounding and the wording |

The analytics layer takes typed domain objects — `TeamGameBattingLine`,
`TeamHitsAnalysis`, `LeagueSeasonIngestionState` — never ORM records and never
`dict[str, Any]`. It imports no SQLAlchemy, no FastAPI, no Jinja, no Plotly, and
no MLB client, so the formula is testable with a list of batting lines.

The route contains no formula and no completeness rule. It reads the coverage
state, asks the analytics layer whether that state earns an MLB-wide average,
and if so hands it the stored season.

### Why the statistic was not pushed into SQL

A full MLB season is roughly 4,860 team-game records. Summing that in Python
over domain objects is immediate, keeps the formula in the layer that owns
baseball calculations, and keeps it testable without a database. `SUM()` in the
repository would move a baseball calculation into persistence and buy nothing
measurable. If a season ever grows large enough for this to matter, the change
belongs in `build_league_hits_context`'s caller, with the measurement that
motivated it.

`list_league_season` orders by `(team_id, game_date, game_number, game_pk)` so
a run over a season is reproducible. The statistic itself does not depend on
order.

## 6. Chart

`build_team_hits_figure(analysis, league_comparison=None)` gains an optional
fourth trace:

| # | Name | Style | Purpose |
| --- | --- | --- | --- |
| 1 | `Game Hits` | thin grey line, small markers | game-to-game variation |
| 2 | `{window}-Game Average` | thick teal line | the trend |
| 3 | `Team Season Average` | dashed navy horizontal line | the team's own level |
| 4 | `MLB Average` | dotted amber horizontal line | MLB overall, when earned |

The team reference line was renamed from `Season Average` to
`Team Season Average`. With two horizontal lines on one chart, "Season Average"
no longer says whose. The strikeout chart still has one reference line and keeps
the shorter label; Milestone 5 does not touch that chart.

The two reference lines differ in both hue and dash pattern, so they stay
distinguishable in greyscale and for a colour-blind reader. Both are straight
lines with `hoverinfo: skip`. No spline is used anywhere on this chart, and no
annotations or controls were added.

When `league_comparison` is `None` the figure is exactly the three-trace chart
Milestone 3 built.

## 7. Summary cards

```text
Recent 15-Game Avg   Season Avg   vs MLB   Games Played
      8.87              8.90       +0.75        40
```

The `vs MLB` card replaced the `vs Prior 15` card rather than being added
beside it, so the row keeps four cards on the existing grid.

`TeamHitsSummary.prior_window_average` and `change_vs_prior_window` are still
calculated, still validated, and still tested. Only the card was removed; the
chart's rolling average shows the same trend that card described, and the
strikeouts page still displays its own `vs Prior N` card unchanged.

Without a comparison the card reads `—` / "Comparison unavailable". It never
shows `0.00`, which would be a real number the data cannot support.

Rounding to two decimals happens only in `app/web/formatting.py`. Every
calculation keeps full floating-point precision.

## 8. No MLB access from the browser path

Normal browser requests remain database-only. `app/web/routes.py` imports no
service module, and the league comparison reads two repository queries and
nothing else. Tests assert this directly by making `requests.Session.request`,
`mlbstatsapi.Mlb.__init__`, `get_team_game_batting_lines`,
`discover_mlb_teams`, and `ingest_league_season` raise, then loading the page
with complete coverage and asserting the MLB line is still drawn.

Getting league data into the database is still an explicit CLI step:

```bash
poetry run python scripts/import_league_season.py --season 2025
```

## 9. Limitations

- The comparison covers hits only. Batting strikeouts had no league context in
  this milestone; issue #23 added their own, documented in
  `docs/team-strikeouts-visualization.md` section 13. That work also renamed the
  strikeout chart's team reference line to `Team Season Average`, so section 6's
  note about the shorter label describes Milestone 5 only.
- `teams_represented` counts clubs with stored games for the season. Under
  complete coverage that is the league; it is reported for transparency, not
  used as a completeness rule.
- Coverage is per season. Complete 2025 coverage says nothing about 2026, and
  the page treats them independently.
- No live MLB validation was performed for this milestone; see section 10.

## 10. Validation performed

Every automated test is offline. Coverage states are written through the
Milestone 4 repository functions and game rows are seeded from normalized test
records, so no test depends on MLB availability.

The page was rendered manually against a seeded local database with complete
2025 coverage for two clubs, confirming the four summary cards, the MLB
reference line, and the explanation wording. No live MLB request was made.
