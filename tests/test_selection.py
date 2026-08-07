"""Tests for team and season selector resolution."""

from app.schemas.catalog import AvailableTeamSeason
from app.web.selection import build_team_options, select_season, select_team

MARINERS = AvailableTeamSeason(
    team_id=136, team_name="Seattle Mariners", season=2025, games_played=162
)
MARINERS_2024 = AvailableTeamSeason(
    team_id=136, team_name="Seattle Mariners", season=2024, games_played=162
)
CUBS = AvailableTeamSeason(
    team_id=112, team_name="Chicago Cubs", season=2025, games_played=162
)


def test_no_persisted_data_produces_no_options() -> None:
    assert build_team_options([]) == []


def test_seasons_are_grouped_under_one_team() -> None:
    options = build_team_options([MARINERS_2024, MARINERS])
    assert len(options) == 1
    assert options[0].seasons == (2025, 2024)


def test_team_name_comes_from_the_most_recent_season() -> None:
    options = build_team_options(
        [
            AvailableTeamSeason(
                team_id=114,
                team_name="Cleveland Indians",
                season=2021,
                games_played=162,
            ),
            AvailableTeamSeason(
                team_id=114,
                team_name="Cleveland Guardians",
                season=2022,
                games_played=162,
            ),
        ]
    )
    assert options[0].team_name == "Cleveland Guardians"


def test_options_are_sorted_alphabetically() -> None:
    options = build_team_options([MARINERS, CUBS])
    assert [option.team_name for option in options] == [
        "Chicago Cubs",
        "Seattle Mariners",
    ]


def test_seattle_is_the_default_team_when_stored() -> None:
    options = build_team_options([MARINERS, CUBS])
    assert select_team(options, None).team_id == 136


def test_first_team_alphabetically_is_the_default_without_seattle() -> None:
    options = build_team_options([CUBS])
    assert select_team(options, None).team_id == 112


def test_requested_team_is_used_when_stored() -> None:
    options = build_team_options([MARINERS, CUBS])
    assert select_team(options, 112).team_id == 112


def test_requested_team_that_is_not_stored_resolves_to_none() -> None:
    assert select_team(build_team_options([MARINERS]), 147) is None


def test_no_options_resolves_to_none() -> None:
    assert select_team([], 136) is None


def test_most_recent_season_is_the_default() -> None:
    option = build_team_options([MARINERS_2024, MARINERS])[0]
    assert select_season(option, None) == 2025


def test_requested_season_is_used_when_stored() -> None:
    option = build_team_options([MARINERS_2024, MARINERS])[0]
    assert select_season(option, 2024) == 2024


def test_requested_season_that_is_not_stored_resolves_to_none() -> None:
    option = build_team_options([MARINERS])[0]
    assert select_season(option, 1998) is None
