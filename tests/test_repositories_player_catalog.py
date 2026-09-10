"""Tests for season-level Player catalog repository behavior."""

from sqlalchemy.orm import Session

from app.database.repositories import (
    list_player_catalog,
    upsert_player_catalog_entry,
)
from app.schemas.ingestion import PlayerPersistenceOutcome
from app.schemas.players import PlayerSeasonCatalogEntry


def entry(
    player_id: int = 677594,
    full_name: str = "Julio Rodríguez",
    position: str = "CF",
    season: int = 2025,
) -> PlayerSeasonCatalogEntry:
    return PlayerSeasonCatalogEntry(
        player_id=player_id,
        full_name=full_name,
        primary_position=position,
        season=season,
    )


def test_new_entry_is_inserted_and_listed(migrated_session: Session) -> None:
    outcome = upsert_player_catalog_entry(migrated_session, entry=entry())
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.INSERTED
    assert list_player_catalog(migrated_session, season=2025) == [entry()]


def test_identical_entry_is_unchanged(migrated_session: Session) -> None:
    upsert_player_catalog_entry(migrated_session, entry=entry())
    migrated_session.commit()
    outcome = upsert_player_catalog_entry(migrated_session, entry=entry())
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.UNCHANGED


def test_changed_identity_is_updated_for_existing_membership(
    migrated_session: Session,
) -> None:
    upsert_player_catalog_entry(migrated_session, entry=entry())
    migrated_session.commit()
    changed = entry(full_name="Julio Rodriguez", position="OF")
    outcome = upsert_player_catalog_entry(migrated_session, entry=changed)
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.UPDATED
    assert list_player_catalog(migrated_session, season=2025) == [changed]


def test_new_season_membership_is_inserted_for_existing_identity(
    migrated_session: Session,
) -> None:
    upsert_player_catalog_entry(migrated_session, entry=entry(season=2024))
    migrated_session.commit()
    outcome = upsert_player_catalog_entry(migrated_session, entry=entry(season=2025))
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.INSERTED
    assert list_player_catalog(migrated_session, season=2024) == [entry(season=2024)]
    assert list_player_catalog(migrated_session, season=2025) == [entry(season=2025)]


def test_catalog_is_season_scoped_and_sorted(migrated_session: Session) -> None:
    entries = [
        entry(3, "Zach Player", "P"),
        entry(2, "Aaron Player", "1B"),
        entry(1, "Other Season", "C", season=2024),
    ]
    for catalog_entry in entries:
        upsert_player_catalog_entry(migrated_session, entry=catalog_entry)
    migrated_session.commit()
    assert list_player_catalog(migrated_session, season=2025) == [
        entries[1],
        entries[0],
    ]
    assert list_player_catalog(migrated_session, season=1900) == []
