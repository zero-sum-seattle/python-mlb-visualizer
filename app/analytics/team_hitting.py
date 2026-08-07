"""Team hitting calculations over normalized game batting lines.

This layer is deliberately free of FastAPI, Jinja, SQLAlchemy, Plotly, and the
MLB API. It takes ``TeamGameBattingLine`` domain records and returns a
``TeamHitsAnalysis``.
"""

from collections.abc import Sequence

from app.schemas.analytics import TeamHitsAnalysis, TeamHitsPoint, TeamHitsSummary
from app.schemas.games import TeamGameBattingLine

DEFAULT_ROLLING_WINDOW = 15


class TeamHitsAnalysisError(ValueError):
    """Team hitting analysis was requested with input it cannot describe."""


def build_team_hits_analysis(
    games: Sequence[TeamGameBattingLine],
    *,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> TeamHitsAnalysis:
    """Calculate a team-season's hits-per-game trend.

    Games are ordered by date, then MLB game number, then game id, so both
    halves of a doubleheader keep their real sequence. The x axis of the chart
    is ``season_game_number``, a continuous 1-based index over that order.

    Raises
    ------
    TeamHitsAnalysisError
        ``games`` is empty, mixes team-seasons, or ``rolling_window`` is not a
        positive number of games.
    """
    if rolling_window < 1:
        raise TeamHitsAnalysisError(
            f"rolling_window must be at least 1 game, got {rolling_window}"
        )
    if not games:
        raise TeamHitsAnalysisError(
            "Cannot analyse hitting for a team-season with no completed games"
        )

    ordered = sorted(
        games, key=lambda game: (game.game_date, game.game_number, game.game_pk)
    )
    team_ids = {game.team_id for game in ordered}
    seasons = {game.season for game in ordered}
    if len(team_ids) > 1 or len(seasons) > 1:
        raise TeamHitsAnalysisError(
            "All games must belong to one team and one season, got teams "
            f"{sorted(team_ids)} and seasons {sorted(seasons)}"
        )

    hits = [game.hits for game in ordered]
    rolling_averages = _trailing_averages(hits, rolling_window)
    points = tuple(
        TeamHitsPoint(
            game_pk=game.game_pk,
            game_number=game.game_number,
            season_game_number=index + 1,
            game_date=game.game_date,
            opponent_name=game.opponent_name,
            home_away=game.home_away,
            hits=game.hits,
            rolling_average=rolling_average,
        )
        for index, (game, rolling_average) in enumerate(
            zip(ordered, rolling_averages, strict=True)
        )
    )

    season_average = sum(hits) / len(hits)
    return TeamHitsAnalysis(
        team_id=ordered[-1].team_id,
        team_name=ordered[-1].team_name,
        season=ordered[-1].season,
        rolling_window=rolling_window,
        season_average=season_average,
        points=points,
        summary=_build_summary(
            hits,
            rolling_window=rolling_window,
            season_average=season_average,
        ),
    )


def _trailing_averages(values: list[int], window: int) -> list[float]:
    """Return the trailing mean ending at each position.

    The average at index ``i`` covers the ``window`` most recent values up to
    and including ``i``. Early positions use every value available so far
    rather than producing a gap, so game 1 of a season is its own average.
    """
    averages: list[float] = []
    running = 0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        averages.append(running / min(index + 1, window))
    return averages


def _build_summary(
    hits: list[int],
    *,
    rolling_window: int,
    season_average: float,
) -> TeamHitsSummary:
    games_played = len(hits)

    recent = hits[-min(rolling_window, games_played) :]
    recent_average = sum(recent) / len(recent)

    prior_window_average: float | None = None
    change_vs_prior_window: float | None = None
    # Two complete windows are required; comparing partial windows would report
    # a change caused by sample size rather than by hitting.
    if games_played >= 2 * rolling_window:
        prior = hits[games_played - 2 * rolling_window : games_played - rolling_window]
        prior_window_average = sum(prior) / len(prior)
        change_vs_prior_window = recent_average - prior_window_average

    return TeamHitsSummary(
        games_played=games_played,
        season_average=season_average,
        recent_average=recent_average,
        prior_window_average=prior_window_average,
        change_vs_prior_window=change_vs_prior_window,
    )
