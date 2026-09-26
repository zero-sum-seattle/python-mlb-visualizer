"""Characterization tests for the team/season page lifecycle every analytics
route shares before it loads any metric-specific records.

Each analytics page resolves a team, a season, and a rolling window from the
query string, handles the empty-database and not-found states, and carries the
reader's selection through the form and the navigation. That behavior is the
same on every page, so it is pinned here once per route. What each page does
after the selection is resolved (loading its own records, missing-data states,
league context, charts, cards) is covered by that route's own test module.

The key contract: terminal states (empty, unknown team, unknown season) keep
the *requested* query values in the navigation, while a resolved selection
rebuilds the navigation from the *resolved* values.

Every test builds a freshly migrated SQLite database, so each test makes all
the assertions for one route and one state rather than one assertion apiece.
"""

from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.database.repositories import upsert_team_season, upsert_team_season_pitching
from app.main import create_app
from app.schemas.games import TeamGameBattingLine
from app.web.dependencies import get_db_session
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_batting_line,
    make_pitching_season,
    make_season,
)

BROWSER_HEADERS = {"accept": "text/html,application/xhtml+xml"}
JSON_HEADERS = {"accept": "application/json"}
GAMES = 20

# --- Page definitions ---------------------------------------------------------


@dataclass(frozen=True)
class AnalyticsPage:
    path: str
    heading: str
    nav_label: str


PAGES = (
    AnalyticsPage("/", "Team Hitting Trends", "Hits"),
    AnalyticsPage("/strikeouts", "Team Batting Strikeout Trends", "Batting Strikeouts"),
    AnalyticsPage("/runs", "Team Run Scoring Trends", "Runs Scored"),
    AnalyticsPage("/baserunners", "Team Baserunners Trends", "Baserunners"),
    AnalyticsPage("/run-differential", "Team Run Differential", "Run Differential"),
    AnalyticsPage("/pitching", "Team Pitching Trends", "Pitching Trends"),
    AnalyticsPage("/hits-allowed", "Team Hits Allowed Trends", "Hits Allowed"),
    AnalyticsPage(
        "/comparison", "Team Hitting Trends Comparison", "Hits vs Batting Strikeouts"
    ),
)
PAGE_PATHS = tuple(page.path for page in PAGES)

# What each page shows once the seeded selection reaches its own data. The
# comparison page has no COMPLETE league coverage in this fixture, so its
# route-specific "unavailable" state is the expected outcome there.
SEEDED_OUTCOMES = {
    "/": "Seattle Mariners — Hits per Game",
    "/strikeouts": "Seattle Mariners — Batting Strikeouts per Game",
    "/runs": "Seattle Mariners — Runs Scored per Game",
    "/baserunners": "Seattle Mariners — Baserunners per Game",
    "/run-differential": "Seattle Mariners — Run Differential per Game",
    "/pitching": "Seattle Mariners &mdash; Pitches per Game",
    "/hits-allowed": "Seattle Mariners &mdash; Hits Allowed per Game",
    "/comparison": "Normalized comparison unavailable",
}

# Values FastAPI must reject before any route code runs, with the query
# parameter the 422 detail must name.
INVALID_QUERIES = (
    ("team_id=0", "team_id"),
    ("team_id=-3", "team_id"),
    ("team_id=banana", "team_id"),
    ("season=0", "season"),
    ("season=-2025", "season"),
    ("season=20.5", "season"),
    ("window=7", "window"),
    ("window=banana", "window"),
)


def page_for(path: str) -> AnalyticsPage:
    return next(page for page in PAGES if page.path == path)


# --- Navigation assertion helpers ---------------------------------------------


