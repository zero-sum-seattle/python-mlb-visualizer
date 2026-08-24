"""MLB-wide hits-allowed context for one season.

Answers one question:

    How many hits per game does this team's pitching allow compared with MLB
    overall?

This module is unusually short, because of an identity worth stating plainly:

    **MLB Hits Allowed/Game == MLB Hits/Game**

Every hit by one team is a hit allowed by another, so summed across the whole
league the two totals are the same number, over the same count of team-game
records. There is no separate league hits-allowed figure to calculate.

That has a practical consequence. The MLB side of this comparison is built from
``team_game_batting_lines`` via the existing ``build_league_hits_context``,
which means it is available for any season with complete **batting** coverage.
It does **not** require every club's pitching lines to be imported, unlike the
ERA comparison on ``/pitching``. Only the selected team needs pitching rows.

The identity holds for the league as a whole and not for any subset of it. One
club's hits allowed has nothing to do with its own hits, and two clubs' figures
do not cancel unless they only ever played each other.
"""

from app.analytics.league_hitting import supports_league_wide_average
from app.schemas.analytics import (
    LeagueHitsContext,
    TeamHitsAllowedAnalysis,
    TeamHitsAllowedLeagueComparison,
)
from app.schemas.ingestion import LeagueSeasonIngestionState


class LeagueHitsAllowedAnalysisError(ValueError):
    """League hits-allowed analysis was requested with input it cannot describe."""


def supports_league_wide_hits_allowed_average(
    coverage: LeagueSeasonIngestionState | None,
) -> bool:
    """Say whether a season's coverage permits an MLB-wide hits-allowed average.

    The same Milestone 5 coverage rule every other league page uses,
    deliberately delegated rather than re-implemented so the copies cannot
    drift.

    Complete coverage is both necessary and sufficient here. ``hits`` is
    required on every persisted batting record, and the league figure is built
    from those, so a covered season cannot be holding unknown totals. This is
    the batting-side rule precisely because the league side of this comparison
    comes from the batting table.
    """
    return supports_league_wide_average(coverage)


def compare_team_hits_allowed_to_league(
    analysis: TeamHitsAllowedAnalysis,
    league: LeagueHitsContext,
) -> TeamHitsAllowedLeagueComparison:
    """Place a team-season's hits allowed per game beside MLB overall.

    ``league`` is a ``LeagueHitsContext`` — the hitting-side context — because
    the league totals are identical either way. See the module docstring.

    The team side reads ``TeamHitsAllowedSummary.season_average``, the same
    number the chart's team reference line and the summary card read, so the
    page cannot show two different team averages.

    The difference is descriptive subtraction and nothing more. Note the
    direction: a **negative** difference means the team allowed fewer hits per
    game than MLB, which is the better direction — the opposite of the hits
    page this one mirrors. Saying so is the presentation layer's job.

    Raises
    ------
    LeagueHitsAllowedAnalysisError
        The team analysis and the league context describe different seasons.
    """
    if analysis.season != league.season:
        raise LeagueHitsAllowedAnalysisError(
            f"Cannot compare a {analysis.season} team-season against "
            f"{league.season} MLB context"
        )

    team_hits_allowed_per_game = analysis.summary.season_average
    return TeamHitsAllowedLeagueComparison(
        team_id=analysis.team_id,
        team_name=analysis.team_name,
        season=analysis.season,
        team_hits_allowed_per_game=team_hits_allowed_per_game,
        league=league,
        difference_vs_mlb=team_hits_allowed_per_game - league.hits_per_game,
    )
