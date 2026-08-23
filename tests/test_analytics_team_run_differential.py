"""Tests for team run differential and Pythagorean expectation.

The metric is unlike the other four in one way that shapes most of these
tests: it is signed. A team can be outscored, so nothing here may assume a
non-negative value, and several cases below exist only to prove that a losing
team is described correctly rather than clamped at zero.
"""

from datetime import date

import pytest

from app.analytics.team_run_differential import (
    PYTHAGOREAN_EXPONENT,
    MissingOpponentDataError,
    TeamRunDifferentialAnalysisError,
    build_team_run_differential_analysis,
)
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    make_run_result,
    make_run_result_season,
)


def test_a_winning_season_reports_a_positive_differential() -> None:
    games = make_run_result_season([6, 5, 7], [2, 3, 1])

    analysis = build_team_run_differential_analysis(games, rolling_window=3)

    assert analysis.summary.total_runs_scored == 18
    assert analysis.summary.total_runs_allowed == 6
    assert analysis.summary.total_run_differential == 12
    assert analysis.summary.season_average == pytest.approx(4.0)


def test_a_losing_season_reports_a_negative_differential() -> None:
    """The signed case. A clamped or absolute-valued metric fails here."""
    games = make_run_result_season([1, 2, 0], [5, 4, 9])

    analysis = build_team_run_differential_analysis(games, rolling_window=3)

    assert analysis.summary.total_run_differential == -15
    assert analysis.summary.season_average == pytest.approx(-5.0)
    assert analysis.summary.recent_average == pytest.approx(-5.0)
    assert all(point.run_differential < 0 for point in analysis.points)
    assert not any(point.is_win for point in analysis.points)


def test_each_point_carries_both_sides_of_the_game() -> None:
    games = make_run_result_season([4, 2], [1, 8])

    analysis = build_team_run_differential_analysis(games, rolling_window=2)

    first, second = analysis.points
    assert (first.runs_scored, first.runs_allowed) == (4, 1)
    assert first.run_differential == 3
    assert first.is_win is True
    assert (second.runs_scored, second.runs_allowed) == (2, 8)
    assert second.run_differential == -6
    assert second.is_win is False


def test_wins_and_losses_are_derived_from_the_score() -> None:
    """No W/L column is stored; outscoring the opponent is the whole definition."""
    games = make_run_result_season([5, 1, 3, 9], [2, 4, 8, 0])

    analysis = build_team_run_differential_analysis(games, rolling_window=4)

    assert [point.is_win for point in analysis.points] == [True, False, False, True]
    assert analysis.pythagorean.actual_wins == 2
    assert analysis.pythagorean.actual_losses == 2
    assert analysis.pythagorean.actual_win_pct == pytest.approx(0.5)


def test_the_rolling_average_can_go_negative() -> None:
    games = make_run_result_season([1, 1, 1, 9], [4, 4, 4, 0])

    analysis = build_team_run_differential_analysis(games, rolling_window=2)

    # Games 1-2 are both -3, so the trailing pair average is -3.
    assert analysis.points[1].rolling_average == pytest.approx(-3.0)
    # Games 3-4 are -3 and +9, averaging +3.
    assert analysis.points[3].rolling_average == pytest.approx(3.0)


def test_early_games_average_only_what_has_been_played() -> None:
    games = make_run_result_season([7, 1], [2, 5])

    analysis = build_team_run_differential_analysis(games, rolling_window=15)

    assert analysis.points[0].rolling_average == pytest.approx(5.0)
    assert analysis.points[1].rolling_average == pytest.approx(0.5)


