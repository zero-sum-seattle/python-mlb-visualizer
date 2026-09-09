"""Tests for player identity and season hitting retrieval/normalization.

Nothing here touches the network: the ``mlbstatsapi.Mlb`` client is replaced at
the service boundary with ``FakeMlb``, and payloads are built directly from the
library's own Pydantic models -- the same models the real client returns.
"""

from typing import Any

import pytest
from mlbstatsapi.exceptions import TheMlbStatsApiException
from mlbstatsapi.models.people import Person, Position
from mlbstatsapi.models.stats import Stat
from mlbstatsapi.models.stats.hitting import HittingSeason, SimpleHittingSplit
from mlbstatsapi.models.teams import Team

from app.schemas.players import PlayerIdentity, PlayerSeasonHitting
from app.services.players import (
    NoHittingStatsError,
    PlayerDataError,
    PlayerNotFoundError,
    get_player_identity,
    get_player_season_hitting,
)

PLAYER_ID = 677594
SEASON = 2025

CF_POSITION = Position(
    code="8", name="Outfielder", type="Outfielder", abbreviation="CF"
)
MARINERS = Team(id=136, link="/api/v1/teams/136", name="Seattle Mariners")
NATIONALS = Team(id=120, link="/api/v1/teams/120", name="Washington Nationals")
PADRES = Team(id=135, link="/api/v1/teams/135", name="San Diego Padres")


def make_person(**overrides: object) -> Person:
    base: dict[str, Any] = {
        "id": PLAYER_ID,
        "link": f"/api/v1/people/{PLAYER_ID}",
        "full_name": "Julio Rodriguez",
        "primary_position": CF_POSITION,
    }
    base.update(overrides)
    return Person(**base)


def make_simple_hitting(**overrides: object) -> SimpleHittingSplit:
    base: dict[str, Any] = {
        "games_played": 150,
        "plate_appearances": 600,
        "at_bats": 500,
        "runs": 80,
        "hits": 150,
        "doubles": 30,
        "triples": 3,
        "home_runs": 20,
        "rbi": 90,
        "base_on_balls": 60,
        "intentional_walks": 5,
        "hit_by_pitch": 5,
        "strikeouts": 100,
        "stolen_bases": 10,
        "caught_stealing": 3,
        "sac_flies": 4,
        "sac_bunts": 2,
    }
    base.update(overrides)
    return SimpleHittingSplit(**base)


def make_split(*, team: Team | None = None, **stat_overrides: object) -> HittingSeason:
    return HittingSeason(
        season=str(SEASON), team=team, stat=make_simple_hitting(**stat_overrides)
    )


def make_stat(splits: list[HittingSeason]) -> Stat:
    return Stat(group="hitting", type="season", totalSplits=len(splits), splits=splits)


_DEFAULT = object()


class FakeMlb:
    """Stands in for ``mlbstatsapi.Mlb`` at the service boundary.

    Either return value may be an exception instance, which is raised instead
    of returned. Passing ``person=None`` means the player was not found, which
    is different from omitting ``person`` altogether.
    """

    def __init__(
        self,
        *,
        person: Person | Exception | None = _DEFAULT,
        player_stats: dict | Exception | None = None,
    ) -> None:
        self._person = make_person() if person is _DEFAULT else person
        self._player_stats = {} if player_stats is None else player_stats
        self.calls: list[str] = []

    def get_person(self, player_id: int, **params: object) -> Person | None:
        self.calls.append("get_person")
        if isinstance(self._person, Exception):
            raise self._person
        return self._person

    def get_player_stats(
        self, person_id: int, stats: list, groups: list, **params: object
    ) -> dict:
        self.calls.append("get_player_stats")
        if isinstance(self._player_stats, Exception):
            raise self._player_stats
        return self._player_stats


# ---------------------------------------------------------------------------
# Identity normalization
# ---------------------------------------------------------------------------


def test_valid_person_normalizes_to_identity() -> None:
    client = FakeMlb(person=make_person())
    identity = get_player_identity(PLAYER_ID, client=client)
    assert identity == PlayerIdentity(
        player_id=PLAYER_ID, full_name="Julio Rodriguez", primary_position="CF"
    )


def test_two_way_player_position_abbreviation_is_preserved() -> None:
    twp = Position(
        code="Y", name="Two-Way Player", type="Two-Way Player", abbreviation="TWP"
    )
    client = FakeMlb(person=make_person(primary_position=twp))
    identity = get_player_identity(PLAYER_ID, client=client)
    assert identity.primary_position == "TWP"


def test_person_not_found_raises_player_not_found() -> None:
    client = FakeMlb(person=None)
    with pytest.raises(PlayerNotFoundError):
        get_player_identity(PLAYER_ID, client=client)


def test_missing_full_name_raises_player_data_error() -> None:
    client = FakeMlb(person=make_person(full_name=None))
    with pytest.raises(PlayerDataError):
        get_player_identity(PLAYER_ID, client=client)


def test_missing_primary_position_raises_player_data_error() -> None:
    client = FakeMlb(person=make_person(primary_position=None))
    with pytest.raises(PlayerDataError):
        get_player_identity(PLAYER_ID, client=client)


