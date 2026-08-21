"""Tests for normalized rolling hits-versus-batting-strikeouts analytics."""

from datetime import date

import pytest

from app.analytics.team_hitting import build_team_hits_analysis
from app.analytics.team_hitting_comparison import (
    InvalidComparisonBaselineError,
    TeamHittingComparisonError,
    build_team_hitting_comparison_analysis,
)
from app.analytics.team_strikeouts import build_team_strikeouts_analysis
from app.schemas.analytics import LeagueHitsContext, LeagueStrikeoutsContext
from tests.factories import (
    make_batting_line,
    make_league_hits_context,
    make_league_strikeouts_context,
    make_season,
)


def comparison_for(
    hits: list[int],
    strikeouts: list[int],
    *,
    window: int = 2,
    mlb_hits_per_game: int = 8,
    mlb_strikeouts_per_game: int = 10,
):
    games = make_season(hits, strikeouts=strikeouts)
    return build_team_hitting_comparison_analysis(
        build_team_hits_analysis(games, rolling_window=window),
        build_team_strikeouts_analysis(games, rolling_window=window),
        make_league_hits_context(
            total_hits=mlb_hits_per_game * 10,
            team_game_records=10,
        ),
        make_league_strikeouts_context(
            total_strikeouts=mlb_strikeouts_per_game * 10,
            team_game_records=10,
        ),
    )


def test_hits_index_normalizes_each_rolling_average_to_mlb_hits_per_game() -> None:
    analysis = comparison_for([8, 16, 8], [10, 5, 15])

    # Rolling Hits/Game is [8, 12, 12], divided by the 8.0 MLB baseline.
    assert [point.hits_index for point in analysis.points] == pytest.approx(
        [100.0, 150.0, 150.0]
    )


def test_strikeout_index_normalizes_each_rolling_average_to_mlb_k_per_game() -> None:
    analysis = comparison_for([8, 16, 8], [10, 5, 15])

    # Rolling batting K/Game is [10, 7.5, 10], divided by the 10.0 MLB baseline.
    assert [point.strikeouts_index for point in analysis.points] == pytest.approx(
        [100.0, 75.0, 100.0]
    )


def test_a_rolling_value_equal_to_its_mlb_average_has_index_100() -> None:
    analysis = comparison_for([8, 8, 8], [10, 10, 10], window=3)

    assert analysis.baseline_index == 100.0
    assert all(point.hits_index == 100.0 for point in analysis.points)
    assert all(point.strikeouts_index == 100.0 for point in analysis.points)


def test_selected_rolling_window_is_preserved_and_used_for_both_indexes() -> None:
    analysis = comparison_for(
        [2, 4, 12, 20],
        [20, 10, 5, 1],
        window=3,
        mlb_hits_per_game=4,
        mlb_strikeouts_per_game=5,
    )

    assert analysis.rolling_window == 3
    # Game 4 uses games 2-4: Hits/Game = 12; batting K/Game = 16 / 3.
    assert analysis.points[-1].hits_index == pytest.approx(300.0)
    assert analysis.points[-1].strikeouts_index == pytest.approx((16 / 3) / 5 * 100)


def test_summary_uses_the_recent_indexes_and_their_descriptive_gap() -> None:
    analysis = comparison_for([8, 16, 8], [10, 5, 15])

    assert analysis.summary.games_played == 3
    assert analysis.summary.recent_hits_index == pytest.approx(150.0)
    assert analysis.summary.recent_strikeouts_index == pytest.approx(100.0)
    assert analysis.summary.trend_gap == pytest.approx(50.0)


def test_negative_trend_gap_is_kept_without_directional_judgment() -> None:
    analysis = comparison_for([4, 4], [15, 15], window=2)

    assert analysis.summary.recent_hits_index == pytest.approx(50.0)
    assert analysis.summary.recent_strikeouts_index == pytest.approx(150.0)
    assert analysis.summary.trend_gap == pytest.approx(-100.0)


