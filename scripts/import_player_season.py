"""Import one player-season of hitting stats into the local database.

Examples
--------
poetry run alembic upgrade head
poetry run python scripts/import_player_season.py --player-id 677594 --season 2025
poetry run python scripts/import_player_season.py \\
    --player-id 677594 --season 2025 --format json

This script calls the live MLB Stats API unless tests replace the client. It is
not part of the automated test suite.
"""

import argparse
import json
import sys

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.database.engine import build_engine, build_session_factory
from app.schemas.ingestion import PlayerSeasonIngestionResult
from app.services.player_season_ingestion import (
    PlayerSeasonIngestionError,
    ingest_player_season,
)
from app.services.players import PlayerDataError

MIGRATION_HINT = "Run: poetry run alembic upgrade head"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--player-id", type=int, required=True, help="MLB player id, e.g. 677594."
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


def format_table(result: PlayerSeasonIngestionResult) -> str:
    """Format an ingestion result for human-readable output."""
    return "\n".join(
        [
            f"Player: {result.full_name} ({result.player_id})",
            f"Season: {result.season}",
            f"Identity: {result.identity_outcome.value}",
            f"Season hitting: {result.hitting_outcome.value}",
        ]
    )


def format_json(result: PlayerSeasonIngestionResult) -> str:
    """Serialize an ingestion result as JSON."""
    return json.dumps(result.model_dump(mode="json"), indent=2)


def main(argv: list[str] | None = None) -> int:
    """Run the import command and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.player_id <= 0:
        print("error: --player-id must be a positive integer", file=sys.stderr)
        return 1
    if args.season <= 0:
        print("error: --season must be a positive integer", file=sys.stderr)
        return 1

    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    session = session_factory()

    try:
        result = ingest_player_season(
            session=session,
            player_id=args.player_id,
            season=args.season,
        )
    except PlayerDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except PlayerSeasonIngestionError as exc:
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
