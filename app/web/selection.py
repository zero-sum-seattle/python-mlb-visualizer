"""Turning persisted team-seasons into selector options and a default choice."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.schemas.catalog import AvailableTeamSeason

SEATTLE_MARINERS_TEAM_ID = 136


@dataclass(frozen=True)
class TeamOption:
    """One team in the team selector, with the seasons stored for it."""

    team_id: int
    team_name: str
    seasons: tuple[int, ...]


def build_team_options(
    available: Sequence[AvailableTeamSeason],
) -> list[TeamOption]:
    """Group persisted team-seasons into one selector entry per team.

    A franchise stores a historical name per season, so the selector shows the
    name from the most recent season it has while every season stays listed.
    """
    seasons_by_team: dict[int, list[AvailableTeamSeason]] = {}
    for entry in available:
        seasons_by_team.setdefault(entry.team_id, []).append(entry)

    options: list[TeamOption] = []
    for team_id, entries in seasons_by_team.items():
        newest_first = sorted(entries, key=lambda entry: entry.season, reverse=True)
        options.append(
            TeamOption(
                team_id=team_id,
                team_name=newest_first[0].team_name,
                seasons=tuple(entry.season for entry in newest_first),
            )
        )

    options.sort(key=lambda option: (option.team_name.casefold(), option.team_id))
    return options


def build_team_seasons_catalog(
    options: Sequence[TeamOption],
) -> dict[str, list[int]]:
    """Map each team id to its stored seasons, newest first.

    The page embeds this so the season selector can be rebuilt in the browser
    when the team changes, without a second request. Keys are strings because
    they are compared against a ``<select>`` value.
    """
    return {str(option.team_id): list(option.seasons) for option in options}


def select_team(
    options: Sequence[TeamOption],
    requested_team_id: int | None,
) -> TeamOption | None:
    """Resolve the team to display.

    Without a request, Seattle is preferred when stored, otherwise the first
    team alphabetically. A requested team that is not stored resolves to None
    so the caller can show a not-found state instead of silently substituting
    different data.
    """
    if not options:
        return None

    by_id = {option.team_id: option for option in options}
    if requested_team_id is not None:
        return by_id.get(requested_team_id)
    return by_id.get(SEATTLE_MARINERS_TEAM_ID, options[0])


def select_season(option: TeamOption, requested_season: int | None) -> int | None:
    """Resolve the season to display, defaulting to the most recent stored one."""
    if requested_season is None:
        return option.seasons[0]
    return requested_season if requested_season in option.seasons else None
