# Team run differential visualization

The `/run-differential` page answers one question: is this team outscoring its
opponents, and does its record reflect that?

It is the fifth per-game metric page, but it is not built like the other four.
Hits, batting strikeouts, runs, and baserunners all read one team's own batting
line. This page reads **both sides of every game**, which changes where the
data comes from, what can go wrong, and what the chart looks like.

## 1. Runs allowed is not a column

There is no `runs_allowed` column and no new MLB request. The existing
`team_game_batting_lines` table already stores everything needed:

| Column | Role here |
| --- | --- |
| `game_pk` | Identifies the game both clubs played in |
| `team_id` | The selected team |
| `opponent_id` | Which club's row holds runs allowed |
| `runs` | Runs scored by whichever team the row belongs to |

For a league-wide import, one MLB game produces two rows — one per club. The
selected team's runs allowed in that game is the *opponent's* `runs` on the
opponent's own row, which is the same number seen from the other side.

`list_team_season_run_results` finds it with a self-join:

```sql
FROM team_game_batting_lines AS team
LEFT JOIN team_game_batting_lines AS opponent
       ON opponent.game_pk = team.game_pk
      AND opponent.team_id = team.opponent_id
WHERE team.team_id = :team_id AND team.season = :season
```

Two details in that join matter:

- It matches on `opponent.team_id = team.opponent_id`, not merely on sharing a
  `game_pk`. Matching on `game_pk` alone would be correct today but would break
  the moment any third row shared the id.
- It is a **LEFT** join. An inner join would silently return zero games for a
  team-season with no opponent rows, which is indistinguishable from a team
  that was never imported. The outer join instead reports which `game_pk`
  values found no partner, so the caller can tell the two states apart.

## 2. Why this page needs a league-wide import

`scripts/import_team_season.py` fetches one club. Nothing in that import
contains the opponents' batting lines, so every game comes back unpaired and
runs allowed is unknown for all of them.

The page refuses in that state rather than charting a partial season. This is
the same refuse-and-guide pattern the batting strikeout and baserunner pages
use, with one difference worth stating plainly: **re-importing the team cannot
fix it.** Nothing is wrong with the team's own rows. The remedy the page names
is `scripts/import_league_season.py`.

A partially paired season — some opponents stored, some not — is refused too.
An average over only the paired games would understate runs allowed and produce
a run differential that looks entirely plausible and is wrong.

## 3. Win/loss is derived, not stored

There is no W/L column anywhere in the schema, and this page does not add one.
A completed MLB game cannot end tied, so:

```text
is_win = runs_scored > runs_allowed
```

That is the whole definition. It gives an actual record for free, which is what
the Pythagorean comparison in section 5 is measured against. Issue #29 covers
showing W/L markers on the other charts; this page needed only the record.

## 4. Run differential calculation

```text
run_differential = runs_scored - runs_allowed
```

Per game, and summed across the season for the headline figure.

This is the only **signed** metric in the application. Every other per-game
value — hits, runs, batting strikeouts, baserunners — has a floor of zero. Run
differential does not, and that single fact drives most of the differences in
the rest of this document: the schema fields have no `ge=0`, the chart's y axis
must not anchor at zero, and every rendered figure carries an explicit sign.

The rolling average uses the same trailing-window definition as the other
pages: the average at game *i* covers the `window` most recent games up to and
including *i*, and early-season games average only what has been played. The
running total can go negative.

## 5. Pythagorean expected record

```text
expected_win_pct = RS^1.83 / (RS^1.83 + RA^1.83)
expected_wins    = expected_win_pct * games_played
```

The exponent is 1.83, the refinement of Bill James' original squared formula
that Baseball Reference publishes against. Pinning it to a public source means
the number on the page can be checked rather than taken on trust; the exponent
is stated on the page for the same reason.

The interesting figure is the gap:

```text
wins_above_expectation = actual_wins - expected_wins
```

A team above its expectation has usually won a lot of close games and lost a
few blowouts. A team below it has usually done the reverse. The page says so,
and says the gap describes games already played rather than predicting
anything — that is the reading most likely to be over-interpreted.

Under one game either way is narrated as "within a game" rather than as a
finding, since that is smaller than the effect of a single blowout.

Both halves are computed from the same paired games, so the expectation and the
record it is compared against always describe exactly the same sample. The
`TeamRunDifferentialAnalysis` validator enforces this: the Pythagorean wins plus
losses must equal the number of chart points, and its run totals must equal the
summary's.

## 6. Chart

Two deliberate departures from the other four charts.

**Diverging bars, not a marker line.** The other charts draw open markers with
a rolling line through them. This one draws bars growing up or down from zero,
split into two traces by outcome so the legend explains the colours. For a
signed quantity the sign is the primary reading, and a bar against a baseline
shows it at a glance where a line through a marker cloud does not.

The two bar traces use `barmode="overlay"`. They are one series split by
outcome, not two series to stack or group — every game has exactly one bar, and
grouping would shift bars off their true x position.

**No MLB reference line.** Every other metric chart draws a dotted amber MLB
average. This one does not, and that is not an omission. League-wide run
differential is exactly zero by construction: every run scored by one team is a
run allowed by another, so the MLB total cancels. The zero line the chart
already draws **is** the league average, and a second line on top of it would
say the same thing twice. The page states this rather than leaving a reader to
wonder what is missing.

The y axis uses `rangemode="normal"`, not the `"tozero"` the other charts use.
Anchoring at zero would clip every loss off the chart. Its zero line is drawn
darker than the gridlines, because here zero is the win/loss boundary rather
than an arbitrary axis end.

## 7. Summary cards

Four cards, like every other metric page, but the third slot holds the
Pythagorean record rather than a `vs MLB` comparison — there being no MLB run
differential to compare against.

| Card | Value |
| --- | --- |
| Recent *n*-Game Avg | Signed run differential per game over the window |
| Season Run Differential | Signed season total, captioned with both run totals |
| Pythagorean Record | Expected wins-losses and expected win % |
| Actual Record | Real wins-losses, win %, and the gap vs expected |

Every signed figure is rendered with an explicit sign, including `+0`. A dead
even season is a real result, and a bare `0` in a column of numbers reads like
a missing value.

Winning percentages are written the way baseball writes them — `.512`, leading
zero dropped, three decimals — with `1.000` keeping its leading digit.

## 8. What run differential does not tell you

It weights a twelve-run win the same as twelve one-run wins. That is exactly
why a team's margin and its record can disagree, and why the Pythagorean gap is
worth showing — but it also means the metric alone does not describe how a
season was won.

It also says nothing about *how* runs were scored or prevented. Hitting,
pitching, and defense all land in the same number. A team with a strong run
differential built on run prevention and one built on run scoring look
identical here.

## 9. Where each responsibility lives

| Concern | Location |
| --- | --- |
| Pairing a team's games with the opponent's rows | `app/database/repositories.py` (`list_team_season_run_results`) |
| Paired-game domain model | `app/schemas/games.py` (`TeamGameRunResult`, `TeamSeasonRunResults`) |
| Differential, rolling average, Pythagorean record | `app/analytics/team_run_differential.py` |
| Analysis models and their consistency guards | `app/schemas/analytics.py` |
| Figure construction | `app/web/charts.py` (`build_team_run_differential_figure`) |
| Cards, notes, win-pct formatting | `app/web/formatting.py` |
| Request handling and page state | `app/web/routes.py` (`/run-differential`) |
| Page markup | `app/web/templates/run_differential.html` |

No migration was required. Every column this page reads already existed.
