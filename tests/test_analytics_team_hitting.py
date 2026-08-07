"""Tests for the team hitting analytics layer."""

from datetime import date

import pytest

from app.analytics.team_hitting import (
    DEFAULT_ROLLING_WINDOW,
    TeamHitsAnalysisError,
    build_team_hits_analysis,
)
from tests.factories import make_batting_line, make_season


def rolling_averages(hits: list[int], window: int) -> list[float]:
    analysis = build_team_hits_analysis(make_season(hits), rolling_window=window)
    return [point.rolling_average for point in analysis.points]


def test_default_rolling_window_is_fifteen_games() -> None:
    assert DEFAULT_ROLLING_WINDOW == 15


def test_games_are_returned_in_deterministic_order() -> None:
    games = make_season([1, 2, 3, 4, 5])
    shuffled = [games[3], games[0], games[4], games[2], games[1]]
    analysis = build_team_hits_analysis(shuffled, rolling_window=3)
    assert [point.game_pk for point in analysis.points] == [
        game.game_pk for game in games
    ]


def test_season_game_numbers_are_sequential() -> None:
    analysis = build_team_hits_analysis(make_season([4] * 7), rolling_window=3)
    assert [point.season_game_number for point in analysis.points] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
    ]


def test_raw_hits_are_preserved() -> None:
    hits = [11, 3, 8, 0, 14]
    analysis = build_team_hits_analysis(make_season(hits), rolling_window=3)
    assert [point.hits for point in analysis.points] == hits


def test_game_context_is_carried_onto_each_point() -> None:
    game = make_batting_line(hits=6, home_away="away", opponent_name="Texas Rangers")
    point = build_team_hits_analysis([game], rolling_window=5).points[0]
    assert point.game_date == game.game_date
    assert point.opponent_name == "Texas Rangers"
    assert point.home_away == "away"
    assert point.game_number == 1


def test_fifteen_game_rolling_average_is_calculated_correctly() -> None:
    averages = rolling_averages(list(range(1, 21)), 15)
    # Game 15 covers games 1-15, game 16 covers games 2-16, game 20 covers 6-20.
    assert averages[14] == pytest.approx(8.0)
    assert averages[15] == pytest.approx(9.0)
    assert averages[19] == pytest.approx(13.0)


def test_first_game_uses_a_one_game_partial_window() -> None:
    averages = rolling_averages([7, 1, 1, 1], 15)
    assert averages[0] == pytest.approx(7.0)


def test_early_season_partial_windows_use_every_game_so_far() -> None:
    averages = rolling_averages([2, 4, 6, 8, 10], 15)
    assert averages == pytest.approx([2.0, 3.0, 4.0, 5.0, 6.0])


def test_rolling_window_drops_the_oldest_game_once_it_is_full() -> None:
    # Window of 3: the fourth game replaces the first rather than adding to it.
    averages = rolling_averages([3, 6, 9, 12], 3)
    assert averages == pytest.approx([3.0, 4.5, 6.0, 9.0])


def test_rolling_average_is_trailing_not_centered() -> None:
    # A spike in the last game can only move the last average.
    averages = rolling_averages([2, 2, 2, 2, 30], 3)
    assert averages[:4] == pytest.approx([2.0, 2.0, 2.0, 2.0])
    assert averages[4] == pytest.approx((2 + 2 + 30) / 3)


def test_season_average_is_total_hits_over_completed_games() -> None:
    analysis = build_team_hits_analysis(make_season([5, 10, 6]), rolling_window=15)
    assert analysis.season_average == pytest.approx(7.0)
    assert analysis.summary.season_average == pytest.approx(7.0)


def test_recent_average_uses_the_most_recent_window() -> None:
    analysis = build_team_hits_analysis(
        make_season([1, 1, 1, 1, 9, 9, 9]), rolling_window=3
    )
    assert analysis.summary.recent_average == pytest.approx(9.0)


def test_recent_average_uses_all_games_when_fewer_than_the_window() -> None:
    analysis = build_team_hits_analysis(make_season([4, 8]), rolling_window=15)
    assert analysis.summary.recent_average == pytest.approx(6.0)


def test_prior_window_average_uses_the_immediately_preceding_window() -> None:
    analysis = build_team_hits_analysis(
        make_season([1] * 15 + [3] * 15), rolling_window=15
    )
    assert analysis.summary.prior_window_average == pytest.approx(1.0)
    assert analysis.summary.recent_average == pytest.approx(3.0)


