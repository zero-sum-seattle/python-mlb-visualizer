"""Tests for the /baserunners page and navigation between the metric pages.

Shares the database-backed client fixtures with the other web tests so every
metric page is exercised against the same persisted rows.
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


def seed_with_baserunners(seed: SeedFn, values: list[int], **kwargs: object) -> None:
    """Seed a team-season whose baserunners equal ``values`` exactly.

    Hits and hit-by-pitch are pinned at 0, so baserunners = 0 + walks + 0.
    """
    length = len(values)
    seed(
        hits=[0] * length,
        base_on_balls=values,
        hit_by_pitch=[0] * length,
        **kwargs,
    )


def prose(body: str) -> str:
    """Collapse whitespace so a sentence wrapped in the template still matches."""
    return re.sub(r"\s+", " ", body)


# --- the page renders persisted baserunner data -------------------------------


def test_baserunners_page_renders_with_data(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    response = client.get("/baserunners")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_baserunners_page_uses_the_precise_title(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    assert "Team Baserunners Trends" in client.get("/baserunners").text


def test_baserunners_page_shows_the_subtitle(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = prose(client.get("/baserunners").text)
    assert "putting a runner on base per game" in body


def test_chart_heading_names_the_team_and_baserunners(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    assert "Seattle Mariners — Baserunners per Game" in client.get("/baserunners").text


def test_chart_heading_uses_the_stored_team_name(
    client: TestClient, seed: SeedFn
) -> None:
    """Nothing about Seattle is hardcoded into the chart title."""
    seed_with_baserunners(seed, [7, 7], team_id=112, team_name="Chicago Cubs")
    body = client.get("/baserunners?team_id=112&season=2025").text
    assert "Chicago Cubs — Baserunners per Game" in body


def test_baserunners_chart_div_is_rendered(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    assert "team-baserunners-chart" in client.get("/baserunners").text


# --- selection -----------------------------------------------------------------


def test_selected_team_and_season_are_honoured(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [4, 4], team_id=112, team_name="Chicago Cubs")
    seed_with_baserunners(seed, [9] * 5)
    body = client.get(f"/baserunners?team_id={MARINERS}&season={SEASON}").text
    assert "Seattle Mariners — Baserunners per Game" in body
    assert "2025 regular season" in body


def test_selected_window_changes_the_rolling_label(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [6] * 20)
    body = client.get("/baserunners?team_id=136&season=2025&window=5").text
    assert "5-Game Average" in body


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_every_supported_window_is_accepted(
    client: TestClient, seed: SeedFn, window: int
) -> None:
    seed_with_baserunners(seed, [6] * 40)
    response = client.get(f"/baserunners?window={window}")
    assert response.status_code == 200


def test_unknown_team_follows_the_existing_not_found_contract(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [6, 6])
    response = client.get("/baserunners?team_id=999")
    assert response.status_code == 404
    assert "No games are stored for team id 999" in response.text


def test_unknown_season_follows_the_existing_not_found_contract(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [6, 6])
    response = client.get(f"/baserunners?team_id={MARINERS}&season=1999")
    assert response.status_code == 404
    assert "No 1999 games are stored" in response.text


def test_invalid_window_is_rejected_readably(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [6, 6])
    response = client.get(
        "/baserunners?window=7",
        headers={"accept": "text/html,application/xhtml+xml"},
    )
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_empty_database_explains_how_to_import(client: TestClient) -> None:
    response = client.get("/baserunners")
    assert response.status_code == 200
    assert "No team data has been imported yet" in response.text
    assert "scripts/import_team_season.py" in response.text


# --- legacy rows with unknown walk / hit-by-pitch totals ------------------------


def test_legacy_null_rows_do_not_render_a_chart(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9, 10])
    response = client.get("/baserunners")
    assert "team-baserunners-chart" not in response.text


def test_legacy_null_rows_show_re_import_guidance(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9, 10])
    body = prose(client.get("/baserunners").text)
    assert "needs to be re-imported" in body
    assert "imported before those two columns were persisted" in body


def test_re_import_guidance_uses_the_selected_team_and_season(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9], team_id=112, team_name="Chicago Cubs", season=2024)
    body = client.get("/baserunners?team_id=112&season=2024").text
    assert (
        "poetry run python scripts/import_team_season.py "
        "--team-id 112 --season 2024" in body
    )


def test_re_import_guidance_counts_the_affected_games(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9, 10])
    body = prose(client.get("/baserunners").text)
    assert "3 of the 3 stored games" in body


def test_a_missing_hit_by_pitch_total_alone_still_triggers_guidance(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9, 10], base_on_balls=[3, 4, 5])
    body = client.get("/baserunners").text
    assert client.get("/baserunners").status_code == 409
    assert "team-baserunners-chart" not in body


def test_partially_backfilled_season_still_asks_for_a_re_import(
    client: TestClient, seed: SeedFn
) -> None:
    """One unknown game is enough; the rest must not be shown as the season."""
    seed(hits=[8] * 3, base_on_balls=[10, None, 12], hit_by_pitch=[0, 0, 0])
    body = prose(client.get("/baserunners").text)
    assert "1 of the 3 stored games" in body
    assert "team-baserunners-chart" not in body


def test_legacy_null_rows_never_chart_a_zero(client: TestClient, seed: SeedFn) -> None:
    seed(hits=[8, 9, 10])
    body = client.get("/baserunners").text
    assert "Season Avg" not in body
    assert "0.00" not in body


def test_legacy_state_uses_a_distinct_status_code(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9, 10])
    assert client.get("/baserunners").status_code == 409


def test_legacy_null_rows_do_not_break_the_runs_page(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9, 10])
    response = client.get("/runs")
    assert response.status_code == 200
    assert "Seattle Mariners — Runs Scored per Game" in response.text


def test_backfilled_rows_make_the_chart_appear(
    client: TestClient, seed: SeedFn, session_factory: Callable[[], Session]
) -> None:
    """The regression path: legacy guidance, then a re-import, then a chart."""
    seed(hits=[8, 9, 10])
    assert client.get("/baserunners").status_code == 409

    session = session_factory()
    try:
        upsert_team_season(
            session,
            lines=make_season(
                hits=[8, 9, 10],
                base_on_balls=[3, 4, 5],
                hit_by_pitch=[0, 1, 0],
            ),
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/baserunners")
    assert response.status_code == 200
    assert "team-baserunners-chart" in response.text
    assert client.get("/runs").status_code == 200


# --- summary cards and explanation ----------------------------------------------


def test_summary_cards_are_rendered(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [10, 8, 12, 6])
    body = client.get("/baserunners?window=5").text
    for label in ("Recent 5-Game Avg", "Season Avg", "vs MLB", "Games Played"):
        assert label in body


def test_summary_cards_describe_stored_completed_games(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12, 6])
    body = prose(client.get("/baserunners").text)
    assert "Completed Games" in body
    assert "completed games currently stored" in body


def test_without_league_coverage_the_mlb_card_is_unavailable(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = client.get("/baserunners?window=30").text
    assert "vs MLB" in body
    assert "Comparison unavailable" in body


def test_the_page_explains_the_formula(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = prose(client.get("/baserunners").text)
    assert "hits plus walks plus hit-by-pitch" in body
    assert "Reached-on-error" in body


def test_the_page_says_obp_is_deferred(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = prose(client.get("/baserunners").text)
    assert "OBP" in body
    assert "Plate appearances are not currently persisted" in body


def test_the_page_does_not_call_more_baserunners_good_or_bad(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = prose(client.get("/baserunners").text)
    assert "not automatically better than a" in body


def test_the_page_uses_the_same_layout_regions_as_the_other_pages(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = client.get("/baserunners").text
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


# --- navigation ------------------------------------------------------------------


def test_every_page_links_to_baserunners(client: TestClient, seed: SeedFn) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    for path in ("/", "/strikeouts", "/runs"):
        body = client.get(path).text
        assert 'href="/baserunners' in body
        assert ">Baserunners</a>" in body


def test_the_baserunners_page_links_back_to_the_other_pages(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = client.get("/baserunners").text
    assert ">Hits</a>" in body
    assert "Batting Strikeouts</a>" in body
    assert ">Runs</a>" in body
    assert ">Comparison</a>" in body


def test_navigation_marks_the_baserunners_page_as_current(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [10, 8, 12])
    body = client.get("/baserunners").text
    assert 'aria-current="page"' in body
    assert body.count('aria-current="page"') == 1


def test_navigation_preserves_the_selection_into_baserunners(
    client: TestClient, seed: SeedFn
) -> None:
    seed_with_baserunners(seed, [6] * 20)
    body = client.get("/runs?team_id=136&season=2025&window=30").text
    assert 'href="/baserunners?team_id=136&amp;season=2025&amp;window=30"' in body


def test_navigation_is_present_on_the_legacy_state(
    client: TestClient, seed: SeedFn
) -> None:
    seed(hits=[8, 9, 10])
    assert 'href="/' in client.get("/baserunners").text


# --- the page stays database-backed -----------------------------------------------


def test_the_baserunners_page_never_calls_the_mlb_api(
    client: TestClient, seed: SeedFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("mlbstatsapi.Mlb.__init__", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)

    seed_with_baserunners(seed, [6] * 20)
    assert (
        client.get("/baserunners?team_id=136&season=2025&window=15").status_code == 200
    )
    assert client.get("/baserunners").status_code == 200


def test_the_legacy_state_does_not_try_to_import(
    client: TestClient, seed: SeedFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page tells the reader to run the import; it never runs it itself."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)

    seed(hits=[8, 9, 10])
    assert client.get("/baserunners").status_code == 409


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
        response = TestClient(app).get("/baserunners")
        assert response.status_code == 503
        assert "poetry run alembic upgrade head" in response.text
        assert "Traceback" not in response.text
    finally:
        engine.dispose()
