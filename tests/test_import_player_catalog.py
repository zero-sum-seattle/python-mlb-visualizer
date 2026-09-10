"""Tests for the season-level Player catalog import CLI."""

import json
from unittest.mock import patch

import pytest
from scripts import import_player_catalog as import_cli
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.schemas.ingestion import PlayerCatalogIngestionResult
from app.services.player_catalog_ingestion import PlayerCatalogIngestionError
from app.services.players import NoPlayersDiscoveredError

MEMORY_SETTINGS = Settings(database_url="sqlite:///:memory:")
RESULT = PlayerCatalogIngestionResult(
    season=2025,
    players_discovered=3,
    inserted=2,
    updated=1,
    unchanged=0,
)


def test_argument_parsing() -> None:
    args = import_cli.build_parser().parse_args(["--season", "2025"])
    assert args.season == 2025
    assert args.format == "table"


def test_table_and_json_output() -> None:
    assert "Players discovered: 3" in import_cli.format_table(RESULT)
    assert json.loads(import_cli.format_json(RESULT)) == {
        "season": 2025,
        "players_discovered": 3,
        "inserted": 2,
        "updated": 1,
        "unchanged": 0,
    }


def test_nonpositive_season_fails_without_calling_service(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch.object(import_cli, "ingest_player_catalog") as ingest:
        code = import_cli.main(["--season", "0"])
    assert code == 1
    assert "positive" in capsys.readouterr().err
    ingest.assert_not_called()


def test_discovery_error_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(
            import_cli,
            "ingest_player_catalog",
            side_effect=NoPlayersDiscoveredError("no players"),
        ),
    ):
        code = import_cli.main(["--season", "2025"])
    assert code == 1
    assert "no players" in capsys.readouterr().err


def test_missing_migration_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    cause = OperationalError("insert", None, Exception("no such table: player_seasons"))
    error = PlayerCatalogIngestionError("persist failed")
    error.__cause__ = cause
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(import_cli, "ingest_player_catalog", side_effect=error),
    ):
        code = import_cli.main(["--season", "2025"])
    assert code == 1
    assert "alembic upgrade head" in capsys.readouterr().err


def test_other_persistence_error_is_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(
            import_cli,
            "ingest_player_catalog",
            side_effect=PlayerCatalogIngestionError("persist failed"),
        ),
    ):
        code = import_cli.main(["--season", "2025"])
    assert code == 1
    assert "persist failed" in capsys.readouterr().err


def test_success_prints_requested_format(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(import_cli, "ingest_player_catalog", return_value=RESULT),
    ):
        code = import_cli.main(["--season", "2025", "--format", "json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["players_discovered"] == 3
