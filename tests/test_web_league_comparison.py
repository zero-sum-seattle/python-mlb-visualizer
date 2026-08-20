"""Tests for how the hits page gates MLB context on league-season coverage.

Every case here is offline. The database is seeded directly and the recorded
coverage state is written directly, because coverage is what the page reads —
never a live MLB call, and never a row count.
"""

from collections.abc import Callable, Generator, Iterator
from datetime import datetime
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.database.repositories import (
    record_league_season_ingestion_finish,
    record_league_season_ingestion_start,
    upsert_team_season,
)
from app.main import create_app
from app.web.dependencies import get_db_session
from app.web.formatting import LEAGUE_COMPARISON_UNAVAILABLE_NOTE
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_season,
)

STARTED = datetime(2026, 3, 1, 12, 0, 0)
FINISHED = datetime(2026, 3, 1, 12, 30, 0)

SeedFn = Callable[..., None]
CoverageFn = Callable[..., None]


@pytest.fixture
def session_factory(migrated_db_path: Path) -> Generator[Callable[[], Session]]:
    engine = build_engine(f"sqlite:///{migrated_db_path}")
    factory = build_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def seed(session_factory: Callable[[], Session]) -> SeedFn:
    """Persist one team-season, with game ids kept unique across teams."""

    def _seed(
        hits: list[int],
        *,
        team_id: int = MARINERS_ID,
        team_name: str = MARINERS_NAME,
        season: int = 2025,
        strikeouts: list[int] | None = None,
    ) -> None:
        lines = [
            line.model_copy(update={"game_pk": line.game_pk + team_id * 100_000})
            for line in make_season(
                hits,
                team_id=team_id,
                team_name=team_name,
                season=season,
                strikeouts=strikeouts,
            )
        ]
        session = session_factory()
        try:
            upsert_team_season(session, lines=lines)
            session.commit()
        finally:
            session.close()

    return _seed


@pytest.fixture
def record_coverage(session_factory: Callable[[], Session]) -> CoverageFn:
    """Write a league-season coverage row the way an ingestion run would."""

    def _record(
        *,
        season: int = 2025,
        teams: int = 30,
        failed: int = 0,
        finished: bool = True,
    ) -> None:
        session = session_factory()
        try:
            with session.begin():
                record_league_season_ingestion_start(
                    session,
                    season=season,
                    expected_team_count=teams,
                    started_at=STARTED,
                )
            if not finished:
                return
            with session.begin():
                record_league_season_ingestion_finish(
                    session,
                    season=season,
                    expected_team_count=teams,
                    successful_team_count=teams - failed,
                    failed_team_count=failed,
                    started_at=STARTED,
                    completed_at=FINISHED,
                )
        finally:
            session.close()

    return _record


@pytest.fixture
def client(session_factory: Callable[[], Session]) -> TestClient:
    app = create_app()

    def override_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def seed_two_teams(seed: SeedFn, *, season: int = 2025) -> None:
    """Mariners average 9 hits, Twins 7; MLB across both is 8.00."""
    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME, season=season)
    seed([7] * 20, team_id=TWINS_ID, team_name=TWINS_NAME, season=season)


def hits_page(client: TestClient, *, season: int = 2025) -> str:
    response = client.get(f"/?team_id={MARINERS_ID}&season={season}")
    assert response.status_code == 200
    return response.text


# ------------------------------------------------------------------ COMPLETE


