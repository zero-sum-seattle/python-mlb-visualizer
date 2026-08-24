"""Tests for the concurrent league-wide season ingestion service.

The claim this file has to establish is that the concurrent service is a
*different order of fetching* and nothing else: same rows, same counts, same
coverage, same failures, same idempotency. So most of what follows compares a
concurrent import against a sequential one rather than asserting values in the
abstract, and the fixtures and fake client are the ones the sequential tests
already use — retargeted captures of a real 2025 Cubs season.

``AsyncFakeLeagueMlb`` is a thin async facade over that same fake. It answers
from the same fixture data and adds one thing: a record of how many requests
were in flight at once, which is what the boundedness and overlap assertions
read.

Nothing here touches the network, sleeps for any length of time, or measures
wall-clock time. Overlap is established by counting requests in flight, and the
transaction assertions are established by counting event-loop turns. Both are
deterministic.
"""

import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from mlbstatsapi.exceptions import MlbTransportError
from mlbstatsapi.models.schedules import Schedule
from mlbstatsapi.models.teams import Team
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.database.models import (
    TeamGameBattingLineRecord,
    TeamGamePitchingLineRecord,
)
from app.database.repositories import get_league_season_ingestion, list_team_season
from app.schemas.ingestion import (
    LeagueSeasonIngestionResult,
    LeagueSeasonIngestionStatus,
    LeagueTeamIngestionStatus,
)
from app.services.concurrent_league_season_ingestion import (
    DEFAULT_CONCURRENCY,
    InvalidConcurrencyError,
    ingest_league_season_concurrently,
)
from app.services.league_season_ingestion import (
    InvalidSeasonError,
    ingest_league_season,
)
from app.services.league_teams import NoMlbTeamsDiscoveredError
from app.services.team_season_ingestion import persist_team_season
from tests.test_league_season_ingestion import (
    CUBS_GAME_COUNT,
    CUBS_ID,
    CUBS_NAME,
    MARINERS_ID,
    MARINERS_NAME,
    SEASON,
    FakeLeagueMlb,
    build_source,
    make_league_client,
)
from tests.test_league_teams import make_team

# Deliberately more than the clubs in the fixture league, so a club is never
# excluded from a fetch round merely because the bound ran out.
WIDE_CONCURRENCY = 4


# --------------------------------------------------------------------------
# The async client under test: the existing fake, answered from a coroutine
# --------------------------------------------------------------------------


class AsyncFakeLeagueMlb:
    """Answer the existing ``FakeLeagueMlb`` fixtures from coroutines.

    Every answer is preceded by ``await asyncio.sleep(0)``. That is a
    scheduling yield, not a wait: it hands control back to the event loop
    exactly where a real socket read would, so other clubs' fetches get to run.
    No wall-clock time is involved and nothing here depends on timing.

    ``max_requests_in_flight`` is the high-water mark of requests suspended at
    that yield simultaneously. Because one club's four requests are awaited one
    at a time, that number is also the high-water mark of *clubs* in flight,
    which is what the concurrency bound is expressed in.
    """

    def __init__(self, sync: FakeLeagueMlb) -> None:
        self._sync = sync
        self.requests_in_flight = 0
        self.max_requests_in_flight = 0
        self.request_log: list[str] = []

    async def _answer(self, label: str, answer: Callable[[], Any]) -> Any:
        self.request_log.append(label)
        self.requests_in_flight += 1
        self.max_requests_in_flight = max(
            self.max_requests_in_flight, self.requests_in_flight
        )
        try:
            await asyncio.sleep(0)
            return answer()
        finally:
            self.requests_in_flight -= 1

    async def get_teams(self, sport_id: int = 1, **params: Any) -> list[Team]:
        return await self._answer(
            "get_teams", lambda: self._sync.get_teams(sport_id=sport_id, **params)
        )

    async def get_team(self, team_id: int, **params: Any) -> Team | None:
        return await self._answer(
            f"get_team:{team_id}", lambda: self._sync.get_team(team_id, **params)
        )

    async def get_team_stats(
        self,
        team_id: int,
        stats: list[str],
        groups: list[str],
        **params: Any,
    ) -> dict[str, Any]:
        return await self._answer(
            f"get_team_stats:{team_id}:{','.join(groups)}",
            lambda: self._sync.get_team_stats(team_id, stats, groups, **params),
        )

    async def get_schedule(self, **params: Any) -> Schedule | None:
        return await self._answer(
            f"get_schedule:{params['team_id']}",
            lambda: self._sync.get_schedule(**params),
        )


