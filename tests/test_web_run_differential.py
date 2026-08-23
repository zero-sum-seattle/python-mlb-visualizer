"""Tests for the /run-differential page.

The page differs from the other four in what it needs from the database: it
reads two rows per game rather than one, so a team-season imported on its own
cannot be charted no matter how complete that team's own rows are. Most of
these tests are about that boundary.
"""

from collections.abc import Callable, Generator, Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.engine import build_engine, build_session_factory
from app.database.repositories import upsert_team_season
from app.main import create_app
from app.schemas.games import TeamGameBattingLine
from app.web.dependencies import get_db_session
from tests.factories import MARINERS_ID, MARINERS_NAME, TWINS_ID, TWINS_NAME

SEASON = 2025
OPENING_DAY = date(2025, 3, 27)
PATH = "/run-differential"


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


def line(**overrides: object) -> TeamGameBattingLine:
    base: dict[str, object] = {
        "game_pk": 776000,
        "game_date": OPENING_DAY,
        "season": SEASON,
        "team_id": MARINERS_ID,
        "team_name": MARINERS_NAME,
        "opponent_id": TWINS_ID,
        "opponent_name": TWINS_NAME,
        "home_away": "home",
        "hits": 8,
        "runs": 4,
        "status": "Final",
        "game_number": 1,
        "doubleheader": False,
        "scheduled_innings": 9,
    }
    base.update(overrides)
    return TeamGameBattingLine(**base)


@pytest.fixture
def seed_both_clubs(
    session_factory: Callable[[], Session],
) -> Callable[[list[int], list[int]], None]:
    """Seed both clubs' rows for each game, as a league-wide import would."""

    def _seed(scored: list[int], allowed: list[int]) -> None:
        mariners: list[TeamGameBattingLine] = []
        twins: list[TeamGameBattingLine] = []
        for index, (own, other) in enumerate(zip(scored, allowed, strict=True)):
            game_pk = SEASON * 1000 + index
            game_date = OPENING_DAY + timedelta(days=index)
            mariners.append(
                line(game_pk=game_pk, game_date=game_date, runs=own, home_away="home")
            )
            twins.append(
                line(
                    game_pk=game_pk,
                    game_date=game_date,
                    team_id=TWINS_ID,
                    team_name=TWINS_NAME,
                    opponent_id=MARINERS_ID,
                    opponent_name=MARINERS_NAME,
                    runs=other,
                    home_away="away",
                )
            )
        session = session_factory()
        try:
            upsert_team_season(session, lines=mariners)
            upsert_team_season(session, lines=twins)
            session.commit()
        finally:
            session.close()

    return _seed


@pytest.fixture
def seed_one_club(
    session_factory: Callable[[], Session],
) -> Callable[[list[int]], None]:
    """Seed only the selected team's rows, as a single-team import would."""

    def _seed(scored: list[int]) -> None:
        session = session_factory()
        try:
            upsert_team_season(
                session,
                lines=[
                    line(
                        game_pk=SEASON * 1000 + index,
                        game_date=OPENING_DAY + timedelta(days=index),
                        runs=own,
                    )
                    for index, own in enumerate(scored)
                ],
            )
            session.commit()
        finally:
            session.close()

    return _seed


def test_the_page_renders_for_a_league_imported_season(
    client: TestClient, seed_both_clubs
) -> None:
    seed_both_clubs([6, 2, 8], [3, 7, 1])

    response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

    assert response.status_code == 200
    assert "Run Differential per Game" in response.text
    assert MARINERS_NAME in response.text


def test_the_season_totals_are_shown(client: TestClient, seed_both_clubs) -> None:
    seed_both_clubs([6, 2, 8], [3, 7, 1])

    response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

    # 16 scored, 11 allowed, +5 differential.
    assert "+5" in response.text
    assert "16 Scored, 11 Allowed" in response.text


def test_a_negative_differential_is_shown_with_its_sign(
    client: TestClient, seed_both_clubs
) -> None:
    seed_both_clubs([1, 2, 0], [5, 4, 9])

    response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

    assert response.status_code == 200
    assert "-15" in response.text


def test_the_actual_record_is_derived_from_the_scores(
    client: TestClient, seed_both_clubs
) -> None:
    """Two wins, two losses, with no W/L column stored anywhere."""
    seed_both_clubs([5, 1, 3, 9], [2, 4, 8, 0])

    response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

    assert "Actual Record" in response.text
    assert "2-2" in response.text


