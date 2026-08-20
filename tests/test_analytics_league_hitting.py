"""Tests for MLB-wide hitting analytics and the coverage rule that gates them."""

from datetime import datetime

import pytest

from app.analytics.league_hitting import (
    LeagueHitsAnalysisError,
    build_league_hits_context,
    compare_team_hits_to_league,
    supports_league_wide_average,
)
from app.analytics.team_hitting import build_team_hits_analysis
from app.schemas.ingestion import (
    LeagueSeasonIngestionState,
    LeagueSeasonIngestionStatus,
)
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_league_hits_context,
    make_season,
)

ANGELS_ID = 108
ANGELS_NAME = "Los Angeles Angels"


def coverage(
    status: LeagueSeasonIngestionStatus,
    *,
    season: int = 2025,
) -> LeagueSeasonIngestionState:
    """Build a persisted coverage state in one of its three real shapes."""
    started = datetime(2026, 3, 1, 12, 0, 0)
    if status is LeagueSeasonIngestionStatus.RUNNING:
        return LeagueSeasonIngestionState(
            season=season,
            status=status,
            expected_team_count=30,
            successful_team_count=0,
            failed_team_count=0,
            started_at=started,
        )
    failed = 0 if status is LeagueSeasonIngestionStatus.COMPLETE else 1
    return LeagueSeasonIngestionState(
        season=season,
        status=status,
        expected_team_count=30,
        successful_team_count=30 - failed,
        failed_team_count=failed,
        started_at=started,
        completed_at=datetime(2026, 3, 1, 12, 30, 0),
    )


# ---------------------------------------------------------------- the formula


def test_mlb_hits_per_game_is_total_hits_over_total_team_game_records() -> None:
    games = make_season([8, 10, 6], team_id=MARINERS_ID, team_name=MARINERS_NAME)
    context = build_league_hits_context(games)
    assert context.total_hits == 24
    assert context.team_game_records == 3
    assert context.hits_per_game == pytest.approx(24 / 3)


