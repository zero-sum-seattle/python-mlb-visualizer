"""Team run-scoring calculations over normalized game batting lines.

Answers one question:

    How many runs per game is this team scoring, and how is that changing as
    the season progresses?

Like ``team_hitting`` and ``team_strikeouts``, this layer is free of FastAPI,
Jinja, SQLAlchemy, Plotly, and the MLB API. It takes ``TeamGameBattingLine``
domain records and returns a ``TeamRunsAnalysis``.

The shape mirrors the hits and batting strikeout analyses deliberately rather
than sharing a metric abstraction with them. Three readable implementations are
easier to change than one parameterized one, and the three statistics are not
interchangeable: hits, batting strikeouts, and runs answer different questions.

Runs scored throughout: runs the selected team put on the board. Runs allowed
and run differential are different statistics and are not calculated here.
"""

from collections.abc import Sequence

from app.schemas.analytics import TeamRunsAnalysis, TeamRunsPoint, TeamRunsSummary
from app.schemas.games import TeamGameBattingLine

DEFAULT_ROLLING_WINDOW = 15


class TeamRunsAnalysisError(ValueError):
    """Team run analysis was requested with input it cannot describe."""


def build_team_runs_analysis(
    games: Sequence[TeamGameBattingLine],
    *,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> TeamRunsAnalysis:
    """Calculate a team-season's runs-per-game trend.

    Games are ordered by date, then MLB game number, then game id, so both
    halves of a doubleheader keep their real sequence. The x axis of the chart
    is ``season_game_number``, a continuous 1-based index over that order.

    There is no unknown-value case to guard against. ``runs`` is required on
    every persisted team-game record, so no equivalent of the batting strikeout
    backfill state exists for this metric.

    Raises
    ------
    TeamRunsAnalysisError
        ``games`` is empty, mixes team-seasons, or ``rolling_window`` is not a
        positive number of games.
    """
    if rolling_window < 1:
        raise TeamRunsAnalysisError(
            f"rolling_window must be at least 1 game, got {rolling_window}"
        )
    if not games:
        raise TeamRunsAnalysisError(
            "Cannot analyse run scoring for a team-season with no completed games"
        )

    ordered = sorted(
        games, key=lambda game: (game.game_date, game.game_number, game.game_pk)
    )
    team_ids = {game.team_id for game in ordered}
    seasons = {game.season for game in ordered}
    if len(team_ids) > 1 or len(seasons) > 1:
        raise TeamRunsAnalysisError(
            "All games must belong to one team and one season, got teams "
            f"{sorted(team_ids)} and seasons {sorted(seasons)}"
        )

    runs = [game.runs for game in ordered]
    rolling_averages = _trailing_averages(runs, rolling_window)
    points = tuple(
        TeamRunsPoint(
            game_pk=game.game_pk,
            game_number=game.game_number,
            season_game_number=index + 1,
            game_date=game.game_date,
            opponent_name=game.opponent_name,
            home_away=game.home_away,
            runs=game.runs,
            rolling_average=rolling_average,
        )
        for index, (game, rolling_average) in enumerate(
            zip(ordered, rolling_averages, strict=True)
        )
    )

    return TeamRunsAnalysis(
        team_id=ordered[-1].team_id,
        team_name=ordered[-1].team_name,
        season=ordered[-1].season,
        rolling_window=rolling_window,
        points=points,
        summary=_build_summary(runs, rolling_window=rolling_window),
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


def _build_summary(runs: list[int], *, rolling_window: int) -> TeamRunsSummary:
    games_played = len(runs)

    recent = runs[-min(rolling_window, games_played) :]
    recent_average = sum(recent) / len(recent)

    prior_window_average: float | None = None
    change_vs_prior_window: float | None = None
    # Two complete windows are required; comparing partial windows would report
    # a change caused by sample size rather than by run scoring.
    if games_played >= 2 * rolling_window:
        prior = runs[games_played - 2 * rolling_window : games_played - rolling_window]
        prior_window_average = sum(prior) / len(prior)
        change_vs_prior_window = recent_average - prior_window_average

    return TeamRunsSummary(
        games_played=games_played,
        season_average=sum(runs) / games_played,
        recent_average=recent_average,
        prior_window_average=prior_window_average,
        change_vs_prior_window=change_vs_prior_window,
    )
