"""Tests for MLB-wide pitching context and the team-versus-league comparison.

League rates are outs-weighted rather than game-weighted, which is a different
weighting from every other league context in the package. The tests that matter
most here build clubs with unequal innings, since equal innings is the one case
where the two weightings agree.
"""

import pytest

from app.analytics.league_pitching import (
    LeaguePitchingAnalysisError,
    build_league_pitching_context,
    compare_team_pitching_to_league,
    supports_league_wide_pitching_average,
)
from app.analytics.team_pitching import build_team_pitching_analysis
from app.schemas.ingestion import LeagueSeasonIngestionStatus
from tests.factories import make_pitching_season


def league_state(status: LeagueSeasonIngestionStatus):
    from datetime import datetime

    from app.schemas.ingestion import LeagueSeasonIngestionState

    return LeagueSeasonIngestionState(
        season=2025,
        status=status,
        expected_team_count=30,
        successful_team_count=30
        if status is LeagueSeasonIngestionStatus.COMPLETE
        else 12,
        failed_team_count=0 if status is LeagueSeasonIngestionStatus.COMPLETE else 18,
        started_at=datetime(2026, 8, 1, 12, 0, 0),
        completed_at=datetime(2026, 8, 1, 12, 5, 0),
    )


class TestCoverageGate:
    def test_complete_coverage_permits_a_league_average(self) -> None:
        assert supports_league_wide_pitching_average(
            league_state(LeagueSeasonIngestionStatus.COMPLETE)
        )

    def test_incomplete_coverage_does_not(self) -> None:
        assert not supports_league_wide_pitching_average(
            league_state(LeagueSeasonIngestionStatus.INCOMPLETE)
        )

    def test_no_recorded_coverage_does_not(self) -> None:
        assert not supports_league_wide_pitching_average(None)


class TestLeagueContext:
    def test_rates_are_outs_weighted_not_game_weighted(self) -> None:
        """A club with more innings pulls the league figure further.

        Club A: 1 ER over 27 outs. Club B: 8 ER over 9 outs. Outs-weighted the
        league ERA is 9 * 27 / 36 = 6.75; weighting each club's own ERA equally
        would give (1.00 + 24.00) / 2 = 12.50.
        """
        games = [
            *make_pitching_season([1], outs=[27], team_id=136, team_name="Mariners"),
            *make_pitching_season([8], outs=[9], team_id=142, team_name="Twins"),
        ]

        league = build_league_pitching_context(games)

        assert league.outs == 36
        assert league.total_earned_runs == 9
        assert league.era == pytest.approx(6.75)

    def test_it_counts_teams_and_records(self) -> None:
        games = [
            *make_pitching_season([1, 2], team_id=136, team_name="Mariners"),
            *make_pitching_season([3], team_id=142, team_name="Twins"),
        ]

        league = build_league_pitching_context(games)

        assert league.teams_represented == 2
        assert league.team_game_records == 3
        assert league.season == 2025

    def test_no_records_is_rejected(self) -> None:
        with pytest.raises(LeaguePitchingAnalysisError, match="no team-game"):
            build_league_pitching_context([])

    def test_mixing_seasons_is_rejected(self) -> None:
        games = [
            *make_pitching_season([1], season=2025),
            *make_pitching_season([1], season=2024, team_id=142, team_name="Twins"),
        ]

        with pytest.raises(LeaguePitchingAnalysisError, match="one season"):
            build_league_pitching_context(games)

    def test_records_with_no_outs_are_rejected(self) -> None:
        with pytest.raises(LeaguePitchingAnalysisError, match="no recorded outs"):
            build_league_pitching_context(make_pitching_season([0], outs=[0]))


class TestComparison:
    def test_a_better_team_reads_as_a_negative_difference(self) -> None:
        """The sign convention: below MLB is the better direction here."""
        team_games = make_pitching_season([1, 1])
        league_games = [
            *team_games,
            *make_pitching_season([5, 5], team_id=142, team_name="Twins"),
        ]

        analysis = build_team_pitching_analysis(team_games, rolling_window=2)
        comparison = compare_team_pitching_to_league(
            analysis, build_league_pitching_context(league_games)
        )

        assert comparison.team_era == pytest.approx(1.0)
        assert comparison.league.era == pytest.approx(3.0)
        assert comparison.era_difference_vs_mlb == pytest.approx(-2.0)

    def test_a_worse_team_reads_as_a_positive_difference(self) -> None:
        team_games = make_pitching_season([5, 5])
        league_games = [
            *team_games,
            *make_pitching_season([1, 1], team_id=142, team_name="Twins"),
        ]

        analysis = build_team_pitching_analysis(team_games, rolling_window=2)
        comparison = compare_team_pitching_to_league(
            analysis, build_league_pitching_context(league_games)
        )

        assert comparison.era_difference_vs_mlb == pytest.approx(2.0)

    def test_the_team_side_reads_the_same_figure_the_cards_show(self) -> None:
        team_games = make_pitching_season([2, 4])
        analysis = build_team_pitching_analysis(team_games, rolling_window=2)

        comparison = compare_team_pitching_to_league(
            analysis, build_league_pitching_context(team_games)
        )

        assert comparison.team_era == analysis.summary.season.era
        assert comparison.team_whip == analysis.summary.season.whip

    def test_comparing_across_seasons_is_rejected(self) -> None:
        analysis = build_team_pitching_analysis(
            make_pitching_season([1], season=2025), rolling_window=1
        )
        league = build_league_pitching_context(
            make_pitching_season([1], season=2024, team_id=142, team_name="Twins")
        )

        with pytest.raises(LeaguePitchingAnalysisError, match="Cannot compare"):
            compare_team_pitching_to_league(analysis, league)
