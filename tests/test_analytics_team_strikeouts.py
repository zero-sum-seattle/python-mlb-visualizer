"""Tests for team batting strikeout analytics.

Every calculation is checked against a hand-computed expectation rather than
against a reimplementation of the same loop.
"""

from datetime import date, timedelta

import pytest

from app.analytics.team_strikeouts import (
    MissingStrikeoutDataError,
    TeamStrikeoutsAnalysisError,
    build_team_strikeouts_analysis,
)
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    make_batting_line,
    make_season,
)


def season_with(strikeouts: list[int | None], **kwargs: object) -> list:
    """Build a team-season carrying the given batting strikeout totals."""
    return make_season(hits=[8] * len(strikeouts), strikeouts=strikeouts, **kwargs)


def test_points_follow_season_order() -> None:
    analysis = build_team_strikeouts_analysis(
        season_with([10, 8, 12]), rolling_window=3
    )
    assert [point.season_game_number for point in analysis.points] == [1, 2, 3]
    assert [point.strikeouts for point in analysis.points] == [10, 8, 12]


def test_unordered_input_is_sorted_before_analysis() -> None:
    games = season_with([10, 8, 12])
    analysis = build_team_strikeouts_analysis(list(reversed(games)), rolling_window=3)
    assert [point.strikeouts for point in analysis.points] == [10, 8, 12]


def test_doubleheader_games_keep_their_real_sequence() -> None:
    """Same date, so game_number decides the order, not the game id."""
    day = date(2025, 5, 1)
    games = [
        make_batting_line(game_pk=500, game_date=day, game_number=2, strikeouts=4),
        make_batting_line(game_pk=900, game_date=day, game_number=1, strikeouts=11),
    ]
    analysis = build_team_strikeouts_analysis(games, rolling_window=2)
    assert [point.strikeouts for point in analysis.points] == [11, 4]


def test_raw_strikeouts_are_carried_through_unmodified() -> None:
    analysis = build_team_strikeouts_analysis(season_with([0, 17, 5]), rolling_window=3)
    assert [point.strikeouts for point in analysis.points] == [0, 17, 5]


def test_rolling_average_is_trailing() -> None:
    """Game 4 averages games 2-4, never games 3-5."""
    analysis = build_team_strikeouts_analysis(
        season_with([10, 4, 7, 1, 100]), rolling_window=3
    )
    assert analysis.points[3].rolling_average == pytest.approx((4 + 7 + 1) / 3)


def test_early_games_use_every_game_played_so_far() -> None:
    analysis = build_team_strikeouts_analysis(
        season_with([10, 8, 12, 6, 9]), rolling_window=15
    )
    assert [point.rolling_average for point in analysis.points] == pytest.approx(
        [10.0, 9.0, 10.0, 9.0, 9.0]
    )


def test_no_early_season_gap_is_left() -> None:
    analysis = build_team_strikeouts_analysis(season_with([7] * 4), rolling_window=30)
    assert all(point.rolling_average is not None for point in analysis.points)
    assert len(analysis.points) == 4


def test_first_game_is_its_own_rolling_average() -> None:
    analysis = build_team_strikeouts_analysis(season_with([13, 2]), rolling_window=10)
    assert analysis.points[0].rolling_average == 13.0


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_each_supported_window_uses_exactly_that_many_games(window: int) -> None:
    values = [index % 7 for index in range(2 * window)]
    analysis = build_team_strikeouts_analysis(
        season_with(values), rolling_window=window
    )
    expected = sum(values[-window:]) / window
    assert analysis.rolling_window == window
    assert analysis.points[-1].rolling_average == pytest.approx(expected)


def test_precision_is_kept_internally() -> None:
    """1/3 is not rounded away; presentation does the rounding."""
    analysis = build_team_strikeouts_analysis(season_with([1, 1, 0]), rolling_window=3)
    assert analysis.points[-1].rolling_average == pytest.approx(2 / 3)


def test_season_average_covers_every_stored_game() -> None:
    analysis = build_team_strikeouts_analysis(
        season_with([10, 8, 12, 6]), rolling_window=2
    )
    assert analysis.summary.season_average == pytest.approx(36 / 4)


def test_recent_average_covers_the_last_window() -> None:
    analysis = build_team_strikeouts_analysis(
        season_with([10, 8, 12, 6]), rolling_window=2
    )
    assert analysis.summary.recent_average == pytest.approx((12 + 6) / 2)


def test_recent_average_matches_the_last_rolling_point() -> None:
    analysis = build_team_strikeouts_analysis(
        season_with([3, 9, 4, 11, 6]), rolling_window=3
    )
    assert analysis.summary.recent_average == pytest.approx(
        analysis.points[-1].rolling_average
    )


def test_prior_window_compares_two_complete_windows() -> None:
    analysis = build_team_strikeouts_analysis(
        season_with([2, 2, 2, 8, 8, 8]), rolling_window=3
    )
    assert analysis.summary.prior_window_average == pytest.approx(2.0)
    assert analysis.summary.recent_average == pytest.approx(8.0)
    assert analysis.summary.change_vs_prior_window == pytest.approx(6.0)


def test_prior_window_is_none_without_two_complete_windows() -> None:
    analysis = build_team_strikeouts_analysis(season_with([5] * 5), rolling_window=3)
    assert analysis.summary.prior_window_average is None
    assert analysis.summary.change_vs_prior_window is None


def test_prior_window_appears_exactly_at_two_full_windows() -> None:
    short = build_team_strikeouts_analysis(season_with([5] * 5), rolling_window=3)
    exact = build_team_strikeouts_analysis(season_with([5] * 6), rolling_window=3)
    assert short.summary.change_vs_prior_window is None
    assert exact.summary.change_vs_prior_window == pytest.approx(0.0)


