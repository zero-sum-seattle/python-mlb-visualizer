"""Links between the analytics pages, keeping the reader's selection intact.

Moving between hits, batting strikeouts, runs, and their normalized comparison
should not throw away the team, season, and rolling window the reader chose, so
each link carries them forward. Only selections that are actually set are
added, so a page that has no team yet links to a plain path rather than one with
empty parameters.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

HITS_PATH = "/"
STRIKEOUTS_PATH = "/strikeouts"
RUNS_PATH = "/runs"
COMPARISON_PATH = "/comparison"

HITS_LABEL = "Hits"
STRIKEOUTS_LABEL = "Batting Strikeouts"
RUNS_LABEL = "Runs"
COMPARISON_LABEL = "Comparison"


@dataclass(frozen=True)
class NavLink:
    """One entry in the page navigation."""

    label: str
    href: str
    is_current: bool


def build_nav_links(
    *,
    current_path: str,
    team_id: int | None = None,
    season: int | None = None,
    window: int | None = None,
) -> list[NavLink]:
    """Build the navigation for every metric page, preserving the selection."""
    selection: dict[str, int] = {}
    if team_id is not None:
        selection["team_id"] = team_id
    if season is not None:
        selection["season"] = season
    if window is not None:
        selection["window"] = window
    query = urlencode(selection)
    suffix = f"?{query}" if query else ""

    return [
        NavLink(
            label=HITS_LABEL,
            href=f"{HITS_PATH}{suffix}",
            is_current=current_path == HITS_PATH,
        ),
        NavLink(
            label=STRIKEOUTS_LABEL,
            href=f"{STRIKEOUTS_PATH}{suffix}",
            is_current=current_path == STRIKEOUTS_PATH,
        ),
        NavLink(
            label=RUNS_LABEL,
            href=f"{RUNS_PATH}{suffix}",
            is_current=current_path == RUNS_PATH,
        ),
        NavLink(
            label=COMPARISON_LABEL,
            href=f"{COMPARISON_PATH}{suffix}",
            is_current=current_path == COMPARISON_PATH,
        ),
    ]
