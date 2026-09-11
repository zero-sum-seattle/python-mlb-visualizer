"""Atomic season-level MLB player catalog ingestion."""

from mlbstatsapi import Mlb
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.repositories import upsert_player_catalog_entry
from app.schemas.ingestion import (
    PlayerCatalogIngestionResult,
    PlayerPersistenceOutcome,
)
from app.services.players import MlbPlayerDirectoryClient, discover_mlb_players


class PlayerCatalogIngestionError(Exception):
    """A discovered MLB player catalog could not be persisted."""


def ingest_player_catalog(
    *,
    session: Session,
    season: int,
    client: MlbPlayerDirectoryClient | None = None,
) -> PlayerCatalogIngestionResult:
    """Discover and atomically persist MLB's player directory for one season.

    The endpoint is one bulk request. When no client is supplied, one ``Mlb``
    instance is created for that complete logical import and closed afterward.
    All response validation finishes before the database transaction begins.
    """
    if client is not None:
        return _ingest_player_catalog(session=session, season=season, client=client)
    with Mlb() as owned_client:
        return _ingest_player_catalog(
            session=session, season=season, client=owned_client
        )


def _ingest_player_catalog(
    *,
    session: Session,
    season: int,
    client: MlbPlayerDirectoryClient,
) -> PlayerCatalogIngestionResult:
    entries = discover_mlb_players(season, client=client)
    counts = {outcome: 0 for outcome in PlayerPersistenceOutcome}

    try:
        with session.begin():
            for entry in entries:
                outcome = upsert_player_catalog_entry(session, entry=entry)
                counts[outcome] += 1
    except SQLAlchemyError as exc:
        raise PlayerCatalogIngestionError(
            f"Unable to persist the MLB player catalog for {season}"
        ) from exc

    return PlayerCatalogIngestionResult(
        season=season,
        players_discovered=len(entries),
        inserted=counts[PlayerPersistenceOutcome.INSERTED],
        updated=counts[PlayerPersistenceOutcome.UPDATED],
        unchanged=counts[PlayerPersistenceOutcome.UNCHANGED],
    )