def test_complete_coverage_shows_the_mlb_comparison(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = hits_page(client)

    assert "vs MLB" in body
    assert "+1.00" in body
    assert "8.00 hits per game" in body
    assert LEAGUE_COMPARISON_UNAVAILABLE_NOTE not in body


def test_complete_coverage_draws_the_mlb_reference_line(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = hits_page(client)

    assert "MLB Average" in body
    assert "Team Season Average" in body


def test_a_team_below_mlb_reads_as_a_negative_difference(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = client.get(f"/?team_id={TWINS_ID}&season=2025").text
    assert "-1.00" in body


def test_the_page_does_not_call_complete_coverage_a_finished_season(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = hits_page(client)
    assert "season complete" not in body.lower()
    assert "currently stored" in body


def test_the_page_reads_the_difference_as_descriptive_context(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """A positive difference means more hits per game, and nothing more."""
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = hits_page(client)
    assert "averaged more hits per game than MLB" in body
    assert "not a measure of significance" in body


# ------------------------------------------- INCOMPLETE, RUNNING, and no record


def test_incomplete_coverage_withholds_the_mlb_average(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2, failed=1)
    body = hits_page(client)

    assert LEAGUE_COMPARISON_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "hits per game across" not in body


def test_incomplete_coverage_still_renders_the_team_chart(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2, failed=1)
    body = hits_page(client)

    assert "Game Hits" in body
    assert "15-Game Average" in body
    assert "Team Season Average" in body
    assert "Season Avg" in body


def test_running_coverage_behaves_like_incomplete(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """A run that never finished leaves coverage unknown, so it is not trusted."""
    seed_two_teams(seed)
    record_coverage(teams=2, finished=False)
    body = hits_page(client)

    assert LEAGUE_COMPARISON_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "Game Hits" in body


def test_no_coverage_record_behaves_like_incomplete(
    client: TestClient, seed: SeedFn
) -> None:
    seed_two_teams(seed)
    body = hits_page(client)

    assert LEAGUE_COMPARISON_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "Game Hits" in body


def test_coverage_for_another_season_does_not_unlock_this_one(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed, season=2025)
    seed([9] * 5, season=2026)
    record_coverage(season=2025, teams=2)

    assert LEAGUE_COMPARISON_UNAVAILABLE_NOTE in hits_page(client, season=2026)
    assert LEAGUE_COMPARISON_UNAVAILABLE_NOTE not in hits_page(client, season=2025)


def test_the_unavailable_card_shows_a_dash_not_a_number(
    client: TestClient, seed: SeedFn
) -> None:
    seed_two_teams(seed)
    body = hits_page(client)
    assert "Comparison unavailable" in body


# ----------------------------------------------------- in-progress 2026 season


def test_complete_coverage_of_a_partial_season_still_compares(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Far fewer rows than a full season, and the comparison is still allowed.

    Coverage says every discovered team was refreshed. Row count is never the
    completeness rule, so 40 team-game records qualify exactly as 4,860 would.
    """
    seed([10] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME, season=2026)
    seed([6] * 20, team_id=TWINS_ID, team_name=TWINS_NAME, season=2026)
    record_coverage(season=2026, teams=2)

    body = hits_page(client, season=2026)
    assert "MLB Average" in body
    assert "8.00 hits per game" in body
    assert "+2.00" in body
    assert "40 team-game records" in body


def test_unequal_game_counts_are_weighted_on_the_page(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Mariners 10 hits over 20 games, Twins 4 hits in 1: MLB is 204/21, not 7.

    The unweighted mean of the two club averages would be 7.00, which would
    show a +3.00 difference. The game-weighted answer is about 9.71.
    """
    seed([10] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME, season=2026)
    seed([4], team_id=TWINS_ID, team_name=TWINS_NAME, season=2026)
    record_coverage(season=2026, teams=2)

    body = hits_page(client, season=2026)
    assert "9.71 hits per game" in body
    assert "+0.29" in body
    assert "+3.00" not in body


# ------------------------------------------------------------------ no MLB calls


def test_the_comparison_never_reaches_the_mlb_api(
    client: TestClient,
    seed: SeedFn,
    record_coverage: CoverageFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    seed_two_teams(seed)
    record_coverage(teams=2)
    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("mlbstatsapi.Mlb.__init__", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)
    monkeypatch.setattr("app.services.league_teams.discover_mlb_teams", fail)
    monkeypatch.setattr(
        "app.services.league_season_ingestion.ingest_league_season", fail
    )

    assert "MLB Average" in hits_page(client)


def test_the_strikeouts_page_is_unaffected_by_league_coverage(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Milestone 5 adds no league context to batting strikeouts."""
    seed([9] * 20, strikeouts=[8] * 20)
    record_coverage(teams=1)
    response = client.get(f"/strikeouts?team_id={MARINERS_ID}&season=2025")
    assert response.status_code == 200

    body = response.text
    assert "Game Strikeouts" in body
    assert "vs Prior 15" in body
    assert "MLB Average" not in body
    assert "vs MLB" not in body