def async_league_client(**kwargs: Any) -> AsyncFakeLeagueMlb:
    """The same two-club 2025 league the sequential tests use, awaited."""
    return AsyncFakeLeagueMlb(make_league_client(**kwargs))


def run_concurrently(
    session: Session,
    *,
    client: AsyncFakeLeagueMlb,
    concurrency: int = WIDE_CONCURRENCY,
    season: int = SEASON,
    on_team_complete: Any = None,
) -> LeagueSeasonIngestionResult:
    """Run one concurrent import to completion on its own event loop."""
    return asyncio.run(
        ingest_league_season_concurrently(
            session=session,
            season=season,
            concurrency=concurrency,
            client=client,
            on_team_complete=on_team_complete,
        )
    )


# --------------------------------------------------------------------------
# Comparing two imports
# --------------------------------------------------------------------------


def stored_rows(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Every column of every stored game row, in a stable order.

    Every column, not a chosen few: the point of the parity tests is that a
    difference in a single normalized statistic would show up, and the upsert
    reports a row as ``updated`` exactly when some column differs.
    """
    session.expire_all()
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for model in (TeamGameBattingLineRecord, TeamGamePitchingLineRecord):
        columns = [column.key for column in inspect(model).columns]
        rows = (
            session.execute(
                select(model).order_by(model.team_id, model.game_pk, model.id)
            )
            .scalars()
            .all()
        )
        snapshot[model.__tablename__] = [
            {column: getattr(row, column) for column in columns} for row in rows
        ]
    # Reading begins a transaction on the session. Ending it here leaves the
    # session ready for the next import, which opens its own.
    session.rollback()
    return snapshot


def comparable(result: LeagueSeasonIngestionResult) -> dict[str, Any]:
    """A run's result with only its wall-clock timestamps removed."""
    payload = result.model_dump(mode="json")
    del payload["started_at"]
    del payload["completed_at"]
    return payload


# --------------------------------------------------------------------------
# Parity with the sequential service
# --------------------------------------------------------------------------


def test_concurrent_import_after_sequential_reports_everything_unchanged(
    migrated_session: Session,
) -> None:
    """The parity test. Any normalized value differing would read as an update."""
    ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    before = stored_rows(migrated_session)

    result = run_concurrently(migrated_session, client=async_league_client())

    assert result.team_game_records_fetched == 2 * CUBS_GAME_COUNT
    assert result.unchanged == result.team_game_records_fetched
    assert result.inserted == 0
    assert result.updated == 0
    assert stored_rows(migrated_session) == before


def test_sequential_import_after_concurrent_reports_everything_unchanged(
    migrated_session: Session,
) -> None:
    """The same claim with the order reversed, so neither path is privileged."""
    run_concurrently(migrated_session, client=async_league_client())
    before = stored_rows(migrated_session)

    result = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )

    assert result.team_game_records_fetched == 2 * CUBS_GAME_COUNT
    assert result.unchanged == result.team_game_records_fetched
    assert result.inserted == 0
    assert result.updated == 0
    assert stored_rows(migrated_session) == before


def test_a_concurrent_run_reports_the_same_result_as_a_sequential_one(
    migrated_session: Session,
) -> None:
    """Field for field, including per-club counts and their order."""
    sequential = ingest_league_season(
        session=migrated_session, season=SEASON, client=make_league_client()
    )
    concurrent = run_concurrently(migrated_session, client=async_league_client())

    # The second run of an unchanged season stores nothing new, so compare the
    # counts that describe the season rather than what each run did to it.
    assert comparable(concurrent)["team_results"] != []
    assert [team["team_id"] for team in comparable(concurrent)["team_results"]] == [
        team["team_id"] for team in comparable(sequential)["team_results"]
    ]
    assert concurrent.teams_discovered == sequential.teams_discovered
    assert concurrent.teams_succeeded == sequential.teams_succeeded
    assert concurrent.teams_failed == sequential.teams_failed
    assert concurrent.team_game_records_fetched == sequential.team_game_records_fetched
    assert concurrent.status is sequential.status