def test_games_are_ordered_by_date_then_doubleheader_game_number() -> None:
    second_game = make_run_result(
        game_pk=776002,
        game_date=date(2025, 4, 2),
        game_number=2,
        runs_scored=1,
        runs_allowed=7,
    )
    first_game = make_run_result(
        game_pk=776001,
        game_date=date(2025, 4, 2),
        game_number=1,
        runs_scored=8,
        runs_allowed=2,
    )
    opener = make_run_result(
        game_pk=776000,
        game_date=date(2025, 4, 1),
        game_number=1,
        runs_scored=3,
        runs_allowed=1,
    )

    analysis = build_team_run_differential_analysis(
        [second_game, opener, first_game], rolling_window=3
    )

    assert [point.game_pk for point in analysis.points] == [776000, 776001, 776002]
    assert [point.season_game_number for point in analysis.points] == [1, 2, 3]


def test_the_prior_window_comparison_needs_two_complete_windows() -> None:
    games = make_run_result_season([5] * 3, [1] * 3)

    analysis = build_team_run_differential_analysis(games, rolling_window=2)

    # Three games is not two complete windows of two.
    assert analysis.summary.prior_window_average is None
    assert analysis.summary.change_vs_prior_window is None


def test_the_prior_window_comparison_appears_with_two_complete_windows() -> None:
    games = make_run_result_season([1, 1, 9, 9], [4, 4, 0, 0])

    analysis = build_team_run_differential_analysis(games, rolling_window=2)

    assert analysis.summary.prior_window_average == pytest.approx(-3.0)
    assert analysis.summary.recent_average == pytest.approx(9.0)
    assert analysis.summary.change_vs_prior_window == pytest.approx(12.0)


class TestPythagoreanExpectation:
    def test_it_matches_the_published_formula(self) -> None:
        games = make_run_result_season([4] * 10, [3] * 10)

        record = build_team_run_differential_analysis(
            games, rolling_window=5
        ).pythagorean

        expected = 40**PYTHAGOREAN_EXPONENT / (
            40**PYTHAGOREAN_EXPONENT + 30**PYTHAGOREAN_EXPONENT
        )
        assert record.exponent == PYTHAGOREAN_EXPONENT
        assert record.runs_scored == 40
        assert record.runs_allowed == 30
        assert record.expected_win_pct == pytest.approx(expected)
        assert record.expected_wins == pytest.approx(expected * 10)

    def test_equal_runs_scored_and_allowed_expect_a_500_season(self) -> None:
        games = make_run_result_season([5, 1], [1, 5])

        record = build_team_run_differential_analysis(
            games, rolling_window=2
        ).pythagorean

        assert record.runs_scored == record.runs_allowed == 6
        assert record.expected_win_pct == pytest.approx(0.5)

    def test_winning_close_and_losing_big_beats_the_expectation(self) -> None:
        """Three one-run wins and one blowout loss: a real 3-1 on a -6 differential."""
        games = make_run_result_season([2, 2, 2, 1], [1, 1, 1, 12])

        record = build_team_run_differential_analysis(
            games, rolling_window=4
        ).pythagorean

        assert record.actual_wins == 3
        assert record.runs_scored == 7
        assert record.runs_allowed == 15
        # Outscored overall, so the expectation is below .500 while the real
        # record is .750. That gap is the whole point of the statistic.
        assert record.expected_win_pct < 0.5
        assert record.actual_win_pct == pytest.approx(0.75)
        assert record.wins_above_expectation > 0

    def test_losing_close_and_winning_big_trails_the_expectation(self) -> None:
        games = make_run_result_season([1, 1, 1, 12], [2, 2, 2, 1])

        record = build_team_run_differential_analysis(
            games, rolling_window=4
        ).pythagorean

        assert record.actual_wins == 1
        assert record.expected_win_pct > 0.5
        assert record.actual_win_pct == pytest.approx(0.25)
        assert record.wins_above_expectation < 0

    def test_wins_and_losses_cover_every_analysed_game(self) -> None:
        games = make_run_result_season([3, 1, 7, 2, 6], [1, 5, 2, 9, 0])

        analysis = build_team_run_differential_analysis(games, rolling_window=3)

        decided = analysis.pythagorean.actual_wins + analysis.pythagorean.actual_losses
        assert decided == len(analysis.points) == 5

    def test_a_shutout_season_expects_no_wins(self) -> None:
        """Scoring zero across the season drives the numerator to zero."""
        games = make_run_result_season([0, 0, 0], [4, 2, 6])

        record = build_team_run_differential_analysis(
            games, rolling_window=3
        ).pythagorean

        assert record.expected_win_pct == pytest.approx(0.0)
        assert record.expected_wins == pytest.approx(0.0)
        assert record.actual_wins == 0


