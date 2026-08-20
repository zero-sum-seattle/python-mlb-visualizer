"""MLB-wide hitting calculations over normalized game batting lines.

Separate from ``app/analytics/team_hitting.py`` because it answers a different
question — how MLB overall hit, not how one club hit — and separate from
ingestion because it only reads what ingestion already persisted. Like every
other module under ``app/analytics``, it knows nothing about FastAPI, Jinja,
SQLAlchemy, Plotly, or the MLB API.
"""

from collections.abc import Sequence

from app.schemas.analytics import (
    LeagueHitsContext,
    TeamHitsAnalysis,
    TeamHitsLeagueComparison,
)
from app.schemas.games import TeamGameBattingLine
from app.schemas.ingestion import (
    LeagueSeasonIngestionState,
    LeagueSeasonIngestionStatus,
)


class LeagueHitsAnalysisError(ValueError):
    """League hitting analysis was requested with input it cannot describe."""


def supports_league_wide_average(
    coverage: LeagueSeasonIngestionState | None,
) -> bool:
    """Say whether a season's stored games may be described as MLB-wide.

    The only acceptable evidence is the coverage state Milestone 4 records:
    ``COMPLETE`` means one league-wide run discovered every MLB team for that
    season and successfully ingested all of them. Anything else — a run still
    ``RUNNING``, a run that lost a club, or a season no league-wide run has
    ever touched — leaves an unknown number of teams missing, and an average of
    whichever teams happen to be stored is not an MLB average.

    Completeness is never inferred from how many rows exist. A row count cannot
    tell a full season from a season missing a club, and it certainly cannot
    tell either from a season still being played.

    ``COMPLETE`` describes the refresh, not the season: an in-progress season
    can hold complete coverage while every club still has games left to play.
    """
    if coverage is None:
        return False
    return coverage.status is LeagueSeasonIngestionStatus.COMPLETE


def build_league_hits_context(
    games: Sequence[TeamGameBattingLine],
) -> LeagueHitsContext:
    """Calculate MLB hits per game across every stored team-game record.

    The average is **game-weighted**::

        hits per game = total hits / total team-game records

    Every stored team-game record counts once, so a club that has played more
    games contributes proportionally more. Averaging each club's own average
    instead would silently give a team with 40 games the same weight as a team
    with 162, which answers a different question and is wrong for this one.

    Raises
    ------
    LeagueHitsAnalysisError
        ``games`` is empty, or the records span more than one season.
    """
    if not games:
        raise LeagueHitsAnalysisError(
            "Cannot describe MLB hitting from no team-game records"
        )

    seasons = {game.season for game in games}
    if len(seasons) > 1:
        raise LeagueHitsAnalysisError(
            f"All team-game records must belong to one season, got {sorted(seasons)}"
        )

    team_game_records = len(games)
    total_hits = sum(game.hits for game in games)
    return LeagueHitsContext(
        season=games[0].season,
        teams_represented=len({game.team_id for game in games}),
        team_game_records=team_game_records,
        total_hits=total_hits,
        hits_per_game=total_hits / team_game_records,
    )


def compare_team_hits_to_league(
    analysis: TeamHitsAnalysis,
    league: LeagueHitsContext,
) -> TeamHitsLeagueComparison:
    """Place a team-season's hits per game beside MLB overall.

    The team side is ``TeamHitsAnalysis.summary.season_average``, which is the
    same number the chart's team reference line and the Season Avg card read,
    so the page cannot show two different team averages.

    The difference is descriptive subtraction and nothing more. It is not
    normalized, not tested for significance, and carries no claim about why the
    two numbers differ.

    Raises
    ------
    LeagueHitsAnalysisError
        The team analysis and the league context describe different seasons.
    """
    if analysis.season != league.season:
        raise LeagueHitsAnalysisError(
            f"Cannot compare a {analysis.season} team-season against "
            f"{league.season} MLB context"
        )

    team_hits_per_game = analysis.summary.season_average
    return TeamHitsLeagueComparison(
        team_id=analysis.team_id,
        team_name=analysis.team_name,
        season=analysis.season,
        team_hits_per_game=team_hits_per_game,
        league=league,
        difference_vs_mlb=team_hits_per_game - league.hits_per_game,
    )
