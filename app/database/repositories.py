"""Repository functions for team game batting line persistence."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database.models import TeamGameBattingLineRecord
from app.schemas.catalog import AvailableTeamSeason
from app.schemas.games import TeamGameBattingLine
from app.schemas.ingestion import TeamGamePersistenceResult

MIGRATION_HINT = "poetry run alembic upgrade head"


class TeamGamePersistenceError(Exception):
    """A persistence operation encountered an invalid or conflicting identity."""


class DatabaseSchemaMissingError(Exception):
    """The database is reachable but the expected tables do not exist yet."""


def _is_missing_team_game_table(error: OperationalError) -> bool:
    """Tell a missing table apart from any other operational failure.

    Only an absent ``team_game_batting_lines`` means migrations have not been
    applied. A locked database, an I/O failure, or an unreadable file is an
    operational problem that ``alembic upgrade head`` would not fix, so those
    must keep their own error rather than being relabelled.
    """
    message = str(error.orig if error.orig is not None else error).lower()
    return (
        "no such table" in message
        and TeamGameBattingLineRecord.__tablename__.lower() in message
    )


def list_available_team_seasons(session: Session) -> list[AvailableTeamSeason]:
    """Return every team-season that has games stored locally.

    One grouped query answers what the UI selectors need: which teams exist,
    the name each was stored under for a given season, and which seasons that
    team has. Many game rows collapse into one entry per team-season.

    Raises
    ------
    DatabaseSchemaMissingError
        Migrations have not been applied to the configured database.
    OperationalError
        Any other operational failure, such as a locked or unreadable
        database, is left untouched for the caller to handle.
    """
    team_name = func.max(TeamGameBattingLineRecord.team_name).label("team_name")
    games_played = func.count().label("games_played")
    stmt = (
        select(
            TeamGameBattingLineRecord.team_id,
            TeamGameBattingLineRecord.season,
            team_name,
            games_played,
        )
        .group_by(
            TeamGameBattingLineRecord.team_id,
            TeamGameBattingLineRecord.season,
        )
        .order_by(
            team_name,
            TeamGameBattingLineRecord.team_id,
            TeamGameBattingLineRecord.season.desc(),
        )
    )

    try:
        rows = session.execute(stmt).all()
    except OperationalError as exc:
        if not _is_missing_team_game_table(exc):
            raise
        raise DatabaseSchemaMissingError(
            f"Table {TeamGameBattingLineRecord.__tablename__!r} is missing. "
            f"Apply migrations with: {MIGRATION_HINT}"
        ) from exc

    return [
        AvailableTeamSeason(
            team_id=row.team_id,
            team_name=row.team_name,
            season=row.season,
            games_played=row.games_played,
        )
        for row in rows
    ]


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
