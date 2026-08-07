"""Presentation helpers shared by the chart builder and the templates."""

from dataclasses import dataclass
from datetime import date

from app.schemas.analytics import TeamHitsAnalysis
from app.schemas.games import HomeAway

HITS_PER_GAME_CAPTION = "Hits per Game"
NO_PRIOR_WINDOW_VALUE = "—"
NO_PRIOR_WINDOW_CAPTION = "Not enough games"

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def format_long_date(value: date) -> str:
    """Format a date as ``May 18, 2025``.

    Written out rather than using ``strftime`` because the no-padding day
    directive is platform specific.
    """
    return f"{_MONTHS[value.month - 1]} {value.day}, {value.year}"


def format_matchup(opponent_name: str, home_away: HomeAway) -> str:
    """Describe an opponent the way a box score does: ``vs`` home, ``at`` away."""
    prefix = "vs" if home_away == "home" else "at"
    return f"{prefix} {opponent_name}"


@dataclass(frozen=True)
class SummaryCard:
    """One headline number rendered above the explanation panel."""

    label: str
    value: str
    caption: str


def build_summary_cards(analysis: TeamHitsAnalysis) -> list[SummaryCard]:
    """Round the analysis for display only; the calculations keep full precision."""
    window = analysis.rolling_window
    summary = analysis.summary

    if summary.change_vs_prior_window is None:
        change_card = SummaryCard(
            label=f"vs Prior {window}",
            value=NO_PRIOR_WINDOW_VALUE,
            caption=NO_PRIOR_WINDOW_CAPTION,
        )
    else:
        change_card = SummaryCard(
            label=f"vs Prior {window}",
            value=f"{summary.change_vs_prior_window:+.2f}",
            caption=HITS_PER_GAME_CAPTION,
        )

    return [
        SummaryCard(
            label=f"Recent {window}-Game Avg",
            value=f"{summary.recent_average:.2f}",
            caption=HITS_PER_GAME_CAPTION,
        ),
        SummaryCard(
            label="Season Avg",
            value=f"{summary.season_average:.2f}",
            caption=HITS_PER_GAME_CAPTION,
        ),
        change_card,
        SummaryCard(
            label="Games Played",
            value=f"{summary.games_played}",
            caption="Completed Games",
        ),
    ]
