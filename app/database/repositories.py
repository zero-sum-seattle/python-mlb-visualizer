"""Repository functions for game line and league ingestion persistence."""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

from app.database.models import (
    LeagueSeasonIngestionRecord,
    PlayerRecord,
    PlayerSeasonHittingRecord,
    TeamGameBattingLineRecord,
    TeamGamePitchingLineRecord,
)
from app.schemas.catalog import AvailableTeamSeason
from app.schemas.games import (
    TeamGameBattingLine,
    TeamGamePitchingLine,
    TeamGameRunResult,
    TeamSeasonRunResults,
)
from app.schemas.ingestion import (
    LeagueSeasonIngestionState,
    LeagueSeasonIngestionStatus,
    PlayerPersistenceOutcome,
    TeamGamePersistenceResult,
)
from app.schemas.players import PlayerIdentity, PlayerSeasonHitting

# The two line tables the generic upsert below reconciles. They hold different
# columns but expose the same to_domain / apply_domain / from_domain interface.
TeamGameLine = TeamGameBattingLine | TeamGamePitchingLine
TeamGameLineRecord = TeamGameBattingLineRecord | TeamGamePitchingLineRecord

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


def list_league_season(
    session: Session,
    *,
    season: int,
) -> list[TeamGameBattingLine]:
    """Return every persisted batting line for a season, across all teams.

    Whether that is actually MLB-wide is not a question this function answers.
    It reports what is stored; the recorded league-season coverage state is
    what says whether the stored rows may be described as covering the league.

    Ordered by team, then in each team's chart order, so a run over the season
    is reproducible. A full MLB season is roughly 4,860 team-game records, so
    the rows are returned as domain objects and the statistics are calculated
    in the analytics layer rather than pushed into SQL.
    """
    stmt = (
        select(TeamGameBattingLineRecord)
        .where(TeamGameBattingLineRecord.season == season)
        .order_by(
            TeamGameBattingLineRecord.team_id,
            TeamGameBattingLineRecord.game_date,
            TeamGameBattingLineRecord.game_number,
            TeamGameBattingLineRecord.game_pk,
        )
    )
    records = session.scalars(stmt).all()
    return [record.to_domain() for record in records]


