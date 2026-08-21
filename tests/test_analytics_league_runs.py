"""Tests for MLB-wide run analytics and the coverage rule that gates them.

Every case here is offline, built from normalized batting lines and from
coverage states constructed directly. Coverage is what decides whether a season
may be described as MLB-wide; a record count never is.
"""

from datetime import datetime

import pytest

from app.analytics.league_runs import (
    LeagueRunsAnalysisError,
    build_league_runs_context,
    compare_team_runs_to_league,
    supports_league_wide_runs_average,
)
from app.analytics.team_runs import build_team_runs_analysis
from app.schemas.analytics import LeagueRunsContext
from app.schemas.ingestion import (
    LeagueSeasonIngestionState,
    LeagueSeasonIngestionStatus,
)
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_league_runs_context,
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


def league_games(
    runs: list[int],
    *,
    team_id: int = MARINERS_ID,
    team_name: str = MARINERS_NAME,
    season: int = 2025,
):
    """Build one team's stored season with the given per-game run totals."""
    return make_season(
        hits=[8] * len(runs),
        runs=runs,
        team_id=team_id,
        team_name=team_name,
        season=season,
    )


def team_analysis(runs: list[int], *, window: int = 2, season: int = 2025):
    return build_team_runs_analysis(
        league_games(list(runs), season=season), rolling_window=window
    )


# ---------------------------------------------------------------- the formula


def test_mlb_runs_per_game_is_total_runs_over_total_team_game_records() -> None:
    context = build_league_runs_context(league_games([5, 3, 4]))
    assert context.total_runs == 12
    assert context.team_game_records == 3
    assert context.runs_per_game == pytest.approx(4.0)