def test_person_id_mismatch_raises_player_data_error() -> None:
    client = FakeMlb(person=make_person(id=999999))
    with pytest.raises(PlayerDataError):
        get_player_identity(PLAYER_ID, client=client)


def test_identity_request_failure_raises_player_data_error() -> None:
    client = FakeMlb(person=TheMlbStatsApiException("network down"))
    with pytest.raises(PlayerDataError):
        get_player_identity(PLAYER_ID, client=client)


# ---------------------------------------------------------------------------
# Season hitting: split selection
# ---------------------------------------------------------------------------


def test_single_split_is_used_as_is() -> None:
    stat = make_stat([make_split(team=MARINERS)])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    hitting = get_player_season_hitting(PLAYER_ID, SEASON, client=client)
    assert hitting.hits == 150
    assert hitting.at_bats == 500


def test_traded_player_selects_the_aggregate_split() -> None:
    """An aggregate split (team=None) plus two team-specific splits."""
    aggregate = make_split(team=None, hits=127, at_bats=524, plate_appearances=664)
    washington = make_split(team=NATIONALS, hits=84, at_bats=342, plate_appearances=436)
    san_diego = make_split(team=PADRES, hits=43, at_bats=182, plate_appearances=228)
    stat = make_stat([aggregate, washington, san_diego])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})

    hitting = get_player_season_hitting(PLAYER_ID, SEASON, client=client)

    assert hitting.hits == 127
    assert hitting.at_bats == 524
    assert hitting.plate_appearances == 664


def test_zero_splits_raises_no_hitting_stats() -> None:
    stat = make_stat([])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    with pytest.raises(NoHittingStatsError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


def test_empty_response_raises_no_hitting_stats() -> None:
    client = FakeMlb(player_stats={})
    with pytest.raises(NoHittingStatsError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


def test_missing_season_stat_type_raises_no_hitting_stats() -> None:
    client = FakeMlb(player_stats={"hitting": {}})
    with pytest.raises(NoHittingStatsError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


def test_multiple_splits_with_no_aggregate_raises_player_data_error() -> None:
    washington = make_split(team=NATIONALS)
    san_diego = make_split(team=PADRES)
    stat = make_stat([washington, san_diego])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    with pytest.raises(PlayerDataError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


def test_multiple_splits_with_two_aggregates_raises_player_data_error() -> None:
    aggregate_one = make_split(team=None, hits=100)
    aggregate_two = make_split(team=None, hits=50)
    team_specific = make_split(team=NATIONALS)
    stat = make_stat([aggregate_one, aggregate_two, team_specific])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    with pytest.raises(PlayerDataError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


def test_hitting_request_failure_raises_player_data_error() -> None:
    client = FakeMlb(player_stats=TheMlbStatsApiException("network down"))
    with pytest.raises(PlayerDataError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


# ---------------------------------------------------------------------------
# Season hitting: required-field normalization
# ---------------------------------------------------------------------------


def test_missing_required_stat_field_raises_player_data_error() -> None:
    """A missing counting stat is a data-integrity failure, never a zero."""
    stat = make_stat([make_split(team=MARINERS, hits=None)])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    with pytest.raises(PlayerDataError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


def test_missing_field_does_not_normalize_to_zero() -> None:
    """A missing stolen_bases value must fail loudly, not become 0."""
    stat = make_stat([make_split(team=MARINERS, stolen_bases=None)])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    with pytest.raises(PlayerDataError) as exc_info:
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)
    assert "stolenBases" in str(exc_info.value)


def test_normalized_result_matches_expected_domain_model() -> None:
    stat = make_stat([make_split(team=MARINERS)])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    hitting = get_player_season_hitting(PLAYER_ID, SEASON, client=client)
    assert hitting == PlayerSeasonHitting(
        player_id=PLAYER_ID,
        season=SEASON,
        games_played=150,
        plate_appearances=600,
        at_bats=500,
        runs=80,
        hits=150,
        doubles=30,
        triples=3,
        home_runs=20,
        rbi=90,
        base_on_balls=60,
        intentional_walks=5,
        hit_by_pitch=5,
        strikeouts=100,
        stolen_bases=10,
        caught_stealing=3,
        sac_flies=4,
        sac_bunts=2,
    )


def test_normalization_validation_failure_raises_player_data_error() -> None:
    """Domain-level invariants (e.g. IBB <= BB) surface as PlayerDataError."""
    stat = make_stat([make_split(team=MARINERS, base_on_balls=5, intentional_walks=10)])
    client = FakeMlb(player_stats={"hitting": {"season": stat}})
    with pytest.raises(PlayerDataError):
        get_player_season_hitting(PLAYER_ID, SEASON, client=client)


def test_no_client_supplied_creates_and_closes_owned_client(monkeypatch) -> None:
    """When no client is supplied, an owned ``Mlb`` client is used."""
    from app.services import players as players_module

    class OwnedClient(FakeMlb):
        closed = False

        def __enter__(self) -> "OwnedClient":
            return self

        def __exit__(self, *exc_info: object) -> None:
            self.closed = True

    owned = OwnedClient(person=make_person())
    monkeypatch.setattr(players_module, "Mlb", lambda: owned)

    identity = get_player_identity(PLAYER_ID)

    assert identity.player_id == PLAYER_ID
    assert owned.closed is True
