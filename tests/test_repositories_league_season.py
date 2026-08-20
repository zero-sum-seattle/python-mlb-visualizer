"""Tests for the season-wide team-game query league analytics reads."""

from datetime import date

from sqlalchemy.orm import Session

from app.database.repositories import list_league_season, upsert_team_season
from app.schemas.games import TeamGameBattingLine
from tests.factories import (
    MARINERS_ID,
    MARINERS_NAME,
    TWINS_ID,
    TWINS_NAME,
    make_season,
)

ANGELS_ID = 108
ANGELS_NAME = "Los Angeles Angels"


def store(session: Session, lines: list[TeamGameBattingLine]) -> None:
    upsert_team_season(session, lines=lines)
    session.commit()


def store_team_season(
    session: Session,
    *,
    hits: list[int],
    team_id: int,
    team_name: str,
    season: int = 2025,
) -> list[TeamGameBattingLine]:
    """Persist one team-season, keeping game ids unique across teams.

    ``make_season`` derives game ids from the season alone, so two clubs built
    for the same season would collide on ``game_pk``. Offsetting by team id
    keeps each stored row's identity distinct, as real MLB data is.
    """
    lines = [
        line.model_copy(update={"game_pk": line.game_pk + team_id * 100_000})
        for line in make_season(
            hits, team_id=team_id, team_name=team_name, season=season
        )
    ]
    store(session, lines)
    return lines


def test_every_team_game_record_for_the_season_is_returned(
    migrated_session: Session,
) -> None:
    store_team_season(
        migrated_session, hits=[8, 9], team_id=MARINERS_ID, team_name=MARINERS_NAME
    )
    store_team_season(
        migrated_session, hits=[4, 5, 6], team_id=TWINS_ID, team_name=TWINS_NAME
    )
    store_team_season(
        migrated_session, hits=[7], team_id=ANGELS_ID, team_name=ANGELS_NAME
    )

    stored = list_league_season(migrated_session, season=2025)
    assert len(stored) == 6
    assert {line.team_id for line in stored} == {MARINERS_ID, TWINS_ID, ANGELS_ID}
    assert sum(line.hits for line in stored) == 39


def test_other_seasons_are_excluded(migrated_session: Session) -> None:
    store_team_season(
        migrated_session,
        hits=[8, 8, 8],
        team_id=MARINERS_ID,
        team_name=MARINERS_NAME,
        season=2025,
    )
    store_team_season(
        migrated_session,
        hits=[3, 3],
        team_id=MARINERS_ID,
        team_name=MARINERS_NAME,
        season=2026,
    )

    stored = list_league_season(migrated_session, season=2025)
    assert [line.season for line in stored] == [2025] * 3
    assert sum(line.hits for line in stored) == 24
    assert len(list_league_season(migrated_session, season=2026)) == 2


def test_a_season_with_nothing_stored_returns_no_records(
    migrated_session: Session,
) -> None:
    store_team_season(
        migrated_session, hits=[8], team_id=MARINERS_ID, team_name=MARINERS_NAME
    )
    assert list_league_season(migrated_session, season=1998) == []


def test_domain_objects_are_returned_not_orm_records(
    migrated_session: Session,
) -> None:
    lines = store_team_season(
        migrated_session, hits=[8, 9], team_id=MARINERS_ID, team_name=MARINERS_NAME
    )
    stored = list_league_season(migrated_session, season=2025)
    assert all(isinstance(line, TeamGameBattingLine) for line in stored)
    assert stored == lines


def test_records_come_back_grouped_by_team_in_game_order(
    migrated_session: Session,
) -> None:
    """Deterministic order, so a run over a season is reproducible."""
    store_team_season(
        migrated_session, hits=[1, 2, 3], team_id=TWINS_ID, team_name=TWINS_NAME
    )
    store_team_season(
        migrated_session, hits=[4, 5, 6], team_id=ANGELS_ID, team_name=ANGELS_NAME
    )

    stored = list_league_season(migrated_session, season=2025)
    assert [line.team_id for line in stored] == [ANGELS_ID] * 3 + [TWINS_ID] * 3
    for team_lines in (stored[:3], stored[3:]):
        dates = [line.game_date for line in team_lines]
        assert dates == sorted(dates)
    assert list_league_season(migrated_session, season=2025) == stored


def test_both_halves_of_a_doubleheader_keep_their_sequence(
    migrated_session: Session,
) -> None:
    day = date(2025, 7, 4)
    lines = [
        line.model_copy(update={"game_date": day, "game_number": number})
        for line, number in zip(
            make_season([5, 9], team_id=MARINERS_ID, team_name=MARINERS_NAME),
            (2, 1),
            strict=True,
        )
    ]
    store(migrated_session, lines)

    stored = list_league_season(migrated_session, season=2025)
    assert [line.game_number for line in stored] == [1, 2]
    assert [line.hits for line in stored] == [9, 5]
