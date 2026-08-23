"""Team pitching rate calculations over normalized game pitching lines.

Answers one question:

    How effectively is this team preventing runs, and how is that changing as
    the season progresses?

This layer differs from every other analytics module in the package in one way
that shapes all of it: **the statistics here are rates, not counts.**

Hits, runs, batting strikeouts, and baserunners are counts per game, and the
mean of the per-game values is the right season figure. ERA, WHIP, K/9 and BB/9
are ratios of two quantities that both vary game to game, and averaging the
per-game ratios is **not** the same statistic as the season ratio. For the 2025
Mariners the season ERA is 3.870, while the mean of the 162 game ERAs is 3.965
— an error of nearly a tenth of a run that would not match any published
figure.

So every rate here is calculated by summing the numerator and denominator
across the games in scope and dividing once, which is what the published
figures do. ``_aggregate_rates`` is the single place that happens; nothing in
this module averages a rate.

Innings never appear as a stored or intermediate float in baseball notation.
The denominator is outs, and the ``27`` and ``9`` multipliers convert to a
per-nine-innings rate directly.

Like the rest of the package this layer is free of FastAPI, Jinja, SQLAlchemy,
Plotly, and the MLB API.
"""

from collections.abc import Sequence

from app.schemas.analytics import (
    TeamPitchingAnalysis,
    TeamPitchingPoint,
    TeamPitchingRates,
    TeamPitchingSummary,
)
from app.schemas.games import (
    OUTS_PER_INNING,
    OUTS_PER_NINE_INNINGS,
    TeamGamePitchingLine,
)

DEFAULT_ROLLING_WINDOW = 15


class TeamPitchingAnalysisError(ValueError):
    """Team pitching analysis was requested with input it cannot describe."""


def build_team_pitching_analysis(
    games: Sequence[TeamGamePitchingLine],
    *,
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
) -> TeamPitchingAnalysis:
    """Calculate a team-season's ERA trend and season pitching rates.

    Games are ordered by date, then MLB game number, then game id, so both
    halves of a doubleheader keep their real sequence. The x axis of the chart
    is ``season_game_number``, a continuous 1-based index over that order.

    Every column on a stored pitching line is NOT NULL, so unlike the batting
    strikeout and baserunner metrics there is no unknown-value state to guard
    against. A team-season either has pitching rows or has none.

    Raises
    ------
    TeamPitchingAnalysisError
        ``games`` is empty, mixes team-seasons, ``rolling_window`` is not a
        positive number of games, or the season recorded no outs at all.
    """
    if rolling_window < 1:
        raise TeamPitchingAnalysisError(
            f"rolling_window must be at least 1 game, got {rolling_window}"
        )
    if not games:
        raise TeamPitchingAnalysisError(
            "Cannot analyse pitching for a team-season with no completed games"
        )

    ordered = sorted(
        games, key=lambda game: (game.game_date, game.game_number, game.game_pk)
    )
    team_ids = {game.team_id for game in ordered}
    seasons = {game.season for game in ordered}
    if len(team_ids) > 1 or len(seasons) > 1:
        raise TeamPitchingAnalysisError(
            "All games must belong to one team and one season, got teams "
            f"{sorted(team_ids)} and seasons {sorted(seasons)}"
        )

    if sum(game.outs for game in ordered) == 0:
        raise TeamPitchingAnalysisError(
            "Cannot analyse pitching for a team-season with no recorded outs; "
            "every rate would divide by zero"
        )

    rolling_eras = _trailing_eras(ordered, rolling_window)
    points = tuple(
        TeamPitchingPoint(
            game_pk=game.game_pk,
            game_number=game.game_number,
            season_game_number=index + 1,
            game_date=game.game_date,
            opponent_name=game.opponent_name,
            home_away=game.home_away,
            outs=game.outs,
            innings_pitched_display=game.innings_pitched_display,
            earned_runs=game.earned_runs,
            runs_allowed=game.runs_allowed,
            hits_allowed=game.hits_allowed,
            base_on_balls=game.base_on_balls,
            strikeouts=game.strikeouts,
            number_of_pitches=game.number_of_pitches,
            strikes=game.strikes,
            game_era=_era(earned_runs=game.earned_runs, outs=game.outs),
            rolling_era=rolling_era,
        )
        for index, (game, rolling_era) in enumerate(
            zip(ordered, rolling_eras, strict=True)
        )
    )

    return TeamPitchingAnalysis(
        team_id=ordered[-1].team_id,
        team_name=ordered[-1].team_name,
        season=ordered[-1].season,
        rolling_window=rolling_window,
        points=points,
        summary=_build_summary(ordered, rolling_window=rolling_window),
    )


def _era(*, earned_runs: int, outs: int) -> float:
    """Earned runs per nine innings.

    Undefined with no outs, which the caller must rule out. A game where a team
    recorded no outs is not something MLB produces for a completed game, but a
    zero denominator is worth failing loudly on rather than returning infinity.
    """
    if outs == 0:
        raise TeamPitchingAnalysisError(
            "ERA is undefined for a game with no recorded outs"
        )
    return earned_runs * OUTS_PER_NINE_INNINGS / outs


