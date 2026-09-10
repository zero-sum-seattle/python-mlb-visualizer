"""Offline tests for bulk, season-scoped MLB player discovery."""

from unittest.mock import Mock

import pytest
from mlbstatsapi.exceptions import TheMlbStatsApiException
from mlbstatsapi.models.people import Person, Position

from app.schemas.players import PlayerSeasonCatalogEntry
from app.services.players import (
    NoPlayersDiscoveredError,
    PlayerDataError,
    discover_mlb_players,
)

SEASON = 2025
CF = Position(code="8", name="Outfielder", type="Outfielder", abbreviation="CF")
PITCHER = Position(code="1", name="Pitcher", type="Pitcher", abbreviation="P")


def make_person(
    player_id: int = 677594,
    full_name: str | None = "Julio Rodríguez",
    position: Position | None = CF,
    *,
    is_player: bool | None = True,
) -> Person:
    return Person(
        id=player_id,
        link=f"/api/v1/people/{player_id}",
        full_name=full_name,
        primary_position=position,
        is_player=is_player,
    )


class FakeDirectory:
    def __init__(self, result: list[Person] | Exception) -> None:
        self.result = result
        self.calls: list[tuple[int, dict[str, object]]] = []
        self.closed = False

    def get_people(self, sport_id: int = 1, **params: object) -> list[Person]:
        self.calls.append((sport_id, params))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def __enter__(self) -> "FakeDirectory":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True


def test_discovery_uses_one_season_scoped_major_league_request() -> None:
    client = FakeDirectory([make_person()])

    discovered = discover_mlb_players(SEASON, client=client)

    assert client.calls == [(1, {"season": SEASON})]
    assert discovered == [
        PlayerSeasonCatalogEntry(
            player_id=677594,
            full_name="Julio Rodríguez",
            primary_position="CF",
            season=SEASON,
        )
    ]


def test_discovery_returns_stable_name_then_id_order() -> None:
    client = FakeDirectory(
        [
            make_person(3, "zoë", PITCHER),
            make_person(2, "Ángel", CF),
            make_person(1, "Zoë", CF),
        ]
    )

    discovered = discover_mlb_players(SEASON, client=client)

    assert [entry.player_id for entry in discovered] == [1, 3, 2]


def test_empty_directory_is_an_explicit_discovery_failure() -> None:
    with pytest.raises(NoPlayersDiscoveredError, match="no players"):
        discover_mlb_players(SEASON, client=FakeDirectory([]))


@pytest.mark.parametrize(
    ("person", "message"),
    [
        (make_person(full_name=None), "No full name"),
        (make_person(position=None), "No primary position"),
        (make_person(is_player=False), "not marked as a player"),
        (make_person(is_player=None), "not marked as a player"),
    ],
)
def test_incomplete_or_non_player_records_are_rejected(
    person: Person, message: str
) -> None:
    with pytest.raises(PlayerDataError, match=message):
        discover_mlb_players(SEASON, client=FakeDirectory([person]))


def test_duplicate_player_ids_are_rejected_instead_of_deduplicated() -> None:
    client = FakeDirectory(
        [make_person(10, "Same Person"), make_person(10, "Different Name")]
    )
    with pytest.raises(PlayerDataError, match="more than once"):
        discover_mlb_players(SEASON, client=client)


def test_invalid_season_cannot_be_normalized_into_catalog_entries() -> None:
    with pytest.raises(PlayerDataError, match="Could not normalize"):
        discover_mlb_players(0, client=FakeDirectory([make_person()]))


def test_upstream_failure_preserves_the_cause() -> None:
    failure = TheMlbStatsApiException("network down")
    with pytest.raises(PlayerDataError) as caught:
        discover_mlb_players(SEASON, client=FakeDirectory(failure))
    assert caught.value.__cause__ is failure


def test_owned_client_is_created_once_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import players as players_module

    owned = FakeDirectory([make_person()])
    factory = Mock(return_value=owned)
    monkeypatch.setattr(players_module, "Mlb", factory)

    discover_mlb_players(SEASON)

    factory.assert_called_once_with()
    assert owned.calls == [(1, {"season": SEASON})]
    assert owned.closed is True


def test_caller_supplied_client_is_not_closed() -> None:
    client = FakeDirectory([make_person()])
    discover_mlb_players(SEASON, client=client)
    assert client.closed is False


def test_owned_client_closes_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import players as players_module

    owned = FakeDirectory([])
    monkeypatch.setattr(players_module, "Mlb", Mock(return_value=owned))
    with pytest.raises(NoPlayersDiscoveredError):
        discover_mlb_players(SEASON)
    assert owned.closed is True
