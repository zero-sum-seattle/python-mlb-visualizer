"""Presentation helpers shared by the chart builder and the templates."""

from dataclasses import dataclass
from datetime import date

from app.schemas.analytics import (
    TeamHitsAnalysis,
    TeamHitsLeagueComparison,
    TeamStrikeoutsAnalysis,
)
from app.schemas.games import HomeAway

HITS_PER_GAME_CAPTION = "Hits per Game"
STRIKEOUTS_PER_GAME_CAPTION = "Batting Strikeouts per Game"
NO_PRIOR_WINDOW_VALUE = "—"
NO_PRIOR_WINDOW_CAPTION = "Not enough games"
NO_LEAGUE_COMPARISON_VALUE = "—"
NO_LEAGUE_COMPARISON_CAPTION = "Comparison unavailable"
LEAGUE_COMPARISON_UNAVAILABLE_NOTE = (
    "MLB comparison unavailable. A complete league-season import is "
    "required before an MLB-wide average can be shown."
)

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


def build_summary_cards(
    analysis: TeamHitsAnalysis,
    league_comparison: TeamHitsLeagueComparison | None = None,
) -> list[SummaryCard]:
    """Round the analysis for display only; the calculations keep full precision.

    The third card is the team's difference against MLB. Without complete
    league coverage for the season there is no MLB average to compare with, so
    the card reads ``—`` rather than showing a number the data cannot support.

    ``TeamHitsSummary`` still calculates the prior-window comparison, and the
    chart's rolling average still shows the same trend the old ``vs Prior N``
    card described. Only the card was replaced, to keep four cards on the row.
    """
    window = analysis.rolling_window
    summary = analysis.summary

    if league_comparison is None:
        league_card = SummaryCard(
            label="vs MLB",
            value=NO_LEAGUE_COMPARISON_VALUE,
            caption=NO_LEAGUE_COMPARISON_CAPTION,
        )
    else:
        league_card = SummaryCard(
            label="vs MLB",
            value=f"{league_comparison.difference_vs_mlb:+.2f}",
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
        league_card,
        SummaryCard(
            label="Games Played",
            value=f"{summary.games_played}",
            caption="Completed Games",
        ),
    ]


def format_league_comparison_note(
    comparison: TeamHitsLeagueComparison | None,
) -> str:
    """Explain the MLB context on the page, or why there is none.

    The available wording deliberately says "currently stored" and names the
    number of team-game records behind the average. Complete league coverage
    means every team was refreshed, not that the season has finished being
    played, and the sentence must not let a reader conclude otherwise.
    """
    if comparison is None:
        return LEAGUE_COMPARISON_UNAVAILABLE_NOTE

    league = comparison.league
    return (
        f"MLB averaged {league.hits_per_game:.2f} hits per game across the "
        f"{league.team_game_records:,} team-game records currently stored for "
        f"{league.season}, covering {league.teams_represented} teams — total "
        f"hits divided by total team-game records, so a club that has played "
        f"more games counts for more. Complete league coverage means every "
        f"team was refreshed, not that the season has finished being played."
    )


def build_strikeout_summary_cards(
    analysis: TeamStrikeoutsAnalysis,
) -> list[SummaryCard]:
    """Round the analysis for display only; the calculations keep full precision.

    The change card is rendered with a plain signed number and no positive or
    negative styling. More batting strikeouts is not automatically worse for
    every question a reader might be asking, so the page states the direction
    and leaves the judgement to them.
    """
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
            caption=STRIKEOUTS_PER_GAME_CAPTION,
        )

    return [
        SummaryCard(
            label=f"Recent {window}-Game Avg",
            value=f"{summary.recent_average:.2f}",
            caption=STRIKEOUTS_PER_GAME_CAPTION,
        ),
        SummaryCard(
            label="Season Avg",
            value=f"{summary.season_average:.2f}",
            caption=STRIKEOUTS_PER_GAME_CAPTION,
        ),
        change_card,
        SummaryCard(
            label="Games Played",
            value=f"{summary.games_played}",
            caption="Completed Games",
        ),
    ]