def _aggregate_rates(games: Sequence[TeamGamePitchingLine]) -> TeamPitchingRates:
    """Calculate every rate over a set of games by summing, never by averaging.

    This is the whole correctness point of the module. Each rate divides one
    summed total by another, exactly the way a published season line does. A
    mean of the per-game rates would weight a two-inning game the same as a
    thirteen-inning one and produce a figure that matches no public source.
    """
    outs = sum(game.outs for game in games)
    if outs == 0:
        raise TeamPitchingAnalysisError(
            "Pitching rates are undefined over games with no recorded outs"
        )

    earned_runs = sum(game.earned_runs for game in games)
    hits_allowed = sum(game.hits_allowed for game in games)
    base_on_balls = sum(game.base_on_balls for game in games)
    strikeouts = sum(game.strikeouts for game in games)
    home_runs_allowed = sum(game.home_runs_allowed for game in games)
    number_of_pitches = sum(game.number_of_pitches for game in games)
    strikes = sum(game.strikes for game in games)
    innings = outs / OUTS_PER_INNING

    return TeamPitchingRates(
        outs=outs,
        innings_pitched=innings,
        earned_runs=earned_runs,
        hits_allowed=hits_allowed,
        base_on_balls=base_on_balls,
        strikeouts=strikeouts,
        home_runs_allowed=home_runs_allowed,
        number_of_pitches=number_of_pitches,
        strikes=strikes,
        pitches_per_game=number_of_pitches / len(games),
        strike_percentage=strikes / number_of_pitches if number_of_pitches else 0.0,
        era=earned_runs * OUTS_PER_NINE_INNINGS / outs,
        whip=(hits_allowed + base_on_balls) / innings,
        strikeouts_per_nine=strikeouts * OUTS_PER_NINE_INNINGS / outs,
        walks_per_nine=base_on_balls * OUTS_PER_NINE_INNINGS / outs,
    )


def _trailing_eras(games: Sequence[TeamGamePitchingLine], window: int) -> list[float]:
    """Return the trailing ERA ending at each game.

    The ERA at index ``i`` covers the ``window`` most recent games up to and
    including ``i``. Early positions use every game available so far rather
    than producing a gap, so game 1 of a season is its own ERA.

    Both the earned runs and the outs are accumulated, and the division happens
    once per position. Keeping a running mean of game ERAs here would reproduce
    exactly the averaging error this module exists to avoid.
    """
    eras: list[float] = []
    running_earned_runs = 0
    running_outs = 0
    for index, game in enumerate(games):
        running_earned_runs += game.earned_runs
        running_outs += game.outs
        if index >= window:
            dropped = games[index - window]
            running_earned_runs -= dropped.earned_runs
            running_outs -= dropped.outs
        eras.append(_era(earned_runs=running_earned_runs, outs=running_outs))
    return eras


def _build_summary(
    games: Sequence[TeamGamePitchingLine], *, rolling_window: int
) -> TeamPitchingSummary:
    games_played = len(games)

    season = _aggregate_rates(games)
    recent = _aggregate_rates(games[-min(rolling_window, games_played) :])

    prior_window_era: float | None = None
    change_vs_prior_window: float | None = None
    # Two complete windows are required; comparing partial windows would report
    # a change caused by sample size rather than by pitching.
    if games_played >= 2 * rolling_window:
        prior = games[games_played - 2 * rolling_window : games_played - rolling_window]
        prior_window_era = _aggregate_rates(prior).era
        change_vs_prior_window = recent.era - prior_window_era

    return TeamPitchingSummary(
        games_played=games_played,
        season=season,
        recent_era=recent.era,
        prior_window_era=prior_window_era,
        change_vs_prior_window=change_vs_prior_window,
    )


def build_pitch_count_points(
    analysis: TeamPitchingAnalysis,
) -> tuple[tuple[int, ...], tuple[float, ...]]:
    """Return per-game pitch counts and their trailing rolling mean.

    Pitches per game is a **count**, not a rate, so unlike everything else in
    this module a plain mean of the per-game values is the right figure. The
    aggregation warning in the module docstring applies to ERA, WHIP, K/9 and
    BB/9 — ratios of two varying quantities — and not to this one.
    """
    counts = [point.number_of_pitches for point in analysis.points]
    return tuple(counts), tuple(_trailing_means(counts, analysis.rolling_window))


def _trailing_means(values: list[int], window: int) -> list[float]:
    """Return the trailing mean ending at each position.

    The same trailing-window definition the count-based pages use: the mean at
    index ``i`` covers the ``window`` most recent values up to and including
    ``i``, and early positions use every value available so far.
    """
    means: list[float] = []
    running = 0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        means.append(running / min(index + 1, window))
    return means
