"""Links between the analytics pages, keeping the reader's selection intact.

Moving between hits, batting strikeouts, runs, baserunners, run differential,
pitching, hits allowed, and the normalized comparison should not throw away the
team, season, and rolling window the reader chose, so each link carries them
forward. Only selections
that are actually set are added, so a page that has no team yet links to a
plain path rather than one with empty parameters.
"""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

HITS_PATH = "/"
STRIKEOUTS_PATH = "/strikeouts"
RUNS_PATH = "/runs"
BASERUNNERS_PATH = "/baserunners"
RUN_DIFFERENTIAL_PATH = "/run-differential"
PITCHING_PATH = "/pitching"
HITS_ALLOWED_PATH = "/hits-allowed"
COMPARISON_PATH = "/comparison"

HITS_LABEL = "Hits"
STRIKEOUTS_LABEL = "Batting Strikeouts"
RUNS_LABEL = "Runs Scored"
BASERUNNERS_LABEL = "Baserunners"
RUN_DIFFERENTIAL_LABEL = "Run Differential"
PITCHING_LABEL = "Pitching Trends"
HITS_ALLOWED_LABEL = "Hits Allowed"
COMPARISON_LABEL = "Hits vs Batting Strikeouts"


@dataclass(frozen=True)
class NavLink:
    """One entry in the page navigation."""

    label: str
    href: str
    is_current: bool
    group: Literal["Offense", "Pitching", "Results"]


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
            group="Offense",
            href=f"{HITS_PATH}{suffix}",
            is_current=current_path == HITS_PATH,
        ),
        NavLink(
            label=STRIKEOUTS_LABEL,
            group="Offense",
            href=f"{STRIKEOUTS_PATH}{suffix}",
            is_current=current_path == STRIKEOUTS_PATH,
        ),
        NavLink(
            label=RUNS_LABEL,
            group="Offense",
            href=f"{RUNS_PATH}{suffix}",
            is_current=current_path == RUNS_PATH,
        ),
        NavLink(
            label=BASERUNNERS_LABEL,
            group="Offense",
            href=f"{BASERUNNERS_PATH}{suffix}",
            is_current=current_path == BASERUNNERS_PATH,
        ),
        NavLink(
            label=RUN_DIFFERENTIAL_LABEL,
            group="Results",
            href=f"{RUN_DIFFERENTIAL_PATH}{suffix}",
            is_current=current_path == RUN_DIFFERENTIAL_PATH,
        ),
        NavLink(
            label=PITCHING_LABEL,
            group="Pitching",
            href=f"{PITCHING_PATH}{suffix}",
            is_current=current_path == PITCHING_PATH,
        ),
        NavLink(
            label=HITS_ALLOWED_LABEL,
            group="Pitching",
            href=f"{HITS_ALLOWED_PATH}{suffix}",
            is_current=current_path == HITS_ALLOWED_PATH,
        ),
        NavLink(
            label=COMPARISON_LABEL,
            group="Offense",
            href=f"{COMPARISON_PATH}{suffix}",
            is_current=current_path == COMPARISON_PATH,
        ),
    ]