class NavigationParser(HTMLParser):
    """Read navigation semantics and the focused group-label markup."""

    def __init__(self, body: str) -> None:
        super().__init__()
        self.landmarks: dict[str, list[dict[str, str]]] = {}
        self.group_labels: list[str] = []
        self.main_ids: list[str | None] = []
        self.skip_target: str | None = None
        self.current_pages = 0
        self.tabs = False
        self._nav: str | None = None
        self._link: dict[str, str] | None = None
        self._group_label = False
        self.feed(body)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("aria-current") == "page":
            self.current_pages += 1
        if attributes.get("role") in ("tab", "tablist"):
            self.tabs = True
        if tag == "nav":
            self._nav = attributes.get("aria-label")
            assert self._nav, "navigation landmarks need accessible names"
            self.landmarks[self._nav] = []
        if tag == "main":
            self.main_ids.append(attributes.get("id"))
        if tag == "a":
            if attributes.get("class") == "skip-link":
                self.skip_target = attributes.get("href")
            if self._nav:
                assert not self._group_label, "group labels must not be links"
                self._link = {
                    "label": "",
                    "href": attributes.get("href") or "",
                    "current": attributes.get("aria-current") or "",
                }
                self.landmarks[self._nav].append(self._link)
        if (
            tag == "p"
            and attributes.get("class") == "team-nav__heading"
            and self._nav == "Team analytics"
        ):
            assert self._link is None, "group labels must not be links"
            self._group_label = True
            self.group_labels.append("")

    def handle_data(self, data: str) -> None:
        if self._link is not None:
            self._link["label"] += data.strip()
        if self._group_label:
            self.group_labels[-1] += data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav":
            self._nav = None
        if tag == "a":
            self._link = None
        if tag == "p":
            self._group_label = False


def assert_navigation(body: str, *, current: AnalyticsPage, query: str) -> None:
    """Every link carries the selection; domain and document states differ."""
    suffix = f"?{query}" if query else ""
    navigation = NavigationParser(body)
    assert list(navigation.landmarks) == ["Primary", "Team analytics"]
    assert navigation.landmarks["Primary"] == [
        {"label": "Teams", "href": f"/{suffix}", "current": "location"},
        {"label": "Players", "href": "/players", "current": ""},
    ]
    order = (
        "/",
        "/strikeouts",
        "/runs",
        "/baserunners",
        "/comparison",
        "/pitching",
        "/hits-allowed",
        "/run-differential",
    )
    assert navigation.landmarks["Team analytics"] == [
        {
            "label": page_for(path).nav_label,
            "href": f"{path}{suffix}",
            "current": "page" if path == current.path else "",
        }
        for path in order
    ]
    assert navigation.group_labels == ["Offense", "Pitching", "Results"]
    assert navigation.current_pages == 1
    assert not navigation.tabs
    assert navigation.skip_target == "#main-content"
    assert navigation.main_ids == ["main-content"]


def expected_query(team_id: int | None, season: int | None, window: int) -> str:
    selection: dict[str, int] = {}
    if team_id is not None:
        selection["team_id"] = team_id
    if season is not None:
        selection["season"] = season
    selection["window"] = window
    return urlencode(selection)


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def session_factory(migrated_db_path: Path) -> Generator[Callable[[], Session]]:
    engine = build_engine(f"sqlite:///{migrated_db_path}")
    factory = build_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


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


@pytest.fixture
def unmigrated_client(tmp_path: Path) -> Generator[TestClient]:
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
        yield TestClient(app)
    finally:
        engine.dispose()


def opponent_rows(lines: list[TeamGameBattingLine]) -> list[TeamGameBattingLine]:
    """The Twins' side of each Seattle game, as a league-wide import stores it."""
    return [
        make_batting_line(
            game_pk=line.game_pk,
            game_date=line.game_date,
            season=line.season,
            team_id=TWINS_ID,
            team_name=TWINS_NAME,
            opponent_id=MARINERS_ID,
            opponent_name=MARINERS_NAME,
            home_away="away" if line.home_away == "home" else "home",
            runs=3,
            strikeouts=9,
            base_on_balls=2,
            hit_by_pitch=0,
        )
        for line in lines
    ]


@pytest.fixture
def seeded(session_factory: Callable[[], Session]) -> None:
    """Store enough for every page to reach its selected-team data.

    Seattle holds 2024 and 2025. Each season has batting lines with every
    optional column known, pitching lines, and the Twins' rows for the same
    games so run differential can pair them. The Twins are a second stored
    team, so the default-team rule has a real choice to make.
    """
    mariners_2025 = make_season(
        hits=[8] * GAMES,
        strikeouts=[7] * GAMES,
        base_on_balls=[3] * GAMES,
        hit_by_pitch=[1] * GAMES,
        runs=[4] * GAMES,
    )
    mariners_2024 = make_season(
        hits=[6] * GAMES,
        season=2024,
        strikeouts=[8] * GAMES,
        base_on_balls=[2] * GAMES,
        hit_by_pitch=[0] * GAMES,
    )
    session = session_factory()
    try:
        for mariners in (mariners_2025, mariners_2024):
            upsert_team_season(session, lines=mariners)
            upsert_team_season(session, lines=opponent_rows(mariners))
        upsert_team_season_pitching(
            session, lines=make_pitching_season([3] * GAMES, season=2025)
        )
        upsert_team_season_pitching(
            session, lines=make_pitching_season([3] * GAMES, season=2024)
        )
        session.commit()
    finally:
        session.close()


