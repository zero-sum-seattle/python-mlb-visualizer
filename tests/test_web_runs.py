"""Tests for the /runs page and its place in the metric navigation.

Shares the database-backed client fixtures with ``test_web`` so every metric
page is exercised against the same persisted rows. Everything here is offline.
"""

import re
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.database.repositories import upsert_team_season
from app.main import create_app
from app.web.dependencies import get_db_session
from tests.factories import make_season

SeedFn = Callable[..., None]

MARINERS = 136
SEASON = 2025


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
    def _seed(**kwargs: object) -> None:
        session = session_factory()
        try:
            upsert_team_season(session, lines=make_season(**kwargs))
            session.commit()
        finally:
            session.close()

    return _seed


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


def seed_with_runs(seed: SeedFn, values: list[int], **kwargs: object) -> None:
    seed(hits=[8] * len(values), runs=values, **kwargs)


def prose(body: str) -> str:
    """Collapse whitespace so a sentence wrapped in the template still matches."""
    return re.sub(r"\s+", " ", body)


# --- the page renders persisted run data --------------------------------------


def test_runs_page_renders_with_data(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [5, 3, 7])
    response = client.get("/runs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_runs_page_uses_the_precise_title(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [5, 3, 7])
    assert "Team Run Scoring Trends" in client.get("/runs").text


def test_runs_page_shows_the_subtitle(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [5, 3, 7])
    body = prose(client.get("/runs").text)
    assert "runs a team is scoring per game" in body


def test_chart_heading_names_the_team_and_runs(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7])
    assert "Seattle Mariners — Runs Scored per Game" in client.get("/runs").text


def test_chart_heading_uses_the_stored_team_name(
    client: TestClient, seed: SeedFn
) -> None:
    """Nothing about Seattle is hardcoded into the chart title."""
    seed_with_runs(seed, [4, 4], team_id=112, team_name="Chicago Cubs")
    body = client.get("/runs?team_id=112&season=2025").text
    assert "Chicago Cubs — Runs Scored per Game" in body


def test_runs_chart_div_is_rendered(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [5, 3, 7])
    assert "team-runs-chart" in client.get("/runs").text


def test_the_page_says_runs_scored_not_runs_allowed(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7])
    body = prose(client.get("/runs").text)
    assert "Runs Scored per Game" in body
    assert "runs scored, not runs allowed" in body
    assert "run differential" in body


def test_the_page_charts_runs_and_not_hits(client: TestClient, seed: SeedFn) -> None:
    """The two columns live on the same stored row; the page must read runs."""
    seed(hits=[12, 12, 12], runs=[1, 2, 3])
    body = client.get("/runs?window=5").text
    assert "Seattle Mariners — Runs Scored per Game" in body
    # Season average is 2.00 runs, never 12.00 hits.
    assert "2.00" in body
    assert "12.00" not in body


def test_rows_with_unknown_strikeouts_still_chart_runs(
    client: TestClient, seed: SeedFn
) -> None:
    """Runs are required on every stored row, so legacy rows are fine here."""
    seed(hits=[8, 9, 10], runs=[4, 2, 6])
    response = client.get("/runs")
    assert response.status_code == 200
    assert "team-runs-chart" in response.text
    # The same rows still send the strikeouts page to its backfill state.
    assert client.get("/strikeouts").status_code == 409


# --- selection ----------------------------------------------------------------


def test_selected_team_and_season_are_honoured(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [2, 2], team_id=112, team_name="Chicago Cubs")
    seed_with_runs(seed, [5] * 5)
    body = client.get(f"/runs?team_id={MARINERS}&season={SEASON}").text
    assert "Seattle Mariners — Runs Scored per Game" in body
    assert "2025 regular season" in body


def test_the_season_selector_switches_stored_seasons(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5] * 5, season=2025)
    seed_with_runs(seed, [3] * 5, season=2026)
    assert "2026 regular season" in client.get("/runs?team_id=136&season=2026").text
    assert "2025 regular season" in client.get("/runs?team_id=136&season=2025").text