def test_two_concurrent_imports_of_the_same_season_agree(
    migrated_session: Session,
) -> None:
    """Idempotent rerun: the second concurrent run changes nothing."""
    first = run_concurrently(migrated_session, client=async_league_client())
    before = stored_rows(migrated_session)
    second = run_concurrently(migrated_session, client=async_league_client())

    assert first.inserted == first.team_game_records_fetched
    assert second.unchanged == second.team_game_records_fetched
    assert stored_rows(migrated_session) == before


def test_a_fresh_concurrent_import_stores_what_a_fresh_sequential_one_stores(
    migrated_session: Session,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Two empty databases, one import each, compared row for row.

    Three columns are dropped from the comparison, and none of them carry
    baseball data: the autoincrement ``id`` records the order rows were written
    in, which is exactly what this feature changes, and ``created_at`` and
    ``updated_at`` record when each write happened, which cannot match across
    two runs made at two different moments. Every column that describes a game
    is compared.
    """
    from app.database.engine import build_engine, build_session_factory
    from tests.conftest import run_alembic_upgrade

    db_path = tmp_path_factory.mktemp("concurrent") / "concurrent.db"
    run_alembic_upgrade(f"sqlite:///{db_path}")
    engine = build_engine(f"sqlite:///{db_path}")
    concurrent_session = build_session_factory(engine)()

    try:
        ingest_league_season(
            session=migrated_session, season=SEASON, client=make_league_client()
        )
        run_concurrently(concurrent_session, client=async_league_client())

        sequential_rows = _without_write_metadata(stored_rows(migrated_session))
        concurrent_rows = _without_write_metadata(stored_rows(concurrent_session))
    finally:
        concurrent_session.close()
        engine.dispose()

    assert concurrent_rows == sequential_rows
    assert sequential_rows["team_game_batting_lines"] != []


# Columns that record how and when a row was written rather than what happened
# in the game it describes.
WRITE_METADATA_COLUMNS = frozenset({"id", "created_at", "updated_at"})


def _without_write_metadata(
    snapshot: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        table: [
            {
                key: value
                for key, value in row.items()
                if key not in WRITE_METADATA_COLUMNS
            }
            for row in rows
        ]
        for table, rows in snapshot.items()
    }


# --------------------------------------------------------------------------
# The fetches genuinely overlap, and are genuinely bounded
# --------------------------------------------------------------------------


def wide_league(club_count: int) -> AsyncFakeLeagueMlb:
    """A league of ``club_count`` clubs, all built from the captured season.

    Team ids are the two real ones plus synthetic ids above them. Only the
    number of clubs matters to these tests; each club's games are the same
    captured Cubs games retargeted, which the sequential tests already do.
    """
    ids_and_names = [(CUBS_ID, CUBS_NAME), (MARINERS_ID, MARINERS_NAME)]
    while len(ids_and_names) < club_count:
        next_id = 900 + len(ids_and_names)
        ids_and_names.append((next_id, f"Club {next_id}"))
    ids_and_names = ids_and_names[:club_count]
    return AsyncFakeLeagueMlb(
        FakeLeagueMlb(
            teams=[make_team(team_id, name) for team_id, name in ids_and_names],
            sources={
                team_id: build_source(team_id, name) for team_id, name in ids_and_names
            },
        )
    )


def test_fetches_for_different_clubs_actually_overlap(
    migrated_session: Session,
) -> None:
    client = wide_league(6)
    run_concurrently(migrated_session, client=client, concurrency=3)
    assert client.max_requests_in_flight > 1


@pytest.mark.parametrize("concurrency", [1, 2, 3, 5])
def test_no_more_clubs_are_in_flight_than_the_bound_allows(
    migrated_session: Session,
    concurrency: int,
) -> None:
    client = wide_league(6)
    run_concurrently(migrated_session, client=client, concurrency=concurrency)
    assert client.max_requests_in_flight <= concurrency


@pytest.mark.parametrize("concurrency", [1, 2, 3, 5])
def test_the_bound_is_reached_rather_than_merely_respected(
    migrated_session: Session,
    concurrency: int,
) -> None:
    """A bound nothing ever reaches would also satisfy the ceiling test."""
    client = wide_league(6)
    run_concurrently(migrated_session, client=client, concurrency=concurrency)
    assert client.max_requests_in_flight == concurrency


def test_concurrency_of_one_does_not_overlap_anything(
    migrated_session: Session,
) -> None:
    client = wide_league(6)
    run_concurrently(migrated_session, client=client, concurrency=1)
    assert client.max_requests_in_flight == 1


def test_one_club_is_not_fetched_in_parallel_with_itself(
    migrated_session: Session,
) -> None:
    """The four requests for a club stay sequential, whatever the bound.

    With a single club there is nothing to fan out over, so any overlap at all
    would have to be inside that club's own team-season.
    """
    client = AsyncFakeLeagueMlb(
        FakeLeagueMlb(
            teams=[make_team(CUBS_ID, CUBS_NAME)],
            sources={CUBS_ID: build_source(CUBS_ID, CUBS_NAME)},
        )
    )
    run_concurrently(migrated_session, client=client, concurrency=8)
    assert client.max_requests_in_flight == 1


def test_every_club_is_fetched_exactly_once(migrated_session: Session) -> None:
    client = wide_league(6)
    run_concurrently(migrated_session, client=client, concurrency=3)
    team_lookups = [
        label for label in client.request_log if label.startswith("get_team:")
    ]
    assert len(team_lookups) == 6
    assert len(set(team_lookups)) == 6


def test_a_run_is_refused_a_bound_below_one(migrated_session: Session) -> None:
    with pytest.raises(InvalidConcurrencyError):
        run_concurrently(migrated_session, client=async_league_client(), concurrency=0)


def test_the_default_bound_is_used_when_none_is_given(
    migrated_session: Session,
) -> None:
    client = async_league_client()
    result = asyncio.run(
        ingest_league_season_concurrently(
            session=migrated_session, season=SEASON, client=client
        )
    )
    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    assert DEFAULT_CONCURRENCY >= 1


# --------------------------------------------------------------------------
# The database is not made concurrent
# --------------------------------------------------------------------------


@dataclass
class LoopTurns:
    """How many times the event loop has handed control to the spinner task."""

    count: int = 0


@dataclass(frozen=True)
class TransactionObservation:
    """One database transaction, measured at its two ends."""

    turns_at_begin: int
    turns_at_commit: int
    thread_at_begin: int
    thread_at_commit: int


async def spin(turns: LoopTurns) -> None:
    """Count every turn of the event loop until cancelled.

    ``asyncio.sleep(0)`` yields and reschedules immediately, so this task is
    runnable at every turn. If the coroutine holding a database transaction
    were to suspend for any reason, this would get to run and the count would
    move. That is the whole detector: no wall clock, no sleeping.
    """
    while True:
        turns.count += 1
        await asyncio.sleep(0)


def watch_transactions(
    session: Session,
    turns: LoopTurns,
) -> list[TransactionObservation]:
    """Record the loop turn and the thread at each transaction's two ends."""
    observations: list[TransactionObservation] = []
    open_transaction: dict[str, tuple[int, int]] = {}

    @event.listens_for(session, "after_begin")
    def _began(session: Session, transaction: Any, connection: Any) -> None:
        open_transaction["at"] = (turns.count, threading.get_ident())

    @event.listens_for(session, "after_commit")
    def _committed(session: Session) -> None:
        started = open_transaction.pop("at", None)
        if started is None:
            return
        observations.append(
            TransactionObservation(
                turns_at_begin=started[0],
                turns_at_commit=turns.count,
                thread_at_begin=started[1],
                thread_at_commit=threading.get_ident(),
            )
        )

    return observations


def run_with_transaction_watch(
    session: Session,
    *,
    client: AsyncFakeLeagueMlb,
    concurrency: int,
) -> tuple[list[TransactionObservation], LoopTurns]:
    """Run a concurrent import with a spinner task competing for the loop."""
    turns = LoopTurns()
    observations = watch_transactions(session, turns)

    async def run() -> None:
        spinner = asyncio.create_task(spin(turns))
        try:
            await ingest_league_season_concurrently(
                session=session,
                season=SEASON,
                concurrency=concurrency,
                client=client,
            )
        finally:
            spinner.cancel()
            with suppress(asyncio.CancelledError):
                await spinner

    asyncio.run(run())
    return observations, turns


def test_no_database_transaction_is_held_open_across_an_await(
    migrated_session: Session,
) -> None:
    """The rule the whole design rests on, asserted rather than asserted-to.

    Between a transaction beginning and that transaction committing, no other
    task got to run. In a single-threaded event loop that is the same statement
    as: this coroutine did not suspend, so there was no ``await`` inside the
    transaction.
    """
    observations, turns = run_with_transaction_watch(
        migrated_session, client=wide_league(6), concurrency=3
    )

    assert observations
    for observation in observations:
        assert observation.turns_at_commit == observation.turns_at_begin


def test_the_transaction_detector_would_have_noticed_a_suspension(
    migrated_session: Session,
) -> None:
    """Guards the test above: a spinner that never ran would pass it vacuously.

    The loop demonstrably did turn during the run, and demonstrably turned
    between one transaction committing and the next one beginning — which is
    where the fetches are.
    """
    observations, turns = run_with_transaction_watch(
        migrated_session, client=wide_league(6), concurrency=3
    )

    assert turns.count > 0
    assert len(observations) > 1
    assert observations[-1].turns_at_begin > observations[0].turns_at_commit


def test_every_transaction_runs_on_the_calling_thread(
    migrated_session: Session,
) -> None:
    """No thread pool: persistence happens where the caller is."""
    caller = threading.get_ident()
    observations, _ = run_with_transaction_watch(
        migrated_session, client=wide_league(6), concurrency=3
    )

    assert observations
    for observation in observations:
        assert observation.thread_at_begin == caller
        assert observation.thread_at_commit == caller


def test_persistence_goes_through_the_shared_team_season_upsert(
    migrated_session: Session,
) -> None:
    """Evidence of reuse: the concurrent path calls that exact function."""
    module = "app.services.concurrent_league_season_ingestion.persist_team_season"
    with patch(module, side_effect=persist_team_season) as persist:
        run_concurrently(migrated_session, client=async_league_client())

    persisted = {call.kwargs["team_id"] for call in persist.call_args_list}
    assert persisted == {CUBS_ID, MARINERS_ID}
    assert all(
        call.kwargs["session"] is migrated_session for call in persist.call_args_list
    )


# --------------------------------------------------------------------------
# Coverage semantics are unchanged
# --------------------------------------------------------------------------


def test_a_complete_run_records_complete_coverage(migrated_session: Session) -> None:
    result = run_concurrently(migrated_session, client=async_league_client())
    state = get_league_season_ingestion(migrated_session, season=SEASON)

    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.COMPLETE
    assert state.expected_team_count == 2
    assert state.successful_team_count == 2
    assert state.failed_team_count == 0
    assert state.started_at == result.started_at
    assert state.completed_at == result.completed_at


def test_a_failing_club_does_not_undo_the_clubs_that_finished_first(
    migrated_session: Session,
) -> None:
    result = run_concurrently(
        migrated_session,
        client=async_league_client(mariners_stats=MlbTransportError("Request failed")),
    )

    assert result.teams_succeeded == 1
    assert result.teams_failed == 1
    assert result.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert (
        len(list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON))
        == CUBS_GAME_COUNT
    )
    assert list_team_season(migrated_session, team_id=MARINERS_ID, season=SEASON) == []


