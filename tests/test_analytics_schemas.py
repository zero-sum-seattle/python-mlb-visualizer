"""Tests for the analytics Pydantic schemas."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.analytics import (
    TeamHitsAnalysis,
    TeamHitsPoint,
    TeamHitsSummary,
    TeamStrikeoutsAnalysis,
    TeamStrikeoutsPoint,
    TeamStrikeoutsSummary,
)


def make_point(**overrides: object) -> TeamHitsPoint:
    base: dict[str, object] = {
        "game_pk": 776000,
        "game_number": 1,
        "season_game_number": 1,
        "game_date": date(2025, 3, 27),
        "opponent_name": "Minnesota Twins",
        "home_away": "home",
        "hits": 8,
        "rolling_average": 8.0,
    }
    base.update(overrides)
    return TeamHitsPoint(**base)


def make_summary(**overrides: object) -> TeamHitsSummary:
    base: dict[str, object] = {
        "games_played": 1,
        "season_average": 8.0,
        "recent_average": 8.0,
    }
    base.update(overrides)
    return TeamHitsSummary(**base)


def make_analysis(**overrides: object) -> TeamHitsAnalysis:
    base: dict[str, object] = {
        "team_id": 136,
        "team_name": "Seattle Mariners",
        "season": 2025,
        "rolling_window": 15,
        "points": (make_point(),),
        "summary": make_summary(),
    }
    base.update(overrides)
    return TeamHitsAnalysis(**base)


def test_point_keeps_the_game_date_as_a_date() -> None:
    assert make_point().game_date == date(2025, 3, 27)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("game_pk", 0),
        ("game_number", 0),
        ("season_game_number", 0),
        ("hits", -1),
        ("rolling_average", -0.5),
    ],
)
def test_point_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_point(**{field: value})


def test_point_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_point(runs=4)


def test_point_is_immutable() -> None:
    point = make_point()
    with pytest.raises(ValidationError):
        point.hits = 9


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("games_played", 0),
        ("season_average", -1.0),
        ("recent_average", -1.0),
        ("prior_window_average", -1.0),
    ],
)
def test_summary_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_summary(**{field: value})


def test_summary_allows_a_negative_change() -> None:
    summary = make_summary(prior_window_average=9.0, change_vs_prior_window=-1.0)
    assert summary.change_vs_prior_window == -1.0


def test_summary_requires_prior_average_and_change_together() -> None:
    with pytest.raises(ValidationError, match="both be present"):
        make_summary(prior_window_average=9.0)
    with pytest.raises(ValidationError, match="both be present"):
        make_summary(change_vs_prior_window=1.0)


def test_analysis_requires_at_least_one_point() -> None:
    with pytest.raises(ValidationError):
        make_analysis(points=())


def test_analysis_rejects_a_summary_that_disagrees_with_the_points() -> None:
    with pytest.raises(ValidationError, match="games_played"):
        make_analysis(summary=make_summary(games_played=2))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("team_id", 0),
        ("season", 0),
        ("rolling_window", 0),
        ("team_name", ""),
    ],
)
def test_analysis_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_analysis(**{field: value})


def test_analysis_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_analysis(league_average=8.5)


def test_analysis_holds_no_season_average_of_its_own() -> None:
    """The summary is the only place a season average lives."""
    assert "season_average" not in TeamHitsAnalysis.model_fields
    with pytest.raises(ValidationError):
        make_analysis(season_average=8.0)


def make_strikeout_point(**overrides: object) -> TeamStrikeoutsPoint:
    base: dict[str, object] = {
        "game_pk": 776000,
        "game_number": 1,
        "season_game_number": 1,
        "game_date": date(2025, 3, 27),
        "opponent_name": "Minnesota Twins",
        "home_away": "home",
        "strikeouts": 9,
        "rolling_average": 9.0,
    }
    base.update(overrides)
    return TeamStrikeoutsPoint(**base)


def make_strikeout_summary(**overrides: object) -> TeamStrikeoutsSummary:
    base: dict[str, object] = {
        "games_played": 1,
        "season_average": 9.0,
        "recent_average": 9.0,
    }
    base.update(overrides)
    return TeamStrikeoutsSummary(**base)


def make_strikeout_analysis(**overrides: object) -> TeamStrikeoutsAnalysis:
    base: dict[str, object] = {
        "team_id": 136,
        "team_name": "Seattle Mariners",
        "season": 2025,
        "rolling_window": 15,
        "points": (make_strikeout_point(),),
        "summary": make_strikeout_summary(),
    }
    base.update(overrides)
    return TeamStrikeoutsAnalysis(**base)


def test_valid_strikeout_analysis_is_accepted() -> None:
    assert make_strikeout_analysis().summary.season_average == 9.0


def test_strikeout_point_requires_a_known_total() -> None:
    """Unknown totals are refused by analytics, so a point cannot carry None."""
    with pytest.raises(ValidationError):
        make_strikeout_point(strikeouts=None)


def test_negative_strikeouts_are_rejected_on_a_point() -> None:
    with pytest.raises(ValidationError):
        make_strikeout_point(strikeouts=-1)


def test_zero_strikeouts_is_valid_on_a_point() -> None:
    assert make_strikeout_point(strikeouts=0).strikeouts == 0


def test_strikeout_prior_window_fields_must_agree() -> None:
    with pytest.raises(ValidationError):
        make_strikeout_summary(prior_window_average=7.0)
    with pytest.raises(ValidationError):
        make_strikeout_summary(change_vs_prior_window=2.0)


def test_strikeout_prior_window_fields_may_both_be_present() -> None:
    summary = make_strikeout_summary(
        prior_window_average=7.0, change_vs_prior_window=2.0
    )
    assert summary.change_vs_prior_window == 2.0


def test_strikeout_change_may_be_negative() -> None:
    summary = make_strikeout_summary(
        prior_window_average=11.0, change_vs_prior_window=-2.0
    )
    assert summary.change_vs_prior_window == -2.0


def test_strikeout_summary_must_match_the_number_of_points() -> None:
    with pytest.raises(ValidationError):
        make_strikeout_analysis(summary=make_strikeout_summary(games_played=2))


def test_strikeout_analysis_requires_at_least_one_point() -> None:
    with pytest.raises(ValidationError):
        make_strikeout_analysis(points=())


def test_strikeout_analysis_last_game_date_is_the_final_point() -> None:
    analysis = make_strikeout_analysis(
        points=(
            make_strikeout_point(),
            make_strikeout_point(
                game_pk=776001, season_game_number=2, game_date=date(2025, 3, 28)
            ),
        ),
        summary=make_strikeout_summary(games_played=2),
    )
    assert analysis.last_game_date == date(2025, 3, 28)


def test_strikeout_analysis_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_strikeout_analysis(strikeout_rate=0.22)
