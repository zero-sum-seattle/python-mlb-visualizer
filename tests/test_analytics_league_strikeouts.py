"""Tests for MLB-wide batting strikeout analytics and the rules that gate them.

Two rules decide whether an MLB batting K/Game may be shown: the league-season
coverage state, and whether every counted record actually carries a strikeout
total. Both are exercised here, offline, from normalized batting lines.
"""

from datetime import datetime

import pytest

from app.analytics.league_strikeouts import (
    LeagueStrikeoutsAnalysisError,
    MissingLeagueStrikeoutDataError,
    build_league_strikeouts_context,
    compare_team_strikeouts_to_league,
    supports_league_wide_strikeout_average,
)
from app.analytics.team_strikeouts import build_team_strikeouts_analysis
from app.schemas.ingestion import (
    LeagueSeasonIngestionState,
    LeagueSeasonIngestionStatus,
)
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_league_strikeouts_context,
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
    strikeouts: list[int | None],
    *,
    team_id: int = MARINERS_ID,
    team_name: str = MARINERS_NAME,
    season: int = 2025,
):
    """Build one team's stored season with the given batting strikeout totals."""
    return make_season(
        hits=[8] * len(strikeouts),
        strikeouts=strikeouts,
        team_id=team_id,
        team_name=team_name,
        season=season,
    )


def team_analysis(strikeouts: list[int], *, window: int = 2, season: int = 2025):
    return build_team_strikeouts_analysis(
        league_games(list(strikeouts), season=season), rolling_window=window
    )


# ---------------------------------------------------------------- the formula


def test_mlb_k_per_game_is_total_strikeouts_over_total_team_game_records() -> None:
    context = build_league_strikeouts_context(league_games([10, 8, 6]))
    assert context.total_strikeouts == 24
    assert context.team_game_records == 3
    assert context.strikeouts_per_game == pytest.approx(8.0)