def test_selected_window_changes_the_rolling_label(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4] * 20)
    body = client.get("/runs?team_id=136&season=2025&window=5").text
    assert "5-Game Average" in body


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_every_supported_window_is_accepted(
    client: TestClient, seed: SeedFn, window: int
) -> None:
    seed_with_runs(seed, [4] * 40)
    response = client.get(f"/runs?window={window}")
    assert response.status_code == 200
    assert f"{window}-Game Average" in response.text


def test_default_rolling_window_is_fifteen(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [4] * 20)
    assert "15-Game Average" in client.get("/runs").text


def test_query_parameters_survive_in_the_form_selection(
    client: TestClient, seed: SeedFn
) -> None:
    """A shared /runs URL comes back with the same selection applied."""
    seed_with_runs(seed, [4] * 20, team_id=112, team_name="Chicago Cubs")
    body = client.get("/runs?team_id=112&season=2025&window=30").text
    assert '<option value="112" selected>Chicago Cubs</option>' in body
    assert '<option value="2025" selected>2025</option>' in body
    assert '<option value="30" selected>30 Games</option>' in body


def test_the_selector_form_posts_back_to_runs(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [4, 4])
    assert 'action="/runs"' in client.get("/runs").text


# --- empty, missing, and invalid states ---------------------------------------


def test_empty_database_explains_how_to_import(client: TestClient) -> None:
    response = client.get("/runs")
    assert response.status_code == 200
    assert "No team data has been imported yet" in response.text
    assert "scripts/import_team_season.py" in response.text
    assert "team-runs-chart" not in response.text


def test_unknown_team_follows_the_existing_not_found_contract(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4, 4])
    response = client.get("/runs?team_id=999")
    assert response.status_code == 404
    assert "No games are stored for team id 999" in response.text


def test_unknown_season_follows_the_existing_not_found_contract(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4, 4])
    response = client.get(f"/runs?team_id={MARINERS}&season=1999")
    assert response.status_code == 404
    assert "No 1999 games are stored" in response.text


def test_not_found_state_still_offers_the_selectors(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4, 4])
    body = client.get("/runs?team_id=999").text
    assert 'id="team_id"' in body
    assert 'action="/runs"' in body


def test_invalid_window_is_rejected_readably(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [4, 4])
    response = client.get(
        "/runs?window=7", headers={"accept": "text/html,application/xhtml+xml"}
    )
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_a_non_numeric_team_id_is_rejected_readably(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4, 4])
    response = client.get(
        "/runs?team_id=seattle", headers={"accept": "text/html,application/xhtml+xml"}
    )
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_missing_schema_points_at_the_migration_command(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    factory = build_session_factory(engine)
    app = create_app()

    def override_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    try:
        response = TestClient(app).get("/runs")
        assert response.status_code == 503
        assert "poetry run alembic upgrade head" in response.text
        assert "Traceback" not in response.text
    finally:
        engine.dispose()


# --- summary cards and explanation --------------------------------------------


def test_summary_cards_are_rendered(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [5, 3, 7, 1])
    body = client.get("/runs?window=5").text
    for label in ("Recent 5-Game Avg", "Season Avg", "vs MLB", "Games Played"):
        assert label in body


def test_summary_cards_describe_stored_completed_games(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7, 1])
    body = prose(client.get("/runs").text)
    assert "Completed Games" in body
    assert "completed games currently stored" in body


def test_games_played_counts_the_stored_games(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [4] * 17)
    body = client.get("/runs").text
    assert "Games Played" in body
    assert ">17<" in body


def test_without_league_coverage_the_mlb_card_is_unavailable(
    client: TestClient, seed: SeedFn
) -> None:
    """Nothing here records league coverage, so the card must read a dash."""
    seed_with_runs(seed, [5, 3, 7])
    body = client.get("/runs?window=30").text
    assert "vs MLB" in body
    assert "Comparison unavailable" in body
    assert "+0.00" not in body


def test_the_explanation_describes_the_rolling_average(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7])
    body = prose(client.get("/runs?window=5").text)
    assert "5-game average covers that game and the 4 games before it" in body
    assert "recent scoring trend" in body
    assert "early-season points use every game played so far" in body


