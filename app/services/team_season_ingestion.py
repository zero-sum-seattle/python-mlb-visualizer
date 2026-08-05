"""Atomic team-season ingestion into the local database."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.repositories import upsert_team_season
from app.schemas.ingestion import TeamSeasonIngestionResult
from app.services.team_game_logs import MlbGameDataClient, get_team_game_batting_lines


class TeamSeasonIngestionError(Exception):
    """Team-season data could not be persisted."""


def ingest_team_season(
    *,
    session: Session,
    team_id: int,
    season: int,
    client: MlbGameDataClient | None = None,
) -> TeamSeasonIngestionResult:
    """Fetch a full team-season from MLB, then persist it in one transaction.

    MLB retrieval completes before the database transaction begins. Rows missing
    from the latest fetch are not deleted.
    """
    lines = get_team_game_batting_lines(team_id, season, client=client)
    fetched = len(lines)
    team_name = lines[0].team_name if lines else f"team {team_id}"

    try:
        with session.begin():
            persistence = upsert_team_season(session, lines=lines)
    except SQLAlchemyError as exc:
        raise TeamSeasonIngestionError(
            f"Unable to persist team {team_id} season {season}"
        ) from exc

    return TeamSeasonIngestionResult(
        team_id=team_id,
        team_name=team_name,
        season=season,
        fetched=fetched,
        inserted=persistence.inserted,
        updated=persistence.updated,
        unchanged=persistence.unchanged,
    )