# --- Empty database (terminal state: requested values) ------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_empty_database_renders_the_empty_state_with_requested_values(
    client: TestClient, path: str
) -> None:
    page = page_for(path)

    response = client.get(path)
    assert response.status_code == 200
    body = response.text
    assert f"<h1>{page.heading}</h1>" in body
    assert "No team data has been imported yet" in body
    assert "--team-id 136 --season 2025" in body
    # No stored team means no selector form to fill in.
    assert '<select id="team_id"' not in body
    assert 'id="team-seasons-data"' not in body
    assert "/static/js/season-selector.js" not in body
    assert_navigation(body, current=page, query="window=15")

    requested = client.get(f"{path}?team_id=147&season=2023&window=5")
    assert requested.status_code == 200
    assert_navigation(requested.text, current=page, query=expected_query(147, 2023, 5))


# --- Unknown team (terminal state: requested values) --------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
def test_unknown_team_is_a_404_that_keeps_the_requested_values(
    client: TestClient, path: str
) -> None:
    page = page_for(path)

    response = client.get(f"{path}?team_id=999&season=2025&window=10")
    assert response.status_code == 404
    body = response.text
    assert f"<h1>{page.heading}</h1>" in body
    assert "That team-season is not stored locally" in body
    assert (
        "No games are stored for team id 999. "
        "Pick a team that has been imported, or import that team."
    ) in body
    # The team selector stays populated, with no stored team selected.
    assert f'<option value="{MARINERS_ID}">{MARINERS_NAME}</option>' in body
    assert f'<option value="{TWINS_ID}">{TWINS_NAME}</option>' in body
    assert '<option value="10" selected>10 Games</option>' in body
    assert f'action="{path}"' in body
    assert_navigation(body, current=page, query=expected_query(999, 2025, 10))

    # A season that was never requested is not invented for the navigation.
    without_season = client.get(f"{path}?team_id=999")
    assert without_season.status_code == 404
    assert_navigation(
        without_season.text, current=page, query=expected_query(999, None, 15)
    )


# --- Unknown season (terminal state: requested values) ------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
def test_unknown_season_is_a_404_that_keeps_the_requested_values(
    client: TestClient, path: str
) -> None:
    page = page_for(path)

    response = client.get(f"{path}?team_id=136&season=1998&window=30")
    assert response.status_code == 404
    body = response.text
    assert f"<h1>{page.heading}</h1>" in body
    assert (
        "No 1998 games are stored for Seattle Mariners. Stored seasons: 2025, 2024."
    ) in body
    # The team stays selected and its stored seasons stay on offer.
    assert f'<option value="{MARINERS_ID}" selected>{MARINERS_NAME}</option>' in body
    assert '<option value="2025">2025</option>' in body
    assert '<option value="2024">2024</option>' in body
    assert '<option value="30" selected>30 Games</option>' in body
    assert_navigation(body, current=page, query=expected_query(136, 1998, 30))


# --- Explicit selection (resolved values) -------------------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
def test_explicit_selection_is_reflected_in_the_form_and_navigation(
    client: TestClient, path: str
) -> None:
    response = client.get(f"{path}?team_id=136&season=2024&window=5")
    assert response.status_code == 200
    body = response.text
    assert f'<form class="controls card" method="get" action="{path}">' in body
    for field in ("team_id", "season", "window"):
        assert f'<label for="{field}">' in body
        assert f'<select id="{field}" name="{field}">' in body
    assert '<img class="control__logo js-logo"' in body
    assert 'alt=""' in body
    assert 'id="team-seasons-data"' in body
    assert "/static/js/season-selector.js" in body
    assert '<button type="submit">Update chart</button>' in body
    assert f'<option value="{MARINERS_ID}" selected>{MARINERS_NAME}</option>' in body
    assert '<option value="2024" selected>2024</option>' in body
    assert '<option value="5" selected>5 Games</option>' in body
    assert_navigation(body, current=page_for(path), query=expected_query(136, 2024, 5))


