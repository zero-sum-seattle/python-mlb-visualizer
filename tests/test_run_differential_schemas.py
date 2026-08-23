"""Tests for the run differential schema guards.

These models carry several figures that are derived from each other — a
differential from two run totals, a win flag from the same two, an expected
win percentage from season totals. The validators exist so a construction that
disagrees with itself cannot be built, and these tests hold that line.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.analytics import (
    PythagoreanRecord,
    TeamRunDifferentialPoint,
    TeamRunDifferentialSummary,
)
from app.schemas.games import TeamGameRunResult


def point(**overrides: object) -> TeamRunDifferentialPoint:
    base: dict[str, object] = {
        "game_pk": 776000,
        "game_number": 1,
        "season_game_number": 1,
        "game_date": date(2025, 4, 1),
        "opponent_name": "Minnesota Twins",
        "home_away": "home",
        "runs_scored": 6,
        "runs_allowed": 2,
        "run_differential": 4,
        "is_win": True,
        "rolling_average": 4.0,
    }
    base.update(overrides)
    return TeamRunDifferentialPoint(**base)


def summary(**overrides: object) -> TeamRunDifferentialSummary:
    base: dict[str, object] = {
        "games_played": 4,
        "total_runs_scored": 20,
        "total_runs_allowed": 12,
        "total_run_differential": 8,
        "season_average": 2.0,
        "recent_average": 2.0,
    }
    base.update(overrides)
    return TeamRunDifferentialSummary(**base)


def record(**overrides: object) -> PythagoreanRecord:
    scored, allowed, exponent = 20, 12, 1.83
    expected_pct = scored**exponent / (scored**exponent + allowed**exponent)
    base: dict[str, object] = {
        "exponent": exponent,
        "runs_scored": scored,
        "runs_allowed": allowed,
        "expected_win_pct": expected_pct,
        "expected_wins": expected_pct * 4,
        "actual_wins": 3,
        "actual_losses": 1,
        "actual_win_pct": 0.75,
        "wins_above_expectation": 3 - expected_pct * 4,
    }
    base.update(overrides)
    return PythagoreanRecord(**base)


class TestPoint:
    def test_a_consistent_point_is_accepted(self) -> None:
        assert point().run_differential == 4

    def test_a_differential_that_contradicts_the_runs_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="run_differential"):
            point(run_differential=99)

    def test_a_win_flag_that_contradicts_the_runs_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="is_win"):
            point(is_win=False)

    def test_a_negative_differential_is_allowed(self) -> None:
        """The signed case: a losing game is a valid point, not a validation error."""
        losing = point(runs_scored=1, runs_allowed=8, run_differential=-7, is_win=False)

        assert losing.run_differential == -7

    def test_a_negative_rolling_average_is_allowed(self) -> None:
        assert point(rolling_average=-3.5).rolling_average == -3.5


class TestSummary:
    def test_a_consistent_summary_is_accepted(self) -> None:
        assert summary().total_run_differential == 8

    def test_a_total_that_contradicts_the_run_totals_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="total_run_differential"):
            summary(total_run_differential=99)

    def test_a_season_average_that_contradicts_the_total_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="season_average"):
            summary(season_average=99.0)

    def test_a_prior_window_without_a_change_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="both be"):
            summary(prior_window_average=1.0)

    def test_a_change_without_a_prior_window_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="both be"):
            summary(change_vs_prior_window=1.0)

    def test_a_negative_season_average_is_allowed(self) -> None:
        outscored = summary(
            total_runs_scored=12,
            total_runs_allowed=20,
            total_run_differential=-8,
            season_average=-2.0,
            recent_average=-2.0,
        )

        assert outscored.season_average == -2.0


class TestPythagoreanRecord:
    def test_a_consistent_record_is_accepted(self) -> None:
        assert record().actual_wins == 3

    def test_an_expected_pct_that_contradicts_the_formula_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="expected_win_pct"):
            record(expected_win_pct=0.5)

    def test_expected_wins_that_contradict_the_pct_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="expected_wins"):
            record(expected_wins=99.0)

    def test_an_actual_pct_that_contradicts_the_record_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="actual_win_pct"):
            record(actual_win_pct=0.1)

    def test_a_gap_that_contradicts_its_two_sides_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="wins_above_expectation"):
            record(wins_above_expectation=99.0)

    def test_a_record_with_no_decided_games_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one decided game"):
            record(actual_wins=0, actual_losses=0)


class TestRunResult:
    def test_run_differential_and_win_are_derived_not_stored(self) -> None:
        result = TeamGameRunResult(
            game_pk=776000,
            game_date=date(2025, 4, 1),
            season=2025,
            team_id=136,
            team_name="Seattle Mariners",
            opponent_id=142,
            opponent_name="Minnesota Twins",
            home_away="home",
            runs_scored=2,
            runs_allowed=9,
            game_number=1,
        )

        assert result.run_differential == -7
        assert result.is_win is False

    def test_a_negative_run_total_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TeamGameRunResult(
                game_pk=776000,
                game_date=date(2025, 4, 1),
                season=2025,
                team_id=136,
                team_name="Seattle Mariners",
                opponent_id=142,
                opponent_name="Minnesota Twins",
                home_away="home",
                runs_scored=-1,
                runs_allowed=9,
                game_number=1,
            )
