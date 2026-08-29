"""Atomic player-season hitting ingestion into the local database."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.repositories import upsert_player, upsert_player_season_hitting
from app.schemas.ingestion import PlayerSeasonIngestionResult
from app.services.players import (
    MlbPlayerDataClient,
    get_player_identity,
    get_player_season_hitting,
)


class PlayerSeasonIngestionError(Exception):
    """Player-season data could not be persisted."""


def ingest_player_season(
    *,
    session: Session,
    player_id: int,
    season: int,
    client: MlbPlayerDataClient | None = None,
) -> PlayerSeasonIngestionResult:
    """Fetch one player-season of hitting stats from MLB, then persist it atomically.

    Both MLB requests -- identity and season hitting -- complete before the
    database transaction begins. The player identity row and the player-season
    hitting row then persist inside that same transaction, so a failure on the
    second write rolls back the first: this ingestion can never leave a player
    row with no matching season row, or a stale identity beside fresh stats.
    """
    identity = get_player_identity(player_id, client=client)
    hitting = get_player_season_hitting(player_id, season, client=client)

    try:
        with session.begin():
            identity_outcome = upsert_player(session, identity=identity)
            hitting_outcome = upsert_player_season_hitting(session, hitting=hitting)
    except SQLAlchemyError as exc:
        raise PlayerSeasonIngestionError(
            f"Unable to persist player {player_id} season {season}"
        ) from exc

    return PlayerSeasonIngestionResult(
        player_id=player_id,
        season=season,
        full_name=identity.full_name,
        identity_outcome=identity_outcome,
        hitting_outcome=hitting_outcome,
    )
