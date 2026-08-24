"""League-wide season ingestion that overlaps the waiting on MLB.

A league import is roughly four MLB requests for each of about thirty clubs,
and almost all of its wall-clock time is spent waiting for MLB to answer. This
service does the same work as ``league_season_ingestion.ingest_league_season``
with that waiting overlapped, and is checked against it: the sequential service
is the reference implementation, not this one.

Every rule about baseball data or coverage is called, not restated. Discovery,
normalization, the schedule join, the completed-game check, the upsert and its
counts, how a club's failure is recorded, how the aggregate result is built and
validated, and when coverage may be written all live where they already lived.
This module contributes exactly one thing: the order in which clubs are
fetched.

What is concurrent, and what is not
-----------------------------------
Concurrent:

* the MLB fetches for **different clubs**, up to ``concurrency`` in flight.

Not concurrent:

* the four requests **within** one club, which are still awaited one at a time
  (see ``async_team_game_logs``);
* the database, entirely — one synchronous connection, one transaction at a
  time, no async driver and no thread pool;
* normalization and every other CPU-bound step, which run on the one thread
  this event loop runs on, between awaits.

So the saving is bounded by how much of the run is spent waiting on the
network, and by ``concurrency``. It is not a speedup of the ingestion work
itself, which is unchanged and still happens once per club.

Transaction boundaries
----------------------
Fetching is concurrent; persistence is not::

    discover teams                         one request, no transaction
    record RUNNING                         short transaction, committed
    fan out N fetches, bounded             network only, no transaction
    as each club's fetch finishes:
        persist that club                  short transaction, committed
    build and validate the result          no transaction
    record COMPLETE / INCOMPLETE           short transaction, committed

The fetches run as tasks that touch nothing but MLB. Persistence happens in the
orchestrating coroutine, one club at a time, and ``_persist_fetched_team_season``
below is a plain ``def`` containing no ``await`` — so the transaction it opens
cannot be suspended, and no other club's work can interleave with it. That is
asserted by a test rather than promised by this docstring, because it is the
property that makes a synchronous SQLAlchemy session safe to use from an event
loop at all.

Ordering
--------
Clubs finish in whatever order MLB answers, which is not discovery order and
is not reproducible between runs. Two things depend on order, and they were
decided differently on purpose.

The **progress callback** fires in completion order, as each club is persisted,
and its ``position`` counts clubs finished so far rather than naming the club's
place in the discovery list. An operator watching a long import wants to know
what has actually happened; replaying progress in discovery order would mean
holding back a finished club to preserve a sequence the run is not following,
and would hide a club that is answering slowly behind one that has not been
reported yet. So the callback tells the truth about the run, and the caller
should not read ``position`` as identifying a club.

``team_results`` is put back into **discovery order** before the result is
built. That result is the record of the run: it is serialized by the CLI, read
by coverage, and compared against a sequential import. Ordering it by whichever
club MLB happened to answer first would make two identical imports produce
different records, and would make a sequential and a concurrent import of the
same season impossible to compare field for field. Discovery order is already
defined as stable — clubs sorted by name then id — so this restores an order
the sequential path also produces.

Persistence order is completion order and is deliberately not restored. The
upsert is keyed by ``(team_id, game_pk)`` and is idempotent, so the stored rows
do not depend on which club committed first.

Failure and coverage semantics are unchanged. Each club commits on its own, one
club's failure does not undo the clubs before it, a run with any failure is
recorded INCOMPLETE, and a rerun re-attempts every club. See
``docs/league-season-ingestion.md`` and ``docs/concurrent-league-ingestion.md``.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from mlbstatsapi import AsyncMlb
from sqlalchemy.orm import Session

from app.schemas.games import TeamGameBattingLine, TeamGamePitchingLine
from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueTeamIngestionResult,
)
from app.schemas.teams import MlbTeam
from app.services.async_league_teams import (
    AsyncMlbTeamDirectoryClient,
    discover_mlb_teams_async,
)
from app.services.async_team_game_logs import (
    AsyncMlbGameDataClient,
    get_team_game_lines_async,
)
from app.services.league_season_ingestion import (
    LEAGUE_TEAM_INGESTION_ERRORS,
    LeagueSeasonIngestionError,
    TeamProgressCallback,
    build_league_result,
    discard_failed_team_transaction,
    league_team_failure,
    now_for_ingestion,
    record_run_finished,
    record_run_started,
    validate_season,
)
from app.services.team_season_ingestion import persist_team_season

# How many clubs are fetched at once when a caller does not choose. Modest on
# purpose: this is a public API belonging to someone else, a league import is
# not latency sensitive, and no measurement established an optimum. Raise it
# deliberately, with the benchmark in scripts/benchmark_league_import.py.
DEFAULT_CONCURRENCY = 8


class InvalidConcurrencyError(LeagueSeasonIngestionError):
    """The requested number of concurrent fetches is not a usable bound."""


class AsyncMlbLeagueDataClient(
    AsyncMlbTeamDirectoryClient,
    AsyncMlbGameDataClient,
    Protocol,
):
    """One async client covering both team discovery and every club's fetch.

    A run reuses a single client throughout rather than opening one per club,
    so the underlying connection pool is shared and is what actually bounds
    sockets held against MLB.
    """


@dataclass(frozen=True)
class _FetchedTeamSeason:
    """One club's season, fetched and waiting to be persisted."""

    team: MlbTeam
    position: int
    batting: list[TeamGameBattingLine]
    pitching: list[TeamGamePitchingLine]


