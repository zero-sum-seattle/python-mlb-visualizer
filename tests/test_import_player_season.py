"""Tests for the player-season import CLI."""

import json
from unittest.mock import patch

import pytest
from scripts import import_player_season as import_cli

from app.config import Settings
from app.schemas.ingestion import PlayerPersistenceOutcome, PlayerSeasonIngestionResult
from app.services.player_season_ingestion import PlayerSeasonIngestionError
from app.services.players import NoHittingStatsError, PlayerNotFoundError

MEMORY_SETTINGS = Settings(database_url="sqlite:///:memory:")

SAMPLE_RESULT = PlayerSeasonIngestionResult(
    player_id=677594,
    season=2025,
    full_name="Julio Rodriguez",
    identity_outcome=PlayerPersistenceOutcome.INSERTED,
    hitting_outcome=PlayerPersistenceOutcome.INSERTED,
)


def test_required_argument_parsing() -> None:
    parser = import_cli.build_parser()
    args = parser.parse_args(["--player-id", "677594", "--season", "2025"])
    assert args.player_id == 677594
    assert args.season == 2025
    assert args.format == "table"


def test_missing_required_arguments_raises_system_exit() -> None:
    parser = import_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--season", "2025"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--player-id", "677594"])


def test_table_output() -> None:
    text = import_cli.format_table(SAMPLE_RESULT)
    assert "Player: Julio Rodriguez (677594)" in text
    assert "Season: 2025" in text
    assert "Identity: INSERTED" in text
    assert "Season hitting: INSERTED" in text


def test_clean_json_output() -> None:
    payload = json.loads(import_cli.format_json(SAMPLE_RESULT))
    assert payload == {
        "player_id": 677594,
        "season": 2025,
        "full_name": "Julio Rodriguez",
        "identity_outcome": "INSERTED",
        "hitting_outcome": "INSERTED",
    }


def test_invalid_player_id_produces_nonzero_exit_without_calling_service(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch.object(import_cli, "ingest_player_season") as ingest:
        code = import_cli.main(["--player-id", "-1", "--season", "2025"])
    assert code == 1
    assert "positive" in capsys.readouterr().err
    ingest.assert_not_called()


def test_invalid_season_produces_nonzero_exit_without_calling_service(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch.object(import_cli, "ingest_player_season") as ingest:
        code = import_cli.main(["--player-id", "677594", "--season", "0"])
    assert code == 1
    assert "positive" in capsys.readouterr().err
    ingest.assert_not_called()


def test_player_not_found_produces_nonzero_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(
            import_cli,
            "ingest_player_season",
            side_effect=PlayerNotFoundError("no such player"),
        ),
    ):
        code = import_cli.main(["--player-id", "1", "--season", "2025"])
    assert code == 1
    assert "no such player" in capsys.readouterr().err


def test_no_hitting_stats_produces_nonzero_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(
            import_cli,
            "ingest_player_season",
            side_effect=NoHittingStatsError("no hitting stats"),
        ),
    ):
        code = import_cli.main(["--player-id", "677594", "--season", "1901"])
    assert code == 1
    assert "no hitting stats" in capsys.readouterr().err


def test_persistence_error_produces_nonzero_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(
            import_cli,
            "ingest_player_season",
            side_effect=PlayerSeasonIngestionError("persist failed"),
        ),
    ):
        code = import_cli.main(["--player-id", "677594", "--season", "2025"])
    assert code == 1
    assert "persist failed" in capsys.readouterr().err


def test_main_json_format_prints_only_json(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(import_cli, "ingest_player_season", return_value=SAMPLE_RESULT),
    ):
        code = import_cli.main(
            ["--player-id", "677594", "--season", "2025", "--format", "json"]
        )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["full_name"] == "Julio Rodriguez"
    assert payload["identity_outcome"] == "INSERTED"


def test_main_table_format_prints_table(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch.object(import_cli, "get_settings", return_value=MEMORY_SETTINGS),
        patch.object(import_cli, "build_engine"),
        patch.object(import_cli, "build_session_factory"),
        patch.object(import_cli, "ingest_player_season", return_value=SAMPLE_RESULT),
    ):
        code = import_cli.main(["--player-id", "677594", "--season", "2025"])
    assert code == 0
    assert "Player: Julio Rodriguez (677594)" in capsys.readouterr().out
