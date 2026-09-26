# UI information architecture

Issue #30 separates application domains from Team metrics. Primary navigation
contains **Teams** and **Players**, both leading to real destinations.

Team analytics uses ordinary document links under noninteractive headings:

- **Offense:** Hits (`/`), Batting Strikeouts (`/strikeouts`), Runs Scored
  (`/runs`), Baserunners (`/baserunners`), Hits vs Batting Strikeouts (`/comparison`).
- **Pitching:** Pitching Trends (`/pitching`), Hits Allowed (`/hits-allowed`).
- **Results:** Run Differential (`/run-differential`).

Offense includes broader outcomes than hitting alone. Comparison belongs there
because it compares hits and batting strikeouts, not arbitrary teams or seasons.
All existing URLs remain canonical, with no aliases or redirects.

`base.html` owns the entity-neutral shell, primary navigation, skip link, main
landmark, and footer. `team_base.html` supplies the active Team domain and grouped
navigation through `_team_navigation.html`. Individual metric templates retain
selectors, charts, interpretation, and recovery states. Player pages extend
`player_base.html`, which only marks Players as the current domain; there is no
secondary Player navigation (see below).

Team metric pages include `_team_selector_form.html` for their Team, season, and
rolling-window GET controls. It relies on the Team-season catalog and stays
outside the entity-neutral shell; Player selection uses its own controls.
The small `_summary_cards.html` partial renders route-provided
cards and preserves Comparison's distinct section class and accessible label.

The brand links to bare `/`. On Team pages, Teams links to the selection-aware
Hits URL. Metric links preserve `team_id`, `season`, and `window`, including the
existing requested-versus-resolved distinction in terminal states. Generic
validation and schema errors do not require Team context.

The current domain uses `aria-current="location"`; only the current Team metric uses
`aria-current="page"`. Groups stack on mobile and links wrap, without navigation
JavaScript or tab semantics. Selectors and chart-local scrolling are unchanged.

## Player directory

`/players` is the Player-domain landing and directory page. Available seasons
and selection membership come from persisted `player_seasons`, independently of
hitting data. With no requested season it selects the newest stored catalog
season. Explicit unavailable seasons and players outside the selected catalog
return a useful 404; an empty catalog or no-match search has a useful 200 state.

GET parameters `season`, `q`, and `player_id` make searches and selections
shareable. Name search reuses the catalog's case/accent folding, shows at most
50 alphabetical matches, and renders no result list until a name is entered.
Submitting the season/search form clears `player_id`. Selection confirms only
the stored name, primary position, and season membership; identity fields are
not historical season attributes. Browser requests read the database only.

The directory remains selection infrastructure and renders no Player stats. When
the selected Player has a stored `player_season_hitting` row for that season, it
links to the hitting overview; otherwise it shows the real import command. That
existence check is a local repository read, not an MLB call.

## Player hitting overview

Issue #57 adds the first Player analytics page:

```text
/players/hitting?season=<season>&player_id=<player_id>
```

It answers *what did this player's overall offensive season look like?* from
one stored full-season aggregate in `player_season_hitting`. It is not a
game-by-game trend, a projection, or a comparison with MLB; none of those exist
for Players yet. AVG, OBP, SLG, OPS, and the K%/BB%/HR% plate-appearance rate
profile are derived in `app/analytics/player_hitting.py` and never persisted.

Both parameters are required positive integers. Membership comes from
`player_seasons` only: a global identity or a hitting row without catalog
membership returns 404. A member with no stored hitting line returns a 200
data-unavailable state with the `import_player_season.py` command, because the
selection is valid and only the analytics are absent; nothing is shown as zero.
A rate with a zero denominator renders as `—` with its reason, never `.000`.

A multi-club season is one combined line. No historical team-stint model exists,
so the page names no club. Primary position is the stored identity value, not a
historical season attribute. Browser requests remain DB-only.

The page links back to `/players?season=…&player_id=…` with that Player still
selected. Because each Player page needs a selected Player-season, a global
"Hitting" nav item would have no real destination, so none exists. Team URLs,
metric navigation, and Team query semantics remain unchanged.
