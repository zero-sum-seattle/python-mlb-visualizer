"""Tests for player identity and player-season hitting repository functions."""

from sqlalchemy.orm import Session

from app.database.models import PlayerRecord, PlayerSeasonHittingRecord
from app.database.repositories import (
    get_player,
    get_player_season_hitting,
    upsert_player,
    upsert_player_season_hitting,
)
from app.schemas.ingestion import PlayerPersistenceOutcome
from app.schemas.players import PlayerIdentity, PlayerSeasonHitting

PLAYER_ID = 677594
SEASON = 2025


def make_identity(**overrides: object) -> PlayerIdentity:
    base = {
        "player_id": PLAYER_ID,
        "full_name": "Julio Rodriguez",
        "primary_position": "CF",
    }
    base.update(overrides)
    return PlayerIdentity(**base)


def make_hitting(**overrides: object) -> PlayerSeasonHitting:
    base = {
        "player_id": PLAYER_ID,
        "season": SEASON,
        "games_played": 150,
        "plate_appearances": 600,
        "at_bats": 500,
        "runs": 80,
        "hits": 150,
        "doubles": 30,
        "triples": 3,
        "home_runs": 20,
        "rbi": 90,
        "base_on_balls": 60,
        "intentional_walks": 5,
        "hit_by_pitch": 5,
        "strikeouts": 100,
        "stolen_bases": 10,
        "caught_stealing": 3,
        "sac_flies": 4,
        "sac_bunts": 2,
    }
    base.update(overrides)
    return PlayerSeasonHitting(**base)


# ---------------------------------------------------------------------------
# upsert_player
# ---------------------------------------------------------------------------


def test_new_player_is_inserted(migrated_session: Session) -> None:
    identity = make_identity()
    outcome = upsert_player(migrated_session, identity=identity)
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.INSERTED
    assert get_player(migrated_session, player_id=PLAYER_ID) == identity


def test_identical_rerun_is_unchanged(migrated_session: Session) -> None:
    identity = make_identity()
    upsert_player(migrated_session, identity=identity)
    migrated_session.commit()
    outcome = upsert_player(migrated_session, identity=identity)
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.UNCHANGED


def test_changed_identity_updates_the_same_row(migrated_session: Session) -> None:
    upsert_player(migrated_session, identity=make_identity())
    migrated_session.commit()

    updated = make_identity(full_name="J-Rod", primary_position="OF")
    outcome = upsert_player(migrated_session, identity=updated)
    migrated_session.commit()

    assert outcome is PlayerPersistenceOutcome.UPDATED
    stored = migrated_session.query(PlayerRecord).all()
    assert len(stored) == 1
    assert get_player(migrated_session, player_id=PLAYER_ID) == updated


def test_update_preserves_created_at_and_bumps_updated_at(
    migrated_session: Session,
) -> None:
    upsert_player(migrated_session, identity=make_identity())
    migrated_session.commit()
    original = migrated_session.query(PlayerRecord).one()
    original_created_at = original.created_at
    original_updated_at = original.updated_at

    upsert_player(migrated_session, identity=make_identity(full_name="J-Rod"))
    migrated_session.commit()

    stored = migrated_session.query(PlayerRecord).one()
    assert stored.created_at == original_created_at
    assert stored.updated_at >= original_updated_at


def test_unchanged_rerun_does_not_bump_updated_at(migrated_session: Session) -> None:
    upsert_player(migrated_session, identity=make_identity())
    migrated_session.commit()
    original_updated_at = migrated_session.query(PlayerRecord).one().updated_at

    upsert_player(migrated_session, identity=make_identity())
    migrated_session.commit()

    assert migrated_session.query(PlayerRecord).one().updated_at == original_updated_at


def test_unknown_player_returns_none(migrated_session: Session) -> None:
    assert get_player(migrated_session, player_id=999999) is None