def test_unequal_team_game_counts_are_weighted_by_games_played() -> None:
    """The worked example from issue #24, and the distinction it protects.

    Team A scores 5 and 3. Team B scores 2 in its only game.

        game-weighted    : (5 + 3 + 2) / 3 == 3.333...  <- what this must be
        mean of averages : ((5 + 3) / 2 + 2) / 2 == 3.0
    """
    games = [
        *league_games([5, 3], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([2], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_runs_context(games)
    assert context.team_game_records == 3
    assert context.runs_per_game == pytest.approx(10 / 3)
    assert context.runs_per_game != pytest.approx(3.0)


def test_a_club_with_more_games_pulls_the_average_further() -> None:
    games = [
        *league_games([6] * 100, team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([0], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_runs_context(games)
    assert context.runs_per_game == pytest.approx(600 / 101)
    assert context.runs_per_game > 5.9


def test_the_denominator_is_team_game_records_not_mlb_games() -> None:
    """Two clubs with 20 stored games each is 40 records, not 20 games."""
    games = [
        *league_games([4] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([6] * 20, team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_runs_context(games)
    assert context.team_game_records == 40
    assert context.runs_per_game == pytest.approx(5.0)


def test_several_teams_in_one_season_are_accepted() -> None:
    games = [
        *league_games([5, 5], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([3, 7], team_id=TWINS_ID, team_name=TWINS_NAME),
        *league_games([4, 0], team_id=ANGELS_ID, team_name=ANGELS_NAME),
    ]
    context = build_league_runs_context(games)
    assert context.teams_represented == 3
    assert context.team_game_records == 6
    assert context.total_runs == 24
    assert context.runs_per_game == pytest.approx(4.0)
    assert context.season == 2025


def test_a_shutout_counts_as_a_game_with_zero_runs() -> None:
    context = build_league_runs_context(league_games([0, 6, 3]))
    assert (context.total_runs, context.team_game_records) == (9, 3)
    assert context.runs_per_game == pytest.approx(3.0)


def test_a_partial_season_is_averaged_over_the_records_it_holds() -> None:
    """An in-progress season divides by its own record count, not 162 or 4,860."""
    games = [
        *league_games([5] * 40, season=2026, team_id=MARINERS_ID),
        *league_games([3] * 38, season=2026, team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_runs_context(games)
    assert context.team_game_records == 78
    assert context.runs_per_game == pytest.approx((5 * 40 + 3 * 38) / 78)


def test_mixed_seasons_are_rejected() -> None:
    games = [
        *league_games([4], season=2025),
        *league_games([5], season=2026),
    ]
    with pytest.raises(LeagueRunsAnalysisError, match="one season"):
        build_league_runs_context(games)


def test_empty_input_is_rejected() -> None:
    """No records means no MLB average, not an average of nothing."""
    with pytest.raises(LeagueRunsAnalysisError, match="no team-game records"):
        build_league_runs_context([])


def test_the_context_refuses_an_average_its_totals_do_not_produce() -> None:
    """No caller, now or later, can hand the page an unrelated MLB number."""
    with pytest.raises(ValueError, match="runs_per_game"):
        LeagueRunsContext(
            season=2025,
            teams_represented=2,
            team_game_records=10,
            total_runs=45,
            runs_per_game=9.9,
        )


def test_more_teams_than_records_is_refused_by_the_context() -> None:
    """A team cannot be represented without at least one stored record."""
    with pytest.raises(ValueError, match="teams_represented"):
        make_league_runs_context(teams_represented=30, team_game_records=10)


# ------------------------------------------------------------- the comparison


def test_a_team_scoring_more_than_mlb_gets_a_positive_difference() -> None:
    """The worked example from the issue: 4.75 against 4.42 reads +0.33."""
    analysis = team_analysis([5, 5, 5, 4])
    league = make_league_runs_context(total_runs=442, team_game_records=100)
    result = compare_team_runs_to_league(analysis, league)
    assert result.team_runs_per_game == pytest.approx(4.75)
    assert result.difference_vs_mlb == pytest.approx(0.33)


def test_a_team_scoring_less_than_mlb_gets_a_negative_difference() -> None:
    analysis = team_analysis([4, 4, 4, 4])
    league = make_league_runs_context(total_runs=442, team_game_records=100)
    result = compare_team_runs_to_league(analysis, league)
    assert result.difference_vs_mlb == pytest.approx(-0.42)


def test_a_team_matching_mlb_reads_as_a_real_zero() -> None:
    """0.00 means matched exactly; unavailable is a separate state entirely."""
    analysis = team_analysis([4, 4])
    league = make_league_runs_context(total_runs=400, team_game_records=100)
    assert compare_team_runs_to_league(analysis, league).difference_vs_mlb == 0.0


def test_the_comparison_reuses_the_team_season_average_it_was_given() -> None:
    """One team average on the page, so the card and the chart cannot disagree."""
    analysis = team_analysis([1, 2, 3, 12])
    result = compare_team_runs_to_league(analysis, make_league_runs_context())
    assert result.team_runs_per_game == analysis.summary.season_average


def test_the_comparison_carries_the_team_identity_and_league_context() -> None:
    analysis = team_analysis([4] * 4)
    league = make_league_runs_context(
        teams_represented=30, team_game_records=100, total_runs=442
    )
    result = compare_team_runs_to_league(analysis, league)
    assert (result.team_id, result.team_name) == (MARINERS_ID, MARINERS_NAME)
    assert result.season == 2025
    assert result.league == league


def test_comparing_across_seasons_is_rejected() -> None:
    analysis = team_analysis([4] * 4, season=2026)
    league = make_league_runs_context(season=2025)
    with pytest.raises(LeagueRunsAnalysisError, match="2026"):
        compare_team_runs_to_league(analysis, league)


def test_the_comparison_against_an_unequal_league_stays_game_weighted() -> None:
    """End to end: the +3.00 an unweighted mean would produce never appears."""
    games = [
        *league_games([5, 3], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([2], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    league = build_league_runs_context(games)
    analysis = build_team_runs_analysis(
        league_games([5, 3], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        rolling_window=2,
    )
    result = compare_team_runs_to_league(analysis, league)
    assert result.difference_vs_mlb == pytest.approx(4.0 - 10 / 3)
    assert result.difference_vs_mlb != pytest.approx(1.0)


# ---------------------------------------------------------- the coverage rule


def test_complete_coverage_allows_a_comparison() -> None:
    assert supports_league_wide_runs_average(
        coverage(LeagueSeasonIngestionStatus.COMPLETE)
    )


@pytest.mark.parametrize(
    "status",
    [LeagueSeasonIngestionStatus.INCOMPLETE, LeagueSeasonIngestionStatus.RUNNING],
)
def test_other_coverage_states_refuse_a_comparison(
    status: LeagueSeasonIngestionStatus,
) -> None:
    assert not supports_league_wide_runs_average(coverage(status))


def test_a_season_with_no_coverage_record_refuses_a_comparison() -> None:
    assert not supports_league_wide_runs_average(None)


def test_complete_coverage_of_an_in_progress_season_still_allows_it() -> None:
    """COMPLETE describes the refresh, not the season being over."""
    assert supports_league_wide_runs_average(
        coverage(LeagueSeasonIngestionStatus.COMPLETE, season=2026)
    )


def test_the_runs_coverage_rule_is_the_shared_one() -> None:
    """One rule across the metric pages, so they cannot disagree on a season."""
    from app.analytics.league_hitting import supports_league_wide_average

    for status in LeagueSeasonIngestionStatus:
        state = coverage(status)
        assert supports_league_wide_runs_average(state) == supports_league_wide_average(
            state
        )
