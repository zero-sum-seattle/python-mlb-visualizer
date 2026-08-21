# Team hitting trends comparison

Issue #25 adds a dedicated comparison page for one descriptive question:

> How are a team's rolling Hits/Game and batting Strikeouts/Game moving relative
> to the corresponding MLB per-game averages?

The two raw statistics have different units and typical values, so the page does
not plot them together directly. Each is converted to its own normalized index,
where that metric's MLB average is 100.

## Formulas

For every rolling point:

```text
Hits Index = rolling team Hits/Game / MLB Hits/Game * 100

Batting Strikeout Index = rolling team batting K/Game
                          / MLB batting K/Game
                          * 100
```

The rolling team values come from the existing Hits and Batting Strikeouts
analyses. The window is trailing and includes the current game. Before a full
window exists, it uses every completed game available so far.

The MLB baselines are game-weighted averages over persisted team-game records:

```text
MLB Hits/Game = total league hits / league team-game records

MLB batting K/Game = total league batting strikeouts
                     / league team-game records
```

Full floating-point precision is preserved through the analytics layer. Plotly
hover labels and summary cards round only for presentation.

## Interpretation

- `100` is MLB average for the named metric.
- A Hits Index of `108` means the rolling team Hits/Game value is 108% of MLB
  Hits/Game.
- A Batting Strikeout Index of `94` means the rolling team batting K/Game value
  is 94% of MLB batting K/Game.
- Above 100 is not automatically good. For batting strikeouts, above 100 means
  the team's hitters struck out more times per game than MLB hitters.

The page applies no positive or negative colour semantics to either series or
to the gap between them.

## Trend Gap

The summary card is calculated as:

```text
Trend Gap = recent Hits Index - recent Batting Strikeout Index
```

It is simply a difference between two normalized indexes. It is not a
validated overall offensive-performance statistic, a ranking, a percentile, or
a significance test. It makes no causal or predictive claim.

## Coverage and data integrity

Both MLB baselines must be trustworthy before any normalized point is
calculated.

The route reuses the existing league rules:

1. The latest persisted league-season ingestion state must be `COMPLETE`.
2. Completeness is never inferred from row, team, or game counts.
3. Every persisted league team-game record for the season must carry a known
   batting strikeout total.
4. Both MLB per-game baselines must be greater than zero.

`COMPLETE` describes the league refresh, not whether the season has finished.
An in-progress season can qualify when every discovered club was refreshed, and
the averages then describe the completed games currently stored.

If coverage is `INCOMPLETE`, `RUNNING`, or absent, the page keeps its selectors
and navigation but shows no chart or normalized value. The same unavailable
state is used when a baseline is zero. No fallback value is fabricated.

If any stored league record has `strikeouts IS NULL`, the known subset is not
averaged and called MLB-wide. The page instead explains that the league season
must be re-imported. If the selected team's own games contain an unknown
strikeout total, it gives the corresponding team-season re-import command.

## Architecture

```text
GET /comparison?team_id=136&season=2025&window=15
        │
        ├─ list_available_team_seasons(...)          database only
        ├─ list_team_season(...)
        ├─ build_team_hits_analysis(...)
        ├─ build_team_strikeouts_analysis(...)
        │
        ├─ get_league_season_ingestion(...)
        ├─ existing COMPLETE coverage checks
        ├─ list_league_season(...)
        ├─ build_league_hits_context(...)
        ├─ build_league_strikeouts_context(...)
        │
        ├─ build_team_hitting_comparison_analysis(...)
        ├─ build_team_hitting_comparison_figure(...)
        └─ comparison.html
```

Layer ownership remains explicit:

- `app/database/repositories.py` returns persisted domain records and coverage
  state; it performs no baseball calculation.
- `app/analytics/team_hitting_comparison.py` validates aligned typed inputs and
  calculates the normalized points and Trend Gap; it imports no SQLAlchemy,
  FastAPI, Jinja, Plotly, or MLB client.
- `app/web/charts.py` constructs the three Plotly traces.
- `app/web/formatting.py` rounds the four summary cards for display.
- `app/web/routes.py` wires persisted inputs to analytics and renders expected
  unavailable states.
- `app/web/templates/comparison.html` owns the server-rendered layout and
  explanatory wording.

Normal browser requests remain database-only. League and team imports continue
to be explicit CLI operations.

## Route and presentation

The route is:

```text
/comparison?team_id=136&season=2025&window=15
```

It supports the same `team_id`, `season`, and `window` query parameters and the
same `5`, `10`, `15`, and `30` game windows as the existing pages. Navigation
preserves all three values.

The chart contains exactly:

1. `Hits Index`
2. `Batting Strikeout Index`
3. `Baseline (100)`

The x axis is `Season Game Number`; the y axis is
`Normalized Index (MLB Avg = 100)`. The baseline uses a distinct hue and dotted
line pattern, and the chart uses straight segments rather than spline
smoothing.

The four cards are:

1. `Recent Hits Index`
2. `Recent K Index`
3. `Trend Gap`
4. `Games Played`

The page adds no placeholder controls from the visual reference. There are no
7D/30D/60D buttons, overflow menu, export control, or Players control.

## Limitations

- Batting K/Game is a per-game count, not K%. Games can contain different
  numbers of plate appearances.
- The indexes are not adjusted for park, opponent, game length, or opportunity.
- The comparison is descriptive only. It includes no regression, significance
  testing, rankings, percentiles, or causal claims.
- Results describe the completed team and league games currently stored, not a
  guaranteed finished season.