def test_unequal_team_game_counts_are_weighted_by_games_played() -> None:
    """The average is game-weighted, not the mean of each club's own average.

    Team A: 10 and 8 batting strikeouts. Team B: 6 in its only game.

        game-weighted    : (10 + 8 + 6) / 3 == 8.0   <- what this must be
        mean of averages : ((10 + 8) / 2 + 6) / 2 == 7.5
    """
    games = [
        *league_games([10, 8], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([6], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_strikeouts_context(games)
    assert context.strikeouts_per_game == pytest.approx(8.0)
    assert context.strikeouts_per_game != pytest.approx(7.5)


def test_a_club_with_more_games_pulls_the_average_further() -> None:
    games = [
        *league_games([12] * 100, team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([2], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_strikeouts_context(games)
    assert context.strikeouts_per_game == pytest.approx(1202 / 101)
    assert context.strikeouts_per_game > 11.5


def test_several_teams_in_one_season_are_accepted() -> None:
    games = [
        *league_games([9, 9], team_id=MARINERS_ID, team_name=MARINERS_NAME),
        *league_games([7, 11], team_id=TWINS_ID, team_name=TWINS_NAME),
        *league_games([8, 4], team_id=ANGELS_ID, team_name=ANGELS_NAME),
    ]
    context = build_league_strikeouts_context(games)
    assert context.teams_represented == 3
    assert context.team_game_records == 6
    assert context.total_strikeouts == 48
    assert context.strikeouts_per_game == pytest.approx(8.0)
    assert context.season == 2025


def test_mixed_seasons_are_rejected() -> None:
    games = [
        *league_games([8], season=2025),
        *league_games([9], season=2026),
    ]
    with pytest.raises(LeagueStrikeoutsAnalysisError, match="one season"):
        build_league_strikeouts_context(games)


def test_empty_input_is_rejected() -> None:
    """No records means no MLB average, not an average of nothing."""
    with pytest.raises(LeagueStrikeoutsAnalysisError, match="no team-game records"):
        build_league_strikeouts_context([])


def test_a_partial_season_is_still_averaged_over_the_games_it_holds() -> None:
    """An in-progress season divides by its own record count, not 162 or 4,860."""
    games = [
        *league_games([9] * 40, season=2026, team_id=MARINERS_ID),
        *league_games([7] * 38, season=2026, team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    context = build_league_strikeouts_context(games)
    assert context.team_game_records == 78
    assert context.strikeouts_per_game == pytest.approx((9 * 40 + 7 * 38) / 78)


def test_a_game_without_a_strikeout_counts_as_a_game() -> None:
    """Nobody struck out is a real zero, unlike an unknown total."""
    context = build_league_strikeouts_context(league_games([0, 8, 4]))
    assert (context.total_strikeouts, context.team_game_records) == (12, 3)
    assert context.strikeouts_per_game == pytest.approx(4.0)


# ------------------------------------------------- legacy rows with no totals


def test_a_single_unknown_strikeout_total_refuses_the_league_context() -> None:
    """One legacy row is enough: the rest are not MLB overall on their own."""
    games = [
        *league_games([10, 10], team_id=MARINERS_ID),
        *league_games([None], team_id=TWINS_ID, team_name=TWINS_NAME),
    ]
    with pytest.raises(MissingLeagueStrikeoutDataError) as exc_info:
        build_league_strikeouts_context(games)

    error = exc_info.value
    assert (error.records_missing, error.records_total) == (1, 3)
    assert error.season == 2025


def test_unknown_totals_are_never_read_as_zero() -> None:
    """A zero would drag the MLB average down with a fabricated value."""
    games = league_games([10, None, 10])
    with pytest.raises(MissingLeagueStrikeoutDataError):
        build_league_strikeouts_context(games)


def test_unknown_totals_are_never_silently_dropped() -> None:
    """Averaging the two known rows would return 10.00 and call it MLB-wide."""
    with pytest.raises(MissingLeagueStrikeoutDataError):
        build_league_strikeouts_context(league_games([10, None, 10]))


def test_a_fully_legacy_season_reports_every_record_as_missing() -> None:
    with pytest.raises(MissingLeagueStrikeoutDataError) as exc_info:
        build_league_strikeouts_context(league_games([None, None, None]))
    assert exc_info.value.records_missing == 3


def test_the_missing_data_error_names_the_backfill_remedy() -> None:
    with pytest.raises(MissingLeagueStrikeoutDataError, match="re-import"):
        build_league_strikeouts_context(league_games([None]))


def test_mixed_seasons_are_rejected_before_missing_data_is_reported() -> None:
    """The input is not a season at all, so a record count would mislead."""
    games = [
        *league_games([None], season=2025),
        *league_games([8], season=2026),
    ]
    with pytest.raises(LeagueStrikeoutsAnalysisError, match="one season"):
        build_league_strikeouts_context(games)


# ------------------------------------------------------------- the comparison


def test_a_team_striking_out_more_than_mlb_gets_a_positive_difference() -> None:
    analysis = team_analysis([9, 9, 9])
    league = make_league_strikeouts_context(total_strikeouts=840, team_game_records=100)
    result = compare_team_strikeouts_to_league(analysis, league)
    assert result.team_strikeouts_per_game == pytest.approx(9.0)
    assert result.difference_vs_mlb == pytest.approx(0.60)


def test_a_team_striking_out_less_than_mlb_gets_a_negative_difference() -> None:
    """The worked example from the issue: 7.80 against 8.40 reads -0.60."""
    analysis = team_analysis([8, 8, 8, 7])
    league = make_league_strikeouts_context(total_strikeouts=840, team_game_records=100)
    result = compare_team_strikeouts_to_league(analysis, league)
    assert result.team_strikeouts_per_game == pytest.approx(7.75)
    assert result.difference_vs_mlb == pytest.approx(-0.65)


def test_the_comparison_reuses_the_team_season_average_it_was_given() -> None:
    """One team average on the page, so the card and the chart cannot disagree."""
    analysis = team_analysis([3, 4, 5, 12])
    result = compare_team_strikeouts_to_league(
        analysis, make_league_strikeouts_context()
    )
    assert result.team_strikeouts_per_game == analysis.summary.season_average


def test_the_comparison_carries_the_team_identity_and_league_context() -> None:
    analysis = team_analysis([8] * 4)
    league = make_league_strikeouts_context(
        teams_represented=30, team_game_records=100, total_strikeouts=840
    )
    result = compare_team_strikeouts_to_league(analysis, league)
    assert (result.team_id, result.team_name) == (MARINERS_ID, MARINERS_NAME)
    assert result.season == 2025
    assert result.league == league


def test_comparing_across_seasons_is_rejected() -> None:
    analysis = team_analysis([8] * 4, season=2026)
    league = make_league_strikeouts_context(season=2025)
    with pytest.raises(LeagueStrikeoutsAnalysisError, match="2026"):
        compare_team_strikeouts_to_league(analysis, league)


def test_a_team_matching_mlb_reads_as_a_real_zero() -> None:
    """0.00 means matched exactly; unavailable is a separate state entirely."""
    analysis = team_analysis([8, 8])
    league = make_league_strikeouts_context(total_strikeouts=800, team_game_records=100)
    assert compare_team_strikeouts_to_league(analysis, league).difference_vs_mlb == 0.0


# ---------------------------------------------------------- the coverage rule


def test_complete_coverage_allows_a_comparison() -> None:
    assert supports_league_wide_strikeout_average(
        coverage(LeagueSeasonIngestionStatus.COMPLETE)
    )


@pytest.mark.parametrize(
    "status",
    [LeagueSeasonIngestionStatus.INCOMPLETE, LeagueSeasonIngestionStatus.RUNNING],
)
def test_other_coverage_states_refuse_a_comparison(
    status: LeagueSeasonIngestionStatus,
) -> None:
    assert not supports_league_wide_strikeout_average(coverage(status))


def test_a_season_with_no_coverage_record_refuses_a_comparison() -> None:
    assert not supports_league_wide_strikeout_average(None)


def test_complete_coverage_of_an_in_progress_season_still_allows_it() -> None:
    """COMPLETE describes the refresh, not the season being over."""
    assert supports_league_wide_strikeout_average(
        coverage(LeagueSeasonIngestionStatus.COMPLETE, season=2026)
    )


def test_complete_coverage_does_not_by_itself_produce_an_average() -> None:
    """Coverage says every team was refreshed, not that the rows carry totals."""
    assert supports_league_wide_strikeout_average(
        coverage(LeagueSeasonIngestionStatus.COMPLETE)
    )
    with pytest.raises(MissingLeagueStrikeoutDataError):
        build_league_strikeouts_context(league_games([9, None, 7]))


def test_more_teams_than_records_is_refused_by_the_context() -> None:
    """A team cannot be represented without at least one stored record."""
    with pytest.raises(ValueError, match="teams_represented"):
        make_league_strikeouts_context(teams_represented=30, team_game_records=10)