# ---------------------------------------------------------------------------
# upsert_player_season_hitting
# ---------------------------------------------------------------------------


def _seed_player(session: Session) -> None:
    upsert_player(session, identity=make_identity())
    session.commit()


def test_new_season_hitting_is_inserted(migrated_session: Session) -> None:
    _seed_player(migrated_session)
    hitting = make_hitting()
    outcome = upsert_player_season_hitting(migrated_session, hitting=hitting)
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.INSERTED
    stored = get_player_season_hitting(
        migrated_session, player_id=PLAYER_ID, season=SEASON
    )
    assert stored == hitting


def test_identical_season_hitting_rerun_is_unchanged(migrated_session: Session) -> None:
    _seed_player(migrated_session)
    hitting = make_hitting()
    upsert_player_season_hitting(migrated_session, hitting=hitting)
    migrated_session.commit()
    outcome = upsert_player_season_hitting(migrated_session, hitting=hitting)
    migrated_session.commit()
    assert outcome is PlayerPersistenceOutcome.UNCHANGED


def test_changed_season_hitting_updates_the_same_row(
    migrated_session: Session,
) -> None:
    _seed_player(migrated_session)
    upsert_player_season_hitting(migrated_session, hitting=make_hitting())
    migrated_session.commit()

    updated = make_hitting(hits=175, home_runs=25)
    outcome = upsert_player_season_hitting(migrated_session, hitting=updated)
    migrated_session.commit()

    assert outcome is PlayerPersistenceOutcome.UPDATED
    stored = migrated_session.query(PlayerSeasonHittingRecord).all()
    assert len(stored) == 1
    assert (
        get_player_season_hitting(migrated_session, player_id=PLAYER_ID, season=SEASON)
        == updated
    )


def test_in_progress_season_totals_can_increase_without_duplicating(
    migrated_session: Session,
) -> None:
    """Simulates a mid-season rerun where counting stats have only grown."""
    _seed_player(migrated_session)
    upsert_player_season_hitting(
        migrated_session,
        hitting=make_hitting(
            games_played=50, hits=50, at_bats=180, doubles=5, triples=1, home_runs=5
        ),
    )
    migrated_session.commit()

    grown = make_hitting(
        games_played=100, hits=110, at_bats=360, doubles=10, triples=2, home_runs=10
    )
    upsert_player_season_hitting(migrated_session, hitting=grown)
    migrated_session.commit()

    stored_rows = migrated_session.query(PlayerSeasonHittingRecord).all()
    assert len(stored_rows) == 1
    assert stored_rows[0].hits == 110


def test_update_preserves_created_at_and_bumps_updated_at_for_hitting(
    migrated_session: Session,
) -> None:
    _seed_player(migrated_session)
    upsert_player_season_hitting(migrated_session, hitting=make_hitting())
    migrated_session.commit()
    original = migrated_session.query(PlayerSeasonHittingRecord).one()
    original_created_at = original.created_at

    upsert_player_season_hitting(migrated_session, hitting=make_hitting(hits=160))
    migrated_session.commit()

    stored = migrated_session.query(PlayerSeasonHittingRecord).one()
    assert stored.created_at == original_created_at
    assert stored.updated_at >= original.updated_at


def test_different_seasons_for_same_player_are_separate_rows(
    migrated_session: Session,
) -> None:
    _seed_player(migrated_session)
    upsert_player_season_hitting(migrated_session, hitting=make_hitting(season=2024))
    upsert_player_season_hitting(migrated_session, hitting=make_hitting(season=2025))
    migrated_session.commit()

    stored = migrated_session.query(PlayerSeasonHittingRecord).all()
    assert {row.season for row in stored} == {2024, 2025}


def test_unknown_player_season_returns_none(migrated_session: Session) -> None:
    _seed_player(migrated_session)
    assert (
        get_player_season_hitting(migrated_session, player_id=PLAYER_ID, season=1999)
        is None
    )
