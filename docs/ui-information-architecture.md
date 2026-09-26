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
selectors, charts, interpretation, and recovery states. `players.html` extends
the same shell directly, with its own season/search form and no secondary nav.

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

Player metrics remain intentionally absent. Player charts and additional routes
will be added only when a real metric is chosen. Team URLs, metric navigation,
and Team query semantics remain unchanged.
