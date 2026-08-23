# Team hits allowed visualization

The `/hits-allowed` page charts hits surrendered per game by a team's pitching.
It is the mirror of the Hits page: the same quantity seen from the other side.

It needs no migration and no new MLB request. `hits_allowed` is already stored
on `team_game_pitching_lines` from the pitching import (#41).

## 1. Two independent sources agree

MLB reports hits in two separate stat groups, fetched in two separate requests:
a team's own hits arrive on the hitting game log, and hits it allowed arrive on
the pitching game log. For any given game those describe opposite sides of the
same event, so:

```text
team A's hits_allowed  ==  team B's hits   (same game_pk)
```

Verified across all 162 games of the 2025 Mariners with zero mismatches. Two
independently fetched payloads agreeing exactly is a strong signal that both are
being parsed correctly, and `tests/test_hits_allowed.py` asserts it against the
captured fixture rather than leaving it as an assumption.

## 2. The MLB average comes from the batting table

Summed across the whole league, every hit is allowed by someone. So the league
total for hits and the league total for hits allowed are the same number, over
the same count of team-game records:

```text
MLB Hits Allowed/Game  ==  MLB Hits/Game

2025:  40,138 hits / 4,860 team-game records  =  8.2588
```

That has a practical consequence worth stating plainly. The MLB side of this
comparison is built by `build_league_hits_context` from
`team_game_batting_lines`, so it is available for any season with complete
**batting** coverage. It does **not** need every club's pitching lines imported,
unlike the ERA comparison on `/pitching`, which usually cannot be shown.

Only the selected team needs pitching rows.

### The identity is league-wide only

It holds for the league as a whole and for no part of it. One club's hits
allowed has nothing to do with its own hits, and two clubs' figures do not
cancel unless they only ever played each other. The module docstring says so,
because the identity is easy to over-generalize.

## 3. Counts and rates, again

The chart is **Hits Allowed/Game**, a count, so its season figure is the plain
mean of the per-game values — like hits, runs, and baserunners.

**H/9** in the summary cards is a rate, and it divides summed totals the way the
other pitching rates do:

```text
H/9 = total hits allowed * 27 / total outs
```

The two differ whenever a team pitches other than regulation length. Extra
innings raise the per-game figure without raising the rate; a game where the
home team never batted in the ninth does the reverse. Showing both is what makes
the distinction visible, and a test builds a season with uneven innings
specifically to pin the gap (6.00 per game against 9.00 per nine).

## 4. Direction

**Lower is better**, which is the reverse of the Hits page this one mirrors, and
the reverse of most comparisons in the application. A negative `vs MLB` value
means the team allowed fewer hits per game than the league.

Nothing in the chart encodes that. The summary card caption reads "Hits Allowed,
Negative Is Better", and `format_hits_allowed_direction_sentence` renders the
direction in words rather than relying on a reader to hold the sign convention
in mind.

## 5. Missing-data state

A team-season imported before pitching was collected has no pitching rows, so
hits allowed is not something the stored batting rows can be read for. The page
returns **409** naming the team re-import, exactly as `/pitching` does.

Every pitching column is `NOT NULL`, so there is no partially-known state.

## 6. Where each responsibility lives

| Concern | Location |
| --- | --- |
| Per-game trend, H/9, rolling window | `app/analytics/team_hits_allowed.py` |
| The league identity and the comparison | `app/analytics/league_hits_allowed.py` |
| Analysis models and their consistency guards | `app/schemas/analytics.py` |
| Figure construction | `app/web/charts.py` |
| Cards, notes, direction sentence | `app/web/formatting.py` |
| Request handling and page state | `app/web/routes.py` (`/hits-allowed`) |
| Page markup | `app/web/templates/hits_allowed.html` |

No new table, column, or MLB request. The data was already there.