class TestMissingOpponentData:
    def test_unpaired_games_are_refused(self) -> None:
        games = make_run_result_season([5, 3], [1, 7])

        with pytest.raises(MissingOpponentDataError) as caught:
            build_team_run_differential_analysis(
                games, unpaired_game_count=4, rolling_window=2
            )

        assert caught.value.missing_game_count == 4
        assert caught.value.total_games == 6
        assert caught.value.season == 2025

    def test_the_message_names_the_league_import_as_the_fix(self) -> None:
        """Re-importing the team cannot help; the opponents' rows are what is absent."""
        games = make_run_result_season([5], [1])

        with pytest.raises(MissingOpponentDataError) as caught:
            build_team_run_differential_analysis(games, unpaired_game_count=1)

        message = str(caught.value)
        assert "league season" in message
        assert "runs allowed is unknown" in message

    def test_a_team_season_with_no_pairs_at_all_is_refused(self) -> None:
        """A single-team import: every game is unpaired and nothing can be charted."""
        with pytest.raises(MissingOpponentDataError) as caught:
            build_team_run_differential_analysis([], unpaired_game_count=162)

        assert caught.value.missing_game_count == 162
        assert caught.value.total_games == 162

    def test_a_partial_season_is_never_quietly_analysed(self) -> None:
        """The refusal is what stops an understated runs-allowed total."""
        games = make_run_result_season([9, 9], [0, 0])

        with pytest.raises(MissingOpponentDataError):
            build_team_run_differential_analysis(games, unpaired_game_count=1)


class TestInvalidInput:
    def test_an_empty_season_is_rejected(self) -> None:
        with pytest.raises(
            TeamRunDifferentialAnalysisError, match="no completed games"
        ):
            build_team_run_differential_analysis([])

    def test_a_rolling_window_below_one_is_rejected(self) -> None:
        games = make_run_result_season([5], [1])

        with pytest.raises(TeamRunDifferentialAnalysisError, match="at least 1 game"):
            build_team_run_differential_analysis(games, rolling_window=0)

    def test_a_negative_unpaired_count_is_rejected(self) -> None:
        games = make_run_result_season([5], [1])

        with pytest.raises(
            TeamRunDifferentialAnalysisError, match="cannot be negative"
        ):
            build_team_run_differential_analysis(games, unpaired_game_count=-1)

    def test_mixing_teams_is_rejected(self) -> None:
        mariners = make_run_result_season([5], [1])
        twins = make_run_result_season(
            [3], [2], team_id=142, team_name="Minnesota Twins"
        )

        with pytest.raises(
            TeamRunDifferentialAnalysisError, match="one team and one season"
        ):
            build_team_run_differential_analysis([*mariners, *twins])

    def test_mixing_seasons_is_rejected(self) -> None:
        this_year = make_run_result_season([5], [1], season=2025)
        last_year = make_run_result_season([3], [2], season=2024)

        with pytest.raises(
            TeamRunDifferentialAnalysisError, match="one team and one season"
        ):
            build_team_run_differential_analysis([*this_year, *last_year])


def test_the_analysis_identifies_the_team_and_season() -> None:
    games = make_run_result_season([5, 3], [1, 7])

    analysis = build_team_run_differential_analysis(games, rolling_window=2)

    assert analysis.team_id == MARINERS_ID
    assert analysis.team_name == MARINERS_NAME
    assert analysis.season == 2025
    assert analysis.rolling_window == 2
    assert analysis.last_game_date == analysis.points[-1].game_date
