"""Tests for atomic season-level Player catalog ingestion."""

from unittest.mock import Mock, patch

import pytest
from mlbstatsapi.models.people import Person, Position
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models import (
    PlayerRecord,
    PlayerSeasonCatalogRecord,
    PlayerSeasonHittingRecord,
)
from app.database.repositories import list_player_catalog
from app.schemas.ingestion import PlayerPersistenceOutcome
from app.services.player_catalog_ingestion import (
    PlayerCatalogIngestionError,
    ingest_player_catalog,
)

SEASON = 2025
CF = Position(code="8", name="Outfielder", type="Outfielder", abbreviation="CF")
PITCHER = Position(code="1", name="Pitcher", type="Pitcher", abbreviation="P")


def make_person(
    player_id: int = 677594,
    full_name: str = "Julio Rodríguez",
    position: Position = CF,
) -> Person:
    return Person(
        id=player_id,
        link=f"/api/v1/people/{player_id}",
        full_name=full_name,
        primary_position=position,
        is_player=True,
    )


class FakeDirectory:
    def __init__(self, people: list[Person]) -> None:
        self.people = people
        self.calls = 0
        self.closed = False

    def get_people(self, sport_id: int = 1, **params: object) -> list[Person]:
        self.calls += 1
        return self.people

    def __enter__(self) -> "FakeDirectory":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True


def make_client() -> FakeDirectory:
    return FakeDirectory(
        [
            make_person(),
            make_person(660271, "Shohei Ohtani", PITCHER),
        ]
    )


def test_import_inserts_identities_and_memberships_but_no_stats(
    migrated_session: Session,
) -> None:
    result = ingest_player_catalog(
        session=migrated_session, season=SEASON, client=make_client()
    )

    assert result.players_discovered == 2
    assert result.inserted == 2
    assert result.updated == 0
    assert result.unchanged == 0
    assert len(list_player_catalog(migrated_session, season=SEASON)) == 2
    assert migrated_session.scalar(select(func.count()).select_from(PlayerRecord)) == 2
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(PlayerSeasonHittingRecord)
        )
        == 0
    )


def test_identical_rerun_is_unchanged(migrated_session: Session) -> None:
    ingest_player_catalog(session=migrated_session, season=SEASON, client=make_client())
    result = ingest_player_catalog(
        session=migrated_session, season=SEASON, client=make_client()
    )
    assert result.inserted == 0
    assert result.updated == 0
    assert result.unchanged == 2
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(PlayerSeasonCatalogRecord)
        )
        == 2
    )


def test_existing_membership_with_changed_identity_is_updated(
    migrated_session: Session,
) -> None:
    ingest_player_catalog(session=migrated_session, season=SEASON, client=make_client())
    changed = FakeDirectory(
        [
            make_person(full_name="Julio Rodriguez"),
            make_person(660271, "Shohei Ohtani", PITCHER),
        ]
    )
    result = ingest_player_catalog(
        session=migrated_session, season=SEASON, client=changed
    )
    assert result.updated == 1
    assert result.unchanged == 1
    entries = list_player_catalog(migrated_session, season=SEASON)
    assert entries[0].full_name == "Julio Rodriguez"


def test_same_player_in_another_season_gets_a_second_membership(
    migrated_session: Session,
) -> None:
    one = FakeDirectory([make_person()])
    ingest_player_catalog(session=migrated_session, season=2024, client=one)
    result = ingest_player_catalog(
        session=migrated_session, season=2025, client=FakeDirectory([make_person()])
    )
    assert result.inserted == 1
    assert migrated_session.scalar(select(func.count()).select_from(PlayerRecord)) == 1
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(PlayerSeasonCatalogRecord)
        )
        == 2
    )


def test_database_failure_rolls_back_entire_catalog(migrated_session: Session) -> None:
    outcomes = [PlayerPersistenceOutcome.INSERTED, SQLAlchemyError("db failed")]
    with (
        patch(
            "app.services.player_catalog_ingestion.upsert_player_catalog_entry",
            side_effect=outcomes,
        ),
        pytest.raises(PlayerCatalogIngestionError),
    ):
        ingest_player_catalog(
            session=migrated_session, season=SEASON, client=make_client()
        )
    assert migrated_session.scalar(select(func.count()).select_from(PlayerRecord)) == 0
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(PlayerSeasonCatalogRecord)
        )
        == 0
    )


def test_discovery_finishes_before_transaction_begins(
    migrated_session: Session,
) -> None:
    events: list[str] = []

    class TrackingClient(FakeDirectory):
        def get_people(self, sport_id: int = 1, **params: object) -> list[Person]:
            events.append("get_people")
            return super().get_people(sport_id, **params)

    class TrackingSession:
        def begin(self) -> object:
            events.append("transaction_begin")
            return migrated_session.begin()

        def __getattr__(self, name: str) -> object:
            return getattr(migrated_session, name)

    ingest_player_catalog(
        session=TrackingSession(),  # type: ignore[arg-type]
        season=SEASON,
        client=TrackingClient([make_person()]),
    )
    assert events == ["get_people", "transaction_begin"]


def test_no_client_supplied_uses_one_owned_client(
    monkeypatch: pytest.MonkeyPatch, migrated_session: Session
) -> None:
    from app.services import player_catalog_ingestion as ingestion_module

    owned = make_client()
    factory = Mock(return_value=owned)
    monkeypatch.setattr(ingestion_module, "Mlb", factory)

    ingest_player_catalog(session=migrated_session, season=SEASON)

    factory.assert_called_once_with()
    assert owned.calls == 1
    assert owned.closed is True


def test_supplied_client_is_not_closed(migrated_session: Session) -> None:
    client = make_client()
    ingest_player_catalog(session=migrated_session, season=SEASON, client=client)
    assert client.closed is False
