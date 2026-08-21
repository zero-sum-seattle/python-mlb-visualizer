"""Tests for how the runs page gates MLB run-scoring context.

Every case here is offline. The database is seeded directly and the recorded
coverage state is written directly, because coverage is what the page reads —
never a live MLB call, and never a row count.

Unlike batting strikeouts there is only one condition to satisfy: complete
league-season coverage. ``runs`` is required on every persisted team-game
record, so a covered season cannot be holding unknown run totals.
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
from app.web.formatting import LEAGUE_RUNS_UNAVAILABLE_NOTE
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
        runs: list[int],
        *,
        team_id: int = MARINERS_ID,
        team_name: str = MARINERS_NAME,
        season: int = 2025,
    ) -> None:
        lines = [
            line.model_copy(update={"game_pk": line.game_pk + team_id * 100_000})
            for line in make_season(
                hits=[8] * len(runs),
                runs=runs,
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
    """Mariners score 5 a game, Twins 3; MLB across both is 4.00."""
    seed([5] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME, season=season)
    seed([3] * 20, team_id=TWINS_ID, team_name=TWINS_NAME, season=season)


def runs_page(
    client: TestClient,
    *,
    team_id: int = MARINERS_ID,
    season: int = 2025,
    window: int | None = None,
) -> str:
    url = f"/runs?team_id={team_id}&season={season}"
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
    body = runs_page(client)

    assert "vs MLB" in body
    assert "+1.00" in body
    assert "scored 4.00 runs per game" in body
    assert LEAGUE_RUNS_UNAVAILABLE_NOTE not in body


def test_complete_coverage_draws_the_mlb_reference_line(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client)

    assert "MLB Average" in body
    assert "Team Season Average" in body
    assert "Game Runs" in body


def test_a_team_below_mlb_reads_as_a_negative_difference(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    assert "-1.00" in runs_page(client, team_id=TWINS_ID)


def test_the_page_keeps_the_existing_series_alongside_the_mlb_line(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client, window=10)

    assert "Game Runs" in body
    assert "10-Game Average" in body
    assert "Team Season Average" in body
    assert "MLB Average" in body


def test_the_page_does_not_call_complete_coverage_a_finished_season(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client)
    assert "season complete" not in body.lower()
    assert "currently stored" in body


def test_the_page_reads_the_difference_as_descriptive_context(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client)
    assert "scored more runs per game" in body
    assert "not a measure of significance" in body
    assert "no claim about why the two numbers differ" in body


def test_the_page_makes_no_ranking_or_adjustment_claim(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Rankings, percentiles, and park/opponent adjustments are out of scope."""
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client).lower()
    for claim in (
        "rank",
        "percentile",
        "park-adjusted",
        "park factor",
        "opponent-adjusted",
        "expected runs",
        "statistically significant",
    ):
        assert claim not in body
    # The page names run differential only to say it is not what is shown.
    assert "nothing here is a run differential" in " ".join(body.split())


def test_unequal_game_counts_are_weighted_on_the_page(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Mariners score 5 over 20 games, Twins 2 in 1: MLB is 102/21, not 3.50.

    The unweighted mean of the two club averages would be 3.50 and would show
    a +1.50 difference. The game-weighted answer is about 4.86.
    """
    seed([5] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME)
    seed([2], team_id=TWINS_ID, team_name=TWINS_NAME)
    record_coverage(teams=2)

    body = runs_page(client)
    assert "scored 4.86 runs per game" in body
    assert "+0.14" in body
    assert "+1.50" not in body


def test_the_note_names_the_records_and_teams_behind_the_average(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client)
    assert "40 team-game records" in body
    assert "covering 2 teams" in body
    assert "total runs divided by total team-game records" in body


def test_the_season_average_is_the_same_number_everywhere(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Card, reference line, and comparison all read one team average.

    The Mariners average 5.00, MLB 4.00, so the difference must read +1.00 and
    the Season Avg card must read 5.00. Any disagreement here means the page
    calculated the team average twice.
    """
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client)
    assert "5.00" in body
    assert "+1.00" in body


# ------------------------------------------- INCOMPLETE, RUNNING, and no record


def test_incomplete_coverage_withholds_the_mlb_average(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2, failed=1)
    body = runs_page(client)

    assert LEAGUE_RUNS_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "runs per game across" not in body


def test_incomplete_coverage_still_renders_the_team_chart(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2, failed=1)
    body = runs_page(client)

    assert "team-runs-chart" in body
    assert "Game Runs" in body
    assert "15-Game Average" in body
    assert "Team Season Average" in body
    assert "Season Avg" in body


