"""Tests for team run-scoring analytics.

Every case here is offline and built from normalized batting lines, never from
the database or the MLB API.
"""

from datetime import date

import pytest

from app.analytics.team_runs import (
    DEFAULT_ROLLING_WINDOW,
    TeamRunsAnalysisError,
    build_team_runs_analysis,
)
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_batting_line,
    make_season,
)


def season_of(runs: list[int], **kwargs: object):
    """Build a stored team-season carrying the given per-game run totals."""
    return make_season(hits=[8] * len(runs), runs=runs, **kwargs)


def analysis_of(runs: list[int], *, window: int = 3, **kwargs: object):
    return build_team_runs_analysis(season_of(runs, **kwargs), rolling_window=window)


# ------------------------------------------------------------- the happy path


def test_one_game_is_its_own_season() -> None:
    analysis = analysis_of([5])
    assert analysis.summary.games_played == 1
    assert analysis.summary.season_average == pytest.approx(5.0)
    assert analysis.summary.recent_average == pytest.approx(5.0)
    assert analysis.points[0].rolling_average == pytest.approx(5.0)


def test_multiple_games_become_one_point_each() -> None:
    analysis = analysis_of([5, 3, 7, 1])
    assert len(analysis.points) == 4
    assert [point.runs for point in analysis.points] == [5, 3, 7, 1]


def test_the_analysis_carries_the_team_and_season() -> None:
    analysis = analysis_of([4, 4])
    assert (analysis.team_id, analysis.team_name) == (MARINERS_ID, MARINERS_NAME)
    assert analysis.season == 2025


def test_season_game_numbers_are_a_continuous_index() -> None:
    analysis = analysis_of([2, 2, 2, 2, 2])
    assert [point.season_game_number for point in analysis.points] == [1, 2, 3, 4, 5]


def test_points_carry_the_opponent_and_home_away() -> None:
    analysis = analysis_of([4, 4])
    assert analysis.points[0].opponent_name == TWINS_NAME
    assert analysis.points[0].home_away == "home"
    assert analysis.points[1].home_away == "away"


def test_a_shutout_is_a_real_zero() -> None:
    """Nobody scored is a genuine 0, and it counts as a completed game."""
    analysis = analysis_of([0, 6])
    assert analysis.summary.games_played == 2
    assert analysis.summary.season_average == pytest.approx(3.0)


def test_the_last_game_date_is_exposed_for_the_footer() -> None:
    analysis = analysis_of([4, 4, 4])
    assert analysis.last_game_date == date(2025, 3, 29)


# ------------------------------------------------------------------- ordering


def test_games_are_ordered_by_date_regardless_of_input_order() -> None:
    games = season_of([1, 2, 3])
    analysis = build_team_runs_analysis(list(reversed(games)), rolling_window=3)
    assert [point.runs for point in analysis.points] == [1, 2, 3]


def test_a_doubleheader_keeps_its_real_sequence() -> None:
    """Same date, so game_number decides which half came first."""
    day = date(2025, 5, 18)
    games = [
        make_batting_line(game_pk=900002, game_date=day, game_number=2, runs=9),
        make_batting_line(game_pk=900001, game_date=day, game_number=1, runs=2),
    ]
    analysis = build_team_runs_analysis(games, rolling_window=2)
    assert [point.runs for point in analysis.points] == [2, 9]
    assert [point.game_number for point in analysis.points] == [1, 2]


def test_game_pk_breaks_a_tie_on_date_and_game_number() -> None:
    day = date(2025, 5, 18)
    games = [
        make_batting_line(game_pk=900_020, game_date=day, runs=6),
        make_batting_line(game_pk=900_010, game_date=day, runs=1),
    ]
    analysis = build_team_runs_analysis(games, rolling_window=2)
    assert [point.game_pk for point in analysis.points] == [900_010, 900_020]


# ------------------------------------------------------------ rolling average


def test_the_rolling_average_is_the_trailing_mean() -> None:
    analysis = analysis_of([3, 6, 9, 0], window=2)
    assert [point.rolling_average for point in analysis.points] == pytest.approx(
        [3.0, 4.5, 7.5, 4.5]
    )


def test_early_points_use_every_game_played_so_far() -> None:
    """No gap at the start of a season: game 1 is its own average."""
    analysis = analysis_of([4, 8, 6], window=15)
    assert [point.rolling_average for point in analysis.points] == pytest.approx(
        [4.0, 6.0, 6.0]
    )


