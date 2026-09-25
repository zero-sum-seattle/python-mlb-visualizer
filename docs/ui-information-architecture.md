# UI information architecture

Issue #30, implementation Slice A separates application domains from Team metrics.
The primary navigation currently contains only **Teams**. Players stays hidden
until a real destination exists; no placeholder links or pages are rendered.

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
selectors, charts, interpretation, and recovery states. A future Player UI can
extend the same shell without copying Team assumptions.

The brand links to bare `/`. On Team pages, Teams links to the selection-aware
Hits URL. Metric links preserve `team_id`, `season`, and `window`, including the
existing requested-versus-resolved distinction in terminal states. Generic
validation and schema errors do not require Team context.

Teams uses `aria-current="location"`; only the current metric uses
`aria-current="page"`. Groups stack on mobile and links wrap, without navigation
JavaScript or tab semantics. Selectors and chart-local scrolling are unchanged.
