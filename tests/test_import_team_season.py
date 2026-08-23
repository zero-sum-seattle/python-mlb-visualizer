"""Tests for the team-season import CLI."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts import import_team_season as import_cli
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.schemas.ingestion import TeamSeasonIngestionResult
from app.services.team_game_logs import TeamGameLogError
from app.services.team_season_ingestion import TeamSeasonIngestionError

MEMORY_SETTINGS = Settings(database_url="sqlite:///:memory:")


SAMPLE_RESULT = TeamSeasonIngestionResult(
    team_id=136,
    team_name="Seattle Mariners",
    season=2025,
    fetched=162,
    inserted=162,
    updated=0,
    unchanged=0,
)


def test_required_argument_parsing() -> None:
    parser = import_cli.build_parser()
    args = parser.parse_args(["--team-id", "136", "--season", "2025"])
    assert args.team_id == 136
    assert args.season == 2025
    assert args.format == "table"


def test_table_output() -> None:
    text = import_cli.format_table(SAMPLE_RESULT)
    assert "Team: Seattle Mariners" in text
    assert "Fetched: 162" in text
    assert "Inserted: 162" in text


def test_clean_json_output() -> None:
    payload = json.loads(import_cli.format_json(SAMPLE_RESULT))
    assert payload == {
        "team_id": 136,
        "team_name": "Seattle Mariners",
        "season": 2025,
        "fetched": 162,
        "inserted": 162,
        "updated": 0,
        "unchanged": 0,
        # Null rather than zero: this sample did not collect pitching at all,
        # which is a different thing from having collected nothing.
        "pitching": None,
    }


def test_mlb_error_produces_nonzero_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(
            import_cli,
            "ingest_team_season",
            side_effect=TeamGameLogError("mlb failed"),
        ),
    ):
        code = import_cli.main(["--team-id", "136", "--season", "2025"])
    assert code == 1
    captured = capsys.readouterr()
    assert "mlb failed" in captured.err


def test_persistence_error_produces_nonzero_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(
            import_cli,
            "ingest_team_season",
            side_effect=TeamSeasonIngestionError("persist failed"),
        ),
    ):
        code = import_cli.main(["--team-id", "136", "--season", "2025"])
    assert code == 1
    assert "persist failed" in capsys.readouterr().err


def test_missing_schema_provides_migration_guidance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "empty.db"
    url = f"sqlite:///{db_path}"
    operational = OperationalError(
        "INSERT",
        {},
        Exception("no such table: team_game_batting_lines"),
    )
    wrapped = TeamSeasonIngestionError("Unable to persist team 136 season 2025")
    wrapped.__cause__ = operational

    settings = Settings(database_url=url)
    with (
        patch.object(import_cli, "get_settings", return_value=settings),
        patch.object(import_cli, "ingest_team_season", side_effect=wrapped),
    ):
        code = import_cli.main(["--team-id", "136", "--season", "2025"])
    assert code == 1
    err = capsys.readouterr().err
    assert "alembic upgrade head" in err


def test_main_json_format_prints_only_json(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(import_cli, "ingest_team_season", return_value=SAMPLE_RESULT),
    ):
        code = import_cli.main(
            ["--team-id", "136", "--season", "2025", "--format", "json"]
        )
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["team_name"] == "Seattle Mariners"
    assert payload["unchanged"] == 0
