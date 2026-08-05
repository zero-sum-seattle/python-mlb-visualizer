"""Tests for team game batting line repository functions."""

from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import TeamGameBattingLineRecord
from app.database.repositories import (
    list_team_season,
    upsert_team_season,
)
from app.schemas.games import TeamGameBattingLine

CUBS_ID = 112
SEASON = 2025


def make_line(**overrides: object) -> TeamGameBattingLine:
    base = {
        "game_pk": 776704,
        "game_date": date(2025, 8, 17),
        "season": SEASON,
        "team_id": CUBS_ID,
        "team_name": "Chicago Cubs",
        "opponent_id": 134,
        "opponent_name": "Pittsburgh Pirates",
        "home_away": "home",
        "hits": 6,
        "runs": 4,
        "status": "Final",
        "game_number": 1,
        "doubleheader": False,
        "scheduled_innings": 9,
    }
    base.update(overrides)
    return TeamGameBattingLine(**base)


def test_new_record_is_inserted(migrated_session: Session) -> None:
    line = make_line()
    result = upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    assert result.inserted == 1
    assert result.updated == 0
    assert result.unchanged == 0
    stored = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    assert stored == [line]


def test_multiple_new_records_are_inserted(migrated_session: Session) -> None:
    lines = [
        make_line(game_pk=1, game_date=date(2025, 4, 1)),
        make_line(game_pk=2, game_date=date(2025, 4, 2)),
    ]
    result = upsert_team_season(migrated_session, lines=lines)
    migrated_session.commit()
    assert result.inserted == 2
    assert list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON) == lines


def test_identical_records_are_reported_as_unchanged(migrated_session: Session) -> None:
    line = make_line()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    result = upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    assert result.inserted == 0
    assert result.updated == 0
    assert result.unchanged == 1


def test_identical_second_import_does_not_create_duplicates(
    migrated_session: Session,
) -> None:
    line = make_line()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    records = migrated_session.scalars(
        select(TeamGameBattingLineRecord).where(
            TeamGameBattingLineRecord.team_id == CUBS_ID
        )
    ).all()
    assert len(records) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hits", 9),
        ("runs", 7),
        ("game_date", date(2025, 8, 18)),
        ("team_name", "Cubs"),
        ("opponent_name", "Pirates"),
        ("status", "Game Over"),
        ("game_number", 2),
        ("doubleheader", True),
        ("scheduled_innings", 7),
    ],
)
def test_changed_baseball_fields_update_existing_row(
    migrated_session: Session, field: str, value: object
) -> None:
    line = make_line()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    updated_line = make_line(**{field: value})
    result = upsert_team_season(migrated_session, lines=[updated_line])
    migrated_session.commit()
    assert result.updated == 1
    stored = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)[0]
    assert stored == updated_line


def test_changed_opponent_id_updates_row(migrated_session: Session) -> None:
    line = make_line()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    updated = make_line(opponent_id=119)
    result = upsert_team_season(migrated_session, lines=[updated])
    migrated_session.commit()
    assert result.updated == 1
    assert (
        list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)[0] == updated
    )


def test_changed_home_away_updates_row(migrated_session: Session) -> None:
    line = make_line(home_away="home")
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    updated = make_line(home_away="away")
    result = upsert_team_season(migrated_session, lines=[updated])
    migrated_session.commit()
    assert result.updated == 1


def test_created_at_is_preserved_during_update(migrated_session: Session) -> None:
    line = make_line()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    record = migrated_session.scalars(select(TeamGameBattingLineRecord)).one()
    created_at = record.created_at
    upsert_team_season(migrated_session, lines=[make_line(hits=10)])
    migrated_session.commit()
    record = migrated_session.scalars(select(TeamGameBattingLineRecord)).one()
    assert record.created_at == created_at


def test_unchanged_record_does_not_change_updated_at(migrated_session: Session) -> None:
    line = make_line()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    record = migrated_session.scalars(select(TeamGameBattingLineRecord)).one()
    updated_at = record.updated_at
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    record = migrated_session.scalars(select(TeamGameBattingLineRecord)).one()
    assert record.updated_at == updated_at


def test_results_are_ordered_by_date_game_number_and_game_pk(
    migrated_session: Session,
) -> None:
    lines = [
        make_line(game_pk=3, game_date=date(2025, 5, 1), game_number=2),
        make_line(game_pk=1, game_date=date(2025, 4, 1), game_number=1),
        make_line(game_pk=2, game_date=date(2025, 5, 1), game_number=1),
    ]
    upsert_team_season(migrated_session, lines=lines)
    migrated_session.commit()
    ordered = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    assert [line.game_pk for line in ordered] == [1, 2, 3]


def test_doubleheader_games_remain_distinct(migrated_session: Session) -> None:
    lines = [
        make_line(
            game_pk=100,
            game_date=date(2025, 6, 1),
            game_number=1,
            doubleheader=True,
        ),
        make_line(
            game_pk=101,
            game_date=date(2025, 6, 1),
            game_number=2,
            doubleheader=True,
        ),
    ]
    upsert_team_season(migrated_session, lines=lines)
    migrated_session.commit()
    stored = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    assert len(stored) == 2
    assert stored[0].game_number == 1
    assert stored[1].game_number == 2


def test_domain_record_round_trips_through_sqlite(migrated_session: Session) -> None:
    line = make_line()
    upsert_team_season(migrated_session, lines=[line])
    migrated_session.commit()
    assert list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)[0] == line


def test_rows_absent_from_later_input_are_not_deleted(
    migrated_session: Session,
) -> None:
    first = make_line(game_pk=1, game_date=date(2025, 4, 1))
    second = make_line(game_pk=2, game_date=date(2025, 4, 2))
    upsert_team_season(migrated_session, lines=[first, second])
    migrated_session.commit()
    upsert_team_season(migrated_session, lines=[first])
    migrated_session.commit()
    stored = list_team_season(migrated_session, team_id=CUBS_ID, season=SEASON)
    assert len(stored) == 2


def test_duplicate_identity_is_prevented_by_database(migrated_session: Session) -> None:
    now = datetime(2025, 1, 1)
    migrated_session.add(
        TeamGameBattingLineRecord.from_domain(
            make_line(game_pk=50), created_at=now, updated_at=now
        )
    )
    migrated_session.commit()
    migrated_session.add(
        TeamGameBattingLineRecord.from_domain(
            make_line(game_pk=50, hits=9), created_at=now, updated_at=now
        )
    )
    with pytest.raises(IntegrityError):
        migrated_session.commit()


def test_empty_input_returns_zero_counts(migrated_session: Session) -> None:
    result = upsert_team_season(migrated_session, lines=[])
    assert result.inserted == 0
    assert result.updated == 0
    assert result.unchanged == 0
