"""Tests for team pitching analytics.

The point these tests exist to hold is that ERA, WHIP, K/9 and BB/9 are
**rates**, and a rate over several games is the ratio of the summed totals, not
the mean of the per-game ratios. Several cases below are built with unequal
innings specifically because that is the only situation where the two
differ — with 27 outs in every game they agree, and a wrong implementation
would pass.
"""

import pytest

from app.analytics.team_pitching import (
    TeamPitchingAnalysisError,
    build_pitch_count_points,
    build_team_pitching_analysis,
)
from tests.factories import MARINERS_ID, MARINERS_NAME, make_pitching_season


class TestRateAggregation:
    def test_era_sums_components_rather_than_averaging_game_eras(self) -> None:
        """The regression this module exists to prevent.

        Two games: 1 ER in 27 outs (9.00 ERA over 9 innings is 1.00) and 8 ER
        in 9 outs (24.00). The correct season ERA divides 9 total earned runs
        by 36 total outs: 9 * 27 / 36 = 6.75. Averaging the two game ERAs gives
        (1.00 + 24.00) / 2 = 12.50, which is nearly double and wrong.
        """
        games = make_pitching_season([1, 8], outs=[27, 9])

        analysis = build_team_pitching_analysis(games, rolling_window=2)

        assert analysis.summary.season.era == pytest.approx(6.75)
        naive = sum(point.game_era for point in analysis.points) / 2
        assert naive == pytest.approx(12.5)
        assert analysis.summary.season.era != pytest.approx(naive)

    def test_the_rolling_era_also_sums_rather_than_averages(self) -> None:
        games = make_pitching_season([1, 8], outs=[27, 9])

        analysis = build_team_pitching_analysis(games, rolling_window=2)

        # The window covers both games, so it must equal the season figure.
        assert analysis.points[-1].rolling_era == pytest.approx(6.75)

    def test_a_short_outing_is_weighted_less_than_a_long_one(self) -> None:
        """Nine scoreless innings and one bad inning should not average out."""
        games = make_pitching_season([0, 3], outs=[27, 3])

        analysis = build_team_pitching_analysis(games, rolling_window=2)

        # 3 earned runs over 30 outs = 2.70, much closer to the long clean
        # outing than to the short disastrous one.
        assert analysis.summary.season.era == pytest.approx(2.7)

    def test_whip_divides_by_innings_not_by_games(self) -> None:
        games = make_pitching_season([0, 0], outs=[27, 9])

        analysis = build_team_pitching_analysis(games, rolling_window=2)
        season = analysis.summary.season

        # 8 hits + 2 walks per game from the factory, so 20 baserunners over
        # 36 outs = 12 innings.
        assert season.hits_allowed == 16
        assert season.base_on_balls == 4
        assert season.innings_pitched == pytest.approx(12.0)
        assert season.whip == pytest.approx(20 / 12)

    def test_strikeouts_and_walks_per_nine_use_outs(self) -> None:
        games = make_pitching_season([0, 0], outs=[27, 9])

        season = build_team_pitching_analysis(games, rolling_window=2).summary.season

        # 9 K and 2 BB per game from the factory, over 36 outs.
        assert season.strikeouts_per_nine == pytest.approx(18 * 27 / 36)
        assert season.walks_per_nine == pytest.approx(4 * 27 / 36)

    def test_equal_innings_is_the_case_that_cannot_distinguish_them(self) -> None:
        """Documents why the other tests use unequal outs.

        With a regulation nine innings every game, the correct aggregation and
        a naive mean of game ERAs agree exactly, so a test built this way would
        pass against a wrong implementation.
        """
        games = make_pitching_season([1, 5, 3])

        analysis = build_team_pitching_analysis(games, rolling_window=3)

        naive = sum(point.game_era for point in analysis.points) / 3
        assert analysis.summary.season.era == pytest.approx(naive)


class TestPitchCounts:
    def test_pitches_per_game_is_a_plain_mean(self) -> None:
        """A count, not a rate: unequal innings must not reweight it."""
        games = make_pitching_season([0, 0], outs=[27, 9])
        games = [
            games[0].model_copy(update={"number_of_pitches": 100, "strikes": 60}),
            games[1].model_copy(update={"number_of_pitches": 200, "strikes": 120}),
        ]

        season = build_team_pitching_analysis(games, rolling_window=2).summary.season

        assert season.number_of_pitches == 300
        assert season.pitches_per_game == pytest.approx(150.0)

    def test_strike_percentage_divides_strikes_by_pitches(self) -> None:
        games = make_pitching_season([0, 0])

        season = build_team_pitching_analysis(games, rolling_window=2).summary.season

        # 150 pitches, 98 strikes per game from the factory.
        assert season.strike_percentage == pytest.approx(196 / 300)

    def test_the_rolling_pitch_count_is_a_trailing_mean(self) -> None:
        games = make_pitching_season([0, 0, 0, 0])
        games = [
            game.model_copy(update={"number_of_pitches": count, "strikes": 50})
            for game, count in zip(games, [100, 200, 120, 180], strict=True)
        ]

        analysis = build_team_pitching_analysis(games, rolling_window=2)
        counts, rolling = build_pitch_count_points(analysis)

        assert counts == (100, 200, 120, 180)
        assert rolling[0] == pytest.approx(100.0)
        assert rolling[1] == pytest.approx(150.0)
        assert rolling[3] == pytest.approx(150.0)