def test_a_full_window_stops_growing() -> None:
    analysis = analysis_of([10, 0, 0, 0, 0], window=2)
    assert analysis.points[-1].rolling_average == pytest.approx(0.0)


def test_the_selected_window_is_carried_on_the_analysis() -> None:
    assert analysis_of([4] * 10, window=5).rolling_window == 5


def test_the_default_window_is_fifteen_games() -> None:
    analysis = build_team_runs_analysis(season_of([4] * 20))
    assert analysis.rolling_window == DEFAULT_ROLLING_WINDOW == 15


# ------------------------------------------------------------------- summary


def test_the_season_average_is_total_runs_over_games_played() -> None:
    analysis = analysis_of([5, 3, 7, 1])
    assert analysis.summary.season_average == pytest.approx(16 / 4)


def test_the_season_average_ignores_the_rolling_window() -> None:
    """The window smooths the chart; it does not narrow the season average."""
    wide = analysis_of([2] * 10 + [8] * 10, window=30)
    narrow = analysis_of([2] * 10 + [8] * 10, window=5)
    assert wide.summary.season_average == narrow.summary.season_average


def test_the_recent_average_covers_the_last_window_of_games() -> None:
    analysis = analysis_of([1] * 10 + [7] * 5, window=5)
    assert analysis.summary.recent_average == pytest.approx(7.0)


def test_the_recent_average_uses_every_game_when_the_window_is_longer() -> None:
    analysis = analysis_of([2, 4], window=30)
    assert analysis.summary.recent_average == pytest.approx(3.0)


def test_the_recent_average_matches_the_last_rolling_point() -> None:
    analysis = analysis_of([3, 9, 1, 5, 8], window=3)
    assert analysis.summary.recent_average == pytest.approx(
        analysis.points[-1].rolling_average
    )


def test_games_played_equals_the_number_of_points() -> None:
    analysis = analysis_of([4] * 7)
    assert analysis.summary.games_played == len(analysis.points) == 7


def test_the_prior_window_needs_two_complete_windows() -> None:
    """A partial prior window would report a change caused by sample size."""
    assert analysis_of([4] * 9, window=5).summary.prior_window_average is None
    assert analysis_of([4] * 10, window=5).summary.prior_window_average is not None


def test_the_prior_window_change_is_the_difference_between_the_windows() -> None:
    analysis = analysis_of([2] * 5 + [6] * 5, window=5)
    assert analysis.summary.prior_window_average == pytest.approx(2.0)
    assert analysis.summary.change_vs_prior_window == pytest.approx(4.0)


# ------------------------------------------------------------ rejected inputs


def test_empty_input_is_rejected() -> None:
    with pytest.raises(TeamRunsAnalysisError, match="no completed games"):
        build_team_runs_analysis([])


def test_mixed_teams_are_rejected() -> None:
    games = [
        *season_of([4], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *season_of([5], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    with pytest.raises(TeamRunsAnalysisError, match="one team and one season"):
        build_team_runs_analysis(games)


def test_mixed_seasons_are_rejected() -> None:
    games = [*season_of([4], season=2025), *season_of([5], season=2026)]
    with pytest.raises(TeamRunsAnalysisError, match="one team and one season"):
        build_team_runs_analysis(games)


@pytest.mark.parametrize("window", [0, -1, -30])
def test_a_non_positive_rolling_window_is_rejected(window: int) -> None:
    with pytest.raises(TeamRunsAnalysisError, match="at least 1 game"):
        build_team_runs_analysis(season_of([4, 4]), rolling_window=window)


def test_the_window_is_validated_before_the_games_are_read() -> None:
    """An invalid window is the caller's bug either way; say so plainly."""
    with pytest.raises(TeamRunsAnalysisError, match="at least 1 game"):
        build_team_runs_analysis([], rolling_window=0)


# ----------------------------------------------------------- runs, not hits


def test_the_analysis_reads_runs_and_not_hits() -> None:
    """The two columns are different statistics on the same stored row."""
    games = make_season(hits=[12, 12, 12], runs=[1, 2, 3])
    analysis = build_team_runs_analysis(games, rolling_window=3)
    assert [point.runs for point in analysis.points] == [1, 2, 3]
    assert analysis.summary.season_average == pytest.approx(2.0)