# --- Default selection (resolved values) --------------------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
def test_default_selection_resolves_seattle_and_its_newest_season(
    client: TestClient, path: str
) -> None:
    page = page_for(path)

    response = client.get(path)
    assert response.status_code == 200
    body = response.text
    assert f'<option value="{MARINERS_ID}" selected>{MARINERS_NAME}</option>' in body
    assert '<option value="2025" selected>2025</option>' in body
    assert '<option value="15" selected>15 Games</option>' in body
    # The navigation is rebuilt from what was resolved, not from the empty query.
    assert_navigation(body, current=page, query=expected_query(136, 2025, 15))
    # Guard the fixture: the selection tests exercise a resolved page, not a
    # terminal state that happens to render the same form.
    assert SEEDED_OUTCOMES[path] in body

    # A requested team without a season resolves that team's newest season.
    team_only = client.get(f"{path}?team_id=136&window=30")
    assert team_only.status_code == 200
    assert_navigation(team_only.text, current=page, query=expected_query(136, 2025, 30))


# --- Query validation ---------------------------------------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_invalid_query_values_are_rejected_by_fastapi_validation(
    client: TestClient, path: str
) -> None:
    for query, parameter in INVALID_QUERIES:
        url = f"{path}?{query}"

        api_response = client.get(url, headers=JSON_HEADERS)
        assert api_response.status_code == 422, url
        assert api_response.json()["detail"][0]["loc"] == ["query", parameter], url

        browser_response = client.get(url, headers=BROWSER_HEADERS)
        assert browser_response.status_code == 422, url
        assert "That link has a value this page cannot use" in browser_response.text
        assert f"{parameter}: " in browser_response.text, url
        assert "Traceback" not in browser_response.text, url
        assert "Team analytics" not in NavigationParser(browser_response.text).landmarks

    window_response = client.get(f"{path}?window=7", headers=BROWSER_HEADERS)
    assert "Input should be 5, 10, 15 or 30" in window_response.text


# --- Missing schema -----------------------------------------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_missing_schema_renders_the_migration_error_page(
    unmigrated_client: TestClient, path: str
) -> None:
    response = unmigrated_client.get(f"{path}?team_id=136&season=2025&window=15")
    assert response.status_code == 503
    body = response.text
    assert "The database schema is not ready" in body
    assert "poetry run alembic upgrade head" in body
    assert "Traceback" not in body
    # error.html, not the route template: no page heading, selector, or nav.
    assert f"<h1>{page_for(path).heading}</h1>" not in body
    assert '<select id="team_id"' not in body
    assert "Team analytics" not in NavigationParser(body).landmarks
    assert NavigationParser(body).current_pages == 0


# --- DB-only browser rendering ------------------------------------------------


@pytest.mark.parametrize("path", PAGE_PATHS)
@pytest.mark.usefixtures("seeded")
def test_browser_rendering_never_calls_the_mlb_api(
    client: TestClient, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Web requests read persisted data only; every MLB entry point fails loudly."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("The web layer must not reach the MLB Stats API")

    monkeypatch.setattr(requests.Session, "request", fail)
    monkeypatch.setattr("mlbstatsapi.Mlb.__init__", fail)
    monkeypatch.setattr("mlbstatsapi.AsyncMlb.__init__", fail)
    monkeypatch.setattr("app.services.team_game_logs.get_team_game_batting_lines", fail)
    monkeypatch.setattr("app.services.league_teams.discover_mlb_teams", fail)
    monkeypatch.setattr(
        "app.services.league_season_ingestion.ingest_league_season", fail
    )

    response = client.get(
        f"{path}?team_id=136&season=2025&window=15", headers=BROWSER_HEADERS
    )
    assert response.status_code == 200
    assert SEEDED_OUTCOMES[path] in response.text


@pytest.mark.parametrize(
    "path",
    ("/strikeouts", "/baserunners", "/run-differential", "/pitching", "/hits-allowed"),
)
def test_reimport_states_keep_team_navigation(
    client: TestClient, session_factory: Callable[[], Session], path: str
) -> None:
    with session_factory() as session:
        upsert_team_season(session, lines=make_season(hits=[8] * GAMES))
        session.commit()
    response = client.get(f"{path}?team_id=136&season=2025&window=10")
    assert response.status_code == 409
    assert_navigation(
        response.text, current=page_for(path), query=expected_query(136, 2025, 10)
    )