def test_unequal_team_game_counts_are_weighted_by_games_played() -> None:
    """The average is game-weighted, not the mean of each club's own average.

    Team A: 10 and 10 hits. Team B: 4 hits in its only game.

        game-weighted   : 24 / 3 == 8.0     <- what this must be
        mean of averages : (10 + 4) / 2 == 7.0
    """
    games = [
        *make_season([10, 10], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *make_season([4], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_hits_context(games)
    assert context.hits_per_game == pytest.approx(8.0)
    assert context.hits_per_game != pytest.approx(7.0)


def test_a_club_with_more_games_pulls_the_average_further() -> None:
    """The heavier club's own average must dominate, which weighting delivers."""
    games = [
        *make_season([12] * 100, team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *make_season([2], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_hits_context(games)
    assert context.hits_per_game == pytest.approx(1202 / 101)
    assert context.hits_per_game > 11.5


def test_several_teams_in_one_season_are_accepted() -> None:
    games = [
        *make_season([8, 8], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *make_season([6, 10], team_id=TWINS_ID, team_name=TWINS_NAME),
        *make_season([7, 5], team_id=ANGELS_ID, team_name=ANGELS_NAME),
    ]
    context = build_league_hits_context(games)
    assert context.teams_represented == 3
    assert context.team_game_records == 6
    assert context.total_hits == 44
    assert context.hits_per_game == pytest.approx(44 / 6)
    assert context.season == 2025


def test_mixed_seasons_are_rejected() -> None:
    games = [
        *make_season([8], season=2025),
        *make_season([9], season=2026),
    ]
    with pytest.raises(LeagueHitsAnalysisError, match="one season"):
        build_league_hits_context(games)


def test_empty_input_is_rejected() -> None:
    """No records means no MLB average, not an average of nothing."""
    with pytest.raises(LeagueHitsAnalysisError, match="no team-game records"):
        build_league_hits_context([])


def test_a_partial_season_is_still_averaged_over_the_games_it_holds() -> None:
    """An in-progress season divides by its own record count, not 162 or 4,860."""
    games = [
        *make_season([9] * 40, season=2026, team_id=MARINERS_ID),
        *make_season([7] * 38, season=2026, team_id=TWINS_ID),
    ]
    context = build_league_hits_context(games)
    assert context.team_game_records == 78
    assert context.hits_per_game == pytest.approx((9 * 40 + 7 * 38) / 78)


def test_a_zero_hit_game_counts_as_a_game() -> None:
    games = make_season([0, 8, 4])
    context = build_league_hits_context(games)
    assert (context.total_hits, context.team_game_records) == (12, 3)
    assert context.hits_per_game == pytest.approx(4.0)


# ------------------------------------------------------------ the comparison


def test_a_team_above_mlb_gets_a_positive_difference() -> None:
    analysis = build_team_hits_analysis(make_season([8, 9, 9]), rolling_window=2)
    league = make_league_hits_context(total_hits=820, team_game_records=100)
    result = compare_team_hits_to_league(analysis, league)
    assert result.team_hits_per_game == pytest.approx(26 / 3)
    assert result.difference_vs_mlb == pytest.approx(26 / 3 - 8.20)
    assert result.difference_vs_mlb > 0


def test_a_team_below_mlb_gets_a_negative_difference() -> None:
    analysis = build_team_hits_analysis(make_season([8, 8, 8, 7]), rolling_window=2)
    league = make_league_hits_context(total_hits=820, team_game_records=100)
    result = compare_team_hits_to_league(analysis, league)
    assert result.team_hits_per_game == pytest.approx(7.75)
    assert result.difference_vs_mlb == pytest.approx(-0.45)


def test_the_comparison_reuses_the_team_season_average_it_was_given() -> None:
    """One team average on the page, so the card and the chart cannot disagree."""
    analysis = build_team_hits_analysis(make_season([3, 4, 5, 12]), rolling_window=2)
    result = compare_team_hits_to_league(analysis, make_league_hits_context())
    assert result.team_hits_per_game == analysis.summary.season_average


def test_the_comparison_carries_the_team_identity_and_league_context() -> None:
    analysis = build_team_hits_analysis(make_season([8] * 4), rolling_window=2)
    league = make_league_hits_context(
        teams_represented=30, team_game_records=100, total_hits=820
    )
    result = compare_team_hits_to_league(analysis, league)
    assert (result.team_id, result.team_name) == (MARINERS_ID, MARINERS_NAME)
    assert result.season == 2025
    assert result.league == league


def test_comparing_across_seasons_is_rejected() -> None:
    analysis = build_team_hits_analysis(make_season([8] * 4, season=2026))
    league = make_league_hits_context(season=2025)
    with pytest.raises(LeagueHitsAnalysisError, match="2026"):
        compare_team_hits_to_league(analysis, league)


# -------------------------------------------------------------- the coverage rule


def test_complete_coverage_supports_an_mlb_wide_average() -> None:
    assert supports_league_wide_average(coverage(LeagueSeasonIngestionStatus.COMPLETE))


@pytest.mark.parametrize(
    "status",
    [LeagueSeasonIngestionStatus.INCOMPLETE, LeagueSeasonIngestionStatus.RUNNING],
)
def test_other_coverage_states_do_not(status: LeagueSeasonIngestionStatus) -> None:
    assert not supports_league_wide_average(coverage(status))


def test_a_season_with_no_coverage_record_does_not() -> None:
    assert not supports_league_wide_average(None)


def test_complete_coverage_of_an_in_progress_season_still_supports_it() -> None:
    """COMPLETE describes the refresh, not the season being over."""
    assert supports_league_wide_average(
        coverage(LeagueSeasonIngestionStatus.COMPLETE, season=2026)
    )


def test_more_teams_than_records_is_refused_by_the_context() -> None:
    """A team cannot be represented without at least one stored record."""
    with pytest.raises(ValueError, match="teams_represented"):
        make_league_hits_context(teams_represented=30, team_game_records=10)
