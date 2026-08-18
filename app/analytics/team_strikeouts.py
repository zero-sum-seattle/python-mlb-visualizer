"""Team batting strikeout calculations over normalized game batting lines.

Answers one question: is this team's offense striking out more or less often
per game as the season progresses?

Like ``team_hitting``, this layer is free of FastAPI, Jinja, SQLAlchemy,
Plotly, and the MLB API. It takes ``TeamGameBattingLine`` domain records and
returns a ``TeamStrikeoutsAnalysis``.

The shape mirrors the hits analysis deliberately rather than sharing a metric
abstraction with it. Two readable implementations are easier to change than one
parameterized one, and the two statistics are not interchangeable: more hits
and more batting strikeouts do not mean the same thing.

K/Game is a per-game count, not a rate. See
``docs/team-strikeouts-visualization.md`` for what it can and cannot support.
"""

from collections.abc import Sequence

from app.schemas.analytics import (
    TeamStrikeoutsAnalysis,
    TeamStrikeoutsPoint,
    TeamStrikeoutsSummary,
)
from app.schemas.games import TeamGameBattingLine

DEFAULT_ROLLING_WINDOW = 15


class TeamStrikeoutsAnalysisError(ValueError):
    """Batting strikeout analysis was requested with input it cannot describe."""


class MissingStrikeoutDataError(TeamStrikeoutsAnalysisError):
    """Some stored games have no batting strikeout total.

    Raised rather than working around the gap. A missing total is unknown, not
    zero, and dropping those games would describe a different set of games than
    the one the page claims to be showing. The caller is expected to tell the
    reader to re-import the team-season.
    """

    def __init__(self, *, games_missing: int, games_total: int) -> None:
        self.games_missing = games_missing
        self.games_total = games_total
        super().__init__(
            f"{games_missing} of {games_total} stored games have no batting "
            f"strikeout total. Those games were imported before batting "
            f"strikeouts were persisted; re-import the team-season to backfill "
            f"them."
        )


def build_team_strikeouts_analysis(
    games: Sequence[TeamGameBattingLine],
    *,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> TeamStrikeoutsAnalysis:
    """Calculate a team-season's batting-strikeouts-per-game trend.

    Games are ordered by date, then MLB game number, then game id, so both
    halves of a doubleheader keep their real sequence. The x axis of the chart
    is ``season_game_number``, a continuous 1-based index over that order.

    Raises
    ------
    MissingStrikeoutDataError
        At least one game has ``strikeouts`` of None.
    TeamStrikeoutsAnalysisError
        ``games`` is empty, mixes team-seasons, or ``rolling_window`` is not a
        positive number of games.
    """
    if rolling_window < 1:
        raise TeamStrikeoutsAnalysisError(
            f"rolling_window must be at least 1 game, got {rolling_window}"
        )
    if not games:
        raise TeamStrikeoutsAnalysisError(
            "Cannot analyse batting strikeouts for a team-season with no "
            "completed games"
        )

    ordered = sorted(
        games, key=lambda game: (game.game_date, game.game_number, game.game_pk)
    )
    team_ids = {game.team_id for game in ordered}
    seasons = {game.season for game in ordered}
    if len(team_ids) > 1 or len(seasons) > 1:
        raise TeamStrikeoutsAnalysisError(
            "All games must belong to one team and one season, got teams "
            f"{sorted(team_ids)} and seasons {sorted(seasons)}"
        )

    missing = [game for game in ordered if game.strikeouts is None]
    if missing:
        raise MissingStrikeoutDataError(
            games_missing=len(missing), games_total=len(ordered)
        )

    # Every value is known past this point, which the None check above proves.
    strikeouts = [game.strikeouts for game in ordered if game.strikeouts is not None]
    rolling_averages = _trailing_averages(strikeouts, rolling_window)
    points = tuple(
        TeamStrikeoutsPoint(
            game_pk=game.game_pk,
            game_number=game.game_number,
            season_game_number=index + 1,
            game_date=game.game_date,
            opponent_name=game.opponent_name,
            home_away=game.home_away,
            strikeouts=value,
            rolling_average=rolling_average,
        )
        for index, (game, value, rolling_average) in enumerate(
            zip(ordered, strikeouts, rolling_averages, strict=True)
        )
    )

    return TeamStrikeoutsAnalysis(
        team_id=ordered[-1].team_id,
        team_name=ordered[-1].team_name,
        season=ordered[-1].season,
        rolling_window=rolling_window,
        points=points,
        summary=_build_summary(strikeouts, rolling_window=rolling_window),
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
    strikeouts: list[int], *, rolling_window: int
) -> TeamStrikeoutsSummary:
    games_played = len(strikeouts)

    recent = strikeouts[-min(rolling_window, games_played) :]
    recent_average = sum(recent) / len(recent)

    prior_window_average: float | None = None
    change_vs_prior_window: float | None = None
    # Two complete windows are required; comparing partial windows would report
    # a change caused by sample size rather than by hitters striking out.
    if games_played >= 2 * rolling_window:
        prior = strikeouts[
            games_played - 2 * rolling_window : games_played - rolling_window
        ]
        prior_window_average = sum(prior) / len(prior)
        change_vs_prior_window = recent_average - prior_window_average

    return TeamStrikeoutsSummary(
        games_played=games_played,
        season_average=sum(strikeouts) / games_played,
        recent_average=recent_average,
        prior_window_average=prior_window_average,
        change_vs_prior_window=change_vs_prior_window,
    )
