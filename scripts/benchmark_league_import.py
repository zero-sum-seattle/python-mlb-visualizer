"""Time a sequential league import against one or more concurrent ones.

This is a **live benchmark against a public API**. It really calls
statsapi.mlb.com, roughly four requests per club per run, and every number it
prints is a measurement of one moment: MLB's load, this machine's network path,
DNS, TLS setup, and how warm any cache along the way happens to be. One pair of
numbers is an anecdote, not a result. If a speedup matters to a decision, run
this several times, in both orders, and look at the spread rather than the best
run. Do not quote a single figure from it as the speedup of the feature.

It is also not a load test, and it is polite to treat it as if MLB's operators
were watching: each run refetches an entire season.

Databases
---------
Every run gets its **own throwaway SQLite file**, created in a temporary
directory and migrated to Alembic head before the run starts. The database
configured for the application is never opened, read, or written — nothing here
consults ``get_settings()``. A run therefore always starts from an empty
database, so each timing includes the same inserting work rather than one run
inserting and the next finding everything unchanged. Temporary files are
removed afterwards unless ``--keep-databases`` is passed.

Examples
--------
poetry run python scripts/benchmark_league_import.py --season 2025
poetry run python scripts/benchmark_league_import.py --season 2025 --concurrency 4 8 16
poetry run python scripts/benchmark_league_import.py --season 2025 \
    --concurrency 8 --reverse

``--reverse`` runs the concurrent imports before the sequential one. Running it
both ways is the cheapest check that the ordering of the runs — a warming DNS
or connection cache, say — is not what produced the difference. If the two
orders disagree, believe neither.

Exit codes
----------
0   every run finished with COMPLETE coverage
1   the benchmark could not be carried out
2   at least one run finished with INCOMPLETE coverage, so its timing describes
    a partial import and is not comparable
"""

import argparse
import asyncio
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueSeasonIngestionStatus,
)
from app.services.concurrent_league_season_ingestion import (
    DEFAULT_CONCURRENCY,
    ingest_league_season_concurrently,
)
from app.services.league_season_ingestion import (
    LeagueSeasonIngestionError,
    ingest_league_season,
)
from app.services.league_teams import MlbTeamDiscoveryError

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 2

SEQUENTIAL_LABEL = "sequential"

# One timed import: given a session and a season, run it and return its result.
Runner = Callable[[Session, int], LeagueSeasonIngestionResult]


@dataclass(frozen=True)
class Timing:
    """One league import, and how long it took end to end."""

    label: str
    seconds: float
    result: LeagueSeasonIngestionResult

    @property
    def comparable(self) -> bool:
        """Whether this run's timing describes a whole league import."""
        return self.result.status is LeagueSeasonIngestionStatus.COMPLETE


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--season", type=int, required=True, help="Season year, e.g. 2025."
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        nargs="+",
        default=[DEFAULT_CONCURRENCY],
        help=(
            "One or more concurrency bounds to time, each as its own run "
            f"(default: {DEFAULT_CONCURRENCY})."
        ),
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help=(
            "Run the concurrent imports before the sequential one, to check "
            "that run order is not what produced the difference."
        ),
    )
    parser.add_argument(
        "--keep-databases",
        action="store_true",
        help="Leave each run's throwaway database behind and print its path.",
    )
    return parser


def migrated_database(directory: Path, label: str) -> str:
    """Create an empty SQLite database at Alembic head and return its URL."""
    database_url = f"sqlite:///{directory / f'{label}.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


def time_run(
    directory: Path,
    label: str,
    run: Runner,
    season: int,
) -> Timing:
    """Run one import against its own fresh database and time it."""
    engine = build_engine(migrated_database(directory, label))
    session: Session = build_session_factory(engine)()
    try:
        started = time.perf_counter()
        result = run(session, season)
        elapsed = time.perf_counter() - started
    finally:
        session.close()
        engine.dispose()
    return Timing(label=label, seconds=elapsed, result=result)


def run_sequential(session: Session, season: int) -> LeagueSeasonIngestionResult:
    return ingest_league_season(session=session, season=season)


def run_concurrent(concurrency: int) -> Runner:
    def run(session: Session, season: int) -> LeagueSeasonIngestionResult:
        return asyncio.run(
            ingest_league_season_concurrently(
                session=session, season=season, concurrency=concurrency
            )
        )

    return run


def planned_runs(concurrency: list[int], *, reverse: bool) -> list[tuple[str, Runner]]:
    """Decide which runs happen, and in what order."""
    sequential = [(SEQUENTIAL_LABEL, run_sequential)]
    concurrent = [
        (f"concurrent-{bound}", run_concurrent(bound))
        for bound in dict.fromkeys(concurrency)
    ]
    return concurrent + sequential if reverse else sequential + concurrent


def format_report(timings: list[Timing]) -> str:
    """Format the timings, with the sequential run as the baseline."""
    baseline = next(
        (timing for timing in timings if timing.label == SEQUENTIAL_LABEL), None
    )
    width = max(len(timing.label) for timing in timings)
    lines = ["", "Run order was as printed. Each run used its own empty database.", ""]
    for timing in timings:
        row = f"{timing.label:<{width}}  {timing.seconds:8.2f}s"
        if baseline is not None and baseline.seconds > 0:
            row += f"  {baseline.seconds / timing.seconds:5.2f}x vs sequential"
        if not timing.comparable:
            row += f"  (coverage {timing.result.status.value}: not comparable)"
        lines.append(row)
    lines.append("")
    lines.append(
        "One pair of numbers is an anecdote. Re-run, and re-run with --reverse, "
        "before believing a ratio."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and return a process exit code."""
    args = build_parser().parse_args(argv)

    for bound in args.concurrency:
        if bound < 1:
            print(f"error: concurrency {bound} must be at least 1", file=sys.stderr)
            return EXIT_ERROR

    directory = Path(tempfile.mkdtemp(prefix="league-import-benchmark-"))
    timings: list[Timing] = []
    try:
        for label, run in planned_runs(args.concurrency, reverse=args.reverse):
            print(f"running {label} ...", flush=True)
            timings.append(time_run(directory, label, run, args.season))
    except (MlbTeamDiscoveryError, LeagueSeasonIngestionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        if args.keep_databases:
            print(f"databases left in {directory}")
        else:
            shutil.rmtree(directory, ignore_errors=True)

    print(format_report(timings))
    if all(timing.comparable for timing in timings):
        return EXIT_SUCCESS
    return EXIT_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
