"""Atomic team-season ingestion into the local database."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.repositories import upsert_team_season, upsert_team_season_pitching
from app.schemas.games import TeamGameBattingLine, TeamGamePitchingLine
from app.schemas.ingestion import TeamSeasonIngestionResult, TeamSeasonLineCounts
from app.services.team_game_logs import (
    AsyncMlbGameDataClient,
    MlbGameDataClient,
    get_team_game_batting_lines,
    get_team_game_lines,
    get_team_game_lines_async,
)


class TeamSeasonIngestionError(Exception):
    """Team-season data could not be persisted."""


def ingest_team_season(
    *,
    session: Session,
    team_id: int,
    season: int,
    client: MlbGameDataClient | None = None,
    include_pitching: bool = True,
) -> TeamSeasonIngestionResult:
    """Fetch a full team-season from MLB, then persist it in one transaction.

    MLB retrieval completes before the database transaction begins. Rows missing
    from the latest fetch are not deleted.

    Batting and pitching are separate game logs, and both are fetched before
    anything is written. Both then persist inside the **same** transaction, so a
    team-season can never end up with batting rows stored and pitching rows
    missing because the second write failed — a state that would look on the
    pitching page exactly like a season imported before pitching existed.

    Fetching both costs four MLB requests, not six: ``get_team_game_lines``
    shares the team lookup and the schedule between the two game logs.

    ``include_pitching`` exists for callers that want the original
    batting-only behaviour, and for tests whose fake client serves no pitching
    game log. Turning it off drops back to three requests and leaves
    ``result.pitching`` None.
    """
    if include_pitching:
        lines, pitching_lines = get_team_game_lines(team_id, season, client=client)
    else:
        lines = get_team_game_batting_lines(team_id, season, client=client)
        pitching_lines = None

    return persist_team_season(
        session,
        team_id=team_id,
        season=season,
        lines=lines,
        pitching_lines=pitching_lines,
    )


async def ingest_team_season_async(
    *,
    session: Session,
    team_id: int,
    season: int,
    client: AsyncMlbGameDataClient,
) -> TeamSeasonIngestionResult:
    """Async counterpart of ``ingest_team_season``.

    Only the MLB fetch is asynchronous. Persistence is the exact same
    synchronous, single-transaction code the sync path uses: no async
    SQLAlchemy, no ``await`` between opening and committing the transaction.
    That matters beyond style, because this is meant to be called from a
    bounded-concurrency league import where several teams fetch at once but
    writes must stay serialized (see ``ingest_league_season_async``); a
    transaction with no internal ``await`` cannot itself be interleaved with
    another team's write.

    Always fetches both batting and pitching, matching the sequential
    league path's default. Requires an existing client, shared across the
    concurrent run rather than created per team.
    """
    lines, pitching_lines = await get_team_game_lines_async(
        team_id, season, client=client
    )
    return persist_team_season(
        session,
        team_id=team_id,
        season=season,
        lines=lines,
        pitching_lines=pitching_lines,
    )


def persist_team_season(
    session: Session,
    *,
    team_id: int,
    season: int,
    lines: list[TeamGameBattingLine],
    pitching_lines: list[TeamGamePitchingLine] | None,
) -> TeamSeasonIngestionResult:
    """Persist one already-fetched team-season in a single short transaction.

    Shared by the sync and async ingestion entry points, so "batting and
    pitching commit atomically, in one transaction, with no network I/O
    inside it" is enforced in exactly one place.
    """
    fetched = len(lines)
    team_name = lines[0].team_name if lines else f"team {team_id}"

    try:
        with session.begin():
            persistence = upsert_team_season(session, lines=lines)
            pitching_persistence = (
                upsert_team_season_pitching(session, lines=pitching_lines)
                if pitching_lines is not None
                else None
            )
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
        pitching=(
            None
            if pitching_persistence is None or pitching_lines is None
            else TeamSeasonLineCounts(
                fetched=len(pitching_lines),
                inserted=pitching_persistence.inserted,
                updated=pitching_persistence.updated,
                unchanged=pitching_persistence.unchanged,
            )
        ),
    )