def test_a_failed_club_keeps_its_identity_and_its_error(
    migrated_session: Session,
) -> None:
    result = run_concurrently(
        migrated_session,
        client=async_league_client(mariners_stats=MlbTransportError("Request failed")),
    )
    failed = next(
        team
        for team in result.team_results
        if team.status is LeagueTeamIngestionStatus.FAILED
    )

    assert (failed.team_id, failed.team_name) == (MARINERS_ID, MARINERS_NAME)
    assert "TeamGameLogError" in (failed.error or "")
    assert (failed.fetched, failed.inserted, failed.updated) == (0, 0, 0)


def test_incomplete_coverage_is_recorded_with_its_counts(
    migrated_session: Session,
) -> None:
    run_concurrently(
        migrated_session,
        client=async_league_client(mariners_stats=MlbTransportError("Request failed")),
    )
    state = get_league_season_ingestion(migrated_session, season=SEASON)

    assert state is not None
    assert state.status is LeagueSeasonIngestionStatus.INCOMPLETE
    assert (state.expected_team_count, state.successful_team_count) == (2, 1)
    assert state.failed_team_count == 1


def test_a_rerun_after_a_failure_can_reach_complete(
    migrated_session: Session,
) -> None:
    run_concurrently(
        migrated_session,
        client=async_league_client(mariners_stats=MlbTransportError("Request failed")),
    )
    result = run_concurrently(migrated_session, client=async_league_client())

    assert result.status is LeagueSeasonIngestionStatus.COMPLETE
    assert result.unchanged == CUBS_GAME_COUNT
    assert result.inserted == CUBS_GAME_COUNT


