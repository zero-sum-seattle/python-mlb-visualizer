"""Tests for the web application: routing, selection, and rendering."""

import json
import re
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.engine import build_engine, build_session_factory
from app.database.repositories import upsert_team_season
from app.main import create_app
from app.web.dependencies import DatabaseNotConfiguredError, get_db_session
from tests.conftest import run_alembic_upgrade
from tests.factories import make_season

BROWSER_HEADERS = {"accept": "text/html,application/xhtml+xml"}

SeedFn = Callable[..., None]

_CATALOG_PATTERN = re.compile(
    r'<script type="application/json" id="team-seasons-data">(.*?)</script>',
    re.DOTALL,
)


def embedded_team_seasons(body: str) -> Any:
    """Parse the team-season catalog the season selector script reads."""
    match = _CATALOG_PATTERN.search(body)
    assert match is not None, "the page did not embed a team-season catalog"
    return json.loads(match.group(1))


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


def test_index_returns_ok_with_data(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[8, 9, 10])
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_index_returns_ok_with_an_empty_database(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "No team data has been imported yet" in response.text


def test_empty_database_shows_the_import_command(client: TestClient) -> None:
    body = client.get("/").text
    assert "scripts/import_team_season.py" in body
    assert "--team-id 136" in body
    assert "--season 2025" in body


def test_empty_database_does_not_render_a_chart(client: TestClient) -> None:
    assert "team-hits-chart" not in client.get("/").text


def test_seattle_is_selected_by_default(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[4], team_id=112, team_name="Chicago Cubs")
    seed(hits=[9] * 5)
    body = client.get("/").text
    assert '<option value="136" selected>Seattle Mariners</option>' in body
    assert "Seattle Mariners — Hits per Game" in body


def test_first_team_alphabetically_is_used_without_seattle(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[4] * 3, team_id=147, team_name="New York Yankees")
    seed(hits=[6] * 3, team_id=112, team_name="Chicago Cubs")
    assert "Chicago Cubs — Hits per Game" in client.get("/").text


def test_most_recent_season_is_selected_by_default(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[3] * 4, season=2024)
    seed(hits=[7] * 4, season=2025)
    body = client.get("/").text
    assert "2025 regular season" in body


def test_default_rolling_window_is_fifteen(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[6] * 20)
    body = client.get("/").text
    assert "15-Game Average" in body
    assert "Recent 15-Game Avg" in body


def test_explicit_query_parameters_are_honoured(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[5] * 20, season=2024)
    seed(hits=[9] * 20)
    response = client.get("/?team_id=136&season=2024&window=5")
    assert response.status_code == 200
    assert "2024 regular season" in response.text
    assert "5-Game Average" in response.text


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_every_allowed_rolling_window_renders(
    client: TestClient, seed: SeedFn, window: int
) -> None:
    seed(hits=[7] * 70)
    response = client.get(f"/?window={window}")
    assert response.status_code == 200
    assert f"{window}-Game Average" in response.text
    assert f"Recent {window}-Game Avg" in response.text


def test_disallowed_rolling_window_is_rejected(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    response = client.get("/?window=7", headers=BROWSER_HEADERS)
    assert response.status_code == 422
    assert "Input should be 5, 10, 15 or 30" in response.text
    assert "Traceback" not in response.text


def test_disallowed_rolling_window_answers_api_clients_with_json(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    response = client.get("/?window=7", headers={"accept": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "window"]


@pytest.mark.parametrize("query", ["team_id=-1", "season=banana", "window=banana"])
def test_malformed_query_parameters_render_a_readable_page(
    client: TestClient, seed: SeedFn, query: str
) -> None:
    seed(hits=[7] * 20)
    response = client.get(f"/?{query}", headers=BROWSER_HEADERS)
    assert response.status_code == 422
    assert "That link has a value this page cannot use" in response.text
    assert "Traceback" not in response.text


def test_unknown_team_produces_a_useful_state(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[7] * 20)
    response = client.get("/?team_id=999")
    assert response.status_code == 404
    assert "That team-season is not stored locally" in response.text
    assert "No games are stored for team id 999" in response.text


def test_unknown_season_lists_the_stored_seasons(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    response = client.get("/?team_id=136&season=1998")
    assert response.status_code == 404
    assert "No 1998 games are stored for Seattle Mariners" in response.text
    assert "Stored seasons: 2025" in response.text


def test_not_found_state_still_offers_the_selectors(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    body = client.get("/?team_id=136&season=1998").text
    assert '<select id="team_id"' in body
    assert '<select id="window"' in body


def test_page_contains_the_team_name(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[7] * 20)
    assert "Seattle Mariners" in client.get("/").text


def test_page_contains_the_chart(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[7] * 20)
    body = client.get("/").text
    assert 'id="team-hits-chart"' in body
    assert "Plotly.newPlot" in body
    assert "/vendor/plotly.min.js" in body


def test_page_contains_all_three_chart_series(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[7] * 20)
    body = client.get("/").text
    assert "Game Hits" in body
    assert "15-Game Average" in body
    assert "Season Average" in body


def test_page_contains_the_summary_cards(client: TestClient, seed: SeedFn) -> None:
    """Milestone 5 replaced the prior-window card with the MLB comparison."""
    seed(hits=[6, 8, 10, 12] * 10)
    body = client.get("/").text
    assert "Recent 15-Game Avg" in body
    assert "Season Avg" in body
    assert "vs MLB" in body
    assert "Games Played" in body
    assert "40" in body


def test_page_contains_the_explanation_panel(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[7] * 20)
    body = client.get("/").text
    assert "About this chart" in body
    assert "rolling average smooths out" in body


def test_page_reports_the_date_the_data_runs_through(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7, 7, 7])
    body = client.get("/").text
    assert "Data through March 29, 2025" in body
    assert "MLB Stats API via python-mlb-statsapi" in body


def test_page_uses_the_product_header(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[7] * 20)
    body = client.get("/").text
    assert "MLB Stats Visualizer" in body
    assert "Team Hitting Trends" in body


def test_the_navigation_sits_inside_the_single_header_bar(
    client: TestClient, seed: SeedFn
) -> None:
    """One header bar carries both the branding and the metric navigation."""
    seed(hits=[7] * 20)
    body = client.get("/").text
    header = body[body.index('class="site-header"') : body.index("</header>")]
    assert "MLB Stats Visualizer" in header
    assert 'aria-label="Metrics"' in header
    assert "Batting Strikeouts</a>" in header


def test_the_selector_shows_the_club_logo_for_the_selected_team(
    client: TestClient, seed: SeedFn
) -> None:
    """Decorative only: the select still names the team in text."""
    seed(hits=[7] * 20)
    body = client.get("/?team_id=136&season=2025").text
    assert "team-logos/136.svg" in body
    assert 'alt=""' in body


def test_the_selectors_keep_their_labels_and_ids(
    client: TestClient, seed: SeedFn
) -> None:
    """The layout must not detach a label from the control it names."""
    seed(hits=[7] * 20)
    body = client.get("/").text
    for field in ("team_id", "season", "window"):
        assert f'for="{field}"' in body
        assert f'id="{field}"' in body


def test_the_footer_keeps_the_data_source_on_one_compact_line(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7, 7, 7])
    body = client.get("/").text
    footer = body[body.index('class="site-footer"') :]
    assert "Data through March 29, 2025" in footer
    assert "Source: MLB Stats API via python-mlb-statsapi" in footer


def test_query_parameters_survive_in_the_form_selection(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 40, season=2024)
    seed(hits=[7] * 40)
    body = client.get("/?team_id=136&season=2024&window=30").text
    assert "2024 regular season" in body
    assert "30-Game Average" in body
    assert '<option value="136" selected>' in body
    assert '<option value="2024" selected>' in body
    assert '<option value="30" selected>' in body


def test_page_embeds_the_seasons_of_every_team(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    seed(hits=[6] * 20, season=2024)
    seed(hits=[5] * 20, season=2024, team_id=112, team_name="Chicago Cubs")
    assert embedded_team_seasons(client.get("/").text) == {
        "112": [2024],
        "136": [2025, 2024],
    }


def test_embedded_catalog_is_present_on_the_not_found_page(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    response = client.get("/?team_id=136&season=1998")
    assert embedded_team_seasons(response.text) == {"136": [2025]}


def test_page_loads_the_season_selector_script(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    assert "/static/js/season-selector.js" in client.get("/").text


def test_empty_database_page_needs_no_season_selector_script(
    client: TestClient,
) -> None:
    body = client.get("/").text
    assert "season-selector.js" not in body
    assert "team-seasons-data" not in body


def test_season_selector_script_is_served(client: TestClient) -> None:
    response = client.get("/static/js/season-selector.js")
    assert response.status_code == 200
    assert "team-seasons-data" in response.text
    assert 'addEventListener("change"' in response.text


def test_a_season_from_another_team_is_still_rejected_by_the_server(
    client: TestClient, seed: SeedFn
) -> None:
    """The browser script is a convenience; the route stays defensive."""
    seed(hits=[7] * 20)
    seed(hits=[5] * 20, season=2024, team_id=112, team_name="Chicago Cubs")
    response = client.get("/?team_id=112&season=2025")
    assert response.status_code == 404
    assert "No 2025 games are stored for Chicago Cubs" in response.text
    assert "Stored seasons: 2024" in response.text


def test_each_team_can_be_loaded_at_its_own_season(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    seed(hits=[5] * 20, season=2024, team_id=112, team_name="Chicago Cubs")
    seattle = client.get("/?team_id=136&season=2025&window=5")
    chicago = client.get("/?team_id=112&season=2024&window=5")
    assert seattle.status_code == 200
    assert "Seattle Mariners — Hits per Game" in seattle.text
    assert chicago.status_code == 200
    assert "Chicago Cubs — Hits per Game" in chicago.text
    assert "2024 regular season" in chicago.text
    assert "5-Game Average" in chicago.text


def test_explanation_does_not_claim_a_complete_season(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[7] * 20)
    body = client.get("/").text
    assert "average for the whole season" not in body
    assert "completed games currently stored for" in body


def test_no_web_route_calls_the_mlb_api(
    client: TestClient, seed: SeedFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("mlbstatsapi.Mlb.__init__", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)

    seed(hits=[7] * 20)
    assert client.get("/?team_id=136&season=2025&window=15").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/static/js/season-selector.js").status_code == 200


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
        response = TestClient(app).get("/")
        assert response.status_code == 503
        assert "poetry run alembic upgrade head" in response.text
        assert "Traceback" not in response.text
    finally:
        engine.dispose()


def test_static_stylesheet_is_served(client: TestClient) -> None:
    response = client.get("/static/css/app.css")
    assert response.status_code == 200
    assert "site-header" in response.text


def test_plotly_bundle_is_served_locally(client: TestClient) -> None:
    response = client.get("/vendor/plotly.min.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_expected_json(client: TestClient) -> None:
    response = client.get("/health")
    assert response.json() == {
        "status": "ok",
        "app": "mlb-stats-visualizer",
    }


def test_lifespan_builds_the_session_factory_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "lifespan.db"
    run_alembic_upgrade(f"sqlite:///{db_path}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as lifespan_client:
            assert app.state.session_factory is not None
            assert lifespan_client.get("/").status_code == 200
        assert app.state.session_factory is None
    finally:
        get_settings.cache_clear()


def test_request_without_a_lifespan_reports_a_configuration_error() -> None:
    app = create_app()
    with pytest.raises(DatabaseNotConfiguredError):
        TestClient(app).get("/")


def test_no_web_route_triggers_league_ingestion(
    client: TestClient, seed: SeedFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """League-wide ingestion is an operational task, never a browser request."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("A web request must not start a league import")

    monkeypatch.setattr(
        "app.services.league_season_ingestion.ingest_league_season", fail
    )
    monkeypatch.setattr("app.services.league_teams.discover_mlb_teams", fail)

    seed(hits=[7] * 20, strikeouts=[8] * 20)
    assert client.get("/").status_code == 200
    assert client.get("/?team_id=136&season=2025&window=15").status_code == 200
    assert client.get("/strikeouts?team_id=136&season=2025").status_code == 200
    assert client.get("/health").status_code == 200
