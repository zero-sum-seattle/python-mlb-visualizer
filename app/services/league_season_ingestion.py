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

    ingest_league_season_async ─► ingest_team_season_async ─► MLB / DB

Two entry points exist. ``ingest_league_season`` is sequential: it fetches and
persists one team-season, then moves to the next, using ``mlbstatsapi.Mlb``.
``ingest_league_season_async`` fetches several teams concurrently, bounded by a
modest configurable limit, using ``mlbstatsapi.AsyncMlb``. Both reuse the exact
same discovery, normalization, and persistence code — only the transport and
the orchestration differ. The sequential path remains available as a simple
reference and debug path: it keeps failure attribution, upstream load, and
SQLite write behavior easiest to reason about one team at a time.

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

Concurrent ingestion follows the same shape, with one addition: persistence is
serialized behind an ``asyncio.Lock`` even though several teams may be fetching
from MLB at the same time. Each team's persistence transaction is synchronous
and contains no ``await``, so once it starts it runs to completion before any
other coroutine can run; the lock makes that guarantee explicit rather than
incidental, so "only one already-fetched team-season is ever being persisted"
holds even if the persistence code changes later. SQLite is never written to
from more than one place at once, and no async SQLAlchemy is involved anywhere.

If one team's task raises an exception that is not an ordinary per-team
failure — or the ``on_team_complete`` callback raises, which is intentionally
never absorbed — every other team's task is explicitly cancelled and awaited
before the exception leaves ``ingest_league_season_async``. No sibling task
can still be mid-fetch, queued behind the concurrency semaphore, or waiting on
the write lock once the caller sees the error; a team that had already
finished persisting before the failure keeps its committed rows.

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
"""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from mlbstatsapi import AsyncMlb, Mlb
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
from app.services.league_teams import (
    AsyncMlbTeamDirectoryClient,
    MlbTeamDirectoryClient,
    discover_mlb_teams,
    discover_mlb_teams_async,
)
from app.services.team_game_logs import (
    AsyncMlbGameDataClient,
    MlbGameDataClient,
    TeamGameLogError,
    get_team_game_lines_async,
)
from app.services.team_season_ingestion import (
    TeamSeasonIngestionError,
    ingest_team_season,
    persist_team_season,
)

# MLB's first National League season. Nothing earlier can be requested.
MLB_FIRST_SEASON = 1876

# A modest bound: enough to overlap MLB round-trip latency across clubs
# without opening dozens of simultaneous connections to a third-party API.
DEFAULT_LEAGUE_CONCURRENCY = 4

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


class InvalidConcurrencyError(LeagueSeasonIngestionError):
    """The requested concurrency bound is not usable."""


class MlbLeagueDataClient(MlbTeamDirectoryClient, MlbGameDataClient, Protocol):
    """One client covering both team discovery and team game data.

    A league-wide run reuses a single client for discovery and for all thirty
    or so team-season fetches rather than opening one per team.
    """


class AsyncMlbLeagueDataClient(
    AsyncMlbTeamDirectoryClient, AsyncMlbGameDataClient, Protocol
):
    """Async counterpart of ``MlbLeagueDataClient``.

    A concurrent league-wide run reuses a single ``AsyncMlb`` client — and
    therefore one shared HTTP connection pool — for discovery and for every
    team-season fetch, never one client per team.
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


