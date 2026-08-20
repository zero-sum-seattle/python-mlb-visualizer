"""Tests for the invariants league ingestion result schemas enforce.

These models are the service contract. If they accept a self-contradictory
result, an incomplete league import can be presented as a complete one, which is
exactly the failure Milestone 4 exists to prevent.
"""

from datetime import datetime

import pytest

from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionResult,
    LeagueTeamIngestionStatus,
    TeamSeasonIngestionResult,
)

SEASON = 2025
STARTED = datetime(2026, 3, 1, 12, 0, 0)
FINISHED = datetime(2026, 3, 1, 12, 5, 0)


def succeeded(team_id: int, name: str, **counts: int) -> LeagueTeamIngestionResult:
    return LeagueTeamIngestionResult.from_team_result(
        TeamSeasonIngestionResult(
            team_id=team_id,
            team_name=name,
            season=SEASON,
            fetched=sum(counts.values()),
            inserted=counts.get("inserted", 0),
            updated=counts.get("updated", 0),
            unchanged=counts.get("unchanged", 0),
        )
    )


def failed(team_id: int, name: str) -> LeagueTeamIngestionResult:
    return LeagueTeamIngestionResult.from_failure(
        team_id=team_id, team_name=name, season=SEASON, error="TeamGameLogError: nope"
    )


def build(**overrides: object) -> LeagueSeasonIngestionResult:
    teams = overrides.pop(
        "team_results", (succeeded(112, "Chicago Cubs", inserted=162),)
    )
    fields: dict[str, object] = {
        "season": SEASON,
        "teams_discovered": len(teams),
        "teams_succeeded": sum(
            1 for team in teams if team.status is LeagueTeamIngestionStatus.SUCCEEDED
        ),
        "teams_failed": sum(
            1 for team in teams if team.status is LeagueTeamIngestionStatus.FAILED
        ),
        "team_game_records_fetched": sum(team.fetched for team in teams),
        "inserted": sum(team.inserted for team in teams),
        "updated": sum(team.updated for team in teams),
        "unchanged": sum(team.unchanged for team in teams),
        "status": LeagueSeasonIngestionStatus.COMPLETE,
        "started_at": STARTED,
        "completed_at": FINISHED,
        "team_results": teams,
    }
    fields.update(overrides)
    return LeagueSeasonIngestionResult(**fields)


def test_a_consistent_result_is_accepted() -> None:
    result = build()
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE


def test_a_failure_forces_incomplete_status() -> None:
    teams = (succeeded(112, "Chicago Cubs", inserted=162), failed(136, "Mariners"))
    with pytest.raises(ValueError, match="disagrees with 1 failed teams"):
        build(team_results=teams, status=LeagueSeasonIngestionStatus.COMPLETE)


def test_incomplete_status_with_no_failures_is_refused() -> None:
    with pytest.raises(ValueError, match="disagrees with 0 failed teams"):
        build(status=LeagueSeasonIngestionStatus.INCOMPLETE)


def test_a_returned_result_is_never_running() -> None:
    with pytest.raises(ValueError, match="never RUNNING"):
        build(status=LeagueSeasonIngestionStatus.RUNNING)


def test_discovered_must_equal_succeeded_plus_failed() -> None:
    with pytest.raises(ValueError, match="teams_discovered"):
        build(teams_discovered=30)


def test_team_results_must_cover_every_discovered_team() -> None:
    teams = (succeeded(112, "Chicago Cubs", inserted=162), failed(136, "Mariners"))
    with pytest.raises(ValueError, match="team_results holds"):
        build(
            team_results=teams,
            teams_discovered=3,
            teams_succeeded=2,
            teams_failed=1,
            status=LeagueSeasonIngestionStatus.INCOMPLETE,
        )


def test_reported_success_count_must_match_the_team_results() -> None:
    teams = (succeeded(112, "Chicago Cubs", inserted=162), failed(136, "Mariners"))
    with pytest.raises(ValueError, match="teams_succeeded"):
        build(
            team_results=teams,
            teams_discovered=2,
            teams_succeeded=2,
            teams_failed=0,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("team_game_records_fetched", 4860),
        ("inserted", 1),
        ("updated", 7),
        ("unchanged", 3),
    ],
)
def test_aggregate_counts_must_sum_the_team_results(field: str, value: int) -> None:
    """A row-count guess must never stand in for what was actually ingested."""
    with pytest.raises(ValueError, match="does not match"):
        build(**{field: value})


def test_team_results_must_share_the_league_season() -> None:
    other_season = LeagueTeamIngestionResult.from_team_result(
        TeamSeasonIngestionResult(
            team_id=136,
            team_name="Seattle Mariners",
            season=2024,
            fetched=1,
            inserted=1,
            updated=0,
            unchanged=0,
        )
    )
    with pytest.raises(ValueError, match="but the league ingestion is for 2025"):
        build(team_results=(succeeded(112, "Chicago Cubs", inserted=162), other_season))


def test_a_team_cannot_appear_twice() -> None:
    teams = (
        succeeded(112, "Chicago Cubs", inserted=162),
        succeeded(112, "Chicago Cubs", inserted=162),
    )
    with pytest.raises(ValueError, match="at most once"):
        build(team_results=teams)


def test_a_succeeded_team_cannot_carry_an_error() -> None:
    with pytest.raises(ValueError, match="must not carry an error"):
        LeagueTeamIngestionResult(
            team_id=112,
            team_name="Chicago Cubs",
            season=SEASON,
            status=LeagueTeamIngestionStatus.SUCCEEDED,
            fetched=1,
            inserted=1,
            updated=0,
            unchanged=0,
            error="something went wrong",
        )


def test_a_succeeded_team_must_account_for_every_fetched_record() -> None:
    with pytest.raises(ValueError, match="must equal inserted"):
        LeagueTeamIngestionResult(
            team_id=112,
            team_name="Chicago Cubs",
            season=SEASON,
            status=LeagueTeamIngestionStatus.SUCCEEDED,
            fetched=162,
            inserted=1,
            updated=0,
            unchanged=0,
        )


def test_a_failed_team_must_explain_itself() -> None:
    with pytest.raises(ValueError, match="must carry an error message"):
        LeagueTeamIngestionResult(
            team_id=112,
            team_name="Chicago Cubs",
            season=SEASON,
            status=LeagueTeamIngestionStatus.FAILED,
            fetched=0,
            inserted=0,
            updated=0,
            unchanged=0,
        )


def test_a_failed_team_cannot_claim_persisted_records() -> None:
    with pytest.raises(ValueError, match="zero persistence counts"):
        LeagueTeamIngestionResult(
            team_id=112,
            team_name="Chicago Cubs",
            season=SEASON,
            status=LeagueTeamIngestionStatus.FAILED,
            fetched=1,
            inserted=1,
            updated=0,
            unchanged=0,
            error="TeamGameLogError: nope",
        )