def list_team_season_run_results(
    session: Session,
    *,
    team_id: int,
    season: int,
) -> TeamSeasonRunResults:
    """Pair a team-season's games with the opponent's stored line for each game.

    Runs allowed is the opponent's runs scored in the same game, so this is an
    outer self-join of ``team_game_batting_lines`` onto itself on ``game_pk``,
    matching the opponent row by ``team_id``. No MLB request is involved and no
    runs-allowed column exists; the figure is already in the table, on the other
    team's row.

    The join is an outer join on purpose. A team-season imported on its own has
    no opponent rows at all, and an inner join would quietly return zero games
    for it — indistinguishable from a team that has not been imported. Instead
    the unpaired ``game_pk`` values are reported so the caller can say which
    state it is in.

    Games are returned in the same chart order as ``list_team_season``.
    """
    opponent = aliased(TeamGameBattingLineRecord, name="opponent")
    stmt = (
        select(TeamGameBattingLineRecord, opponent)
        .outerjoin(
            opponent,
            (opponent.game_pk == TeamGameBattingLineRecord.game_pk)
            & (opponent.team_id == TeamGameBattingLineRecord.opponent_id),
        )
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

    results: list[TeamGameRunResult] = []
    unpaired: list[int] = []
    for row, opponent_row in session.execute(stmt):
        if opponent_row is None:
            unpaired.append(row.game_pk)
            continue
        results.append(
            TeamGameRunResult(
                game_pk=row.game_pk,
                game_date=row.game_date,
                season=row.season,
                team_id=row.team_id,
                team_name=row.team_name,
                opponent_id=row.opponent_id,
                opponent_name=row.opponent_name,
                home_away=row.home_away,
                runs_scored=row.runs,
                runs_allowed=opponent_row.runs,
                game_number=row.game_number,
            )
        )

    return TeamSeasonRunResults(
        results=tuple(results),
        unpaired_game_pks=tuple(unpaired),
    )


def list_team_season_pitching(
    session: Session,
    *,
    team_id: int,
    season: int,
) -> list[TeamGamePitchingLine]:
    """Return persisted pitching lines for a team-season in chart order.

    A team-season imported before pitching was persisted simply has no rows
    here, which is what an empty list means. There is no partially-populated
    state to guard against: every column on the pitching table is NOT NULL.
    """
    stmt = (
        select(TeamGamePitchingLineRecord)
        .where(
            TeamGamePitchingLineRecord.team_id == team_id,
            TeamGamePitchingLineRecord.season == season,
        )
        .order_by(
            TeamGamePitchingLineRecord.game_date,
            TeamGamePitchingLineRecord.game_number,
            TeamGamePitchingLineRecord.game_pk,
        )
    )
    records = session.scalars(stmt).all()
    return [record.to_domain() for record in records]


def list_league_season_pitching(
    session: Session,
    *,
    season: int,
) -> list[TeamGamePitchingLine]:
    """Return every persisted pitching line for a season, across all teams.

    The pitching counterpart of ``list_league_season``, and it answers the same
    limited question: what is stored, not whether that is actually MLB-wide.
    The recorded league-season coverage state is what says whether the stored
    rows may be described as covering the league.
    """
    stmt = (
        select(TeamGamePitchingLineRecord)
        .where(TeamGamePitchingLineRecord.season == season)
        .order_by(
            TeamGamePitchingLineRecord.team_id,
            TeamGamePitchingLineRecord.game_date,
            TeamGamePitchingLineRecord.game_number,
            TeamGamePitchingLineRecord.game_pk,
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
    return _upsert_team_season_lines(
        session, lines=lines, record_type=TeamGameBattingLineRecord
    )


def upsert_team_season_pitching(
    session: Session,
    *,
    lines: list[TeamGamePitchingLine],
) -> TeamGamePersistenceResult:
    """Insert, update, or leave unchanged pitching rows for a batch of domain lines.

    Does not commit or roll back. Rows absent from ``lines`` are not deleted.
    """
    return _upsert_team_season_lines(
        session, lines=lines, record_type=TeamGamePitchingLineRecord
    )


def _upsert_team_season_lines(
    session: Session,
    *,
    lines: Sequence[TeamGameLine],
    record_type: type[TeamGameLineRecord],
) -> TeamGamePersistenceResult:
    """Upsert one team-season's lines into whichever table holds them.

    Batting and pitching lines are stored in different tables with different
    columns, but the reconciliation is identical: match on ``(team_id,
    game_pk)``, refuse a game already stored under another team, update only
    rows whose values actually changed, and leave rows absent from ``lines``
    alone. The two record classes expose the same ``to_domain`` /
    ``apply_domain`` / ``from_domain`` interface, so that logic lives here once
    rather than being kept in step across two near-identical copies.
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
        select(record_type).where(
            record_type.team_id == team_id,
            record_type.season == season,
        )
    ).all()

    by_team_game: dict[tuple[int, int], TeamGameLineRecord] = {
        (record.team_id, record.game_pk): record for record in existing
    }
    by_game_pk: dict[int, TeamGameLineRecord] = {
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
            new_record = record_type.from_domain(line, created_at=now, updated_at=now)
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


def get_league_season_ingestion(
    session: Session,
    *,
    season: int,
) -> LeagueSeasonIngestionState | None:
    """Return the stored coverage state for a season, or None if never run."""
    record = _load_league_season_ingestion(session, season)
    return None if record is None else record.to_domain()


def record_league_season_ingestion_start(
    session: Session,
    *,
    season: int,
    expected_team_count: int,
    started_at: datetime,
) -> LeagueSeasonIngestionState:
    """Mark a season's league-wide ingestion as RUNNING and return the state.

    Replaces any state a previous run left behind. A season's coverage is only
    ever as good as its most recent league-wide ingestion, so a new run
    invalidates the old answer the moment it starts rather than leaving a stale
    ``COMPLETE`` in place while teams are being re-fetched.

    Does not commit or roll back.
    """
    state = LeagueSeasonIngestionState(
        season=season,
        status=LeagueSeasonIngestionStatus.RUNNING,
        expected_team_count=expected_team_count,
        successful_team_count=0,
        failed_team_count=0,
        started_at=started_at,
        completed_at=None,
    )
    _store_league_season_ingestion(session, state)
    return state


def record_league_season_ingestion_finish(
    session: Session,
    *,
    season: int,
    expected_team_count: int,
    successful_team_count: int,
    failed_team_count: int,
    started_at: datetime,
    completed_at: datetime,
) -> LeagueSeasonIngestionState:
    """Store the final coverage state of a league-wide ingestion.

    The status is derived here rather than accepted from the caller so a
    finished run cannot be labelled ``COMPLETE`` while any discovered team
    failed. Does not commit or roll back.
    """
    status = (
        LeagueSeasonIngestionStatus.COMPLETE
        if failed_team_count == 0 and expected_team_count > 0
        else LeagueSeasonIngestionStatus.INCOMPLETE
    )
    state = LeagueSeasonIngestionState(
        season=season,
        status=status,
        expected_team_count=expected_team_count,
        successful_team_count=successful_team_count,
        failed_team_count=failed_team_count,
        started_at=started_at,
        completed_at=completed_at,
    )
    _store_league_season_ingestion(session, state)
    return state


def _load_league_season_ingestion(
    session: Session,
    season: int,
) -> LeagueSeasonIngestionRecord | None:
    return session.scalars(
        select(LeagueSeasonIngestionRecord).where(
            LeagueSeasonIngestionRecord.season == season
        )
    ).one_or_none()


def _store_league_season_ingestion(
    session: Session,
    state: LeagueSeasonIngestionState,
) -> None:
    """Insert or update the single row that holds a season's coverage state."""
    record = _load_league_season_ingestion(session, state.season)
    if record is None:
        session.add(LeagueSeasonIngestionRecord.from_domain(state))
        return
    record.apply_domain(state)


def get_player(session: Session, *, player_id: int) -> PlayerIdentity | None:
    """Return a player's stored identity, or None if never imported."""
    record = _load_player(session, player_id)
    return None if record is None else record.to_domain()


def get_player_season_hitting(
    session: Session,
    *,
    player_id: int,
    season: int,
) -> PlayerSeasonHitting | None:
    """Return a player's stored season hitting aggregate, or None if not stored."""
    record = _load_player_season_hitting(session, player_id=player_id, season=season)
    return None if record is None else record.to_domain()


def upsert_player(
    session: Session,
    *,
    identity: PlayerIdentity,
) -> PlayerPersistenceOutcome:
    """Insert, update, or leave unchanged the one row for a player's identity.

    Does not commit or roll back.
    """
    record = _load_player(session, identity.player_id)
    now = datetime.now(UTC).replace(tzinfo=None)

    if record is None:
        session.add(PlayerRecord.from_domain(identity, created_at=now, updated_at=now))
        return PlayerPersistenceOutcome.INSERTED

    if record.to_domain() == identity:
        return PlayerPersistenceOutcome.UNCHANGED

    record.apply_domain(identity)
    record.updated_at = now
    return PlayerPersistenceOutcome.UPDATED


def upsert_player_season_hitting(
    session: Session,
    *,
    hitting: PlayerSeasonHitting,
) -> PlayerPersistenceOutcome:
    """Insert, update, or leave unchanged the one row for a player-season.

    Does not commit or roll back.
    """
    record = _load_player_season_hitting(
        session, player_id=hitting.player_id, season=hitting.season
    )
    now = datetime.now(UTC).replace(tzinfo=None)

    if record is None:
        session.add(
            PlayerSeasonHittingRecord.from_domain(
                hitting, created_at=now, updated_at=now
            )
        )
        return PlayerPersistenceOutcome.INSERTED

    if record.to_domain() == hitting:
        return PlayerPersistenceOutcome.UNCHANGED

    record.apply_domain(hitting)
    record.updated_at = now
    return PlayerPersistenceOutcome.UPDATED


def _load_player(session: Session, player_id: int) -> PlayerRecord | None:
    return session.scalars(
        select(PlayerRecord).where(PlayerRecord.player_id == player_id)
    ).one_or_none()


def _load_player_season_hitting(
    session: Session,
    *,
    player_id: int,
    season: int,
) -> PlayerSeasonHittingRecord | None:
    return session.scalars(
        select(PlayerSeasonHittingRecord).where(
            PlayerSeasonHittingRecord.player_id == player_id,
            PlayerSeasonHittingRecord.season == season,
        )
    ).one_or_none()
