"""Team baserunners calculations over normalized game batting lines.

Answers one question: how many times per game is this team putting a runner
on base, and how is that changing as the season progresses?

Baserunners here means hits + walks + hit-by-pitch: times a batter reached
base safely by one of those three means. It is the standard OBP numerator,
excluding reached-on-error and fielder's choice, which are not persisted.

Like ``team_hitting`` and ``team_strikeouts``, this layer is free of FastAPI,
Jinja, SQLAlchemy, Plotly, and the MLB API. It takes ``TeamGameBattingLine``
domain records and returns a ``TeamBaserunnersAnalysis``.

The shape mirrors the batting strikeout analysis deliberately rather than
sharing a metric abstraction with it: both need to guard against an unknown
component total on a row imported before that component was persisted. Unlike
strikeouts, two source fields (``base_on_balls`` and ``hit_by_pitch``) can each
independently be unknown, so a game is only usable once every component it
needs is known.
"""

from collections.abc import Sequence

from app.schemas.analytics import (
    TeamBaserunnersAnalysis,
    TeamBaserunnersPoint,
    TeamBaserunnersSummary,
)
from app.schemas.games import TeamGameBattingLine

DEFAULT_ROLLING_WINDOW = 15


class TeamBaserunnersAnalysisError(ValueError):
    """Baserunners analysis was requested with input it cannot describe."""


class MissingBaserunnerDataError(TeamBaserunnersAnalysisError):
    """Some stored games have no walk or hit-by-pitch total.

    Raised rather than working around the gap. A missing total is unknown, not
    zero, and dropping those games would describe a different set of games than
    the one the page claims to be showing. The caller is expected to tell the
    reader to re-import the team-season.
    """

    def __init__(self, *, games_missing: int, games_total: int) -> None:
        self.games_missing = games_missing
        self.games_total = games_total
        super().__init__(
            f"{games_missing} of {games_total} stored games have no walk or "
            f"hit-by-pitch total. Those games were imported before baserunner "
            f"components were persisted; re-import the team-season to backfill "
            f"them."
        )


def build_team_baserunners_analysis(
    games: Sequence[TeamGameBattingLine],
    *,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> TeamBaserunnersAnalysis:
    """Calculate a team-season's baserunners-per-game trend.

    Games are ordered by date, then MLB game number, then game id, so both
    halves of a doubleheader keep their real sequence. The x axis of the chart
    is ``season_game_number``, a continuous 1-based index over that order.

    Raises
    ------
    MissingBaserunnerDataError
        At least one game has ``base_on_balls`` or ``hit_by_pitch`` of None.
    TeamBaserunnersAnalysisError
        ``games`` is empty, mixes team-seasons, or ``rolling_window`` is not a
        positive number of games.
    """
    if rolling_window < 1:
        raise TeamBaserunnersAnalysisError(
            f"rolling_window must be at least 1 game, got {rolling_window}"
        )
    if not games:
        raise TeamBaserunnersAnalysisError(
            "Cannot analyse baserunners for a team-season with no completed games"
        )

    ordered = sorted(
        games, key=lambda game: (game.game_date, game.game_number, game.game_pk)
    )
    team_ids = {game.team_id for game in ordered}
    seasons = {game.season for game in ordered}
    if len(team_ids) > 1 or len(seasons) > 1:
        raise TeamBaserunnersAnalysisError(
            "All games must belong to one team and one season, got teams "
            f"{sorted(team_ids)} and seasons {sorted(seasons)}"
        )

    missing = [
        game
        for game in ordered
        if game.base_on_balls is None or game.hit_by_pitch is None
    ]
    if missing:
        raise MissingBaserunnerDataError(
            games_missing=len(missing), games_total=len(ordered)
        )

    # Every component is known past this point, which the None check above
    # proves.
    baserunners = [
        game.hits + game.base_on_balls + game.hit_by_pitch
        for game in ordered
        if game.base_on_balls is not None and game.hit_by_pitch is not None
    ]
    rolling_averages = _trailing_averages(baserunners, rolling_window)
    points = tuple(
        TeamBaserunnersPoint(
            game_pk=game.game_pk,
            game_number=game.game_number,
            season_game_number=index + 1,
            game_date=game.game_date,
            opponent_name=game.opponent_name,
            home_away=game.home_away,
            baserunners=value,
            rolling_average=rolling_average,
        )
        for index, (game, value, rolling_average) in enumerate(
            zip(ordered, baserunners, rolling_averages, strict=True)
        )
    )

    return TeamBaserunnersAnalysis(
        team_id=ordered[-1].team_id,
        team_name=ordered[-1].team_name,
        season=ordered[-1].season,
        rolling_window=rolling_window,
        points=points,
        summary=_build_summary(baserunners, rolling_window=rolling_window),
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
    baserunners: list[int], *, rolling_window: int
) -> TeamBaserunnersSummary:
    games_played = len(baserunners)

    recent = baserunners[-min(rolling_window, games_played) :]
    recent_average = sum(recent) / len(recent)

    prior_window_average: float | None = None
    change_vs_prior_window: float | None = None
    # Two complete windows are required; comparing partial windows would report
    # a change caused by sample size rather than by baserunners.
    if games_played >= 2 * rolling_window:
        prior = baserunners[
            games_played - 2 * rolling_window : games_played - rolling_window
        ]
        prior_window_average = sum(prior) / len(prior)
        change_vs_prior_window = recent_average - prior_window_average

    return TeamBaserunnersSummary(
        games_played=games_played,
        season_average=sum(baserunners) / games_played,
        recent_average=recent_average,
        prior_window_average=prior_window_average,
        change_vs_prior_window=change_vs_prior_window,
    )