def test_prior_window_average_is_none_without_two_complete_windows() -> None:
    analysis = build_team_hits_analysis(make_season([6] * 29), rolling_window=15)
    assert analysis.summary.prior_window_average is None
    assert analysis.summary.change_vs_prior_window is None


def test_prior_window_average_appears_at_exactly_two_windows() -> None:
    analysis = build_team_hits_analysis(make_season([6] * 30), rolling_window=15)
    assert analysis.summary.prior_window_average == pytest.approx(6.0)


def test_change_vs_prior_window_is_recent_minus_prior() -> None:
    analysis = build_team_hits_analysis(
        make_season([4] * 5 + [7] * 5), rolling_window=5
    )
    assert analysis.summary.change_vs_prior_window == pytest.approx(3.0)


def test_change_vs_prior_window_can_be_negative() -> None:
    analysis = build_team_hits_analysis(
        make_season([10] * 5 + [8] * 5), rolling_window=5
    )
    assert analysis.summary.change_vs_prior_window == pytest.approx(-2.0)


def test_doubleheader_games_keep_their_order() -> None:
    second = make_batting_line(
        game_pk=900002,
        game_date=date(2025, 6, 1),
        game_number=2,
        doubleheader=True,
        hits=2,
    )
    first = make_batting_line(
        game_pk=900001,
        game_date=date(2025, 6, 1),
        game_number=1,
        doubleheader=True,
        hits=10,
    )
    later = make_batting_line(game_pk=900003, game_date=date(2025, 6, 2), hits=4)
    analysis = build_team_hits_analysis([later, second, first], rolling_window=3)
    assert [point.game_pk for point in analysis.points] == [900001, 900002, 900003]
    assert [point.season_game_number for point in analysis.points] == [1, 2, 3]
    assert [point.game_number for point in analysis.points] == [1, 2, 1]


def test_games_played_counts_every_game() -> None:
    analysis = build_team_hits_analysis(make_season([3] * 162), rolling_window=15)
    assert analysis.summary.games_played == 162
    assert len(analysis.points) == 162


def test_team_identity_comes_from_the_games() -> None:
    analysis = build_team_hits_analysis(make_season([3, 4]), rolling_window=5)
    assert analysis.team_id == 136
    assert analysis.team_name == "Seattle Mariners"
    assert analysis.season == 2025
    assert analysis.rolling_window == 5


def test_last_game_date_is_the_most_recent_game() -> None:
    analysis = build_team_hits_analysis(make_season([3, 4, 5]), rolling_window=5)
    assert analysis.last_game_date == date(2025, 3, 29)


def test_empty_input_is_rejected_explicitly() -> None:
    with pytest.raises(TeamHitsAnalysisError, match="no completed games"):
        build_team_hits_analysis([], rolling_window=15)


@pytest.mark.parametrize("window", [0, -1])
def test_non_positive_rolling_window_is_rejected(window: int) -> None:
    with pytest.raises(TeamHitsAnalysisError, match="at least 1 game"):
        build_team_hits_analysis(make_season([4, 5]), rolling_window=window)


def test_mixed_teams_are_rejected() -> None:
    games = [
        make_batting_line(game_pk=1, team_id=136, team_name="Seattle Mariners"),
        make_batting_line(game_pk=2, team_id=112, team_name="Chicago Cubs"),
    ]
    with pytest.raises(TeamHitsAnalysisError, match="one team and one season"):
        build_team_hits_analysis(games, rolling_window=5)


def test_mixed_seasons_are_rejected() -> None:
    games = [
        make_batting_line(game_pk=1, season=2024, game_date=date(2024, 4, 1)),
        make_batting_line(game_pk=2, season=2025, game_date=date(2025, 4, 1)),
    ]
    with pytest.raises(TeamHitsAnalysisError, match="one team and one season"):
        build_team_hits_analysis(games, rolling_window=5)


def test_window_larger_than_the_season_averages_every_game() -> None:
    analysis = build_team_hits_analysis(make_season([2, 4, 6]), rolling_window=30)
    assert analysis.points[-1].rolling_average == pytest.approx(4.0)
    assert analysis.summary.recent_average == pytest.approx(4.0)


def test_analysis_is_immutable() -> None:
    analysis = build_team_hits_analysis(make_season([2, 4]), rolling_window=5)
    with pytest.raises(ValueError, match="frozen"):
        analysis.season_average = 99.0
