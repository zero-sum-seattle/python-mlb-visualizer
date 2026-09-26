"""Player pages stay local, season-scoped, and accessible.

The directory selects Players and never renders stats; the hitting overview
renders one stored season line only after catalog membership is confirmed.
"""

import re
from collections.abc import Iterator
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.database.repositories import (
    upsert_player,
    upsert_player_catalog_entry,
    upsert_player_season_hitting,
)
from app.main import create_app
from app.web.dependencies import get_db_session
from tests.test_repositories_player_catalog import entry
from tests.test_repositories_players import make_hitting, make_identity
from tests.test_web_page_scaffolding import NavigationParser


@pytest.fixture(autouse=True)
def forbid_mlb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail every Player browser state immediately on network/ingestion calls.

    ``app.services.players.get_player_season_hitting`` normalizes an MLB
    response and is forbidden. The repository function of the same name is a
    local read that both Player pages are allowed to make, so it is not here.
    """

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Player browser requests must read the database only")

    monkeypatch.setattr(requests.Session, "request", fail)
    for target in (
        "mlbstatsapi.Mlb.__init__",
        "mlbstatsapi.AsyncMlb.__init__",
        "app.services.players.discover_mlb_players",
        "app.services.players.get_player_identity",
        "app.services.players.get_player_season_hitting",
        "app.services.player_catalog_ingestion.ingest_player_catalog",
        "app.services.player_season_ingestion.ingest_player_season",
        "app.services.team_game_logs.get_team_game_batting_lines",
        "app.services.league_teams.discover_mlb_teams",
        "app.services.league_season_ingestion.ingest_league_season",
    ):
        monkeypatch.setattr(target, fail)


@pytest.fixture
def client(migrated_session: Session) -> Iterator[TestClient]:
    app = create_app()

    def override_session() -> Iterator[Session]:
        yield migrated_session

    app.dependency_overrides[get_db_session] = override_session
    yield TestClient(app)


@pytest.fixture
def catalog(migrated_session: Session) -> None:
    # Deliberately old seasons guard against a calendar-based default.
    for player in (
        entry(677594, "Julio Rodríguez", "CF", 2003),
        entry(2, "Ángel Martínez", "2B", 2003),
        entry(3, "Other Season Rodríguez", "P", 1999),
    ):
        upsert_player_catalog_entry(migrated_session, entry=player)
    upsert_player(migrated_session, identity=make_identity(player_id=99))
    migrated_session.commit()


def assert_player_navigation(body: str) -> None:
    nav = NavigationParser(body)
    assert nav.landmarks == {
        "Primary": [
            {"label": "Teams", "href": "/", "current": ""},
            {"label": "Players", "href": "/players", "current": "location"},
        ]
    }
    assert nav.main_ids == ["main-content"]
    assert nav.skip_target == "#main-content"
    assert not nav.tabs


def test_empty_catalog_has_real_import_guidance(client: TestClient) -> None:
    response = client.get("/players")
    assert response.status_code == 200
    assert "No Player catalog is stored locally" in response.text
    assert (
        "poetry run python scripts/import_player_catalog.py --season &lt;season&gt;"
        in response.text
    )
    assert "player-results__list" not in response.text
    assert "Selected Player" not in response.text
    assert_player_navigation(response.text)


@pytest.mark.usefixtures("catalog")
def test_default_season_comes_from_catalog_and_does_not_dump_names(
    client: TestClient,
) -> None:
    response = client.get("/players")
    assert response.status_code == 200
    assert '<option value="2003" selected>2003</option>' in response.text
    assert "Find a player" in response.text
    assert "Julio Rodríguez" not in response.text
    assert "player-results__list" not in response.text
    assert_player_navigation(response.text)


@pytest.mark.usefixtures("catalog")
def test_explicit_season_and_scoped_case_accent_insensitive_search(
    client: TestClient,
) -> None:
    response = client.get("/players?season=1999&q=RODRIGUEZ")
    assert response.status_code == 200
    assert '<option value="1999" selected>1999</option>' in response.text
    assert "Other Season Rodríguez" in response.text
    assert "Julio Rodríguez" not in response.text
    response = client.get("/players?season=2003&q=rOdRiGuEz")
    assert "Julio Rodríguez" in response.text
    assert "Other Season Rodríguez" not in response.text
    assert "Ángel Martínez" not in response.text
    assert "Ángel Martínez" in client.get("/players?q=ANGEL").text


@pytest.mark.usefixtures("catalog")
def test_no_match_is_useful_200(client: TestClient) -> None:
    response = client.get("/players?q=nomatch")
    assert response.status_code == 200
    assert "No players match" in response.text
    assert "Try another name or season" in response.text
    assert "player-results__list" not in response.text


@pytest.mark.parametrize("query", ["", "&q=julio&player_id=677594"])
@pytest.mark.usefixtures("catalog")
def test_unavailable_season_preserved_with_stored_choices(
    client: TestClient, query: str
) -> None:
    response = client.get(f"/players?season=1980{query}")
    assert response.status_code == 404
    assert "No Player catalog is stored locally for 1980" in response.text
    assert "Stored seasons: 2003, 1999." in response.text
    assert '<option value="1980" selected>1980 — not stored</option>' in response.text
    assert "Selected Player" not in response.text
    assert_player_navigation(response.text)


def test_explicit_missing_season_in_empty_catalog(client: TestClient) -> None:
    response = client.get("/players?season=1980")
    assert response.status_code == 404
    assert "No Player catalog is stored locally for 1980" in response.text
    assert "scripts/import_player_catalog.py --season 1980" in response.text
    # One empty-catalog notice, not a second contradictory selection notice.
    assert "That Player selection is not stored locally" not in response.text
    assert response.text.count('class="card notice"') == 1


def test_selection_in_empty_catalog_does_not_invent_a_season(
    client: TestClient,
) -> None:
    response = client.get("/players?player_id=677594")
    assert response.status_code == 404
    assert "Player 677594 is not in the locally stored catalog." in response.text
    assert "None" not in response.text
    assert "No Player catalog is stored locally" in response.text
    assert "Selected Player" not in response.text


@pytest.mark.usefixtures("catalog")
def test_result_link_is_shareable_and_selection_is_identity_only(
    client: TestClient, migrated_session: Session
) -> None:
    upsert_player_season_hitting(migrated_session, hitting=make_hitting(season=2003))
    migrated_session.commit()
    response = client.get("/players?season=2003&q=julio&team_id=136&window=15")
    link = "/players?season=2003&amp;q=julio&amp;player_id=677594"
    assert f'href="{link}"' in response.text
    selected = client.get(unescape(link))
    assert selected.status_code == 200
    assert "Selected Player" in selected.text
    assert "You selected <strong>Julio Rodríguez</strong>" in selected.text
    assert "<strong>2003</strong> catalog" in selected.text
    assert "Primary position: <strong>CF</strong>" in selected.text
    assert "— Selected" in selected.text
    assert selected.text.count('aria-current="true"') == 1
    assert 'aria-current="true">Julio Rodríguez</a>' in selected.text
    assert_player_navigation(selected.text)
    for absent in (
        "Batting Average",
        "Home Runs",
        "Plate Appearances",
        "OPS",
        "Strikeouts",
        "Walks",
        "Hits",
        "plotly",
        'name="team_id"',
        'name="window"',
        'name="player_id"',
        "/players/compare",
        "import_player_season.py",
    ):
        assert absent not in selected.text
    # The one deliberate addition: a real link, because hitting is stored.
    assert selected.text.count("/players/hitting") == 1
    assert (
        '<a class="player-link" href="/players/hitting?season=2003&amp;'
        'player_id=677594">View Hitting Overview</a>'
    ) in selected.text
    # Search form submits only season and q, intentionally clearing selection.
    assert 'method="get" action="/players"' in selected.text
    assert '<label for="season">Season</label>' in selected.text
    assert '<label for="q">Search player</label>' in selected.text
    assert parse_qs(urlsplit(unescape(link)).query) == {
        "season": ["2003"],
        "q": ["julio"],
        "player_id": ["677594"],
    }


@pytest.mark.parametrize("player_id", [3, 99, 99999])
@pytest.mark.usefixtures("catalog")
def test_selection_requires_season_membership(
    client: TestClient, player_id: int
) -> None:
    response = client.get(f"/players?season=2003&player_id={player_id}")
    assert response.status_code == 404
    assert (
        f"Player {player_id} is not in the locally stored catalog for 2003"
        in response.text
    )
    assert "Selected Player" not in response.text
    assert_player_navigation(response.text)


@pytest.mark.usefixtures("catalog")
def test_selection_is_independent_of_search(client: TestClient) -> None:
    for query in ("", "&q=nomatch"):
        response = client.get(f"/players?player_id=677594{query}")
        assert response.status_code == 200
        assert "You selected <strong>Julio Rodríguez</strong>" in response.text


def test_result_limit_and_selection_beyond_limit(
    client: TestClient, migrated_session: Session
) -> None:
    for number in range(55):
        upsert_player_catalog_entry(
            migrated_session,
            entry=entry(number + 1, f"Test Player {number:02}", "P", 2001),
        )
    migrated_session.commit()
    response = client.get("/players?season=2001&q=test&player_id=55")
    assert response.status_code == 200
    assert "55 matching players" in response.text
    assert (
        "Showing the first 50 results alphabetically. Refine your search"
        in response.text
    )
    assert response.text.count('href="/players?') == 50
    assert "Test Player 49</a>" in response.text
    assert "Test Player 50</a>" not in response.text
    assert "You selected <strong>Test Player 54</strong>" in response.text


@pytest.mark.usefixtures("catalog")
def test_query_encoding_and_html_escaping(client: TestClient) -> None:
    query = '"><script>alert(1)</script>&season=1999'
    response = client.get("/players", params={"q": query})
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert '<option value="2003" selected>' in response.text
    assert "player-results__list" not in client.get("/players?q=%20%20").text


@pytest.mark.parametrize(
    "query", ["season=0", "season=no", "player_id=-1", "player_id=no"]
)
def test_invalid_parameters_follow_browser_validation(
    client: TestClient, query: str
) -> None:
    response = client.get(f"/players?{query}", headers={"accept": "text/html"})
    assert response.status_code == 422
    assert "That link has a value this page cannot use" in response.text
    assert "Traceback" not in response.text


def test_missing_schema_has_migration_guidance(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    factory = build_session_factory(engine)
    app = create_app()

    def override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        response = TestClient(app).get("/players")
        assert response.status_code == 503
        assert "The database schema is not ready" in response.text
        assert "poetry run alembic upgrade head" in response.text
        assert "Traceback" not in response.text
        assert_player_navigation(response.text)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Directory → hitting overview
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("catalog")
def test_selection_without_hitting_has_no_overview_link(client: TestClient) -> None:
    response = client.get("/players?season=2003&player_id=677594")
    assert response.status_code == 200
    assert "You selected <strong>Julio Rodríguez</strong>" in response.text
    assert "/players/hitting" not in response.text
    assert "View Hitting Overview" not in response.text
    assert "No 2003 hitting line is stored locally for this Player." in response.text
    assert (
        "poetry run python scripts/import_player_season.py "
        "--player-id 677594 --season 2003"
    ) in response.text


@pytest.mark.usefixtures("catalog")
def test_overview_link_needs_hitting_for_the_selected_season(
    client: TestClient, migrated_session: Session
) -> None:
    # Hitting stored for another season must not link from this one.
    upsert_player_catalog_entry(
        migrated_session, entry=entry(677594, "Julio Rodríguez", "CF", 1999)
    )
    upsert_player_season_hitting(migrated_session, hitting=make_hitting(season=1999))
    migrated_session.commit()
    assert (
        "/players/hitting"
        not in client.get("/players?season=2003&player_id=677594").text
    )
    assert (
        "/players/hitting?season=1999&amp;player_id=677594"
        in client.get("/players?season=1999&player_id=677594").text
    )


# ---------------------------------------------------------------------------
# /players/hitting
# ---------------------------------------------------------------------------

HITTING_URL = "/players/hitting?season=2003&player_id=677594"


@pytest.fixture
def stored_hitting(catalog: None, migrated_session: Session) -> None:
    upsert_player_season_hitting(migrated_session, hitting=make_hitting(season=2003))
    migrated_session.commit()


def prose(body: str) -> str:
    """Collapse template line wrapping so sentences can be asserted whole."""
    return " ".join(body.split())


def card_values(body: str) -> dict[str, str]:
    """Map each summary card label to its rendered value."""
    labels = re.findall(r'summary-card__label">([^<]+)<', body)
    values = re.findall(r'summary-card__value">([^<]+)<', body)
    assert len(labels) == len(values)
    return dict(zip(labels, values, strict=True))


@pytest.mark.usefixtures("stored_hitting")
def test_overview_renders_stored_season_line(client: TestClient) -> None:
    response = client.get(HITTING_URL)
    assert response.status_code == 200
    body = response.text
    assert "<h1>Julio Rodríguez — 2003 Hitting</h1>" in body
    assert "<title>Julio Rodríguez — 2003 Hitting" in body
    # 150/500, 215/569, 246/500, and their sum, to three places.
    assert card_values(body) == {
        "AVG": ".300",
        "OBP": ".378",
        "SLG": ".492",
        "OPS": ".870",
        "Games": "150",
        "Plate Appearances": "600",
        "Home Runs": "20",
        "Walks": "60",
        "Strikeouts": "100",
        "Stolen Bases": "10",
    }
    assert "246 TB in 500 AB" in body
    assert "not a game-by-game trend" in prose(body)
    assert "not a historical position for 2003" in prose(body)
    assert_player_navigation(body)
    assert "team-nav" not in body


@pytest.mark.usefixtures("stored_hitting")
def test_overview_chart_is_a_local_plate_appearance_profile(
    client: TestClient,
) -> None:
    body = client.get(HITTING_URL).text
    assert '<script src="/vendor/plotly.min.js"></script>' in body
    assert "cdn.plot.ly" not in body
    assert 'id="player-plate-appearance-rates-chart"' in body
    assert "Plate Appearance Rate Profile" in body
    assert "percentages of 600 plate appearances" in body
    for label in ('"K%"', '"BB%"', '"HR%"', '"16.7%"', '"10.0%"', '"3.3%"'):
        assert label in body
    for absent in ("MLB Average", "League Average", "Percentile", "Rank", "Elite"):
        assert absent not in body


@pytest.mark.usefixtures("stored_hitting")
def test_overview_makes_no_team_or_comparison_claims(client: TestClient) -> None:
    body = client.get(HITTING_URL).text
    assert "combined here; this page does not show which clubs" in prose(body)
    for absent in ('name="team_id"', "team_id=", "vs MLB", "Mariners"):
        assert absent not in body


@pytest.mark.usefixtures("stored_hitting")
def test_overview_links_back_to_the_selected_player(client: TestClient) -> None:
    body = client.get(HITTING_URL).text
    back = "/players?season=2003&amp;player_id=677594"
    assert f'<a class="player-link" href="{back}">← Back to Player Directory</a>' in (
        body
    )
    directory = client.get(unescape(back))
    assert directory.status_code == 200
    assert "You selected <strong>Julio Rodríguez</strong>" in directory.text
    assert "View Hitting Overview" in directory.text


@pytest.mark.usefixtures("stored_hitting")
def test_overview_query_is_shareable_and_order_independent(
    client: TestClient,
) -> None:
    first = client.get("/players/hitting?player_id=677594&season=2003")
    assert first.status_code == 200
    assert first.text == client.get(HITTING_URL).text


@pytest.mark.usefixtures("catalog")
def test_missing_hitting_row_is_unavailable_not_zero(client: TestClient) -> None:
    """200: the Player-season selection is valid; only its analytics are absent.

    This follows Team comparison without league data rather than an unstored
    Team-season (404) or rows that conflict with the page (409).
    """
    response = client.get(HITTING_URL)
    assert response.status_code == 200
    body = response.text
    assert "No 2003 hitting line is stored for Julio Rodríguez" in body
    assert (
        "poetry run python scripts/import_player_season.py "
        "--player-id 677594 --season 2003"
    ) in body
    assert "summary-card" not in body
    assert ".000" not in body
    assert "zero production" in prose(body)
    assert "plotly" not in body
    assert "/players?season=2003&amp;player_id=677594" in body
    assert_player_navigation(body)


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("season=2003&player_id=99", "global identity without membership"),
        ("season=2003&player_id=3", "member of another season only"),
        ("season=2003&player_id=99999", "unknown Player"),
        ("season=1980&player_id=677594", "season with no stored catalog"),
    ],
)
@pytest.mark.usefixtures("catalog")
def test_overview_requires_catalog_membership(
    client: TestClient, migrated_session: Session, query: str, reason: str
) -> None:
    # A hitting row for every requested pair proves stats cannot bypass
    # the catalog. Player 99999 has no identity row, so no hitting row either.
    for player_id, season in ((99, 2003), (3, 2003), (677594, 1980)):
        upsert_player_season_hitting(
            migrated_session,
            hitting=make_hitting(player_id=player_id, season=season),
        )
    migrated_session.commit()
    response = client.get(f"/players/hitting?{query}")
    assert response.status_code == 404, reason
    assert "That Player selection is not stored locally" in response.text
    assert "is not in the locally stored" in response.text
    assert "summary-card" not in response.text
    assert "plotly" not in response.text
    assert '<a class="player-link" href="/players">' in response.text
    assert_player_navigation(response.text)


@pytest.mark.usefixtures("catalog")
def test_no_plate_appearances_leaves_rates_undefined(
    client: TestClient, migrated_session: Session
) -> None:
    zeros = dict.fromkeys(
        (
            "plate_appearances",
            "at_bats",
            "runs",
            "hits",
            "doubles",
            "triples",
            "home_runs",
            "rbi",
            "base_on_balls",
            "intentional_walks",
            "hit_by_pitch",
            "strikeouts",
            "stolen_bases",
            "caught_stealing",
            "sac_flies",
            "sac_bunts",
        ),
        0,
    )
    upsert_player_season_hitting(
        migrated_session,
        hitting=make_hitting(season=2003, games_played=2, **zeros),
    )
    migrated_session.commit()
    response = client.get(HITTING_URL)
    assert response.status_code == 200
    values = card_values(response.text)
    assert {values[label] for label in ("AVG", "OBP", "SLG", "OPS")} == {"—"}
    assert values["Plate Appearances"] == "0"
    assert "Undefined: no at-bats" in response.text
    assert ".000<" not in response.text
    assert "0.0%" not in response.text
    assert "plotly" not in response.text
    assert "K%, BB%, and HR% are undefined" in response.text


@pytest.mark.usefixtures("catalog")
def test_self_contradicting_hitting_row_is_a_conflict(
    client: TestClient, migrated_session: Session
) -> None:
    upsert_player_season_hitting(
        migrated_session,
        hitting=make_hitting(season=2003, at_bats=100, hits=150),
    )
    migrated_session.commit()
    response = client.get(HITTING_URL)
    assert response.status_code == 409
    assert "150 hits in 100 at-bats" in response.text
    assert "summary-card" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        "season=0&player_id=677594",
        "season=no&player_id=677594",
        "season=2003&player_id=-1",
        "season=2003&player_id=no",
        "season=2003",
        "player_id=677594",
        "",
    ],
)
def test_overview_invalid_parameters_follow_browser_validation(
    client: TestClient, query: str
) -> None:
    response = client.get(f"/players/hitting?{query}", headers={"accept": "text/html"})
    assert response.status_code == 422
    assert "That link has a value this page cannot use" in response.text
    assert "Traceback" not in response.text


def test_overview_missing_schema_has_migration_guidance(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    factory = build_session_factory(engine)
    app = create_app()

    def override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        response = TestClient(app).get(HITTING_URL)
        assert response.status_code == 503
        assert "The database schema is not ready" in response.text
        assert "poetry run alembic upgrade head" in response.text
        assert "Traceback" not in response.text
        assert_player_navigation(response.text)
    finally:
        engine.dispose()


@pytest.mark.usefixtures("stored_hitting")
def test_team_navigation_and_health_are_unchanged(client: TestClient) -> None:
    team = NavigationParser(client.get("/").text)
    assert team.landmarks["Primary"] == [
        {"label": "Teams", "href": "/?window=15", "current": "location"},
        {"label": "Players", "href": "/players", "current": ""},
    ]
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
