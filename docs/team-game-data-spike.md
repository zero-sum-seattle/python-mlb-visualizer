# Team game-level data spike (Milestone 1)

Goal: find the cleanest reliable way to retrieve every completed regular-season
game for one MLB team and season, with team hits and runs, using
[`python-mlb-statsapi`](https://pypi.org/project/python-mlb-statsapi/) 0.8.0.

All findings below come from running the installed package against the live MLB
Stats API for the completed 2025 regular season. Request counts and field
observations are measured, not assumed.

## 1. Approaches investigated

### 1a. Team hitting statistics with the `gameLog` stat type

```python
mlb.get_team_stats(136, stats=["gameLog"], groups=["hitting"], season=2025, gameType="R")
```

Calls `GET /api/v1/teams/{teamId}/stats` once and returns
`{"hitting": {"gameLog": Stat}}`, where `Stat.splits` is a list of
`mlbstatsapi.models.stats.HittingGameLog`. One split per played game: 162 splits
for each of the 2025 Mariners (136), Red Sox (111), Cardinals (138), Orioles
(110), Rockies (115), and Yankees (147).

Fields available on a split:

| Need | Source | Available |
| --- | --- | --- |
| Game id | `split.game.game_pk` | yes |
| Game date | `split.date` (`"2025-08-17"`) | yes |
| Season | `split.season` (`"2025"`) | yes |
| Selected team | `split.team.id`, `split.team.name` | yes |
| Opponent id | `split.opponent.id` | yes |
| Opponent name | `split.opponent.name` | **no, always `None`** |
| Home or away | `split.is_home` | yes |
| Team hits | `split.stat.hits` | yes |
| Team runs | `split.stat.runs` | yes |
| Game status | — | **not in the payload** |
| Game number | — | **dropped by the package model** |
| Scheduled innings | — | **not in the payload** |

The raw opponent object is only `{"id": 133, "link": "/api/v1/teams/133"}`, so
`opponent.name` is `None`. The raw `game` object does contain `gameNumber`, but
`mlbstatsapi.models.game.Game` models only `game_pk`, `link`, `metadata`,
`game_data`, and `live_data`, and `MLBBaseModel` is configured with
`extra="ignore"`, so the game number is discarded during parsing.

Postponed and cancelled games never produce a split, because no stats exist for
a game that was not played.

### 1b. Team schedule plus game box score

```python
mlb.get_schedule(start_date=..., end_date=..., team_id=136, gameTypes="R")
mlb.get_game_box_score(game_pk)   # once per game
```

163 requests for a 162-game season. `BoxScore.teams.home` is a
`BoxScoreTeam` whose `team_stats` field is typed as a bare `dict`, so team hits
require `box_score.teams.home.team_stats["batting"]["hits"]`. Verified on game
778547: `team_stats` keys are `['batting', 'pitching', 'fielding']` and
`batting["hits"]` is 5. That is exactly the kind of undocumented dictionary
access this milestone is meant to avoid, and it is also the most expensive
option. Rejected.

### 1c. Team schedule plus game linescore

```python
mlb.get_game_line_score(game_pk)  # once per game
```

Also 163 requests. Unlike the box score this path is fully typed:
`Linescore.teams.home` and `.away` are `LinescoreTeamScoring` with `hits`,
`runs`, `errors`, and `left_on_base`. Verified on game 778547: home
`hits=5 runs=4`, away `hits=3 runs=2`, which matches the Mariners' `gameLog`
split for that game exactly. The linescore carries no team identity at all, so
the schedule is still required to decide which side is the selected team.
Correct but 54x the requests of the chosen approach. Rejected as the primary
path; it remains the natural fallback if the `gameLog` stat type ever stops
returning per-game hitting.

### 1d. Rejected shortcut: `schedule?hydrate=linescore`

`GET /api/v1/schedule?...&hydrate=linescore` returns the linescore inline, which
would make the whole task a single request. The raw response does include
`linescore.teams.home.hits`, but `ScheduleGames` has no `linescore` field and
`MLBBaseModel` ignores unknown keys, so the package silently drops it. Using it
would mean bypassing the package's models and parsing raw JSON. Rejected.

## 2. Chosen approach

Team hitting `gameLog` for the hitting numbers, joined on `gamePk` with a single
team schedule request for the game context the stat split does not carry.

Three requests per team-season, independent of how many games are played:

| Call | Purpose |
| --- | --- |
| `Mlb.get_team(team_id, season=season)` | Confirm the id is an MLB team (`team.sport.id == 1`) and get its name for that season |
| `Mlb.get_team_stats(team_id, stats=["gameLog"], groups=["hitting"], season=..., gameType="R")` | Per-game hits and runs, home/away, opponent id, date |
| `Mlb.get_schedule(start_date=f"{season}-01-01", end_date=f"{season}-12-31", sport_id=1, team_id=..., gameTypes="R")` | Status, opponent name, game number, doubleheader flag, scheduled innings |

Package return models the service depends on:

- `mlbstatsapi.models.stats.HittingGameLog` (`date`, `is_home`, `team`,
  `opponent`, `game`, `stat`)
- `mlbstatsapi.models.stats.SimpleHittingSplit` (`hits`, `runs`)
- `mlbstatsapi.models.schedules.Schedule` → `ScheduleDates` → `ScheduleGames`
  (`game_pk`, `official_date`, `status`, `teams`, `game_number`,
  `double_header`, `scheduled_innings`)
- `mlbstatsapi.models.game.gamedata.GameStatus` (`coded_game_state`,
  `detailed_state`)
- `mlbstatsapi.models.teams.Team` (`id`, `name`, `sport`)

Every field used is a declared model attribute. No raw response dictionaries are
read.

### The join is enforced, not assumed

The two sources were first measured against each other across the full 2025
regular seasons of teams 136, 111, 138, 110, 115, and 147 (972 games), and they
agreed on every game. Rather than rely on that observation, the service now
checks the overlap for every game it normalizes and raises `TeamGameDataError`
when any of these disagree:

| Invariant | Sources compared |
| --- | --- |
| Selected team | `split.team.id` vs the requested team id |
| Official date | `split.date` vs `ScheduleGames.official_date`, both parsed as dates |
| Opponent | `split.opponent.id` vs the other side of `ScheduleGames.teams` |
| Home or away | `split.is_home` vs which side of `ScheduleGames.teams` holds the team id |
| Runs | `split.stat.runs` vs the selected side's schedule `score` |

Every message names the `gamePk`, the invariant, and both conflicting values.
The score check is skipped when `ScheduleGameTeam.score` is `None`, because that
field is optional upstream; it is not treated as a mismatch.

Team **names** are deliberately excluded from the comparison. The game log
reports the franchise's current name while the team lookup reports its name for
the requested season, so the two legitimately differ for a renamed or relocated
club.

Home/away and opponent are taken from the schedule's structured `teams.home` /
`teams.away` blocks, which also supply the opponent's display name. No string
parsing is involved anywhere.

These checks exist to catch a future upstream field change or package-model
change as a loud failure rather than a silently wrong record. They hold on real
data for every season spot-checked from 1908 to 2025, including the 60-game 2020
season and the 2021 seven-inning doubleheaders.

### Duplicate splits

The hitting game log is expected to return one split per game. A repeated
`gamePk` is accepted only when the two splits normalize to an identical
`TeamGameBattingLine`; the duplicate is then ignored. If the normalized records
differ, the service raises `TeamGameDataError` naming the `gamePk` and each
field that conflicts, for example `hits 6 vs 9`. Neither the first nor the last
value is silently preferred.

## 3. Status filtering rules

Completed means `ScheduleGames.status.coded_game_state in {"F", "O"}`.

- `F` — Final, including the `Completed Early: *` (rain-shortened) variants
- `O` — Game Over, also including `Completed Early: *`

Everything else is excluded: `D` postponed, `C` cancelled, `U` suspended,
`I` in progress, `S`/`P` preview, `Q`/`R` forfeit, `X`/`W` other.

**`abstractGameState` must not be used for this.** It is `"Final"` for postponed
and cancelled games as well. League-wide 2025 regular season (2464 schedule
rows), every row reported `abstractGameState == "Final"`:

| Count | codedGameState | statusCode | detailedState |
| --- | --- | --- | --- |
| 2429 | F | F | Final |
| 23 | D | DR | Postponed |
| 6 | D | DI | Postponed |
| 4 | F | FR | Completed Early |
| 1 | D | DS | Postponed |
| 1 | F | FO | Completed Early |

The stored `status` value is `detailed_state`, so rain-shortened games are
visible as `Completed Early: Rain` rather than being flattened to `Final`.

The full status vocabulary is documented by MLB at
`GET https://statsapi.mlb.com/api/v1/gameStatus` (210 entries).

## 4. Doubleheader behaviour

`ScheduleGames.double_header` is `"N"` (none), `"S"` (split doubleheader, two
admissions) or `"Y"` (traditional doubleheader). 2025 regular season: 2403 `N`,
49 `S`, 12 `Y`, with 30 rows at `game_number == 2`.

Both games of a doubleheader have their own `gamePk` and their own `gameLog`
split, so both are emitted as separate records.

Sorting by date and game id is **not** sufficient. The Cubs' 2025-08-19 split
doubleheader is `gamePk 776691` for game 1 and `gamePk 776676` for game 2, so
game ids run backwards relative to game numbers. The service sorts by
`(game_date, game_number, game_pk)`.

`doubleheader` mirrors MLB's flag rather than counting games on a date. Cubs
game 777459 was the only game played on 2025-08-18 but is flagged `"S"`, because
its scheduled counterpart was moved to 2025-08-19.

## 5. Postponed, cancelled, and duplicated schedule rows

A postponed game **keeps its `gamePk`** when it is made up, so the same game
appears twice in one team's schedule: once under the original date with
`coded_game_state == "D"`, and once under the date it was actually played with
`"F"`. League-wide 2025 the schedule returned 2464 rows for 2430 distinct games.

The two rows can disagree. For Cubs game 776691 the postponed row reports
`gameNumber 2` and the played row reports `gameNumber 1`. The service indexes the
schedule by `game_pk` and keeps the completed row, so game context always comes
from the row describing the game that was played, and no game is emitted twice.

Cancelled games (`C`) appear once, carry no scores, and never appear in the
game log.

## 6. Suspended-game behaviour

- **Suspended and not resumed**: `abstractGameState` `Live`,
  `coded_game_state` `U` (`Suspended`, `Suspended: Rain`, …). Excluded, because
  the hits and runs are not final. The 2025 regular season contained no such
  game, so this path is covered only by synthetic fixture data, not by verified
  live data.
- **Suspended and later resumed**: game 777294 (Reds at Red Sox) was suspended
  on 2025-07-01 and resumed on 2025-07-02. The Red Sox schedule lists it twice,
  both rows `coded_game_state == "F"` with `official_date == "2025-07-01"`, one
  carrying `resume_date` and the other `resumed_from`. The hitting game log has
  exactly one split for it, dated 2025-07-01, with 8 hits and 5 runs. The
  service therefore emits one record dated the original official date. This was
  verified for that single game only; a resumption whose official date moves has
  not been observed.

## 7. Extra-inning and seven-inning games

Nothing in the service assumes nine innings. `scheduled_innings` is carried
through from the schedule. All 2464 rows of the 2025 regular season were
scheduled for 9 innings, but MLB used 7-inning doubleheader games in 2020 and
2021: the 2021 Mariners return 157 nine-inning games and 5 seven-inning games,
including both halves of the 2021-04-13 and 2021-04-15 doubleheaders.
Extra-inning games simply report more innings played than scheduled.

## 8. Known limitations

- Opponent display name and game status are only available because of the
  schedule request. A game log split with no matching schedule row raises
  `TeamGameDataError` rather than being dropped.
- `mlb_module.create_split_data` → `return_splits` only builds a split when the
  raw `stat` object is truthy, so a played game returned with an empty stat block
  would silently disappear before the service sees it. This cannot be detected
  from the package's return values.
- The schedule window is the calendar year of the season
  (`{season}-01-01` … `{season}-12-31`). That covers every MLB regular season to
  date, including the 2025 Tokyo Series in March, but a season spanning two
  calendar years would need a different window.
- `reverse_home_away_status` was `False` for all 2464 rows of the 2025 regular
  season, so a game where the designated home team bats first has not been
  exercised. Home and away follow MLB's designated `teams.home` / `teams.away`
  blocks, not batting order.
- Team ids are the stable identity; names are display values that change. The
  selected team's name is requested for the season under inspection, so team 133
  is `"Oakland Athletics"` for 2024 and `"Athletics"` for 2025, and team 120 is
  `"Montreal Expos"` for 2004. Opponent names come from that season's schedule
  entry and are historical for the same reason.
- Requesting a season before a team existed 404s on `teams/{id}?season=...`, so
  `get_team` returns `None` and the service raises `TeamNotFoundError` naming the
  season rather than reporting an empty game log.
- Minor league team ids are rejected via `team.sport.id != 1`, which costs the
  extra `get_team` request.
- Only team-level hitting is covered. Pitching, fielding, and player-level
  stats are out of scope for this milestone.

## 9. Recommendation for Milestone 2 ingestion

Milestone 2 implemented team-season persistence as described in
`docs/team-season-ingestion.md`. Summary:

- Ingest per team-season with `get_team_game_batting_lines`; three requests per
  team-season means a full league season is 90 requests, which needs no
  batching, caching, or background worker.
- Use `(team_id, game_pk)` as the natural unique key. `game_pk` is stable across
  postponement and resumption, so upserting on that pair is idempotent and
  re-running an ingest cannot create duplicates.
- Treat a `TeamGameDataError` from an ingest run as a data-integrity alarm rather
  than a game to skip. The cross-source invariants and the conflicting-duplicate
  check exist so that an upstream or package-model change fails the ingest
  instead of writing a wrong row.
- Persist `status`, `game_number`, `doubleheader`, and `scheduled_innings`
  alongside hits and runs. `status` is what makes the completeness rule
  auditable after the fact, and `game_number` is required to order a
  doubleheader.
- For an in-progress season, re-ingest the whole season rather than appending
  recent dates: postponed and suspended games mutate existing schedule rows and
  can change a game's date, game number, and doubleheader flag after the fact.
- Share one `Mlb` client across an ingest run by passing it as the `client`
  argument, so a single HTTP session and its retry policy are reused.
- Keep the linescore path (1c) documented as the fallback. If per-game hitting
  is ever needed for a single game rather than a whole season,
  `get_game_line_score` is the typed way to get it.
