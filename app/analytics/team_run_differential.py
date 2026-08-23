"""Team run differential and Pythagorean expectation over paired game results.

Answers one question:

    Is this team outscoring its opponents, and does its record reflect that?

Every other analytics module in this package reads one team's own batting
line. This one is different: it reads ``TeamGameRunResult`` records, each of
which pairs a team's game with the opponent's game. Runs allowed is not a
stored column and not an MLB request — it is the opponent's runs scored, which
is the same number seen from the other side.

Two things follow from having both sides of every game:

- **Run differential**, the signed per-game margin. Unlike hits, runs, batting
  strikeouts, and baserunners, this statistic can be negative, so nothing here
  may assume a non-negative value or a zero floor.
- **Win/loss**, derived rather than stored. A completed MLB game cannot end
  tied, so a team that outscored its opponent won it. That gives an actual
  record to place beside the Pythagorean expectation without a W/L column.

Like the rest of the package this layer is free of FastAPI, Jinja, SQLAlchemy,
Plotly, and the MLB API.
"""

from collections.abc import Sequence

from app.schemas.analytics import (
    PythagoreanRecord,
    TeamRunDifferentialAnalysis,
    TeamRunDifferentialPoint,
    TeamRunDifferentialSummary,
)
from app.schemas.games import TeamGameRunResult

DEFAULT_ROLLING_WINDOW = 15

PYTHAGOREAN_EXPONENT = 1.83
"""Bill James' Pythagorean exponent as refined by Baseball Reference.

The original formula squared runs. 1.83 is the exponent that best fits modern
MLB scoring levels, and is the one Baseball Reference publishes against, so
figures on this page can be checked against a public source.
"""


class TeamRunDifferentialAnalysisError(ValueError):
    """Run differential analysis was requested with input it cannot describe."""


class MissingOpponentDataError(TeamRunDifferentialAnalysisError):
    """Some games of the team-season have no stored opponent line.

    Runs allowed for those games is unknown, not zero. Describing the season
    without them would understate runs allowed and overstate run differential
    by a margin that looks entirely plausible, so the analysis refuses instead.

    The remedy differs from the batting strikeout and baserunner backfills:
    nothing is wrong with the team's own rows, and re-importing the team will
    not help. The opponents' rows are what is absent, which is what importing
    the league season provides.
    """

    def __init__(self, *, season: int, missing_game_count: int, total_games: int):
        self.season = season
        self.missing_game_count = missing_game_count
        self.total_games = total_games
        super().__init__(
            f"{missing_game_count} of {total_games} games in the {season} season "
            "have no stored opponent line, so runs allowed is unknown for them. "
            "Import the full league season to pair every game."
        )