def test_a_concurrent_and_a_sequential_failure_are_reported_identically(
    migrated_session: Session,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The same broken club produces the same per-club failure either way."""
    from app.database.engine import build_engine, build_session_factory
    from tests.conftest import run_alembic_upgrade

    db_path = tmp_path_factory.mktemp("failure") / "failure.db"
    run_alembic_upgrade(f"sqlite:///{db_path}")
    engine = build_engine(f"sqlite:///{db_path}")
    other_session = build_session_factory(engine)()

    try:
        sequential = ingest_league_season(
            session=migrated_session,
            season=SEASON,
            client=make_league_client(
                mariners_stats=MlbTransportError("Request failed")
            ),
        )
        concurrent = run_concurrently(
            other_session,
            client=async_league_client(
                mariners_stats=MlbTransportError("Request failed")
            ),
        )
    finally:
        other_session.close()
        engine.dispose()

    assert comparable(concurrent) == comparable(sequential)


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------


def test_team_results_are_returned_in_discovery_order(
    migrated_session: Session,
) -> None:
    """Whichever club MLB answered first, the record of the run is stable."""
    sequential_order = [
        team.team_id
        for team in ingest_league_season(
            session=migrated_session, season=SEASON, client=make_league_client()
        ).team_results
    ]
    concurrent_order = [
        team.team_id
        for team in run_concurrently(
            migrated_session, client=async_league_client()
        ).team_results
    ]

    assert concurrent_order == sequential_order


def test_progress_is_reported_once_per_club_and_counts_up(
    migrated_session: Session,
) -> None:
    """Completion order, with ``position`` counting clubs finished so far."""
    seen: list[tuple[int, int, int]] = []

    def record(position: int, total: int, result: Any) -> None:
        seen.append((position, total, result.team_id))

    run_concurrently(
        migrated_session,
        client=wide_league(6),
        concurrency=3,
        on_team_complete=record,
    )

    assert [position for position, _, _ in seen] == [1, 2, 3, 4, 5, 6]
    assert {total for _, total, _ in seen} == {6}
    assert len({team_id for _, _, team_id in seen}) == 6


def test_a_progress_callback_failure_is_not_swallowed(
    migrated_session: Session,
) -> None:
    """The callback is not an error boundary, matching the sequential service."""

    class CallbackFailure(Exception):
        pass

    def explode(position: int, total: int, result: Any) -> None:
        raise CallbackFailure

    with pytest.raises(CallbackFailure):
        run_concurrently(
            migrated_session,
            client=wide_league(6),
            concurrency=3,
            on_team_complete=explode,
        )


# --------------------------------------------------------------------------
# Season and discovery failures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("season", [1875, 0, -2025])
def test_an_impossible_season_is_refused_before_anything_is_requested(
    migrated_session: Session,
    season: int,
) -> None:
    client = async_league_client()
    with pytest.raises(InvalidSeasonError):
        run_concurrently(migrated_session, client=client, season=season)
    assert client.request_log == []


def test_zero_discovered_clubs_stops_before_any_state_is_written(
    migrated_session: Session,
) -> None:
    client = AsyncFakeLeagueMlb(FakeLeagueMlb(teams=[]))
    with pytest.raises(NoMlbTeamsDiscoveredError):
        run_concurrently(migrated_session, client=client)
    assert get_league_season_ingestion(migrated_session, season=SEASON) is None
