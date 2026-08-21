"""Normalized rolling team hits-versus-batting-strikeouts comparison.

This module combines the existing, independently calculated team Hits/Game and
batting K/Game rolling analyses. Each rolling value is divided by its matching
MLB per-game context and multiplied by 100, so MLB average is 100 on both
scales. The indexes are descriptive only: above 100 means more of the named
statistic than the MLB baseline, not automatically better performance.

The module is deliberately narrow. It does not query persistence, decide
whether league coverage is complete, build a Plotly figure, or generalize the
two statistics into a metric framework.
"""

from app.schemas.analytics import (
    LeagueHitsContext,
    LeagueStrikeoutsContext,
    TeamHitsAnalysis,
    TeamHittingComparisonAnalysis,
    TeamHittingComparisonPoint,
    TeamHittingComparisonSummary,
    TeamStrikeoutsAnalysis,
)

NORMALIZED_INDEX_BASELINE = 100.0


class TeamHittingComparisonError(ValueError):
    """The supplied analyses cannot form one trustworthy comparison."""


class InvalidComparisonBaselineError(TeamHittingComparisonError):
    """An MLB per-game baseline is not positive, so division is unsafe."""

    def __init__(self, *, metric: str, value: float) -> None:
        self.metric = metric
        self.value = value
        super().__init__(
            f"MLB {metric} baseline must be greater than zero to calculate a "
            f"normalized index, got {value}"
        )


def build_team_hitting_comparison_analysis(
    hits_analysis: TeamHitsAnalysis,
    strikeouts_analysis: TeamStrikeoutsAnalysis,
    league_hits: LeagueHitsContext,
    league_strikeouts: LeagueStrikeoutsContext,
) -> TeamHittingComparisonAnalysis:
    """Normalize rolling team Hits/Game and batting K/Game to MLB = 100.

    For each aligned rolling point::

        Hits Index = rolling team Hits/Game / MLB Hits/Game * 100
        Batting Strikeout Index = rolling team batting K/Game
                                  / MLB batting K/Game * 100

    ``trend_gap`` is the latest Hits Index minus the latest batting strikeout
    index. It is only a difference between two normalized indexes, not a
    validated overall offensive-performance statistic.

    Coverage is intentionally decided before this function is called. The
    caller must only build the two league contexts after the existing complete
    league-coverage checks pass; constructing the batting-strikeout context
    additionally refuses any stored record with an unknown strikeout total.

    Raises
    ------
    InvalidComparisonBaselineError
        Either MLB per-game baseline is zero or otherwise non-positive.
    TeamHittingComparisonError
        The team analyses do not describe the same team, season, rolling
        window, or ordered games, or a league context is for another season.
    """
    _validate_analysis_identity(hits_analysis, strikeouts_analysis)
    _validate_league_seasons(hits_analysis, league_hits, league_strikeouts)
    _validate_positive_baselines(league_hits, league_strikeouts)

    points = tuple(
        TeamHittingComparisonPoint(
            game_pk=hits_point.game_pk,
            season_game_number=hits_point.season_game_number,
            game_date=hits_point.game_date,
            opponent_name=hits_point.opponent_name,
            hits_index=(
                hits_point.rolling_average
                / league_hits.hits_per_game
                * NORMALIZED_INDEX_BASELINE
            ),
            strikeouts_index=(
                strikeouts_point.rolling_average
                / league_strikeouts.strikeouts_per_game
                * NORMALIZED_INDEX_BASELINE
            ),
        )
        for hits_point, strikeouts_point in zip(
            hits_analysis.points,
            strikeouts_analysis.points,
            strict=True,
        )
    )
    recent = points[-1]

    return TeamHittingComparisonAnalysis(
        team_id=hits_analysis.team_id,
        team_name=hits_analysis.team_name,
        season=hits_analysis.season,
        rolling_window=hits_analysis.rolling_window,
        mlb_hits_per_game=league_hits.hits_per_game,
        mlb_strikeouts_per_game=league_strikeouts.strikeouts_per_game,
        baseline_index=NORMALIZED_INDEX_BASELINE,
        points=points,
        summary=TeamHittingComparisonSummary(
            games_played=len(points),
            recent_hits_index=recent.hits_index,
            recent_strikeouts_index=recent.strikeouts_index,
            trend_gap=recent.hits_index - recent.strikeouts_index,
        ),
    )


def _validate_analysis_identity(
    hits_analysis: TeamHitsAnalysis,
    strikeouts_analysis: TeamStrikeoutsAnalysis,
) -> None:
    hits_identity = (
        hits_analysis.team_id,
        hits_analysis.team_name,
        hits_analysis.season,
        hits_analysis.rolling_window,
    )
    strikeouts_identity = (
        strikeouts_analysis.team_id,
        strikeouts_analysis.team_name,
        strikeouts_analysis.season,
        strikeouts_analysis.rolling_window,
    )
    if hits_identity != strikeouts_identity:
        raise TeamHittingComparisonError(
            "Hits and batting strikeout analyses must describe the same team, "
            "season, and rolling window"
        )

    if len(hits_analysis.points) != len(strikeouts_analysis.points):
        raise TeamHittingComparisonError(
            "Hits and batting strikeout analyses must contain the same games"
        )

    for hits_point, strikeouts_point in zip(
        hits_analysis.points,
        strikeouts_analysis.points,
        strict=True,
    ):
        hits_game = (
            hits_point.game_pk,
            hits_point.game_number,
            hits_point.season_game_number,
            hits_point.game_date,
            hits_point.opponent_name,
            hits_point.home_away,
        )
        strikeouts_game = (
            strikeouts_point.game_pk,
            strikeouts_point.game_number,
            strikeouts_point.season_game_number,
            strikeouts_point.game_date,
            strikeouts_point.opponent_name,
            strikeouts_point.home_away,
        )
        if hits_game != strikeouts_game:
            raise TeamHittingComparisonError(
                "Hits and batting strikeout analyses must contain the same "
                "games in the same order"
            )


def _validate_league_seasons(
    hits_analysis: TeamHitsAnalysis,
    league_hits: LeagueHitsContext,
    league_strikeouts: LeagueStrikeoutsContext,
) -> None:
    if not (hits_analysis.season == league_hits.season == league_strikeouts.season):
        raise TeamHittingComparisonError(
            "Team and MLB hitting contexts must describe the same season"
        )


def _validate_positive_baselines(
    league_hits: LeagueHitsContext,
    league_strikeouts: LeagueStrikeoutsContext,
) -> None:
    if league_hits.hits_per_game <= 0:
        raise InvalidComparisonBaselineError(
            metric="Hits/Game",
            value=league_hits.hits_per_game,
        )
    if league_strikeouts.strikeouts_per_game <= 0:
        raise InvalidComparisonBaselineError(
            metric="batting K/Game",
            value=league_strikeouts.strikeouts_per_game,
        )
