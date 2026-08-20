"""Import every MLB team-season for one season into the local database.

Examples
--------
poetry run alembic upgrade head
poetry run python scripts/import_league_season.py --season 2025
poetry run python scripts/import_league_season.py --season 2025 --format json

Exit codes
----------
0   every discovered team was ingested (COMPLETE coverage)
1   the run could not be carried out: invalid season, discovery failure, or
    coverage state could not be persisted
2   the run finished but at least one discovered team failed (INCOMPLETE
    coverage). Teams that succeeded are committed; rerun to re-attempt.

This script calls the live MLB Stats API unless tests replace the client. It is
not part of the automated test suite. All ingestion behavior lives in
``app.services.league_season_ingestion``; this file only parses arguments,
builds dependencies, formats output, and chooses an exit code.
"""

import argparse
import json
import sys

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.database.engine import build_engine, build_session_factory
from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionResult,
    LeagueTeamIngestionStatus,
)
from app.services.league_season_ingestion import (
    LeagueSeasonIngestionError,
    ingest_league_season,
)
from app.services.league_teams import MlbTeamDiscoveryError

MIGRATION_HINT = "Run: poetry run alembic upgrade head"

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 2

# Widest club name the leader dots are padded to. Longer names simply push the
# outcome column right rather than being truncated.
NAME_COLUMN_WIDTH = 26


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--season", type=int, required=True, help="Season year, e.g. 2025."
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format (default: table).",
    )
    return parser


def team_outcome(result: LeagueTeamIngestionResult) -> str:
    """Summarize one team's outcome in a single word for the progress column."""
    if result.status is LeagueTeamIngestionStatus.FAILED:
        return "failed"
    if result.fetched == 0:
        return "no games"
    if result.unchanged == result.fetched:
        return "unchanged"
    if result.inserted == result.fetched:
        return "inserted"
    if result.updated:
        return "updated"
    return "inserted"


def format_progress_line(
    position: int,
    total: int,
    result: LeagueTeamIngestionResult,
) -> str:
    """Format one completed team as a progress row."""
    width = len(str(total))
    padding = max(NAME_COLUMN_WIDTH - len(result.team_name), 1)
    return (
        f"[{position:>{width}}/{total}] {result.team_name} "
        f"{'.' * padding} {team_outcome(result)}"
    )


def format_table(result: LeagueSeasonIngestionResult) -> str:
    """Format a league ingestion result for human-readable output.

    ``Ingestion coverage`` is deliberately not labelled ``Status``: COMPLETE
    means every discovered team was refreshed by this run, not that the season
    has finished being played.
    """
    lines = [
        f"Season: {result.season}",
        f"Teams discovered: {result.teams_discovered}",
        f"Teams succeeded: {result.teams_succeeded}",
        f"Teams failed: {result.teams_failed}",
        f"Team-game records fetched: {result.team_game_records_fetched}",
        f"Inserted: {result.inserted}",
        f"Updated: {result.updated}",
        f"Unchanged: {result.unchanged}",
        f"Ingestion coverage: {result.status.value}",
    ]
    failures = [
        team
        for team in result.team_results
        if team.status is LeagueTeamIngestionStatus.FAILED
    ]
    if failures:
        lines.append("")
        lines.append("Failures:")
        lines.extend(
            f"  {team.team_name} (id {team.team_id}): {team.error}" for team in failures
        )
    return "\n".join(lines)


def format_json(result: LeagueSeasonIngestionResult) -> str:
    """Serialize a league ingestion result as JSON."""
    return json.dumps(result.model_dump(mode="json"), indent=2)


def _report_operational_error(exc: OperationalError) -> None:
    """Print an operational database failure, pointing at migrations if apt."""
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "no such table" in message.lower():
        print(
            f"error: database schema is missing ({message}). {MIGRATION_HINT}",
            file=sys.stderr,
        )
        return
    print(f"error: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the league import command and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    show_progress = args.format == "table"

    def on_team_complete(
        position: int,
        total: int,
        team_result: LeagueTeamIngestionResult,
    ) -> None:
        if position == 1:
            print(f"Teams discovered: {total}")
        print(format_progress_line(position, total, team_result), flush=True)

    if show_progress:
        print(f"MLB League Import — {args.season}")

    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    session = session_factory()

    try:
        result = ingest_league_season(
            session=session,
            season=args.season,
            on_team_complete=on_team_complete if show_progress else None,
        )
    except MlbTeamDiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except LeagueSeasonIngestionError as exc:
        cause = exc.__cause__
        if isinstance(cause, OperationalError):
            _report_operational_error(cause)
            return EXIT_ERROR
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OperationalError as exc:
        _report_operational_error(exc)
        return EXIT_ERROR
    finally:
        session.close()
        engine.dispose()

    if args.format == "json":
        print(format_json(result))
    else:
        print(format_table(result))

    if result.status is LeagueSeasonIngestionStatus.COMPLETE:
        return EXIT_SUCCESS
    return EXIT_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
