"""Retrieve and normalize player identity and season hitting stats from MLB.

Identity comes from ``Mlb.get_person``, which returns exactly one biographical
record per player id or ``None`` if the id is not a known MLB person.

Season hitting comes from ``Mlb.get_player_stats`` in the ``hitting`` group and
``season`` stat type. That request returns a ``Stat`` whose ``splits`` are
``HittingSeason`` objects: one split for a player who played the whole season
with one club, or several splits for a player who changed clubs mid-season —
one full-season aggregate split with ``team is None``, plus one team-specific
split per club. Only the aggregate is ever normalized; see
``_select_season_aggregate_split`` for why picking ``splits[0]`` would be
wrong.
"""

from typing import Protocol

from mlbstatsapi import Mlb
from mlbstatsapi.exceptions import TheMlbStatsApiException
from mlbstatsapi.models.people.people import Person
from mlbstatsapi.models.stats import HittingSeason
from pydantic import ValidationError

from app.schemas.players import PlayerIdentity, PlayerSeasonHitting

HITTING_STAT_GROUP = "hitting"
SEASON_STAT_TYPE = "season"


class PlayerDataError(Exception):
    """Upstream MLB player data was missing, ambiguous, or could not be normalized.

    Also raised directly when the MLB request itself fails, mirroring
    ``TeamGameLogError`` in ``app.services.team_game_logs``.
    """


class PlayerNotFoundError(PlayerDataError):
    """The requested player id is not a known MLB person."""


class NoHittingStatsError(PlayerDataError):
    """The player has no season hitting stats for the requested season."""


class MlbPlayerDataClient(Protocol):
    """The subset of ``mlbstatsapi.Mlb`` this service depends on."""

    def get_person(self, player_id: int, **params: object) -> Person | None: ...

    def get_player_stats(
        self,
        person_id: int,
        stats: list[str],
        groups: list[str],
        **params: object,
    ) -> dict: ...


def get_player_identity(
    player_id: int,
    *,
    client: MlbPlayerDataClient | None = None,
) -> PlayerIdentity:
    """Fetch and normalize a player's identity.

    Parameters
    ----------
    player_id:
        MLB person id, for example 677594 for Julio Rodriguez.
    client:
        An existing ``mlbstatsapi.Mlb`` client. When omitted a client is
        created and closed for this call.

    Raises
    ------
    PlayerNotFoundError
        No MLB person exists for ``player_id``.
    PlayerDataError
        The MLB request failed or the response could not be normalized.
    """
    if client is not None:
        return _fetch_player_identity(client, player_id)
    with Mlb() as owned_client:
        return _fetch_player_identity(owned_client, player_id)


def _fetch_player_identity(
    client: MlbPlayerDataClient, player_id: int
) -> PlayerIdentity:
    try:
        person = client.get_person(player_id)
    except TheMlbStatsApiException as exc:
        raise PlayerDataError(f"Unable to retrieve MLB player {player_id}") from exc

    if person is None:
        raise PlayerNotFoundError(f"No MLB player found for player id {player_id}")
    if person.id != player_id:
        raise PlayerDataError(
            f"MLB returned person id {person.id} for requested player {player_id}"
        )
    if not person.full_name:
        raise PlayerDataError(f"No full name returned for player {player_id}")
    if person.primary_position is None:
        raise PlayerDataError(f"No primary position returned for player {player_id}")

    try:
        return PlayerIdentity(
            player_id=person.id,
            full_name=person.full_name,
            primary_position=person.primary_position.abbreviation,
        )
    except ValidationError as exc:
        raise PlayerDataError(
            f"Could not normalize identity for player {player_id}: {exc}"
        ) from exc


def get_player_season_hitting(
    player_id: int,
    season: int,
    *,
    client: MlbPlayerDataClient | None = None,
) -> PlayerSeasonHitting:
    """Fetch and normalize a player's full-season hitting aggregate.

    Parameters
    ----------
    player_id:
        MLB person id.
    season:
        Four digit season year.
    client:
        An existing ``mlbstatsapi.Mlb`` client. When omitted a client is
        created and closed for this call.

    Raises
    ------
    NoHittingStatsError
        The player has no season hitting stats for ``season``.
    PlayerDataError
        The MLB request failed, the aggregate split could not be determined,
        or the response could not be normalized.
    """
    if client is not None:
        return _fetch_player_season_hitting(client, player_id, season)
    with Mlb() as owned_client:
        return _fetch_player_season_hitting(owned_client, player_id, season)


