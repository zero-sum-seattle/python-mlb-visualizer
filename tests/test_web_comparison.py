"""Offline HTTP tests for the normalized hitting-trends comparison page.

The comparison is allowed only when the persisted league-season coverage is
COMPLETE, every stored batting strikeout total is known, and both MLB per-game
baselines are non-zero. These tests seed the database directly so no browser
request has any reason to reach the MLB Stats API.
"""

import html
import json
import re
from collections.abc import Callable, Generator, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

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
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_season,
)

COMPARISON_PATH = "/comparison"
COMPARISON_CHART_DIV_ID = "team-hitting-comparison-chart"
HITS_INDEX_TRACE_NAME = "Hits Index"
STRIKEOUTS_INDEX_TRACE_NAME = "Batting Strikeout Index"
BASELINE_TRACE_NAME = "Baseline (100)"

STARTED = datetime(2026, 3, 1, 12, 0, 0)
FINISHED = datetime(2026, 3, 1, 12, 30, 0)

SeedFn = Callable[..., None]
CoverageFn = Callable[..., None]

_SUMMARY_CARD_PATTERN = re.compile(
    r'<article[^>]*class="[^"]*\bsummary-card\b[^"]*"[^>]*>(.*?)</article>',
    re.DOTALL,
)


@pytest.fixture
def session_factory(
    migrated_db_path: Path,
) -> Generator[Callable[[], Session], None, None]:
    engine = build_engine(f"sqlite:///{migrated_db_path}")
    factory = build_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def seed(session_factory: Callable[[], Session]) -> SeedFn:
    """Persist a team-season whose batting fields are chosen by the test."""

    def _seed(
        hits: list[int],
        *,
        strikeouts: list[int | None],
        team_id: int = MARINERS_ID,
        team_name: str = MARINERS_NAME,
        season: int = 2025,
    ) -> None:
        # Keep fixture game ids distinct across clubs. Real opponents normally
        # share a game_pk, but these compact league fixtures are not schedules.
        lines = [
            line.model_copy(update={"game_pk": line.game_pk + team_id * 100_000})
            for line in make_season(
                hits,
                strikeouts=strikeouts,
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
    """Record COMPLETE, INCOMPLETE, or RUNNING league-season coverage."""

    def _record(
        *,
        season: int = 2025,
        teams: int = 2,
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
def client(
    session_factory: Callable[[], Session],
) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


def seed_exact_comparison(seed: SeedFn, record_coverage: CoverageFn) -> None:
    """Seed MLB baselines of exactly 8 Hits/Game and 10 batting K/Game.

    Seattle's five-game totals are 56 hits and 40 strikeouts. Minnesota adds
    24 hits and 60 strikeouts, so the ten persisted team-game records total 80
    hits and 100 strikeouts. The deliberately small row count also demonstrates
    that COMPLETE coverage, rather than an inferred full-season row count, is
    what authorizes the comparison.
    """

    seed([8, 16, 8, 16, 8], strikeouts=[10, 5, 10, 5, 10])
    seed(
        [4, 5, 5, 5, 5],
        strikeouts=[12, 12, 12, 12, 12],
        team_id=TWINS_ID,
        team_name=TWINS_NAME,
    )
    record_coverage(teams=2)


def comparison_response(
    client: TestClient,
    *,
    team_id: int = MARINERS_ID,
    season: int = 2025,
    window: int = 5,
):
    return client.get(
        f"{COMPARISON_PATH}?team_id={team_id}&season={season}&window={window}"
    )


def visible_text(markup: str) -> str:
    """Return collapsed visible text for prose assertions."""

    without_tags = re.sub(r"<[^>]+>", " ", markup)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def plotly_traces(body: str) -> list[dict[str, Any]]:
    """Decode the data argument from the page's Plotly.newPlot call."""

    marker = "Plotly.newPlot("
    call_start = body.index(marker) + len(marker)
    payload = body[call_start:].lstrip()
    decoder = json.JSONDecoder()

    div_id, offset = decoder.raw_decode(payload)
    assert div_id == COMPARISON_CHART_DIV_ID
    payload = payload[offset:].lstrip()
    assert payload.startswith(",")

    traces, _ = decoder.raw_decode(payload[1:].lstrip())
    assert isinstance(traces, list)
    return traces


def summary_card_values(body: str) -> dict[str, str]:
    """Map each rendered summary-card label to its displayed value."""

    cards: dict[str, str] = {}
    for block in _SUMMARY_CARD_PATTERN.findall(body):
        label_match = re.search(
            r'<p[^>]*class="summary-card__label"[^>]*>(.*?)</p>',
            block,
            re.DOTALL,
        )
        value_match = re.search(
            r'<p[^>]*class="summary-card__value"[^>]*>(.*?)</p>',
            block,
            re.DOTALL,
        )
        assert label_match is not None
        assert value_match is not None
        cards[visible_text(label_match.group(1))] = visible_text(value_match.group(1))
    return cards


def displayed_number(value: str) -> float:
    """Parse a signed, presentation-formatted summary-card number."""

    return float(value.replace(",", "").replace("+", "").replace("\N{MINUS SIGN}", "-"))


def assert_comparison_unavailable(response) -> None:
    """Assert the shared functional state used when honest indexes are impossible."""

    assert response.status_code == 200
    body = response.text
    assert "Normalized comparison unavailable" in body
    assert COMPARISON_CHART_DIV_ID not in body
    assert "Plotly.newPlot" not in body
    for label in (
        "Recent Hits Index",
        "Recent K Index",
        "Trend Gap",
        "Games Played",
    ):
        assert label not in body


# --- COMPLETE coverage: exact analytics and rendered contract -----------------


def test_complete_coverage_renders_exact_rolling_indexes_and_four_summary_cards(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_exact_comparison(seed, record_coverage)

    response = comparison_response(client)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Seattle Mariners — Hits vs Batting Strikeouts" in body

    traces = plotly_traces(body)
    assert [trace["name"] for trace in traces] == [
        HITS_INDEX_TRACE_NAME,
        STRIKEOUTS_INDEX_TRACE_NAME,
        BASELINE_TRACE_NAME,
    ]
    assert traces[0]["x"] == [1, 2, 3, 4, 5]
    assert traces[1]["x"] == [1, 2, 3, 4, 5]
    assert traces[0]["y"] == pytest.approx(
        [100.0, 150.0, 133.33333333333334, 150.0, 140.0]
    )
    assert traces[1]["y"] == pytest.approx([100.0, 75.0, 83.33333333333334, 75.0, 80.0])
    assert traces[2]["x"] == [1, 5]
    assert traces[2]["y"] == pytest.approx([100.0, 100.0])

    cards = summary_card_values(body)
    assert set(cards) == {
        "Recent Hits Index",
        "Recent K Index",
        "Trend Gap",
        "Games Played",
    }
    assert displayed_number(cards["Recent Hits Index"]) == pytest.approx(140.0)
    assert displayed_number(cards["Recent K Index"]) == pytest.approx(80.0)
    assert displayed_number(cards["Trend Gap"]) == pytest.approx(60.0)
    assert displayed_number(cards["Games Played"]) == pytest.approx(5.0)


def test_page_explains_both_formulas_the_baseline_and_trend_gap(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_exact_comparison(seed, record_coverage)
    body = visible_text(comparison_response(client).text)

    assert "Team Hitting Trends Comparison" in body
    assert (
        "Hits Index is rolling team Hits/Game divided by MLB Hits/Game, times 100"
        in body
    )
    assert (
        "Batting Strikeout Index applies the same calculation to batting K/Game" in body
    )
    assert "100, meaning MLB average for that metric" in body
    assert "Above 100 means above the MLB average, not automatically better" in body
    assert "Trend Gap is simply the recent Hits Index minus the recent K Index" in body
    assert "not a validated overall offensive-performance statistic" in body
    assert "descriptive only" in body


def test_page_uses_local_plotly_and_the_required_axes_and_layout_regions(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_exact_comparison(seed, record_coverage)
    body = comparison_response(client).text

    assert f'id="{COMPARISON_CHART_DIV_ID}"' in body
    assert '<script src="/vendor/plotly.min.js"></script>' in body
    assert "cdn.plot.ly" not in body
    assert "Season Game Number" in body
    assert "Normalized Index (MLB Avg = 100)" in body
    assert "About this chart" in body
    for region in (
        'class="site-header"',
        'class="shell page"',
        'class="controls card"',
        'class="card chart-card chart-card--comparison"',
        'class="summary summary--comparison"',
        'class="about"',
        'class="site-footer"',
    ):
        assert region in body


def test_comparison_page_does_not_add_dead_mockup_controls(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed_exact_comparison(seed, record_coverage)
    body = visible_text(comparison_response(client).text)

    for dead_control in ("7D", "30D", "60D", "Export", "Players"):
        assert dead_control not in body


# --- coverage and data-integrity gates ----------------------------------------


@pytest.mark.parametrize(
    ("failed", "finished"),
    [
        pytest.param(1, True, id="incomplete"),
        pytest.param(0, False, id="running"),
    ],
)
def test_incomplete_or_running_coverage_withholds_all_normalized_values(
    client: TestClient,
    seed: SeedFn,
    record_coverage: CoverageFn,
    failed: int,
    finished: bool,
) -> None:
    seed([8] * 5, strikeouts=[10] * 5)
    seed(
        [8] * 5,
        strikeouts=[10] * 5,
        team_id=TWINS_ID,
        team_name=TWINS_NAME,
    )
    record_coverage(teams=2, failed=failed, finished=finished)

    response = comparison_response(client)
    assert_comparison_unavailable(response)
    assert (
        "latest league-season import must have complete coverage"
        in visible_text(response.text).lower()
    )


def test_no_coverage_record_withholds_all_normalized_values(
    client: TestClient, seed: SeedFn
) -> None:
    seed([8] * 5, strikeouts=[10] * 5)
    seed(
        [8] * 5,
        strikeouts=[10] * 5,
        team_id=TWINS_ID,
        team_name=TWINS_NAME,
    )

    response = comparison_response(client)
    assert_comparison_unavailable(response)
    assert (
        "latest league-season import must have complete coverage"
        in visible_text(response.text).lower()
    )


def test_complete_coverage_with_a_null_league_strikeout_asks_for_league_reimport(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed([8] * 5, strikeouts=[10] * 5)
    seed(
        [8] * 5,
        strikeouts=[10, 10, 10, 10, None],
        team_id=TWINS_ID,
        team_name=TWINS_NAME,
    )
    record_coverage(teams=2)

    response = comparison_response(client)
    assert_comparison_unavailable(response)
    body = visible_text(response.text)
    assert "re-import" in body.lower()
    assert "import_league_season.py --season 2025" in body


def test_selected_team_with_a_null_strikeout_asks_for_team_reimport(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    seed([8] * 5, strikeouts=[10, 10, None, 10, 10])
    seed(
        [8] * 5,
        strikeouts=[10] * 5,
        team_id=TWINS_ID,
        team_name=TWINS_NAME,
    )
    record_coverage(teams=2)

    response = comparison_response(client)
    assert_comparison_unavailable(response)
    body = visible_text(response.text)
    assert "re-import" in body.lower()
    assert "import_team_season.py --team-id 136 --season 2025" in body
    assert "import_league_season.py" not in body


@pytest.mark.parametrize(
    ("mariners_hits", "twins_hits", "mariners_ks", "twins_ks"),
    [
        pytest.param([0] * 5, [0] * 5, [8] * 5, [12] * 5, id="zero-hits"),
        pytest.param([6] * 5, [10] * 5, [0] * 5, [0] * 5, id="zero-strikeouts"),
    ],
)
def test_zero_mlb_baseline_is_protected_and_renders_unavailable(
    client: TestClient,
    seed: SeedFn,
    record_coverage: CoverageFn,
    mariners_hits: list[int],
    twins_hits: list[int],
    mariners_ks: list[int],
    twins_ks: list[int],
) -> None:
    seed(mariners_hits, strikeouts=mariners_ks)
    seed(
        twins_hits,
        strikeouts=twins_ks,
        team_id=TWINS_ID,
        team_name=TWINS_NAME,
    )
    record_coverage(teams=2)

    response = comparison_response(client)
    assert_comparison_unavailable(response)
    assert "baseline" in visible_text(response.text).lower()


# --- selectors, shareable URLs, and navigation --------------------------------


@pytest.mark.parametrize("window", [5, 10, 15, 30])
def test_every_supported_rolling_window_is_selected_and_used(
    client: TestClient,
    seed: SeedFn,
    record_coverage: CoverageFn,
    window: int,
) -> None:
    seed_exact_comparison(seed, record_coverage)
    response = comparison_response(client, window=window)

    assert response.status_code == 200
    assert f'<option value="{window}" selected>{window} Games</option>' in response.text
    assert f"trailing {window}-game team average" in visible_text(response.text)


def test_team_season_window_query_round_trips_through_form_and_navigation(
    client: TestClient, seed: SeedFn, record_coverage: CoverageFn
) -> None:
    cubs_id = 112
    cubs_name = "Chicago Cubs"
    seed(
        [7] * 40,
        strikeouts=[9] * 40,
        team_id=cubs_id,
        team_name=cubs_name,
        season=2024,
    )
    # A second Cubs season proves 2024 was selected rather than merely being
    # the only season available in the control.
    seed(
        [8] * 2,
        strikeouts=[8] * 2,
        team_id=cubs_id,
        team_name=cubs_name,
        season=2025,
    )
    seed([9] * 40, strikeouts=[7] * 40, season=2024)
    record_coverage(season=2024, teams=2)

    response = comparison_response(client, team_id=cubs_id, season=2024, window=30)
    assert response.status_code == 200
    body = response.text
    assert "Chicago Cubs — Hits vs Batting Strikeouts" in body
    assert '<option value="112" selected>Chicago Cubs</option>' in body
    assert '<option value="2024" selected>2024</option>' in body
    assert '<option value="30" selected>30 Games</option>' in body
    assert 'action="/comparison"' in body
    for href in (
        "/?team_id=112&amp;season=2024&amp;window=30",
        "/strikeouts?team_id=112&amp;season=2024&amp;window=30",
        "/runs?team_id=112&amp;season=2024&amp;window=30",
        "/comparison?team_id=112&amp;season=2024&amp;window=30",
    ):
        assert f'href="{href}"' in body
    assert body.count('aria-current="page"') == 1


# --- DB-only rendering and existing-route regressions -------------------------


def test_comparison_browser_rendering_never_calls_the_mlb_api(
    client: TestClient,
    seed: SeedFn,
    record_coverage: CoverageFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    seed_exact_comparison(seed, record_coverage)
    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("mlbstatsapi.Mlb.__init__", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)
    monkeypatch.setattr("app.services.league_teams.discover_mlb_teams", fail)
    monkeypatch.setattr(
        "app.services.league_season_ingestion.ingest_league_season", fail
    )

    response = comparison_response(client, window=15)
    assert response.status_code == 200
    assert COMPARISON_CHART_DIV_ID in response.text


@pytest.mark.parametrize(
    ("path", "heading", "chart_id"),
    [
        pytest.param(
            "/", "Seattle Mariners — Hits per Game", "team-hits-chart", id="hits"
        ),
        pytest.param(
            "/strikeouts",
            "Seattle Mariners — Batting Strikeouts per Game",
            "team-strikeouts-chart",
            id="strikeouts",
        ),
        pytest.param(
            "/runs",
            "Seattle Mariners — Runs Scored per Game",
            "team-runs-chart",
            id="runs",
        ),
    ],
)
def test_existing_metric_pages_are_unchanged(
    client: TestClient,
    seed: SeedFn,
    record_coverage: CoverageFn,
    path: str,
    heading: str,
    chart_id: str,
) -> None:
    seed_exact_comparison(seed, record_coverage)
    response = client.get(f"{path}?team_id=136&season=2025&window=15")

    assert response.status_code == 200
    assert heading in response.text
    assert chart_id in response.text


def test_health_is_unchanged(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "mlb-stats-visualizer",
    }
