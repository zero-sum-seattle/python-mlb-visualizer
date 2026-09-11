"""Import MLB's season-level player directory into the local database.

Examples
--------
poetry run alembic upgrade head
poetry run python scripts/import_player_catalog.py --season 2025
poetry run python scripts/import_player_catalog.py --season 2025 --format json

This is one bulk MLB request and does not import player statistics. It stores
the identities and season memberships needed for later DB-only Player search
and selection. All ingestion behavior lives in
``app.services.player_catalog_ingestion``.
"""

import argparse
import json
import sys

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.database.engine import build_engine, build_session_factory
from app.schemas.ingestion import PlayerCatalogIngestionResult
from app.services.player_catalog_ingestion import (
    PlayerCatalogIngestionError,
    ingest_player_catalog,
)
from app.services.players import PlayerDataError

MIGRATION_HINT = "Run: poetry run alembic upgrade head"


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


def format_table(result: PlayerCatalogIngestionResult) -> str:
    """Format an ingestion result for human-readable output."""
    return "\n".join(
        [
            f"Season: {result.season}",
            f"Players discovered: {result.players_discovered}",
            f"Inserted: {result.inserted}",
            f"Updated: {result.updated}",
            f"Unchanged: {result.unchanged}",
        ]
    )


def format_json(result: PlayerCatalogIngestionResult) -> str:
    """Serialize an ingestion result as JSON."""
    return json.dumps(result.model_dump(mode="json"), indent=2)


def _report_operational_error(exc: OperationalError) -> None:
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "no such table" in message.lower():
        print(
            f"error: database schema is missing ({message}). {MIGRATION_HINT}",
            file=sys.stderr,
        )
        return
    print(f"error: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the import command and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.season <= 0:
        print("error: --season must be a positive integer", file=sys.stderr)
        return 1

    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        result = ingest_player_catalog(session=session, season=args.season)
    except PlayerDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except PlayerCatalogIngestionError as exc:
        cause = exc.__cause__
        if isinstance(cause, OperationalError):
            _report_operational_error(cause)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    except OperationalError as exc:
        _report_operational_error(exc)
        return 1
    finally:
        session.close()
        engine.dispose()

    print(format_json(result) if args.format == "json" else format_table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
