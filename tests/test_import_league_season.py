"""Tests for the league-season import CLI."""

import json
from datetime import datetime
from unittest.mock import patch

import pytest
from scripts import import_league_season as league_cli
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionResult,
    LeagueTeamIngestionStatus,
)
from app.services.league_season_ingestion import (
    InvalidSeasonError,
    LeagueIngestionStateError,
)
from app.services.league_teams import NoMlbTeamsDiscoveredError

MEMORY_SETTINGS = Settings(database_url="sqlite:///:memory:")
STARTED = datetime(2026, 3, 1, 12, 0, 0)
FINISHED = datetime(2026, 3, 1, 12, 5, 0)


def succeeded(
    team_id: int,
    team_name: str,
    *,
    inserted: int = 0,
    updated: int = 0,
    unchanged: int = 0,
) -> LeagueTeamIngestionResult:
    return LeagueTeamIngestionResult(
        team_id=team_id,
        team_name=team_name,
        season=2025,
        status=LeagueTeamIngestionStatus.SUCCEEDED,
        fetched=inserted + updated + unchanged,
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
    )


def failed(team_id: int, team_name: str, error: str) -> LeagueTeamIngestionResult:
    return LeagueTeamIngestionResult.from_failure(
        team_id=team_id, team_name=team_name, season=2025, error=error
    )


def league_result(
    *teams: LeagueTeamIngestionResult,
) -> LeagueSeasonIngestionResult:
    failures = sum(
        1 for team in teams if team.status is LeagueTeamIngestionStatus.FAILED
    )
    return LeagueSeasonIngestionResult(
        season=2025,
        teams_discovered=len(teams),
        teams_succeeded=len(teams) - failures,
        teams_failed=failures,
        team_game_records_fetched=sum(team.fetched for team in teams),
        inserted=sum(team.inserted for team in teams),
        updated=sum(team.updated for team in teams),
        unchanged=sum(team.unchanged for team in teams),
        status=(
            LeagueSeasonIngestionStatus.COMPLETE
            if failures == 0
            else LeagueSeasonIngestionStatus.INCOMPLETE
        ),
        started_at=STARTED,
        completed_at=FINISHED,
        team_results=teams,
    )


COMPLETE_RESULT = league_result(
    succeeded(133, "Athletics", unchanged=162),
    succeeded(108, "Los Angeles Angels", updated=162),
    succeeded(136, "Seattle Mariners", inserted=162),
)

INCOMPLETE_RESULT = league_result(
    succeeded(133, "Athletics", unchanged=162),
    failed(108, "Los Angeles Angels", "TeamGameLogError: Unable to retrieve MLB data"),
)


def run_cli(
    argv: list[str],
    *,
    result: LeagueSeasonIngestionResult | Exception,
) -> int:
    """Run the CLI with the service and settings replaced."""
    kwargs = (
        {"side_effect": result}
        if isinstance(result, Exception)
        else {"return_value": result}
    )
    with (
        patch(
            "scripts.import_league_season.get_settings", return_value=MEMORY_SETTINGS
        ),
        patch("scripts.import_league_season.ingest_league_season", **kwargs),
    ):
        return league_cli.main(argv)


def test_argument_parsing() -> None:
    args = league_cli.build_parser().parse_args(["--season", "2025"])
    assert args.season == 2025
    assert args.format == "table"


def test_table_output_reports_the_totals() -> None:
    text = league_cli.format_table(COMPLETE_RESULT)
    assert "Season: 2025" in text
    assert "Teams discovered: 3" in text
    assert "Teams succeeded: 3" in text
    assert "Teams failed: 0" in text
    assert "Team-game records fetched: 486" in text
    assert "Inserted: 162" in text
    assert "Updated: 162" in text
    assert "Unchanged: 162" in text


def test_table_output_labels_coverage_rather_than_season_finality() -> None:
    """COMPLETE describes the refresh, so the label must not read as a season."""
    text = league_cli.format_table(COMPLETE_RESULT)
    assert "Ingestion coverage: COMPLETE" in text


def test_table_output_lists_failures_with_their_errors() -> None:
    text = league_cli.format_table(INCOMPLETE_RESULT)
    assert "Ingestion coverage: INCOMPLETE" in text
    assert "Los Angeles Angels (id 108): TeamGameLogError" in text


def test_progress_lines_name_the_team_and_outcome() -> None:
    line = league_cli.format_progress_line(
        1, 30, succeeded(136, "Seattle Mariners", unchanged=162)
    )
    assert line.startswith("[ 1/30] Seattle Mariners ")
    assert line.endswith(" unchanged")


@pytest.mark.parametrize(
    ("team", "expected"),
    [
        (succeeded(1, "All new", inserted=5), "inserted"),
        (succeeded(1, "All same", unchanged=5), "unchanged"),
        (succeeded(1, "Mixed", inserted=1, updated=4), "updated"),
        (succeeded(1, "Nothing played"), "no games"),
        (failed(1, "Broken", "TeamGameLogError: nope"), "failed"),
    ],
)
def test_team_outcome_labels(team: LeagueTeamIngestionResult, expected: str) -> None:
    assert league_cli.team_outcome(team) == expected


