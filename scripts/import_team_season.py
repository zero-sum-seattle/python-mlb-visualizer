"""Import one team-season of batting lines into the local database.

Examples
--------
poetry run alembic upgrade head
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
poetry run python scripts/import_team_season.py \\
    --team-id 136 --season 2025 --format json

This script calls the live MLB Stats API unless tests replace the client. It is
not part of the automated test suite.
"""

import argparse
import json
import sys

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.database.engine import build_engine, build_session_factory
from app.schemas.ingestion import TeamSeasonIngestionResult
from app.services.team_game_logs import TeamGameLogError
from app.services.team_season_ingestion import (
    TeamSeasonIngestionError,
    ingest_team_season,
)

MIGRATION_HINT = "Run: poetry run alembic upgrade head"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--team-id", type=int, required=True, help="MLB team id, e.g. 136."
    )
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


def format_table(result: TeamSeasonIngestionResult) -> str:
    """Format an ingestion result for human-readable output.

    The unlabelled counts are batting lines, which is what they have always
    meant. Pitching lines land in their own table from their own request, so
    they are reported on their own rows rather than folded into the totals.
    """
    rows = [
        f"Team: {result.team_name}",
        f"Season: {result.season}",
        f"Fetched: {result.fetched}",
        f"Inserted: {result.inserted}",
        f"Updated: {result.updated}",
        f"Unchanged: {result.unchanged}",
    ]
    if result.pitching is not None:
        rows.extend(
            [
                f"Pitching fetched: {result.pitching.fetched}",
                f"Pitching inserted: {result.pitching.inserted}",
                f"Pitching updated: {result.pitching.updated}",
                f"Pitching unchanged: {result.pitching.unchanged}",
            ]
        )
    return "\n".join(rows)


def format_json(result: TeamSeasonIngestionResult) -> str:
    """Serialize an ingestion result as JSON."""
    return json.dumps(result.model_dump(mode="json"), indent=2)


def main(argv: list[str] | None = None) -> int:
    """Run the import command and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    session = session_factory()

    try:
        result = ingest_team_season(
            session=session,
            team_id=args.team_id,
            season=args.season,
        )
    except TeamGameLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except TeamSeasonIngestionError as exc:
        orig = exc.__cause__
        if isinstance(orig, OperationalError):
            message = str(orig.orig) if orig.orig is not None else str(orig)
            if "no such table" in message.lower():
                print(
                    f"error: database schema is missing ({message}). {MIGRATION_HINT}",
                    file=sys.stderr,
                )
                return 1
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OperationalError as exc:
        message = str(exc.orig) if exc.orig is not None else str(exc)
        if "no such table" in message.lower():
            print(
                f"error: database schema is missing ({message}). {MIGRATION_HINT}",
                file=sys.stderr,
            )
            return 1
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
        engine.dispose()

    if args.format == "json":
        print(format_json(result))
    else:
        print(format_table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
