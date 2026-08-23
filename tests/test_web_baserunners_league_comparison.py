"""Tests for how the baserunners page gates MLB baserunners context.

Every case here is offline. The database is seeded directly and the recorded
coverage state is written directly, because coverage is what the page reads —
never a live MLB call, and never a row count.

Two things must both hold before an MLB Baserunners/Game is shown: complete
league-season coverage, and known walk and hit-by-pitch totals on every stored
record for the season. The second rule has no equivalent on the hits or runs
pages, because those fields were never missing there.

``seed`` pins hits and hit-by-pitch at 0, so a chosen list of baserunners
equals the walk total directly and every expected number below can be read
off the input.
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
from app.web.formatting import LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE
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
        baserunners: list[int | None],
        *,
        team_id: int = MARINERS_ID,
        team_name: str = MARINERS_NAME,
        season: int = 2025,
    ) -> None:
        length = len(baserunners)
        lines = [
            line.model_copy(update={"game_pk": line.game_pk + team_id * 100_000})
            for line in make_season(
                hits=[0] * length,
                base_on_balls=baserunners,
                hit_by_pitch=[0] * length,
                team_id=team_id,
                team_name=team_name,
                season=season,
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
    """Mariners put 9 baserunners a game, Twins 7; MLB across both is 8.00."""
    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME, season=season)
    seed([7] * 20, team_id=TWINS_ID, team_name=TWINS_NAME, season=season)


def baserunners_page(
    client: TestClient,
    *,
    team_id: int = MARINERS_ID,
    season: int = 2025,
    window: int | None = None,
) -> str:
    url = f"/baserunners?team_id={team_id}&season={season}"
    if window is not None:
        url = f"{url}&window={window}"
    response = client.get(url)
    assert response.status_code == 200
    return response.text


# ------------------------------------------------------------------ COMPLETE


def test_complete_coverage_shows_the_mlb_comparison(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = baserunners_page(client)

    assert "vs MLB" in body
    assert "+1.00" in body
    assert "put a runner on base 8.00 times per game" in body
    assert LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE not in body


def test_complete_coverage_draws_the_mlb_reference_line(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = baserunners_page(client)

    assert "MLB Average" in body
    assert "Team Season Average" in body
    assert "Game Baserunners" in body


def test_a_team_below_mlb_reads_as_a_negative_difference(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    assert "-1.00" in baserunners_page(client, team_id=TWINS_ID)


def test_the_page_keeps_the_existing_series_alongside_the_mlb_line(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = baserunners_page(client, window=10)

    assert "Game Baserunners" in body
    assert "10-Game Average" in body
    assert "Team Season Average" in body
    assert "MLB Average" in body


def test_the_page_does_not_call_complete_coverage_a_finished_season(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = baserunners_page(client)
    assert "season complete" not in body.lower()
    assert "currently stored" in body


def test_the_page_reads_the_difference_as_descriptive_context(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """More baserunners than MLB is neither good nor bad on this page."""
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = baserunners_page(client)
    assert "put runners on base more times per game" in body
    assert "not a measure of significance" in body


def test_unequal_game_counts_are_weighted_on_the_page(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Mariners 10 baserunners over 20 games, Twins 4 in 1: MLB is 204/21.

    The unweighted mean of the two club averages would be 7.00 and would show
    a +3.00 difference. The game-weighted answer is about 9.71.
    """
    seed([10] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([4], team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)

    body = baserunners_page(client)
    assert "put a runner on base 9.71 times per game" in body
    assert "+0.29" in body
    assert "+3.00" not in body


# ------------------------------------------- INCOMPLETE, RUNNING, and no record


def test_incomplete_coverage_withholds_the_mlb_average(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2, failed=1)
    body = baserunners_page(client)

    assert LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "times per game across" not in body


def test_incomplete_coverage_still_renders_the_team_chart(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2, failed=1)
    body = baserunners_page(client)

    assert "team-baserunners-chart" in body
    assert "Game Baserunners" in body
    assert "15-Game Average" in body
    assert "Team Season Average" in body
    assert "Season Avg" in body


def test_running_coverage_behaves_like_incomplete(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """A run that never finished leaves coverage unknown, so it is not trusted."""
    seed_two_teams(seed)
    record_coverage(teams=2, finished=False)
    body = baserunners_page(client)

    assert LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "Game Baserunners" in body


def test_no_coverage_record_behaves_like_incomplete(
    client: TestClient, seed: SeedFn
) -> None:
    seed_two_teams(seed)
    body = baserunners_page(client)

    assert LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "Game Baserunners" in body


def test_the_unavailable_card_shows_a_dash_not_a_number(
    client: TestClient, seed: SeedFn
) -> None:
    seed_two_teams(seed)
    body = baserunners_page(client)
    assert "Comparison unavailable" in body
    assert "+0.00" not in body


def test_coverage_for_another_season_does_not_unlock_this_one(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed, season=2025)
    seed([9] * 5, season=2026)
    record_coverage(season=2025, teams=2)

    assert LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE in baserunners_page(client, season=2026)
    assert LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE not in baserunners_page(
        client, season=2025
    )


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

    body = baserunners_page(client, season=2026)
    assert "MLB Average" in body
    assert "put a runner on base 8.00 times per game" in body
    assert "+2.00" in body
    assert "40 team-game records" in body


# --------------------------------- COMPLETE coverage over legacy null records


def test_legacy_null_league_rows_withhold_the_mlb_average(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Another club's rows predate baserunner components being stored: no MLB average.

    The selected team's own data is complete, so its page renders in full. The
    MLB-wide claim is the only thing withheld, because 20 of the 40 stored
    records have no known total.
    """
    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([None] * 20, team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)

    body = baserunners_page(client)
    assert "MLB Average" not in body
    assert "Comparison unavailable" in body
    assert "team-baserunners-chart" in body
    assert "Game Baserunners" in body


def test_legacy_null_league_rows_ask_for_a_backfill(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([None] * 20, team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)

    body = baserunners_page(client)
    assert "20 of the 40 team-game records stored for 2025" in body
    assert "import_league_season.py --season 2025" in body
    assert LEAGUE_BASERUNNERS_UNAVAILABLE_NOTE not in body


def test_a_single_legacy_null_league_row_is_enough(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """One unknown total and the remaining rows are not MLB overall."""
    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([7] * 19 + [None], team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)

    body = baserunners_page(client)
    assert "MLB Average" not in body
    assert "1 of the 40 team-game records" in body


def test_legacy_null_league_rows_never_average_the_known_subset(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """9.00 over the Mariners' rows alone would look like an MLB average."""
    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([None] * 20, team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)

    body = baserunners_page(client)
    assert "times per game across" not in body
    assert "+0.00" not in body


def test_backfilling_the_league_makes_the_comparison_appear(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """The regression path: backfill guidance, then a re-import, then MLB context."""
    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([None] * 20, team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)
    assert "MLB Average" not in baserunners_page(client)

    seed([7] * 20, team_id=TWINS_ID, team_name=TWINS_NAME)
    body = baserunners_page(client)
    assert "MLB Average" in body
    assert "+1.00" in body


def test_the_selected_team_keeps_its_own_re_import_guidance(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """A selected team with unknown totals is still the existing 409 state."""
    seed([None] * 3, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([7] * 20, team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)

    response = client.get(f"/baserunners?team_id={MARINERS_ID}&season=2025")
    assert response.status_code == 409
    body = response.text
    assert "needs to be re-imported" in body
    assert "import_team_season.py --team-id 136 --season 2025" in body
    assert "MLB Average" not in body


# ------------------------------------------------ the rest of the page is intact


def test_the_team_and_season_selectors_still_work(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    seed([5] * 10, season=2026)
    record_coverage(teams=2)

    assert "Minnesota Twins — Baserunners per Game" in baserunners_page(
        client, team_id=TWINS_ID
    )
    assert "2026 regular season" in baserunners_page(client, season=2026)


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_the_window_selector_still_works_with_mlb_context(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn, window: int
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = baserunners_page(client, window=window)
    assert f"{window}-Game Average" in body
    assert "MLB Average" in body


def test_shareable_query_parameters_still_round_trip(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = baserunners_page(client, team_id=TWINS_ID, window=30)
    assert "Minnesota Twins — Baserunners per Game" in body
    assert 'href="/?team_id=142&amp;season=2025&amp;window=30"' in body


def test_the_hits_page_is_unchanged_by_the_baserunners_comparison(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = client.get(f"/?team_id={MARINERS_ID}&season=2025").text
    assert "Seattle Mariners — Hits per Game" in body
    assert "Baserunners per Game" not in body


def test_health_is_unchanged(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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

    assert "MLB Average" in baserunners_page(client)


def test_the_backfill_state_does_not_try_to_import(
    client: TestClient,
    seed: SeedFn,
    record_coverage: CoverageFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page tells the reader to run the import; it never runs it itself."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    seed([9] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([None] * 20, team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)
    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("mlbstatsapi.Mlb.__init__", fail)
    monkeypatch.setattr("app.services.league_teams.discover_mlb_teams", fail)

    assert "import_league_season.py" in baserunners_page(client)