async def ingest_league_season_async(
    *,
    session: Session,
    season: int,
    client: AsyncMlbLeagueDataClient | None = None,
    concurrency: int = DEFAULT_LEAGUE_CONCURRENCY,
    on_team_complete: TeamProgressCallback | None = None,
) -> LeagueSeasonIngestionResult:
    """Bounded-concurrency counterpart of ``ingest_league_season``.

    Discovers the same teams, applies the same per-team ingestion, and builds
    and validates the same result model. The difference is transport and
    orchestration: teams are fetched from MLB concurrently, up to
    ``concurrency`` at a time, over one shared ``AsyncMlb`` client, while
    persistence for a fetched team-season is always serialized — never two
    teams writing at once. See the module docstring for the full picture.

    Parameters
    ----------
    session:
        Session for the target database. Must have no transaction in progress;
        this service opens and commits its own short transactions.
    season:
        Four digit season year.
    client:
        An existing ``mlbstatsapi.AsyncMlb`` client, reused for team discovery
        and for every team-season fetch. When omitted, one client is created
        for the whole run and closed afterwards. Never create one client per
        team.
    concurrency:
        Maximum number of teams fetching from MLB at the same time. Must be
        at least 1.
    on_team_complete:
        Optional callback invoked as ``(position, total, result)`` as each
        team finishes. Unlike the sequential path, ``position`` reflects
        completion order, not discovery order, since teams may finish out of
        order under concurrency. It is not an error boundary: exceptions
        raised by the callback propagate.

    Raises
    ------
    InvalidSeasonError
        The season is outside the range MLB could have played.
    InvalidConcurrencyError
        ``concurrency`` is less than 1.
    NoMlbTeamsDiscoveredError
        MLB returned no eligible Major League clubs for the season.
    MlbTeamDiscoveryError
        Team discovery failed or returned a club that could not be trusted.
    LeagueIngestionStateError
        Coverage state could not be persisted.
    """
    _validate_season(season)
    _validate_concurrency(concurrency)

    if client is not None:
        return await _ingest_async(
            session=session,
            season=season,
            client=client,
            concurrency=concurrency,
            on_team_complete=on_team_complete,
        )
    async with AsyncMlb() as owned_client:
        return await _ingest_async(
            session=session,
            season=season,
            client=owned_client,
            concurrency=concurrency,
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


def _validate_concurrency(concurrency: int) -> None:
    if concurrency < 1:
        raise InvalidConcurrencyError(
            f"concurrency must be at least 1, got {concurrency}"
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

    return _finish_league_ingestion(
        session=session,
        season=season,
        teams=teams,
        team_results=team_results,
        started_at=started_at,
    )


async def _ingest_async(
    *,
    session: Session,
    season: int,
    client: AsyncMlbLeagueDataClient,
    concurrency: int,
    on_team_complete: TeamProgressCallback | None,
) -> LeagueSeasonIngestionResult:
    teams = await discover_mlb_teams_async(season, client=client)
    started_at = _now()
    with _coverage_transaction(session, season):
        record_league_season_ingestion_start(
            session,
            season=season,
            expected_team_count=len(teams),
            started_at=started_at,
        )

    fetch_limit = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()
    total = len(teams)
    completed = 0

    async def run_one(team: MlbTeam) -> LeagueTeamIngestionResult:
        nonlocal completed

        async with fetch_limit:
            try:
                lines, pitching_lines = await get_team_game_lines_async(
                    team.team_id, team.season, client=client
                )
            except TeamGameLogError as exc:
                result = LeagueTeamIngestionResult.from_failure(
                    team_id=team.team_id,
                    team_name=team.team_name,
                    season=team.season,
                    error=f"{type(exc).__name__}: {exc}",
                )
                async with write_lock:
                    completed += 1
                    position = completed
                if on_team_complete is not None:
                    on_team_complete(position, total, result)
                return result

        # Only one already-fetched team-season is ever being persisted at a
        # time, even though several teams' fetches above may be in flight
        # together. Persistence itself contains no ``await``, so once a team
        # enters this block it runs to completion before the lock is released.
        async with write_lock:
            try:
                team_result = persist_team_season(
                    session,
                    team_id=team.team_id,
                    season=team.season,
                    lines=lines,
                    pitching_lines=pitching_lines,
                )
            except TeamSeasonIngestionError as exc:
                _discard_failed_team_transaction(session)
                result = LeagueTeamIngestionResult.from_failure(
                    team_id=team.team_id,
                    team_name=team.team_name,
                    season=team.season,
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                result = LeagueTeamIngestionResult.from_team_result(team_result)
            completed += 1
            position = completed

        if on_team_complete is not None:
            on_team_complete(position, total, result)
        return result

    # Tasks are created explicitly — not left for ``asyncio.gather`` to wrap
    # internally — so this function owns them outright. Plain ``gather``
    # propagates the first exception as soon as one coroutine raises, but
    # does not cancel the coroutines still running: a sibling team could keep
    # making MLB requests, or reach ``persist_team_season`` and the shared
    # session, after this function has already told its caller the run
    # failed. Owning the tasks lets that be closed off explicitly rather than
    # left to whatever incidentally cleans up orphaned tasks later (such as
    # ``asyncio.run``'s shutdown, which only helps when the caller happens to
    # run this inside a fresh event loop of its own).
    tasks = [
        asyncio.create_task(run_one(team), name=f"ingest-team-{team.team_id}")
        for team in teams
    ]
    try:
        # ``asyncio.gather`` returns results in the order its arguments were
        # given, i.e. discovery order, regardless of which team actually
        # finished first. ``team_results`` therefore lines up with ``teams``
        # exactly the way the sequential path's does.
        team_results = list(await asyncio.gather(*tasks))
    except BaseException:
        # An ordinary per-team failure (``TeamGameLogError`` /
        # ``TeamSeasonIngestionError``) never reaches here: ``run_one`` already
        # converts those into a FAILED result instead of raising. Only a truly
        # unexpected exception from a team, or one raised by
        # ``on_team_complete``, lands in this branch — and once it does, every
        # other team task is cancelled and awaited to completion before the
        # exception is allowed to leave this function, so no still-running
        # task remains capable of another MLB request, another entry into
        # ``persist_team_season``, or touching ``session`` at all.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    return _finish_league_ingestion(
        session=session,
        season=season,
        teams=teams,
        team_results=team_results,
        started_at=started_at,
    )


def _finish_league_ingestion(
    *,
    session: Session,
    season: int,
    teams: list[MlbTeam],
    team_results: list[LeagueTeamIngestionResult],
    started_at: datetime,
) -> LeagueSeasonIngestionResult:
    """Build, validate, and record the final result of a league ingestion run.

    Shared by the sequential and concurrent paths, so "the result is built and
    validated before COMPLETE/INCOMPLETE coverage is ever recorded" holds
    regardless of which path produced ``team_results``.
    """
    succeeded = sum(
        1
        for result in team_results
        if result.status is LeagueTeamIngestionStatus.SUCCEEDED
    )
    failed = len(team_results) - succeeded
    completed_at = _now()

    # Built and validated before any coverage state is written. The result
    # model enforces invariants the coverage row cannot express on its own,
    # such as each discovered team appearing exactly once. Persisting first
    # would let the database claim COMPLETE for a run the domain model then
    # rejects, so the validated result is the precondition for recording
    # coverage rather than a description of what was already recorded.
    result = LeagueSeasonIngestionResult(
        season=season,
        teams_discovered=len(teams),
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

    with _coverage_transaction(session, season):
        record_league_season_ingestion_finish(
            session,
            season=season,
            expected_team_count=result.teams_discovered,
            successful_team_count=result.teams_succeeded,
            failed_team_count=result.teams_failed,
            started_at=result.started_at,
            completed_at=result.completed_at,
        )

    return result


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
