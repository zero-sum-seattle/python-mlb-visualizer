"""League-wide season ingestion: every MLB team for one season.

This service is orchestration. It discovers the clubs that played the requested
season, calls the existing team-season ingestion for each of them, aggregates
the typed per-team results, and records whether the run covered every discovered
team. It contains no MLB normalization, no upsert comparison, and no baseball
analytics; all of that already lives in ``team_game_logs``,
``team_season_ingestion``, and the repositories, and is reused unchanged.

    CLI ───────────────┐
    scheduler ─────────┼──► ingest_league_season ─► ingest_team_season ─► MLB / DB
    admin operation ───┘

Ingestion is sequential and deterministic. Roughly thirty team-seasons is not
enough work to justify concurrency, and sequential ingestion keeps failure
attribution, debugging, upstream load, and SQLite write behavior simple.

Transaction boundaries
----------------------
No database transaction is ever held open across an MLB request::

    discover teams                      network only, no transaction
    record RUNNING                      short transaction, committed
    for each team:
        fetch team-season from MLB      network only, no transaction
        persist team-season             short transaction, committed
    record COMPLETE / INCOMPLETE        short transaction, committed

Each team-season therefore commits on its own. One club failing does not undo
the clubs that already succeeded: those rows stay committed, the run is recorded
as INCOMPLETE, and a rerun re-attempts every team using the existing idempotent
upsert.

Crash behavior
--------------
If the process dies mid-run, the season's row is left ``RUNNING`` with no
``completed_at``. That row is a statement about the past, not a lock: it means
the previous run never finished, so the season's coverage is unknown and must
not be trusted. The next invocation overwrites it and proceeds normally. There
is no lease, heartbeat, or resume checkpoint, because idempotent full reruns
make them unnecessary.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from mlbstatsapi import Mlb
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.repositories import (
    record_league_season_ingestion_finish,
    record_league_season_ingestion_start,
)
from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionResult,
    LeagueTeamIngestionStatus,
)
from app.schemas.teams import MlbTeam
from app.services.league_teams import MlbTeamDirectoryClient, discover_mlb_teams
from app.services.team_game_logs import MlbGameDataClient, TeamGameLogError
from app.services.team_season_ingestion import (
    TeamSeasonIngestionError,
    ingest_team_season,
)

# MLB's first National League season. Nothing earlier can be requested.
MLB_FIRST_SEASON = 1876

TeamProgressCallback = Callable[[int, int, LeagueTeamIngestionResult], None]


class LeagueSeasonIngestionError(Exception):
    """A league-wide season ingestion could not be carried out."""


class InvalidSeasonError(LeagueSeasonIngestionError):
    """The requested season is not a season MLB could have played."""


class LeagueIngestionStateError(LeagueSeasonIngestionError):
    """League coverage state could not be persisted.

    Raised when the run itself may have ingested teams but the record of what
    it covered could not be written. Coverage is unknown after this, which is
    why it is an error rather than a warning.
    """


class MlbLeagueDataClient(MlbTeamDirectoryClient, MlbGameDataClient, Protocol):
    """One client covering both team discovery and team game data.

    A league-wide run reuses a single client for discovery and for all thirty
    or so team-season fetches rather than opening one per team.
    """


def ingest_league_season(
    *,
    session: Session,
    season: int,
    client: MlbLeagueDataClient | None = None,
    on_team_complete: TeamProgressCallback | None = None,
) -> LeagueSeasonIngestionResult:
    """Ingest every MLB team-season for ``season`` and record the coverage.

    Parameters
    ----------
    session:
        Session for the target database. Must have no transaction in progress;
        this service opens and commits its own short transactions.
    season:
        Four digit season year.
    client:
        An existing ``mlbstatsapi.Mlb`` client, reused for team discovery and
        for every team-season fetch. When omitted, one client is created for
        the whole run and closed afterwards.
    on_team_complete:
        Optional callback invoked as ``(position, total, result)`` after each
        team finishes, so a long-running caller can report progress. It is not
        an error boundary: exceptions raised by the callback propagate.

    Raises
    ------
    InvalidSeasonError
        The season is outside the range MLB could have played.
    NoMlbTeamsDiscoveredError
        MLB returned no eligible Major League clubs for the season.
    MlbTeamDiscoveryError
        Team discovery failed or returned a club that could not be trusted.
    LeagueIngestionStateError
        Coverage state could not be persisted.
    """
    _validate_season(season)

    if client is not None:
        return _ingest(
            session=session,
            season=season,
            client=client,
            on_team_complete=on_team_complete,
        )
    with Mlb() as owned_client:
        return _ingest(
            session=session,
            season=season,
            client=owned_client,
            on_team_complete=on_team_complete,
        )


def _validate_season(season: int) -> None:
    """Reject a season MLB could not have played.

    The upper bound is next year rather than this year so a season can be
    ingested as soon as its schedule is published, while a typo such as 20255
    still fails immediately instead of after thirty upstream requests.
    """
    latest = datetime.now(UTC).year + 1
    if season < MLB_FIRST_SEASON or season > latest:
        raise InvalidSeasonError(
            f"Season {season} is outside {MLB_FIRST_SEASON}-{latest}"
        )


def _ingest(
    *,
    session: Session,
    season: int,
    client: MlbLeagueDataClient,
    on_team_complete: TeamProgressCallback | None,
) -> LeagueSeasonIngestionResult:
    teams = discover_mlb_teams(season, client=client)
    started_at = _now()
    with _coverage_transaction(session, season):
        record_league_season_ingestion_start(
            session,
            season=season,
            expected_team_count=len(teams),
            started_at=started_at,
        )

    team_results: list[LeagueTeamIngestionResult] = []
    for position, team in enumerate(teams, start=1):
        result = _ingest_one_team(session=session, team=team, client=client)
        team_results.append(result)
        if on_team_complete is not None:
            on_team_complete(position, len(teams), result)

    succeeded = sum(
        1
        for result in team_results
        if result.status is LeagueTeamIngestionStatus.SUCCEEDED
    )
    failed = len(team_results) - succeeded
    completed_at = _now()
    with _coverage_transaction(session, season):
        record_league_season_ingestion_finish(
            session,
            season=season,
            expected_team_count=len(teams),
            successful_team_count=succeeded,
            failed_team_count=failed,
            started_at=started_at,
            completed_at=completed_at,
        )

    return LeagueSeasonIngestionResult(
        season=season,
        teams_discovered=len(teams),
        teams_succeeded=succeeded,
        teams_failed=failed,
        team_game_records_fetched=sum(result.fetched for result in team_results),
        inserted=sum(result.inserted for result in team_results),
        updated=sum(result.updated for result in team_results),
        unchanged=sum(result.unchanged for result in team_results),
        status=(
            LeagueSeasonIngestionStatus.COMPLETE
            if failed == 0
            else LeagueSeasonIngestionStatus.INCOMPLETE
        ),
        started_at=started_at,
        completed_at=completed_at,
        team_results=tuple(team_results),
    )


def _ingest_one_team(
    *,
    session: Session,
    team: MlbTeam,
    client: MlbLeagueDataClient,
) -> LeagueTeamIngestionResult:
    """Ingest one club, converting its failure into a recorded per-team result.

    Only the ingestion path's own errors are absorbed here. A club that cannot
    be fetched or persisted must not abort the other twenty-nine, but anything
    else is an unexpected failure and is left to propagate rather than being
    reported as an ordinary missing team.
    """
    try:
        result = ingest_team_season(
            session=session,
            team_id=team.team_id,
            season=team.season,
            client=client,
        )
    except (TeamGameLogError, TeamSeasonIngestionError) as exc:
        _discard_failed_team_transaction(session)
        return LeagueTeamIngestionResult.from_failure(
            team_id=team.team_id,
            team_name=team.team_name,
            season=team.season,
            error=f"{type(exc).__name__}: {exc}",
        )
    return LeagueTeamIngestionResult.from_team_result(result)


def _discard_failed_team_transaction(session: Session) -> None:
    """Leave the session usable for the next team after a failure.

    ``ingest_team_season`` commits or rolls back its own transaction, so this is
    normally a no-op. It exists for the case where a fetch failure left the
    session with an implicitly begun transaction: the next team's
    ``session.begin()`` would otherwise refuse to start.
    """
    if session.in_transaction():
        session.rollback()


@contextmanager
def _coverage_transaction(session: Session, season: int) -> Iterator[None]:
    """Run one coverage-state write in its own short transaction."""
    try:
        with session.begin():
            yield
    except SQLAlchemyError as exc:
        raise LeagueIngestionStateError(
            f"Unable to record league ingestion coverage for season {season}"
        ) from exc


def _now() -> datetime:
    """Return a naive UTC timestamp, matching how other rows store time."""
    return datetime.now(UTC).replace(tzinfo=None)