def test_running_coverage_behaves_like_incomplete(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """A run that never finished leaves coverage unknown, so it is not trusted."""
    seed_two_teams(seed)
    record_coverage(teams=2, finished=False)
    body = runs_page(client)

    assert LEAGUE_RUNS_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "Game Runs" in body


def test_no_coverage_record_behaves_like_incomplete(
    client: TestClient, seed: SeedFn
) -> None:
    seed_two_teams(seed)
    body = runs_page(client)

    assert LEAGUE_RUNS_UNAVAILABLE_NOTE in body
    assert "MLB Average" not in body
    assert "Game Runs" in body


def test_the_unavailable_card_shows_a_dash_not_a_number(
    client: TestClient, seed: SeedFn
) -> None:
    seed_two_teams(seed)
    body = runs_page(client)
    assert "Comparison unavailable" in body
    assert "+0.00" not in body


def test_coverage_for_another_season_does_not_unlock_this_one(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed, season=2025)
    seed([5] * 5, season=2026)
    record_coverage(season=2025, teams=2)

    assert LEAGUE_RUNS_UNAVAILABLE_NOTE in runs_page(client, season=2026)
    assert LEAGUE_RUNS_UNAVAILABLE_NOTE not in runs_page(client, season=2025)


# ----------------------------------------------------- in-progress 2026 season


def test_complete_coverage_of_a_partial_season_still_compares(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Far fewer rows than a full season, and the comparison is still allowed.

    Coverage says every discovered team was refreshed. Row count is never the
    completeness rule, so 40 team-game records qualify exactly as 4,860 would.
    """
    seed([6] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME, season=2026)
    seed([2] * 20, team_id=TWINS_ID, team_name=TWINS_NAME, season=2026)
    record_coverage(season=2026, teams=2)

    body = runs_page(client, season=2026)
    assert "MLB Average" in body
    assert "scored 4.00 runs per game" in body
    assert "+2.00" in body
    assert "40 team-game records" in body


def test_an_in_progress_season_is_not_described_as_finished(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed([6] * 20, team_id=MARINERS_ID, team_name=MARINERS_NAME, season=2026)
    seed([2] * 20, team_id=TWINS_ID, team_name=TWINS_NAME, season=2026)
    record_coverage(season=2026, teams=2)

    body = runs_page(client, season=2026)
    assert "currently stored" in body
    assert "not that the season has finished being played" in body


# --------------------------------------------- the rest of the page is intact


def test_the_team_and_season_selectors_still_work(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    seed([4] * 10, season=2026)
    record_coverage(teams=2)

    assert "Minnesota Twins — Runs Scored per Game" in runs_page(
        client, team_id=TWINS_ID
    )
    assert "2026 regular season" in runs_page(client, season=2026)


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_the_window_selector_still_works_with_mlb_context(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn, window: int
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client, window=window)
    assert f"{window}-Game Average" in body
    assert "MLB Average" in body


def test_shareable_query_parameters_still_round_trip(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = runs_page(client, team_id=TWINS_ID, window=30)
    assert "Minnesota Twins — Runs Scored per Game" in body
    assert 'href="/?team_id=142&amp;season=2025&amp;window=30"' in body
    assert 'href="/strikeouts?team_id=142&amp;season=2025&amp;window=30"' in body


def test_a_missing_team_season_is_still_handled_safely(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """Complete coverage does not turn an unstored selection into an error."""
    seed_two_teams(seed)
    record_coverage(teams=2)
    response = client.get("/runs?team_id=999&season=2025")
    assert response.status_code == 404
    assert "No games are stored for team id 999" in response.text
    assert "Traceback" not in response.text


def test_the_hits_page_is_unchanged_by_the_runs_comparison(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_two_teams(seed)
    record_coverage(teams=2)
    body = client.get(f"/?team_id={MARINERS_ID}&season=2025").text
    assert "Seattle Mariners — Hits per Game" in body
    assert "8.00 hits per game" in body
    assert "Runs Scored per Game" not in body


def test_the_strikeouts_page_is_unchanged_by_the_runs_comparison(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    """These rows carry no strikeout totals, so that page keeps its own state."""
    seed_two_teams(seed)
    record_coverage(teams=2)
    response = client.get(f"/strikeouts?team_id={MARINERS_ID}&season=2025")
    assert response.status_code == 409
    assert "needs to be re-imported" in response.text
    assert "Runs Scored per Game" not in response.text


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

    assert "MLB Average" in runs_page(client)