def test_the_explanation_does_not_claim_a_complete_season(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7])
    body = prose(client.get("/runs").text)
    assert "completed games currently stored for this season" in body


def test_the_page_uses_the_same_layout_regions_as_the_other_pages(
    client: TestClient, seed: SeedFn
) -> None:
    """Every metric page is built from the same shell, cards, and panels."""
    seed_with_runs(seed, [5, 3, 7])
    body = client.get("/runs").text
    for region in (
        'class="site-header"',
        'class="shell page"',
        'class="controls card"',
        'class="card chart-card"',
        'class="summary"',
        'class="about"',
        'class="site-footer"',
    ):
        assert region in body


def test_the_footer_reports_the_date_the_data_runs_through(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7])
    assert "Data through March 29, 2025" in client.get("/runs").text


# --- navigation ----------------------------------------------------------------


def test_every_page_links_to_runs(client: TestClient, seed: SeedFn) -> None:
    seed_with_runs(seed, [5, 3, 7])
    for path in ("/", "/strikeouts", "/runs"):
        body = client.get(path).text
        assert 'href="/runs' in body
        assert ">Runs</a>" in body


def test_the_runs_page_links_back_to_the_other_pages(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7])
    body = client.get("/runs").text
    assert ">Hits</a>" in body
    assert "Batting Strikeouts</a>" in body


def test_navigation_marks_the_runs_page_as_current(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [5, 3, 7])
    body = client.get("/runs").text
    assert 'aria-current="page"' in body
    assert body.count('aria-current="page"') == 1


def test_navigation_preserves_the_selection_into_runs(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4] * 20)
    body = client.get("/?team_id=136&season=2025&window=30").text
    assert "/runs?team_id=136&amp;season=2025&amp;window=30" in body


def test_navigation_preserves_the_selection_out_of_runs(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4] * 20)
    body = client.get("/runs?team_id=136&season=2025&window=30").text
    assert 'href="/?team_id=136&amp;season=2025&amp;window=30"' in body
    assert 'href="/strikeouts?team_id=136&amp;season=2025&amp;window=30"' in body


def test_navigation_links_resolve_to_real_routes(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8] * 20, runs=[4] * 20, strikeouts=[9] * 20)
    query = "?team_id=136&season=2025&window=30"
    for path in ("/", "/strikeouts", "/runs"):
        assert client.get(f"{path}{query}").status_code == 200


def test_navigation_is_present_on_the_empty_state(client: TestClient) -> None:
    assert 'href="/runs' in client.get("/runs").text


def test_navigation_is_present_on_the_not_found_state(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_runs(seed, [4, 4])
    assert 'href="/runs' in client.get("/runs?team_id=999").text


# --- the other pages are unchanged ---------------------------------------------


def test_the_hits_page_is_unchanged(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[8, 9, 10], runs=[4, 2, 6], strikeouts=[9, 8, 7])
    response = client.get("/?team_id=136&season=2025&window=15")
    assert response.status_code == 200
    assert "Seattle Mariners — Hits per Game" in response.text
    assert "Runs Scored per Game" not in response.text


def test_the_strikeouts_page_is_unchanged(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[8, 9, 10], runs=[4, 2, 6], strikeouts=[9, 8, 7])
    response = client.get("/strikeouts?team_id=136&season=2025&window=15")
    assert response.status_code == 200
    assert "Seattle Mariners — Batting Strikeouts per Game" in response.text
    assert "Runs Scored per Game" not in response.text


def test_health_is_unchanged(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --- the page stays database-backed --------------------------------------------


def test_the_runs_page_never_calls_the_mlb_api(
    client: TestClient, seed: SeedFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("mlbstatsapi.Mlb.__init__", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)
    monkeypatch.setattr("app.services.league_teams.discover_mlb_teams", fail)

    seed_with_runs(seed, [4] * 20)
    assert client.get("/runs?team_id=136&season=2025&window=15").status_code == 200
    assert client.get("/runs").status_code == 200


def test_the_empty_runs_page_does_not_try_to_import(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page tells the reader to run the import; it never runs it itself."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)

    assert client.get("/runs").status_code == 200