@dataclass(frozen=True)
class _FailedTeamSeason:
    """One club whose fetch failed, waiting to be recorded as a failure."""

    team: MlbTeam
    position: int
    error: Exception


# What a fetch task hands back. Both carry ``position`` — the club's place in
# discovery order — because clubs are persisted in the order MLB answers and
# ``team_results`` is put back into discovery order afterwards.
_TeamSeasonFetch = _FetchedTeamSeason | _FailedTeamSeason


async def ingest_league_season_concurrently(
    *,
    session: Session,
    season: int,
    concurrency: int = DEFAULT_CONCURRENCY,
    client: AsyncMlbLeagueDataClient | None = None,
    on_team_complete: TeamProgressCallback | None = None,
) -> LeagueSeasonIngestionResult:
    """Ingest every MLB team-season for ``season``, overlapping the MLB waits.

    Produces the same result, the same stored rows, and the same coverage as
    ``league_season_ingestion.ingest_league_season``. The difference is that up
    to ``concurrency`` clubs are fetched from MLB at once.

    Parameters
    ----------
    session:
        Session for the target database. Must have no transaction in progress.
        This service opens and commits its own short transactions, from this
        coroutine only, and never holds one across an ``await``. The session is
        an ordinary synchronous SQLAlchemy session and is used from one thread.
    season:
        Four digit season year.
    concurrency:
        Maximum clubs fetched from MLB at once. Must be at least 1; 1 means the
        fetches do not overlap at all, which is not the same code path as the
        sequential service but does the same amount of waiting.
    client:
        An existing ``mlbstatsapi.AsyncMlb`` client, reused for discovery and
        for every club. When omitted, one client is created for the whole run
        and closed afterwards.
    on_team_complete:
        Optional callback invoked as ``(position, total, result)`` after each
        club is persisted. ``position`` counts clubs finished so far, in
        completion order — it does not identify the club's place in the
        discovery list. It is not an error boundary: exceptions raised by the
        callback propagate.

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
    validate_season(season)
    _validate_concurrency(concurrency)

    if client is not None:
        return await _ingest(
            session=session,
            season=season,
            concurrency=concurrency,
            client=client,
            on_team_complete=on_team_complete,
        )
    async with AsyncMlb() as owned_client:
        return await _ingest(
            session=session,
            season=season,
            concurrency=concurrency,
            client=owned_client,
            on_team_complete=on_team_complete,
        )


def _validate_concurrency(concurrency: int) -> None:
    """Reject a bound that does not describe a number of clubs in flight."""
    if concurrency < 1:
        raise InvalidConcurrencyError(
            f"Concurrency {concurrency} is not a usable bound; it must be at least 1"
        )


async def _ingest(
    *,
    session: Session,
    season: int,
    concurrency: int,
    client: AsyncMlbLeagueDataClient,
    on_team_complete: TeamProgressCallback | None,
) -> LeagueSeasonIngestionResult:
    teams = await discover_mlb_teams_async(season, client=client)
    started_at = now_for_ingestion()
    record_run_started(
        session,
        season=season,
        expected_team_count=len(teams),
        started_at=started_at,
    )

    semaphore = asyncio.Semaphore(concurrency)
    fetches = [
        asyncio.create_task(
            _fetch_team_season(team, position, client=client, semaphore=semaphore),
            name=f"fetch-team-{team.team_id}-{team.season}",
        )
        for position, team in enumerate(teams)
    ]

    by_discovery_position: dict[int, LeagueTeamIngestionResult] = {}
    try:
        for finished in asyncio.as_completed(fetches):
            fetched = await finished
            result = _persist_fetched_team_season(session, fetched)
            by_discovery_position[fetched.position] = result
            if on_team_complete is not None:
                on_team_complete(len(by_discovery_position), len(teams), result)
    finally:
        await _drain(fetches)

    team_results = [by_discovery_position[position] for position in range(len(teams))]
    result = build_league_result(
        season=season,
        teams_discovered=len(teams),
        team_results=team_results,
        started_at=started_at,
        completed_at=now_for_ingestion(),
    )
    record_run_finished(session, result)
    return result


async def _fetch_team_season(
    team: MlbTeam,
    position: int,
    *,
    client: AsyncMlbGameDataClient,
    semaphore: asyncio.Semaphore,
) -> _TeamSeasonFetch:
    """Fetch one club's season, holding a slot in the concurrency bound.

    Touches MLB and nothing else — no session, no transaction, no repository.
    The semaphore is what bounds clubs in flight; it is acquired around the
    whole club rather than around each request, so a club's four requests are
    not interleaved with a thirty-first club starting.

    A club's own ingestion failure is carried back rather than raised, so one
    club cannot abort the others. Anything else propagates, which cancels the
    run — the same rule the sequential service applies.
    """
    async with semaphore:
        try:
            batting, pitching = await get_team_game_lines_async(
                team.team_id,
                team.season,
                client=client,
            )
        except LEAGUE_TEAM_INGESTION_ERRORS as exc:
            return _FailedTeamSeason(team=team, position=position, error=exc)
    return _FetchedTeamSeason(
        team=team,
        position=position,
        batting=batting,
        pitching=pitching,
    )


def _persist_fetched_team_season(
    session: Session,
    fetched: _TeamSeasonFetch,
) -> LeagueTeamIngestionResult:
    """Persist one already-fetched club and record its outcome.

    Synchronous on purpose, and this is the load-bearing detail of the whole
    module. There is no ``await`` in this function or in anything it calls, so
    from the moment ``persist_team_season`` opens its transaction to the moment
    it commits, this coroutine cannot suspend and no other club's work can run.
    The database therefore sees exactly what it sees in a sequential import:
    one connection, one transaction at a time, on one thread.

    Making this ``async`` — even without adding an ``await`` today — would
    remove that guarantee, because a later edit could then add one inside the
    transaction without anything failing.
    """
    if isinstance(fetched, _FailedTeamSeason):
        discard_failed_team_transaction(session)
        return league_team_failure(fetched.team, fetched.error)

    try:
        result = persist_team_season(
            session=session,
            team_id=fetched.team.team_id,
            season=fetched.team.season,
            lines=fetched.batting,
            pitching_lines=fetched.pitching,
        )
    except LEAGUE_TEAM_INGESTION_ERRORS as exc:
        discard_failed_team_transaction(session)
        return league_team_failure(fetched.team, exc)
    return LeagueTeamIngestionResult.from_team_result(result)


async def _drain(fetches: list[asyncio.Task[_TeamSeasonFetch]]) -> None:
    """Cancel and collect any fetch still outstanding.

    Only reached with work left when something unexpected ended the run early.
    Cancelling stops clubs nobody will persist from continuing to call MLB, and
    collecting every task keeps a failure from a fetch that was never consumed
    from surfacing later as an unretrieved task exception, on top of the error
    that actually stopped the run.
    """
    for task in fetches:
        if not task.done():
            task.cancel()
    await asyncio.gather(*fetches, return_exceptions=True)