def _fetch_player_season_hitting(
    client: MlbPlayerDataClient, player_id: int, season: int
) -> PlayerSeasonHitting:
    try:
        stat_groups = client.get_player_stats(
            player_id,
            stats=[SEASON_STAT_TYPE],
            groups=[HITTING_STAT_GROUP],
            season=season,
        )
    except TheMlbStatsApiException as exc:
        raise PlayerDataError(
            f"Unable to retrieve MLB season hitting stats for player {player_id} "
            f"in {season}"
        ) from exc

    try:
        season_stat = stat_groups[HITTING_STAT_GROUP][SEASON_STAT_TYPE]
    except (KeyError, TypeError) as exc:
        raise NoHittingStatsError(
            f"No season hitting stats returned for player {player_id} in {season}"
        ) from exc

    splits = season_stat.splits or []
    split = _select_season_aggregate_split(splits, player_id=player_id, season=season)
    return _normalize_season_hitting_split(split, player_id=player_id, season=season)


def _select_season_aggregate_split(
    splits: list[HittingSeason],
    *,
    player_id: int,
    season: int,
) -> HittingSeason:
    """Select the split representing the full-season aggregate.

    Zero splits means there are no usable season hitting stats. Exactly one
    split is used as-is, whether or not it carries a team, since a player who
    played for a single club that season has no separate aggregate row to
    prefer. More than one split means the player changed clubs mid-season:
    MLB then returns one aggregate split with ``team is None`` alongside one
    team-specific split per club, and only the aggregate represents the full
    season. A response with any other shape among several splits -- zero or
    more than one aggregate row -- cannot be interpreted and is refused rather
    than guessed at with ``splits[0]``.
    """
    if not splits:
        raise NoHittingStatsError(
            f"No season hitting stats returned for player {player_id} in {season}"
        )
    if len(splits) == 1:
        return splits[0]

    aggregates = [split for split in splits if split.team is None]
    if len(aggregates) != 1:
        raise PlayerDataError(
            f"Expected exactly one full-season aggregate split among "
            f"{len(splits)} splits for player {player_id} in {season}, found "
            f"{len(aggregates)}"
        )
    return aggregates[0]


# (domain field name, upstream MLB field name) for every persisted counting
# stat. Every one of these is Optional on the upstream model because other
# stat types omit some of them; a missing value here means the payload is
# incomplete, not that the real count is zero.
_REQUIRED_STAT_FIELDS = (
    ("games_played", "gamesPlayed"),
    ("plate_appearances", "plateAppearances"),
    ("at_bats", "atBats"),
    ("runs", "runs"),
    ("hits", "hits"),
    ("doubles", "doubles"),
    ("triples", "triples"),
    ("home_runs", "homeRuns"),
    ("rbi", "rbi"),
    ("base_on_balls", "baseOnBalls"),
    ("intentional_walks", "intentionalWalks"),
    ("hit_by_pitch", "hitByPitch"),
    ("strikeouts", "strikeOuts"),
    ("stolen_bases", "stolenBases"),
    ("caught_stealing", "caughtStealing"),
    ("sac_flies", "sacFlies"),
    ("sac_bunts", "sacBunts"),
)


def _normalize_season_hitting_split(
    split: HittingSeason,
    *,
    player_id: int,
    season: int,
) -> PlayerSeasonHitting:
    context = f"player {player_id} season {season}"
    if split.stat is None:
        raise PlayerDataError(f"No hitting stat line returned for {context}")

    values: dict[str, int] = {}
    for field_name, raw_name in _REQUIRED_STAT_FIELDS:
        value = getattr(split.stat, field_name)
        if value is None:
            raise PlayerDataError(
                f"No {raw_name} in the season hitting stats for {context}"
            )
        # ``bool`` is an ``int`` subclass, so it is rejected explicitly rather
        # than being counted as 0 or 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise PlayerDataError(
                f"{raw_name} {value!r} is not an integer for {context}"
            )
        values[field_name] = value

    try:
        return PlayerSeasonHitting(player_id=player_id, season=season, **values)
    except ValidationError as exc:
        raise PlayerDataError(f"Could not normalize {context}: {exc}") from exc
