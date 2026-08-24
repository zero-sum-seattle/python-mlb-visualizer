"""Team hits-allowed calculations over normalized game pitching lines.

Answers one question:

    How many hits per game is this team's pitching surrendering, and how is
    that changing as the season progresses?

The mirror image of ``team_hitting``: that module counts hits by a team's
hitters, this one counts hits against its pitchers. The two are deliberately
separate modules rather than one parameterized builder, for the same reason the
rest of the package keeps its metrics apart — they answer different questions
and their labels, comparisons, and directions differ.

Hits allowed per game is a **count**, so its season figure is the plain mean of
the per-game values, like hits, runs, and baserunners. That is unlike the rate
statistics in ``team_pitching`` (ERA, WHIP, K/9), which must sum numerators and
denominators. ``hits_per_nine`` here is the one rate, and it follows the
summing rule.

One direction note that the presentation layer is responsible for stating:
fewer hits allowed is better, the opposite of the page this one mirrors.
"""

from collections.abc import Sequence

from app.schemas.analytics import (
    TeamHitsAllowedAnalysis,
    TeamHitsAllowedPoint,
    TeamHitsAllowedSummary,
)
from app.schemas.games import OUTS_PER_NINE_INNINGS, TeamGamePitchingLine

DEFAULT_ROLLING_WINDOW = 15


class TeamHitsAllowedAnalysisError(ValueError):
    """Hits-allowed analysis was requested with input it cannot describe."""


def build_team_hits_allowed_analysis(
    games: Sequence[TeamGamePitchingLine],
    *,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> TeamHitsAllowedAnalysis:
    """Calculate a team-season's hits-allowed-per-game trend.

    Games are ordered by date, then MLB game number, then game id, so both
    halves of a doubleheader keep their real sequence. The x axis of the chart
    is ``season_game_number``, a continuous 1-based index over that order.

    Every column on a stored pitching line is NOT NULL, so there is no
    unknown-value state to guard against. A team-season either has pitching
    rows or has none, and an empty input is refused here.

    Raises
    ------
    TeamHitsAllowedAnalysisError
        ``games`` is empty, mixes team-seasons, ``rolling_window`` is not a
        positive number of games, or the season recorded no outs.
    """
    if rolling_window < 1:
        raise TeamHitsAllowedAnalysisError(
            f"rolling_window must be at least 1 game, got {rolling_window}"
        )
    if not games:
        raise TeamHitsAllowedAnalysisError(
            "Cannot analyse hits allowed for a team-season with no completed games"
        )

    ordered = sorted(
        games, key=lambda game: (game.game_date, game.game_number, game.game_pk)
    )
    team_ids = {game.team_id for game in ordered}
    seasons = {game.season for game in ordered}
    if len(team_ids) > 1 or len(seasons) > 1:
        raise TeamHitsAllowedAnalysisError(
            "All games must belong to one team and one season, got teams "
            f"{sorted(team_ids)} and seasons {sorted(seasons)}"
        )

    total_outs = sum(game.outs for game in ordered)
    if total_outs == 0:
        raise TeamHitsAllowedAnalysisError(
            "Cannot analyse hits allowed for a team-season with no recorded outs; "
            "the per-nine-innings rate would divide by zero"
        )

    hits_allowed = [game.hits_allowed for game in ordered]
    rolling_averages = _trailing_averages(hits_allowed, rolling_window)
    points = tuple(
        TeamHitsAllowedPoint(
            game_pk=game.game_pk,
            game_number=game.game_number,
            season_game_number=index + 1,
            game_date=game.game_date,
            opponent_name=game.opponent_name,
            home_away=game.home_away,
            hits_allowed=game.hits_allowed,
            outs=game.outs,
            innings_pitched_display=game.innings_pitched_display,
            rolling_average=rolling_average,
        )
        for index, (game, rolling_average) in enumerate(
            zip(ordered, rolling_averages, strict=True)
        )
    )

    return TeamHitsAllowedAnalysis(
        team_id=ordered[-1].team_id,
        team_name=ordered[-1].team_name,
        season=ordered[-1].season,
        rolling_window=rolling_window,
        points=points,
        summary=_build_summary(ordered, rolling_window=rolling_window),
    )


def _trailing_averages(values: list[int], window: int) -> list[float]:
    """Return the trailing mean ending at each position.

    The average at index ``i`` covers the ``window`` most recent values up to
    and including ``i``. Early positions use every value available so far
    rather than producing a gap, so game 1 of a season is its own average.

    A plain mean is correct here because hits allowed is a count per game. The
    rates in ``team_pitching`` deliberately do not use this helper.
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
    games: Sequence[TeamGamePitchingLine], *, rolling_window: int
) -> TeamHitsAllowedSummary:
    games_played = len(games)
    hits_allowed = [game.hits_allowed for game in games]

    recent = hits_allowed[-min(rolling_window, games_played) :]
    recent_average = sum(recent) / len(recent)

    prior_window_average: float | None = None
    change_vs_prior_window: float | None = None
    # Two complete windows are required; comparing partial windows would report
    # a change caused by sample size rather than by pitching.
    if games_played >= 2 * rolling_window:
        prior = hits_allowed[
            games_played - 2 * rolling_window : games_played - rolling_window
        ]
        prior_window_average = sum(prior) / len(prior)
        change_vs_prior_window = recent_average - prior_window_average

    total_hits_allowed = sum(hits_allowed)
    total_outs = sum(game.outs for game in games)

    return TeamHitsAllowedSummary(
        games_played=games_played,
        total_hits_allowed=total_hits_allowed,
        total_outs=total_outs,
        season_average=total_hits_allowed / games_played,
        # The one rate on this page, and it follows the summing rule the rest
        # of the pitching rates do rather than averaging per-game values.
        hits_per_nine=total_hits_allowed * OUTS_PER_NINE_INNINGS / total_outs,
        recent_average=recent_average,
        prior_window_average=prior_window_average,
        change_vs_prior_window=change_vs_prior_window,
    )
