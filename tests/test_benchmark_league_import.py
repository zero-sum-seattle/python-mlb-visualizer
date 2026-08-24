"""Tests for the league-import benchmark script.

The benchmark itself calls the live MLB Stats API, which these tests never do:
both ingestion services are replaced, so what is exercised here is the script's
own logic — which runs happen and in what order, that each run gets its own
migrated throwaway database, that the configured application database is never
consulted, how the report reads, and which exit code a partial run produces.

The timings such a run reports are meaningless here and are not asserted on.
"""

import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from scripts import benchmark_league_import as benchmark

from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionResult,
    LeagueTeamIngestionStatus,
)

STARTED = datetime(2026, 3, 1, 12, 0, 0)
FINISHED = datetime(2026, 3, 1, 12, 5, 0)


def team(team_id: int, name: str, *, failed: bool = False) -> LeagueTeamIngestionResult:
    if failed:
        return LeagueTeamIngestionResult.from_failure(
            team_id=team_id, team_name=name, season=2025, error="TeamGameLogError: no"
        )
    return LeagueTeamIngestionResult(
        team_id=team_id,
        team_name=name,
        season=2025,
        status=LeagueTeamIngestionStatus.SUCCEEDED,
        fetched=162,
        inserted=162,
        updated=0,
        unchanged=0,
    )


def result(*, complete: bool = True) -> LeagueSeasonIngestionResult:
    teams = [
        team(112, "Chicago Cubs"),
        team(136, "Seattle Mariners", failed=not complete),
    ]
    succeeded = sum(
        1 for one in teams if one.status is LeagueTeamIngestionStatus.SUCCEEDED
    )
    return LeagueSeasonIngestionResult(
        season=2025,
        teams_discovered=len(teams),
        teams_succeeded=succeeded,
        teams_failed=len(teams) - succeeded,
        team_game_records_fetched=sum(one.fetched for one in teams),
        inserted=sum(one.inserted for one in teams),
        updated=0,
        unchanged=0,
        status=(
            LeagueSeasonIngestionStatus.COMPLETE
            if complete
            else LeagueSeasonIngestionStatus.INCOMPLETE
        ),
        started_at=STARTED,
        completed_at=FINISHED,
        team_results=tuple(teams),
    )


COMPLETE_RESULT = result()
INCOMPLETE_RESULT = result(complete=False)


def timing(label: str, seconds: float, *, complete: bool = True) -> benchmark.Timing:
    return benchmark.Timing(
        label=label,
        seconds=seconds,
        result=COMPLETE_RESULT if complete else INCOMPLETE_RESULT,
    )


def run_benchmark(
    argv: list[str],
    *,
    sequential: LeagueSeasonIngestionResult = COMPLETE_RESULT,
    concurrent: LeagueSeasonIngestionResult = COMPLETE_RESULT,
) -> int:
    """Run the benchmark with both ingestion services replaced."""
    with (
        patch.object(benchmark, "ingest_league_season", return_value=sequential),
        patch.object(
            benchmark,
            "ingest_league_season_concurrently",
            new=AsyncMock(return_value=concurrent),
        ),
    ):
        return benchmark.main(argv)


# --------------------------------------------------------------------------
# Which runs happen, and in what order
# --------------------------------------------------------------------------


def test_sequential_runs_first_by_default() -> None:
    labels = [label for label, _ in benchmark.planned_runs([4, 8], reverse=False)]
    assert labels == ["sequential", "concurrent-4", "concurrent-8"]


def test_reverse_puts_the_concurrent_runs_first() -> None:
    """So run order can be ruled out as the cause of a difference."""
    labels = [label for label, _ in benchmark.planned_runs([4, 8], reverse=True)]
    assert labels == ["concurrent-4", "concurrent-8", "sequential"]


def test_more_than_one_concurrency_is_timed_separately() -> None:
    labels = [label for label, _ in benchmark.planned_runs([2, 4, 16], reverse=False)]
    assert labels == ["sequential", "concurrent-2", "concurrent-4", "concurrent-16"]


def test_a_repeated_concurrency_is_only_timed_once() -> None:
    labels = [label for label, _ in benchmark.planned_runs([8, 8], reverse=False)]
    assert labels == ["sequential", "concurrent-8"]


def test_a_bound_below_one_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_benchmark(["--season", "2025", "--concurrency", "0"]) == 1
    assert "must be at least 1" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Databases
# --------------------------------------------------------------------------


def test_each_run_gets_its_own_migrated_database(tmp_path: Path) -> None:
    first = benchmark.migrated_database(tmp_path, "sequential")
    second = benchmark.migrated_database(tmp_path, "concurrent-8")

    assert first != second
    assert (tmp_path / "sequential.db").exists()
    assert (tmp_path / "concurrent-8.db").exists()


def test_the_configured_application_database_is_never_consulted() -> None:
    """The benchmark must not be able to reach the real database.

    Two ways, because either alone is escapable: the script holds no reference
    to ``get_settings`` at all, and a run still succeeds when reading the
    application settings is made an error.
    """
    assert not hasattr(benchmark, "get_settings")

    def refuse() -> None:
        raise AssertionError("The benchmark must not read the application settings")

    with patch("app.config.get_settings", refuse):
        assert run_benchmark(["--season", "2025", "--concurrency", "2"]) == 0


def test_throwaway_databases_are_removed_unless_kept(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_benchmark(["--season", "2025", "--concurrency", "2", "--keep-databases"])
    kept = [
        line
        for line in capsys.readouterr().out.splitlines()
        if "databases left" in line
    ]
    assert len(kept) == 1
    directory = Path(kept[0].split("databases left in ")[1])
    assert directory.exists()
    shutil.rmtree(directory, ignore_errors=True)

    run_benchmark(["--season", "2025", "--concurrency", "2"])
    assert "databases left" not in capsys.readouterr().out


# --------------------------------------------------------------------------
# The report, and exit codes
# --------------------------------------------------------------------------


def test_the_report_compares_each_run_against_the_sequential_one() -> None:
    text = benchmark.format_report(
        [timing("sequential", 100.0), timing("concurrent-8", 25.0)]
    )
    assert "1.00x vs sequential" in text
    assert "4.00x vs sequential" in text


def test_the_report_says_a_single_measurement_is_not_a_result() -> None:
    """The caveat is not optional prose; it is what stops a number being quoted."""
    text = benchmark.format_report([timing("sequential", 100.0)])
    assert "anecdote" in text
    assert "--reverse" in text


def test_an_incomplete_run_is_marked_not_comparable() -> None:
    text = benchmark.format_report(
        [timing("sequential", 100.0), timing("concurrent-8", 9.0, complete=False)]
    )
    assert "coverage INCOMPLETE: not comparable" in text


def test_a_complete_benchmark_exits_zero() -> None:
    assert run_benchmark(["--season", "2025", "--concurrency", "2"]) == 0


def test_an_incomplete_run_exits_two() -> None:
    """A partial import's timing does not describe a league import."""
    assert (
        run_benchmark(
            ["--season", "2025", "--concurrency", "2"], concurrent=INCOMPLETE_RESULT
        )
        == 2
    )
