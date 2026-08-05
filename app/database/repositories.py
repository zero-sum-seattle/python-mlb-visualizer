"""Repository functions for team game batting line persistence."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import TeamGameBattingLineRecord
from app.schemas.games import TeamGameBattingLine
from app.schemas.ingestion import TeamGamePersistenceResult


class TeamGamePersistenceError(Exception):
    """A persistence operation encountered an invalid or conflicting identity."""


def list_team_season(
    session: Session,
    *,
    team_id: int,
    season: int,
) -> list[TeamGameBattingLine]:
    """Return persisted batting lines for a team-season in chart order."""
    stmt = (
        select(TeamGameBattingLineRecord)
        .where(
            TeamGameBattingLineRecord.team_id == team_id,
            TeamGameBattingLineRecord.season == season,
        )
        .order_by(
            TeamGameBattingLineRecord.game_date,
            TeamGameBattingLineRecord.game_number,
            TeamGameBattingLineRecord.game_pk,
        )
    )
    records = session.scalars(stmt).all()
    return [record.to_domain() for record in records]


def upsert_team_season(
    session: Session,
    *,
    lines: list[TeamGameBattingLine],
) -> TeamGamePersistenceResult:
    """Insert, update, or leave unchanged rows for a batch of domain lines.

    Does not commit or roll back. Rows absent from ``lines`` are not deleted.
    """
    if not lines:
        return TeamGamePersistenceResult(inserted=0, updated=0, unchanged=0)

    team_id = lines[0].team_id
    season = lines[0].season
    for line in lines:
        if line.team_id != team_id or line.season != season:
            raise TeamGamePersistenceError(
                "All lines in one upsert must share the same team_id and season"
            )

    existing = session.scalars(
        select(TeamGameBattingLineRecord).where(
            TeamGameBattingLineRecord.team_id == team_id,
            TeamGameBattingLineRecord.season == season,
        )
    ).all()

    by_team_game: dict[tuple[int, int], TeamGameBattingLineRecord] = {
        (record.team_id, record.game_pk): record for record in existing
    }
    by_game_pk: dict[int, TeamGameBattingLineRecord] = {
        record.game_pk: record for record in existing
    }

    inserted = 0
    updated = 0
    unchanged = 0
    now = datetime.now(UTC).replace(tzinfo=None)

    for line in lines:
        key = (line.team_id, line.game_pk)
        record = by_team_game.get(key)
        if record is None:
            conflict = by_game_pk.get(line.game_pk)
            if conflict is not None and conflict.team_id != line.team_id:
                raise TeamGamePersistenceError(
                    f"Game {line.game_pk} is already stored for team "
                    f"{conflict.team_id}, cannot store for team {line.team_id}"
                )
            new_record = TeamGameBattingLineRecord.from_domain(
                line, created_at=now, updated_at=now
            )
            session.add(new_record)
            by_team_game[key] = new_record
            by_game_pk[line.game_pk] = new_record
            inserted += 1
            continue

        if record.team_id != line.team_id or record.game_pk != line.game_pk:
            raise TeamGamePersistenceError(
                f"Identity mismatch for game {line.game_pk}: stored "
                f"team_id={record.team_id}, incoming team_id={line.team_id}"
            )

        if record.to_domain() == line:
            unchanged += 1
            continue

        record.apply_domain(line)
        record.updated_at = now
        updated += 1

    return TeamGamePersistenceResult(
        inserted=inserted, updated=updated, unchanged=unchanged
    )