def build_team_run_differential_analysis(
    results: Sequence[TeamGameRunResult],
    *,
    unpaired_game_count: int = 0,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> TeamRunDifferentialAnalysis:
    """Calculate a team-season's run differential trend and Pythagorean record.

    Games are ordered by date, then MLB game number, then game id, so both
    halves of a doubleheader keep their real sequence. The x axis of the chart
    is ``season_game_number``, a continuous 1-based index over that order.

    Raises
    ------
    MissingOpponentDataError
        ``unpaired_game_count`` is positive, meaning at least one game of the
        season has no stored opponent line and runs allowed is unknown for it.
    TeamRunDifferentialAnalysisError
        ``results`` is empty, mixes team-seasons, or ``rolling_window`` is not
        a positive number of games.
    """
    if rolling_window < 1:
        raise TeamRunDifferentialAnalysisError(
            f"rolling_window must be at least 1 game, got {rolling_window}"
        )
    if unpaired_game_count < 0:
        raise TeamRunDifferentialAnalysisError(
            f"unpaired_game_count cannot be negative, got {unpaired_game_count}"
        )
    if not results and not unpaired_game_count:
        raise TeamRunDifferentialAnalysisError(
            "Cannot analyse run differential for a team-season with no completed games"
        )

    ordered = sorted(
        results, key=lambda game: (game.game_date, game.game_number, game.game_pk)
    )
    if unpaired_game_count:
        # Raised before the team-season consistency check below so that a
        # team-season imported on its own — where `ordered` is empty and there
        # is no team id to report — still gets the message that names the fix.
        season = ordered[-1].season if ordered else 0
        raise MissingOpponentDataError(
            season=season,
            missing_game_count=unpaired_game_count,
            total_games=len(ordered) + unpaired_game_count,
        )

    team_ids = {game.team_id for game in ordered}
    seasons = {game.season for game in ordered}
    if len(team_ids) > 1 or len(seasons) > 1:
        raise TeamRunDifferentialAnalysisError(
            "All games must belong to one team and one season, got teams "
            f"{sorted(team_ids)} and seasons {sorted(seasons)}"
        )

    differentials = [game.run_differential for game in ordered]
    rolling_averages = _trailing_averages(differentials, rolling_window)
    points = tuple(
        TeamRunDifferentialPoint(
            game_pk=game.game_pk,
            game_number=game.game_number,
            season_game_number=index + 1,
            game_date=game.game_date,
            opponent_name=game.opponent_name,
            home_away=game.home_away,
            runs_scored=game.runs_scored,
            runs_allowed=game.runs_allowed,
            run_differential=game.run_differential,
            is_win=game.is_win,
            rolling_average=rolling_average,
        )
        for index, (game, rolling_average) in enumerate(
            zip(ordered, rolling_averages, strict=True)
        )
    )

    return TeamRunDifferentialAnalysis(
        team_id=ordered[-1].team_id,
        team_name=ordered[-1].team_name,
        season=ordered[-1].season,
        rolling_window=rolling_window,
        points=points,
        summary=_build_summary(ordered, rolling_window=rolling_window),
        pythagorean=_build_pythagorean_record(ordered),
    )


def _trailing_averages(values: list[int], window: int) -> list[float]:
    """Return the trailing mean ending at each position.

    The average at index ``i`` covers the ``window`` most recent values up to
    and including ``i``. Early positions use every value available so far
    rather than producing a gap, so game 1 of a season is its own average.

    Identical in shape to the helper in the other trend modules, but the values
    here are signed, so the running total can go negative.
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
    games: list[TeamGameRunResult], *, rolling_window: int
) -> TeamRunDifferentialSummary:
    games_played = len(games)
    differentials = [game.run_differential for game in games]

    recent = differentials[-min(rolling_window, games_played) :]
    recent_average = sum(recent) / len(recent)

    prior_window_average: float | None = None
    change_vs_prior_window: float | None = None
    # Two complete windows are required; comparing partial windows would report
    # a change caused by sample size rather than by run differential.
    if games_played >= 2 * rolling_window:
        prior = differentials[
            games_played - 2 * rolling_window : games_played - rolling_window
        ]
        prior_window_average = sum(prior) / len(prior)
        change_vs_prior_window = recent_average - prior_window_average

    total_runs_scored = sum(game.runs_scored for game in games)
    total_runs_allowed = sum(game.runs_allowed for game in games)

    return TeamRunDifferentialSummary(
        games_played=games_played,
        total_runs_scored=total_runs_scored,
        total_runs_allowed=total_runs_allowed,
        total_run_differential=total_runs_scored - total_runs_allowed,
        season_average=(total_runs_scored - total_runs_allowed) / games_played,
        recent_average=recent_average,
        prior_window_average=prior_window_average,
        change_vs_prior_window=change_vs_prior_window,
    )


def _build_pythagorean_record(games: list[TeamGameRunResult]) -> PythagoreanRecord:
    """Build the expected-versus-actual record for a team-season.

    Both halves come from the same paired games, so the expectation and the
    record it is compared against always describe exactly the same sample.
    """
    total_runs_scored = sum(game.runs_scored for game in games)
    total_runs_allowed = sum(game.runs_allowed for game in games)
    games_played = len(games)

    actual_wins = sum(1 for game in games if game.is_win)
    actual_losses = games_played - actual_wins

    scored_component = total_runs_scored**PYTHAGOREAN_EXPONENT
    allowed_component = total_runs_allowed**PYTHAGOREAN_EXPONENT
    denominator = scored_component + allowed_component
    if denominator == 0:
        # Reachable only if a team neither scored nor allowed a run across
        # every completed game of the season, which no real season contains.
        raise TeamRunDifferentialAnalysisError(
            "Pythagorean expectation is undefined for a team-season with no runs "
            "scored and no runs allowed"
        )

    expected_win_pct = scored_component / denominator
    expected_wins = expected_win_pct * games_played

    return PythagoreanRecord(
        exponent=PYTHAGOREAN_EXPONENT,
        runs_scored=total_runs_scored,
        runs_allowed=total_runs_allowed,
        expected_win_pct=expected_win_pct,
        expected_wins=expected_wins,
        actual_wins=actual_wins,
        actual_losses=actual_losses,
        actual_win_pct=actual_wins / games_played,
        wins_above_expectation=actual_wins - expected_wins,
    )
