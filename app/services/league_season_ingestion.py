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
    build and validate the result       no transaction
    record COMPLETE / INCOMPLETE        short transaction, committed

Each team-season therefore commits on its own. One club failing does not undo
the clubs that already succeeded: those rows stay committed, the run is recorded
as INCOMPLETE, and a rerun re-attempts every team using the existing idempotent
upsert.

The final result is constructed and validated before coverage is recorded, so a
result the domain model rejects can never leave the database claiming COMPLETE.
If that validation fails the error propagates and the row stays ``RUNNING``,
which is the honest state: the run did not establish trustworthy coverage.

Crash behavior
--------------
If the process dies mid-run, the season's row is left ``RUNNING`` with no
``completed_at``. That row is a statement about the past, not a lock: it means
the previous run never finished, so the season's coverage is unknown and must
not be trusted. The next invocation overwrites it and proceeds normally. There
is no lease, heartbeat, or resume checkpoint, because idempotent full reruns
make them unnecessary.

Shared with other orchestrations
--------------------------------
The rules a league-wide run is made of — which seasons may be requested, how a
club's failure is recorded, how the aggregate result is built and validated,
and when coverage may be written — are public functions here rather than
private steps of the loop below. An orchestration that visits the same clubs in
a different order calls them; it does not restate them. ``ingest_league_season``
remains the reference implementation and the thing such a run is checked
against.
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

# The failures a single club is allowed to have inside a league-wide run. A club
# that cannot be fetched or persisted must not abort the other twenty-nine.
# Anything else is unexpected and propagates rather than being recorded as an
# ordinary missing team.
LEAGUE_TEAM_INGESTION_ERRORS = (TeamGameLogError, TeamSeasonIngestionError)


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
    validate_season(season)

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


def validate_season(season: int) -> None:
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
    started_at = now_for_ingestion()
    record_run_started(
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

    result = build_league_result(
        season=season,
        teams_discovered=len(teams),
        team_results=team_results,
        started_at=started_at,
        completed_at=now_for_ingestion(),
    )
    record_run_finished(session, result)
    return result


def record_run_started(
    session: Session,
    *,
    season: int,
    expected_team_count: int,
    started_at: datetime,
) -> None:
    """Reset the season's coverage row to RUNNING before any club is fetched.

    A season's coverage is only ever as good as its most recent league-wide
    ingestion, so a new run invalidates the old answer the moment it begins
    rather than leaving a stale COMPLETE readable while clubs are re-fetched.
    """
    with _coverage_transaction(session, season):
        record_league_season_ingestion_start(
            session,
            season=season,
            expected_team_count=expected_team_count,
            started_at=started_at,
        )


def build_league_result(
    *,
    season: int,
    teams_discovered: int,
    team_results: list[LeagueTeamIngestionResult],
    started_at: datetime,
    completed_at: datetime,
) -> LeagueSeasonIngestionResult:
    """Aggregate per-club results into the validated result for one run.

    A run is COMPLETE only when no club failed, and the aggregate counts are
    summed from the per-club counts rather than recounted from the database.
    Constructing the model validates invariants the coverage row cannot express
    on its own, such as each discovered team appearing exactly once.
    """
    succeeded = sum(
        1
        for result in team_results
        if result.status is LeagueTeamIngestionStatus.SUCCEEDED
    )
    failed = len(team_results) - succeeded
    return LeagueSeasonIngestionResult(
        season=season,
        teams_discovered=teams_discovered,
        teams_succeeded=succeeded,
        teams_failed=failed,
        team_game_records_fetched=sum(team.fetched for team in team_results),
        inserted=sum(team.inserted for team in team_results),
        updated=sum(team.updated for team in team_results),
        unchanged=sum(team.unchanged for team in team_results),
        status=(
            LeagueSeasonIngestionStatus.COMPLETE
            if failed == 0
            else LeagueSeasonIngestionStatus.INCOMPLETE
        ),
        started_at=started_at,
        completed_at=completed_at,
        team_results=tuple(team_results),
    )


def record_run_finished(
    session: Session,
    result: LeagueSeasonIngestionResult,
) -> None:
    """Record what a finished run covered.

    Takes an already-constructed result on purpose. The result model enforces
    invariants the coverage row cannot, so recording coverage first would let
    the database claim COMPLETE for a run the domain model then rejects. The
    validated result is the precondition for writing coverage, not a
    description of what was already written.
    """
    with _coverage_transaction(session, result.season):
        record_league_season_ingestion_finish(
            session,
            season=result.season,
            expected_team_count=result.teams_discovered,
            successful_team_count=result.teams_succeeded,
            failed_team_count=result.teams_failed,
            started_at=result.started_at,
            completed_at=result.completed_at,
        )


def league_team_failure(team: MlbTeam, exc: Exception) -> LeagueTeamIngestionResult:
    """Record one club's failure as a per-team result.

    The club keeps its identity and carries the error message, so an operator
    or a rerun can tell exactly which club is missing and why. The message
    format is part of what a run reports, so it is written once here.
    """
    return LeagueTeamIngestionResult.from_failure(
        team_id=team.team_id,
        team_name=team.team_name,
        season=team.season,
        error=f"{type(exc).__name__}: {exc}",
    )


def _ingest_one_team(
    *,
    session: Session,
    team: MlbTeam,
    client: MlbLeagueDataClient,
) -> LeagueTeamIngestionResult:
    """Ingest one club, converting its failure into a recorded per-team result."""
    try:
        result = ingest_team_season(
            session=session,
            team_id=team.team_id,
            season=team.season,
            client=client,
        )
    except LEAGUE_TEAM_INGESTION_ERRORS as exc:
        discard_failed_team_transaction(session)
        return league_team_failure(team, exc)
    return LeagueTeamIngestionResult.from_team_result(result)


def discard_failed_team_transaction(session: Session) -> None:
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


def now_for_ingestion() -> datetime:
    """Return a naive UTC timestamp, matching how other rows store time."""
    return datetime.now(UTC).replace(tzinfo=None)