class TestOrderingAndWindows:
    def test_games_are_ordered_by_date_then_game_number(self) -> None:
        games = make_pitching_season([1, 2, 3])
        shuffled = [games[2], games[0], games[1]]

        analysis = build_team_pitching_analysis(shuffled, rolling_window=3)

        assert [point.earned_runs for point in analysis.points] == [1, 2, 3]
        assert [point.season_game_number for point in analysis.points] == [1, 2, 3]

    def test_early_games_use_only_what_has_been_played(self) -> None:
        games = make_pitching_season([1, 5])

        analysis = build_team_pitching_analysis(games, rolling_window=15)

        assert analysis.points[0].rolling_era == pytest.approx(1.0)
        assert analysis.points[1].rolling_era == pytest.approx(3.0)

    def test_the_prior_window_needs_two_complete_windows(self) -> None:
        games = make_pitching_season([1, 1, 1])

        analysis = build_team_pitching_analysis(games, rolling_window=2)

        assert analysis.summary.prior_window_era is None
        assert analysis.summary.change_vs_prior_window is None

    def test_the_prior_window_appears_with_two_complete_windows(self) -> None:
        games = make_pitching_season([6, 6, 1, 1])

        analysis = build_team_pitching_analysis(games, rolling_window=2)

        assert analysis.summary.prior_window_era == pytest.approx(6.0)
        assert analysis.summary.recent_era == pytest.approx(1.0)
        # Negative is an improvement, since a lower ERA is better.
        assert analysis.summary.change_vs_prior_window == pytest.approx(-5.0)


class TestInningsAreCountedInOuts:
    def test_a_fractional_inning_is_exact(self) -> None:
        """32 outs is ten and two-thirds innings, not 10.2."""
        games = make_pitching_season([9], outs=[32])

        analysis = build_team_pitching_analysis(games, rolling_window=1)

        assert analysis.points[0].innings_pitched_display == "10.2"
        assert analysis.summary.season.innings_pitched == pytest.approx(32 / 3)
        # The figure MLB itself publishes for this line.
        assert analysis.summary.season.era == pytest.approx(7.59, abs=0.005)

    def test_the_display_string_is_never_used_in_a_calculation(self) -> None:
        """Reading '10.2' as a decimal would give 7.90 rather than 7.59."""
        games = make_pitching_season([9], outs=[32])

        analysis = build_team_pitching_analysis(games, rolling_window=1)

        decimal_reading = 9 * 9 / 10.2
        assert analysis.summary.season.era != pytest.approx(decimal_reading, abs=0.01)


class TestInvalidInput:
    def test_an_empty_season_is_rejected(self) -> None:
        with pytest.raises(TeamPitchingAnalysisError, match="no completed games"):
            build_team_pitching_analysis([])

    def test_a_rolling_window_below_one_is_rejected(self) -> None:
        games = make_pitching_season([1])

        with pytest.raises(TeamPitchingAnalysisError, match="at least 1 game"):
            build_team_pitching_analysis(games, rolling_window=0)

    def test_a_season_with_no_outs_is_rejected(self) -> None:
        """Every rate would divide by zero, so it fails loudly instead."""
        games = make_pitching_season([0], outs=[0])

        with pytest.raises(TeamPitchingAnalysisError, match="no recorded outs"):
            build_team_pitching_analysis(games)

    def test_mixing_teams_is_rejected(self) -> None:
        mariners = make_pitching_season([1])
        twins = make_pitching_season([1], team_id=142, team_name="Minnesota Twins")

        with pytest.raises(TeamPitchingAnalysisError, match="one team and one season"):
            build_team_pitching_analysis([*mariners, *twins])

    def test_mixing_seasons_is_rejected(self) -> None:
        this_year = make_pitching_season([1], season=2025)
        last_year = make_pitching_season([1], season=2024)

        with pytest.raises(TeamPitchingAnalysisError, match="one team and one season"):
            build_team_pitching_analysis([*this_year, *last_year])


def test_the_analysis_identifies_the_team_and_season() -> None:
    analysis = build_team_pitching_analysis(
        make_pitching_season([1, 2]), rolling_window=2
    )

    assert analysis.team_id == MARINERS_ID
    assert analysis.team_name == MARINERS_NAME
    assert analysis.season == 2025
    assert analysis.last_game_date == analysis.points[-1].game_date
