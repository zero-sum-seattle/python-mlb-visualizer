"""Tests for the async MLB game-log retrieval path.

These mirror ``test_team_game_logs.py`` rather than duplicating it: the goal
is to prove the async path produces the identical normalized output, makes
the identical requests, and translates upstream errors the identical way as
the synchronous path — because both call the same private normalization and
validation helpers in ``app.services.team_game_logs``. Nothing here touches
the network; ``AsyncFakeMlb`` wraps the existing ``FakeMlb`` fixture fake so
the two paths are driven by the exact same captured data.
"""

import asyncio

import pytest
from mlbstatsapi.exceptions import MlbTransportError

from app.services.team_game_logs import (
    TeamGameDataError,
    TeamGameLogError,
    TeamNotFoundError,
    get_team_game_lines,
    get_team_game_lines_async,
)
from tests.test_team_game_logs import (
    SEASON,
    FakeMlb,
    build_schedule,
    build_team_stats,
    drop_game_log_splits,
    load_payload,
    make_client,
)

CUBS_ID = 112


class AsyncFakeMlb:
    """Async counterpart of ``FakeMlb``.

    Wraps a ``FakeMlb`` instance rather than reimplementing it, so both
    transports are driven by one fixture-loading and error-raising
    implementation. ``delay`` lets a test force overlapping in-flight
    requests without touching the network.
    """

    def __init__(self, sync: FakeMlb, *, delay: float = 0.0) -> None:
        self._sync = sync
        self._delay = delay

    async def _maybe_delay(self) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)

    async def get_team(self, team_id: int, **params: object):
        await self._maybe_delay()
        return self._sync.get_team(team_id, **params)

    async def get_team_stats(
        self, team_id: int, stats: list[str], groups: list[str], **params: object
    ):
        await self._maybe_delay()
        return self._sync.get_team_stats(team_id, stats, groups, **params)

    async def get_schedule(self, **params: object):
        await self._maybe_delay()
        return self._sync.get_schedule(**params)

    @property
    def calls(self) -> dict:
        return self._sync.calls

    @property
    def stat_group_calls(self) -> list[tuple[str, ...]]:
        return self._sync.stat_group_calls


def make_async_client(**kwargs: object) -> AsyncFakeMlb:
    return AsyncFakeMlb(make_client(**kwargs))


def test_async_batting_and_pitching_match_sync() -> None:
    sync_batting, sync_pitching = get_team_game_lines(
        CUBS_ID, SEASON, client=make_client()
    )
    async_batting, async_pitching = asyncio.run(
        get_team_game_lines_async(CUBS_ID, SEASON, client=make_async_client())
    )
    assert async_batting == sync_batting
    assert async_pitching == sync_pitching
    assert len(async_batting) == 6


def test_async_requests_match_sync_parameters() -> None:
    sync_client = make_client()
    get_team_game_lines(CUBS_ID, SEASON, client=sync_client)

    async_client = make_async_client()
    asyncio.run(get_team_game_lines_async(CUBS_ID, SEASON, client=async_client))

    assert async_client.calls == sync_client.calls
    assert async_client.stat_group_calls == sync_client.stat_group_calls


def test_async_transport_error_is_translated_the_same_way() -> None:
    client = AsyncFakeMlb(FakeMlb(team=MlbTransportError("Request failed")))
    with pytest.raises(TeamGameLogError):
        asyncio.run(get_team_game_lines_async(CUBS_ID, SEASON, client=client))


def test_async_missing_team_is_reported_the_same_way() -> None:
    client = AsyncFakeMlb(FakeMlb(team=None))
    with pytest.raises(TeamNotFoundError):
        asyncio.run(get_team_game_lines_async(CUBS_ID, SEASON, client=client))


def test_async_missing_completed_game_is_refused_the_same_way() -> None:
    """A split ``python-mlb-statsapi`` drops must fail the same way async."""
    missing_game_pk = 776640
    short_log = drop_game_log_splits(
        load_payload("cubs_2025_hitting_game_log"), missing_game_pk
    )
    client = AsyncFakeMlb(
        FakeMlb(
            team_stats=build_team_stats(short_log),
            schedule=build_schedule(load_payload("cubs_2025_schedule")),
        )
    )
    with pytest.raises(TeamGameDataError, match=str(missing_game_pk)):
        asyncio.run(get_team_game_lines_async(CUBS_ID, SEASON, client=client))