def test_zero_mlb_hits_baseline_is_rejected_before_division() -> None:
    games = make_season([8, 9], strikeouts=[10, 9])
    hits = build_team_hits_analysis(games, rolling_window=2)
    strikeouts = build_team_strikeouts_analysis(games, rolling_window=2)
    zero_hits = make_league_hits_context(total_hits=0, team_game_records=10)

    with pytest.raises(InvalidComparisonBaselineError, match="Hits/Game") as error:
        build_team_hitting_comparison_analysis(
            hits,
            strikeouts,
            zero_hits,
            make_league_strikeouts_context(),
        )

    assert error.value.metric == "Hits/Game"
    assert error.value.value == 0.0


def test_zero_mlb_strikeout_baseline_is_rejected_before_division() -> None:
    games = make_season([8, 9], strikeouts=[10, 9])
    hits = build_team_hits_analysis(games, rolling_window=2)
    strikeouts = build_team_strikeouts_analysis(games, rolling_window=2)
    zero_strikeouts = make_league_strikeouts_context(
        total_strikeouts=0,
        team_game_records=10,
    )

    with pytest.raises(InvalidComparisonBaselineError, match="batting K/Game") as error:
        build_team_hitting_comparison_analysis(
            hits,
            strikeouts,
            make_league_hits_context(),
            zero_strikeouts,
        )

    assert error.value.metric == "batting K/Game"
    assert error.value.value == 0.0


def test_team_analyses_must_use_the_same_rolling_window() -> None:
    games = make_season([8, 9], strikeouts=[10, 9])

    with pytest.raises(TeamHittingComparisonError, match="rolling window"):
        build_team_hitting_comparison_analysis(
            build_team_hits_analysis(games, rolling_window=2),
            build_team_strikeouts_analysis(games, rolling_window=1),
            make_league_hits_context(),
            make_league_strikeouts_context(),
        )


def test_team_analyses_must_contain_the_same_ordered_games() -> None:
    hits_games = make_season([8, 9], strikeouts=[10, 9])
    strikeout_games = [
        make_batting_line(
            game_pk=999001,
            game_date=date(2025, 3, 27),
            hits=8,
            strikeouts=10,
        ),
        hits_games[1],
    ]

    with pytest.raises(TeamHittingComparisonError, match="same games"):
        build_team_hitting_comparison_analysis(
            build_team_hits_analysis(hits_games, rolling_window=2),
            build_team_strikeouts_analysis(strikeout_games, rolling_window=2),
            make_league_hits_context(),
            make_league_strikeouts_context(),
        )


def test_team_analyses_must_contain_the_same_number_of_games() -> None:
    games = make_season([8, 9], strikeouts=[10, 9])

    with pytest.raises(TeamHittingComparisonError, match="same games"):
        build_team_hitting_comparison_analysis(
            build_team_hits_analysis(games, rolling_window=2),
            build_team_strikeouts_analysis(games[:1], rolling_window=2),
            make_league_hits_context(),
            make_league_strikeouts_context(),
        )


def test_both_league_contexts_must_match_the_team_season() -> None:
    games = make_season([8, 9], strikeouts=[10, 9])

    with pytest.raises(TeamHittingComparisonError, match="same season"):
        build_team_hitting_comparison_analysis(
            build_team_hits_analysis(games, rolling_window=2),
            build_team_strikeouts_analysis(games, rolling_window=2),
            make_league_hits_context(),
            LeagueStrikeoutsContext(
                season=2024,
                teams_represented=2,
                team_game_records=10,
                total_strikeouts=80,
                strikeouts_per_game=8.0,
            ),
        )


def test_indexes_keep_calculation_precision_for_presentation_to_round() -> None:
    games = make_season([1, 2, 2], strikeouts=[2, 1, 1])
    analysis = build_team_hitting_comparison_analysis(
        build_team_hits_analysis(games, rolling_window=3),
        build_team_strikeouts_analysis(games, rolling_window=3),
        LeagueHitsContext(
            season=2025,
            teams_represented=2,
            team_game_records=3,
            total_hits=4,
            hits_per_game=4 / 3,
        ),
        LeagueStrikeoutsContext(
            season=2025,
            teams_represented=2,
            team_game_records=3,
            total_strikeouts=5,
            strikeouts_per_game=5 / 3,
        ),
    )

    assert analysis.points[-1].hits_index == pytest.approx(125.0)
    assert analysis.points[-1].strikeouts_index == pytest.approx(80.0)