def test_the_pythagorean_record_is_shown(client: TestClient, seed_both_clubs) -> None:
    seed_both_clubs([6, 2, 8], [3, 7, 1])

    response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

    assert "Pythagorean Record" in response.text
    assert "Expected record from runs scored and allowed" in response.text


def test_the_page_explains_why_there_is_no_mlb_line(
    client: TestClient, seed_both_clubs
) -> None:
    seed_both_clubs([6, 2, 8], [3, 7, 1])

    response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

    # The template wraps its prose, so the sentence is matched against the
    # rendered text with runs of whitespace collapsed.
    flattened = " ".join(response.text.split())
    assert "league-wide run differential is exactly zero" in flattened


class TestMissingOpponentRows:
    def test_a_single_team_import_returns_409(
        self, client: TestClient, seed_one_club
    ) -> None:
        seed_one_club([6, 2, 8])

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

        assert response.status_code == 409

    def test_the_page_names_the_league_import_as_the_fix(
        self, client: TestClient, seed_one_club
    ) -> None:
        """Re-importing the team cannot help, so the page must not suggest it."""
        seed_one_club([6, 2, 8])

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

        assert "scripts/import_league_season.py --season 2025" in response.text
        assert "This season needs a league-wide import" in response.text

    def test_the_page_says_how_many_games_are_unpaired(
        self, client: TestClient, seed_one_club
    ) -> None:
        seed_one_club([6, 2, 8])

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

        assert "3 of the 3 stored games" in response.text

    def test_a_partially_paired_season_is_also_refused(
        self, client: TestClient, session_factory
    ) -> None:
        """One opponent row stored, two missing: still not chartable."""
        session = session_factory()
        try:
            upsert_team_season(
                session,
                lines=[
                    line(
                        game_pk=SEASON * 1000 + index,
                        runs=5,
                        game_date=OPENING_DAY + timedelta(days=index),
                    )
                    for index in range(3)
                ],
            )
            upsert_team_season(
                session,
                lines=[
                    line(
                        game_pk=SEASON * 1000,
                        game_date=OPENING_DAY,
                        team_id=TWINS_ID,
                        team_name=TWINS_NAME,
                        opponent_id=MARINERS_ID,
                        opponent_name=MARINERS_NAME,
                        runs=2,
                        home_away="away",
                    )
                ],
            )
            session.commit()
        finally:
            session.close()

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": SEASON})

        assert response.status_code == 409
        assert "2 of the 3 stored games" in response.text

    def test_the_other_metric_pages_still_work(
        self, client: TestClient, seed_one_club
    ) -> None:
        """They read only the team's own rows, so a single-team import suits them."""
        seed_one_club([6, 2, 8])

        for path in ("/", "/runs"):
            response = client.get(
                path, params={"team_id": MARINERS_ID, "season": SEASON}
            )
            assert response.status_code == 200, path


class TestSelection:
    def test_no_imported_data_shows_the_import_command(
        self, client: TestClient
    ) -> None:
        response = client.get(PATH)

        assert response.status_code == 200
        assert "No team data has been imported yet" in response.text

    def test_an_unknown_team_returns_404(
        self, client: TestClient, seed_both_clubs
    ) -> None:
        seed_both_clubs([6], [3])

        response = client.get(PATH, params={"team_id": 999, "season": SEASON})

        assert response.status_code == 404

    def test_an_unknown_season_returns_404(
        self, client: TestClient, seed_both_clubs
    ) -> None:
        seed_both_clubs([6], [3])

        response = client.get(PATH, params={"team_id": MARINERS_ID, "season": 1999})

        assert response.status_code == 404

    def test_the_rolling_window_is_honoured(
        self, client: TestClient, seed_both_clubs
    ) -> None:
        seed_both_clubs([6, 2, 8, 1, 5], [3, 7, 1, 9, 2])

        response = client.get(
            PATH, params={"team_id": MARINERS_ID, "season": SEASON, "window": 5}
        )

        assert response.status_code == 200
        assert "Recent 5-Game Avg" in response.text

    def test_an_unsupported_window_is_rejected(
        self, client: TestClient, seed_both_clubs
    ) -> None:
        seed_both_clubs([6], [3])

        response = client.get(
            PATH, params={"team_id": MARINERS_ID, "season": SEASON, "window": 7}
        )

        assert response.status_code == 422


def test_the_page_is_linked_from_the_other_metric_pages(
    client: TestClient, seed_both_clubs
) -> None:
    seed_both_clubs([6, 2, 8], [3, 7, 1])

    response = client.get("/runs", params={"team_id": MARINERS_ID, "season": SEASON})

    assert "/run-differential?team_id=136&amp;season=2025" in response.text
