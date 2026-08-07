"""Tests for the analytics Pydantic schemas."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.analytics import TeamHitsAnalysis, TeamHitsPoint, TeamHitsSummary


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
        "season_average": 8.0,
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
        ("season_average", -1.0),
        ("team_name", ""),
    ],
)
def test_analysis_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_analysis(**{field: value})


def test_analysis_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_analysis(league_average=8.5)