def test_json_output_is_parseable_and_matches_the_result() -> None:
    payload = json.loads(league_cli.format_json(COMPLETE_RESULT))
    assert payload["season"] == 2025
    assert payload["teams_discovered"] == 3
    assert payload["teams_succeeded"] == 3
    assert payload["teams_failed"] == 0
    assert payload["team_game_records_fetched"] == 486
    assert payload["status"] == "COMPLETE"
    assert len(payload["team_results"]) == 3
    assert payload["team_results"][0]["team_name"] == "Athletics"


def test_json_totals_match_the_summed_team_results() -> None:
    payload = json.loads(league_cli.format_json(COMPLETE_RESULT))
    assert payload["inserted"] == sum(
        team["inserted"] for team in payload["team_results"]
    )
    assert payload["team_game_records_fetched"] == sum(
        team["fetched"] for team in payload["team_results"]
    )


def test_successful_table_run_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run_cli(["--season", "2025"], result=COMPLETE_RESULT)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MLB League Import — 2025" in captured.out
    assert "Ingestion coverage: COMPLETE" in captured.out
    assert captured.err == ""


def test_successful_json_run_writes_only_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_cli(
        ["--season", "2025", "--format", "json"], result=COMPLETE_RESULT
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["status"] == "COMPLETE"


def test_json_run_passes_no_progress_callback() -> None:
    """Progress prose in stdout would make the JSON unparseable."""
    with (
        patch(
            "scripts.import_league_season.get_settings", return_value=MEMORY_SETTINGS
        ),
        patch(
            "scripts.import_league_season.ingest_league_season",
            return_value=COMPLETE_RESULT,
        ) as ingest,
    ):
        league_cli.main(["--season", "2025", "--format", "json"])
    assert ingest.call_args.kwargs["on_team_complete"] is None


def test_table_run_passes_a_progress_callback() -> None:
    with (
        patch(
            "scripts.import_league_season.get_settings", return_value=MEMORY_SETTINGS
        ),
        patch(
            "scripts.import_league_season.ingest_league_season",
            return_value=COMPLETE_RESULT,
        ) as ingest,
    ):
        league_cli.main(["--season", "2025"])
    assert ingest.call_args.kwargs["on_team_complete"] is not None


def test_progress_callback_prints_the_header_and_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def report(**kwargs: object) -> LeagueSeasonIngestionResult:
        callback = kwargs["on_team_complete"]
        assert callable(callback)
        for position, team in enumerate(COMPLETE_RESULT.team_results, start=1):
            callback(position, 3, team)
        return COMPLETE_RESULT

    with (
        patch(
            "scripts.import_league_season.get_settings", return_value=MEMORY_SETTINGS
        ),
        patch("scripts.import_league_season.ingest_league_season", side_effect=report),
    ):
        league_cli.main(["--season", "2025"])

    out = capsys.readouterr().out
    assert "Teams discovered: 3" in out
    assert "[1/3] Athletics" in out
    assert "[3/3] Seattle Mariners" in out


def test_incomplete_run_exits_nonzero_but_still_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_cli(["--season", "2025"], result=INCOMPLETE_RESULT)
    captured = capsys.readouterr()
    assert exit_code == league_cli.EXIT_INCOMPLETE
    assert exit_code != 0
    assert "Ingestion coverage: INCOMPLETE" in captured.out
    assert "Teams failed: 1" in captured.out


def test_incomplete_json_run_exits_nonzero_with_valid_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_cli(
        ["--season", "2025", "--format", "json"], result=INCOMPLETE_RESULT
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == league_cli.EXIT_INCOMPLETE
    assert payload["status"] == "INCOMPLETE"
    assert payload["teams_failed"] == 1


@pytest.mark.parametrize(
    "error",
    [
        NoMlbTeamsDiscoveredError("MLB returned no Major League teams for 1776"),
        InvalidSeasonError("Season 1776 is outside 1876-2027"),
    ],
)
def test_a_failed_run_reports_on_stderr_without_a_traceback(
    capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    exit_code = run_cli(["--season", "1776"], result=error)
    captured = capsys.readouterr()
    assert exit_code == league_cli.EXIT_ERROR
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_a_missing_schema_points_at_the_migration_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = OperationalError(
        "SELECT", {}, Exception("no such table: league_season_ingestions")
    )
    state_error = LeagueIngestionStateError("Unable to record league ingestion")
    state_error.__cause__ = missing
    exit_code = run_cli(["--season", "2025"], result=state_error)
    captured = capsys.readouterr()
    assert exit_code == league_cli.EXIT_ERROR
    assert "database schema is missing" in captured.err
    assert "alembic upgrade head" in captured.err


def test_an_operational_database_error_is_not_relabelled_as_migrations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    locked = OperationalError("SELECT", {}, Exception("database is locked"))
    exit_code = run_cli(["--season", "2025"], result=locked)
    captured = capsys.readouterr()
    assert exit_code == league_cli.EXIT_ERROR
    assert "alembic upgrade head" not in captured.err
    assert "database is locked" in captured.err