def test_a_decrease_is_reported_as_a_negative_change() -> None:
    """More strikeouts is not treated as good; the sign is just the direction."""
    analysis = build_team_strikeouts_analysis(
        season_with([9, 9, 3, 3]), rolling_window=2
    )
    assert analysis.summary.change_vs_prior_window == pytest.approx(-6.0)


def test_summary_games_played_counts_stored_games() -> None:
    analysis = build_team_strikeouts_analysis(season_with([4] * 7), rolling_window=5)
    assert analysis.summary.games_played == 7
    assert len(analysis.points) == 7


def test_team_and_season_come_from_the_records() -> None:
    analysis = build_team_strikeouts_analysis(season_with([6, 6]), rolling_window=2)
    assert (analysis.team_id, analysis.team_name, analysis.season) == (
        MARINERS_ID,
        MARINERS_NAME,
        2025,
    )


def test_opponent_and_home_away_reach_the_points() -> None:
    analysis = build_team_strikeouts_analysis(season_with([6, 6]), rolling_window=2)
    assert [point.home_away for point in analysis.points] == ["home", "away"]
    assert all(point.opponent_name for point in analysis.points)


def test_last_game_date_is_the_most_recent_game() -> None:
    analysis = build_team_strikeouts_analysis(season_with([1, 2, 3]), rolling_window=2)
    assert analysis.last_game_date == date(2025, 3, 29)


def test_a_single_missing_strikeout_total_is_refused() -> None:
    with pytest.raises(MissingStrikeoutDataError) as excinfo:
        build_team_strikeouts_analysis(season_with([10, None, 12]), rolling_window=3)
    assert excinfo.value.games_missing == 1
    assert excinfo.value.games_total == 3


def test_missing_totals_are_never_read_as_zero() -> None:
    """A zero would drag the average down and look like a real result."""
    with pytest.raises(MissingStrikeoutDataError):
        build_team_strikeouts_analysis(season_with([10, None]), rolling_window=2)


def test_missing_totals_are_never_silently_dropped() -> None:
    """Analysing only the known games would misdescribe the season."""
    with pytest.raises(MissingStrikeoutDataError):
        build_team_strikeouts_analysis(season_with([None] + [8] * 20), rolling_window=5)


def test_a_fully_legacy_season_reports_every_game_as_missing() -> None:
    with pytest.raises(MissingStrikeoutDataError) as excinfo:
        build_team_strikeouts_analysis(season_with([None] * 4), rolling_window=2)
    assert (excinfo.value.games_missing, excinfo.value.games_total) == (4, 4)


def test_missing_data_error_names_the_re_import_remedy() -> None:
    with pytest.raises(MissingStrikeoutDataError) as excinfo:
        build_team_strikeouts_analysis(season_with([None, 5]), rolling_window=2)
    assert "re-import" in str(excinfo.value)


def test_zero_strikeouts_is_analysed_rather_than_refused() -> None:
    analysis = build_team_strikeouts_analysis(season_with([0, 0]), rolling_window=2)
    assert analysis.summary.season_average == 0.0


def test_empty_input_is_rejected() -> None:
    with pytest.raises(TeamStrikeoutsAnalysisError):
        build_team_strikeouts_analysis([], rolling_window=5)


@pytest.mark.parametrize("window", [0, -1])
def test_non_positive_windows_are_rejected(window: int) -> None:
    with pytest.raises(TeamStrikeoutsAnalysisError):
        build_team_strikeouts_analysis(season_with([5, 5]), rolling_window=window)


def test_mixed_teams_are_rejected() -> None:
    games = season_with([5, 5]) + season_with(
        [6, 6], team_id=112, team_name="Chicago Cubs"
    )
    with pytest.raises(TeamStrikeoutsAnalysisError) as excinfo:
        build_team_strikeouts_analysis(games, rolling_window=2)
    assert "one team and one season" in str(excinfo.value)


def test_mixed_seasons_are_rejected() -> None:
    games = season_with([5, 5]) + season_with([6, 6], season=2024)
    with pytest.raises(TeamStrikeoutsAnalysisError) as excinfo:
        build_team_strikeouts_analysis(games, rolling_window=2)
    assert "one team and one season" in str(excinfo.value)


def test_mixed_teams_are_rejected_before_missing_data_is_reported() -> None:
    """A mixed-team request is malformed regardless of what it contains."""
    games = season_with([None, None]) + season_with(
        [None, None], team_id=112, team_name="Chicago Cubs"
    )
    with pytest.raises(TeamStrikeoutsAnalysisError) as excinfo:
        build_team_strikeouts_analysis(games, rolling_window=2)
    assert not isinstance(excinfo.value, MissingStrikeoutDataError)


def test_a_long_season_stays_consistent_end_to_end() -> None:
    """162 games with a known pattern; spot-check the shape holds."""
    values = [(index % 5) + 5 for index in range(162)]
    start = date(2025, 3, 27)
    games = [
        make_batting_line(
            game_pk=700000 + index,
            game_date=start + timedelta(days=index),
            strikeouts=value,
        )
        for index, value in enumerate(values)
    ]
    analysis = build_team_strikeouts_analysis(games, rolling_window=15)
    assert analysis.summary.games_played == 162
    assert analysis.summary.season_average == pytest.approx(sum(values) / 162)
    assert analysis.points[-1].rolling_average == pytest.approx(sum(values[-15:]) / 15)
