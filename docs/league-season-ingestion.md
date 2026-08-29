# League-wide season ingestion

This document describes how Milestone 4 imports an entire MLB season and how the
application establishes that the import actually covered every team.

## 1. The problem this solves

Before Milestone 4 the only way to fill the database was one team at a time:

```bash
poetry run python scripts/import_team_season.py --team-id 136 --season 2025
```

That works, but it leaves no way to answer the question league-wide analytics
will eventually depend on:

> Was every MLB team for this season successfully ingested?

Counting rows cannot answer it. A season's table could hold a great many rows
and still be missing a club, or hold every club and be missing games. Milestone 4
therefore records what a league-wide run *did* — which teams it discovered, which
it ingested, which it failed on — rather than inferring coverage after the fact.

This is a data-ingestion and data-integrity milestone. It adds no visualization.

## 2. Team-game records, not games

One real MLB game produces **two** team batting lines, one per club. Stored
league-wide, a standard 30-team, 162-game season is:

```text
4860 team-game records  ==  2430 MLB games
```

Counts reported by this feature — `team_game_records_fetched`, `inserted`,
`updated`, `unchanged` — are always team-game records. Never describe 4,860 rows
as 4,860 games. The number 4,860 is also not a completeness requirement anywhere
in the code; see section 6.

## 3. Team discovery

### Method and model

| | |
| --- | --- |
| Library call | `mlbstatsapi.Mlb.get_teams(sport_id=1, season=<year>)` |
| HTTP endpoint | `GET https://statsapi.mlb.com/api/v1/teams?sportId=1&season=<year>` |
| Returns | `list[mlbstatsapi.models.teams.Team]` |
| Arguments passed | `sport_id=1` (Major League Baseball) and `season=<four digit year>`; nothing else |
| Domain model produced | `app/schemas/teams.py::MlbTeam` (`team_id`, `team_name`, `season`) |

Discovery lives in `app/services/league_teams.py::discover_mlb_teams`.

### How MLB eligibility is determined

`sportId=1` already scopes the request, but every returned record is re-checked
before it is accepted:

- `Team.sport.id` must be `1`. This is the same rule the existing single-team
  path already applies in `team_game_logs._fetch_mlb_team`, so a club that would
  be refused as a single import is refused here too. It also means a broader
  upstream response, or a caller passing extra parameters, cannot pull a minor
  league or other-sport club into an MLB league import.
- `Team.all_star_status` must not be `"Y"`. All-Star squads carry the Major
  League sport id but play no regular season, so they have no regular-season
  game log to ingest.
- `Team.name` must be present. A nameless club cannot be reported to an
  operator, and guessing a name would fabricate source data.
- `Team.season`, when upstream sets it, must equal the requested season. A
  mismatch means the `season` parameter was not honored, which would silently
  ingest the wrong club set — a data-integrity failure, not a value to work
  around.
- No team id may appear twice among the eligible clubs. A repeat is refused
  with `MlbTeamDiscoveryError` naming the id and the season, never silently
  deduplicated: `teams_discovered` is the number coverage is measured against,
  so collapsing a duplicate would quietly shrink what the run claims to have
  covered. Two different names under one id is the same failure — the id is the
  identity every stored record is keyed by. A duplicate among *ineligible*
  clubs is irrelevant, since those never enter the run.

Clubs are returned sorted by name then id, so a run visits them in a stable,
reproducible order.

An empty result is treated as a discovery **failure**
(`NoMlbTeamsDiscoveredError`), not as "no teams played that season". The library
returns `[]` for any 4xx response, so `[]` cannot be read as a factual answer.

### Season awareness

The `season` argument is what makes discovery season-aware: MLB resolves both
the club set and each club's name as of that season. Nothing in this feature
hardcodes today's 30 team ids, and no such list exists in the codebase.

`MlbTeam.team_name` is the name upstream reports for the requested season, so a
franchise that has since been renamed or relocated keeps its contemporary name.
This matches the convention persisted game rows already follow (see
`docs/team-season-ingestion.md`, section 3).

### Limitations, and what was and was not verified

The behavior above was determined by reading the installed
`python-mlb-statsapi` 0.8.0 source (`mlbstatsapi/mlb_api.py`,
`mlbstatsapi/models/teams/team.py`) and the parameters that endpoint documents.

**It was not verified against the live MLB Stats API.** Outbound access to
`statsapi.mlb.com` was denied by the network policy of the environment this
milestone was implemented in, so no live request of any kind was made. The
automated tests are offline by design and prove how *this application* treats
the upstream response; they cannot prove what MLB actually returns for, say,
1969. Specifically unconfirmed:

- whether every historical season returns exactly the clubs that played it;
- how far back the endpoint's season support usefully extends;
- whether any season returns records this service's eligibility rules would
  wrongly include or exclude.

Do not assume a season has 30 teams, current franchise names, the current league
structure, or 162 games per club. Nothing in the implementation assumes any of
those. A historical import should be spot-checked against a known club list the
first time it is run.

### Splits the package discards before the application sees them

`python-mlb-statsapi` drops a stat split whose raw stat object is empty. A
completed game can therefore be missing from the parsed hitting game log
without any error being raised anywhere upstream of this application.

That limitation still exists — the package is unchanged, and the batting line
for such a game is simply not available. What changed is that the application
now **detects the consequence**: the completed game is still in the schedule, so
the reverse completeness check in section 5 finds it missing from the normalized
set and refuses the team-season.

This does not recover the missing batting data. It converts a silently short
team-season into an explicit failure, so the club is reported failed and the
season's coverage is recorded `INCOMPLETE` rather than `COMPLETE`. Re-running
later is the remedy, if and when upstream returns the split.

## 4. Architecture

```text
CLI ───────────────┐
scheduler ─────────┼──► league ingestion service ─► MLB / database
admin operation ───┘
```

The league ingestion service is `app/services/league_season_ingestion.py::ingest_league_season`.
Expanded, one run looks like:

```text
ingest_league_season
      │
      ├─► discover_mlb_teams            (app/services/league_teams.py)
      │
      └─► for each discovered team:
              ingest_team_season        (app/services/team_season_ingestion.py)
                    │
                    ├─► get_team_game_batting_lines   (unchanged)
                    └─► upsert_team_season            (unchanged)
```

The league service is **orchestration only**. It contains no MLB fetching, no
schedule handling, no hitting or strikeout normalization, no cross-source
validation, no domain construction, no ORM mapping, and no upsert comparison.
All of that already existed and is called, not re-implemented. It performs no
baseball analytics either.

The future trigger model matters here: a scheduler or an admin operation should
call `ingest_league_season(...)` **directly**, as a Python function, the same way
the CLI does. Executing `scripts/import_league_season.py` in a subprocess is not
the architecture — the script is a thin adapter around the service, and so is
anything else that triggers a run. This follows the operational-entry-point rule
in the root `AGENTS.md`.

### Reuse of team-season ingestion

`ingest_team_season(...)` was reused unchanged; Milestone 4 required no
refactor of it. Its existing signature already accepted an injected client:

```python
ingest_team_season(session=..., team_id=..., season=..., client=...)
```

The league service passes its own client through, so one `mlbstatsapi.Mlb`
client serves discovery and every team fetch in the run rather than one client
being opened per team.

### Sequential by design

Teams are ingested one at a time, in discovery order. Roughly thirty
team-seasons is not enough work to justify concurrency, and sequential ingestion
buys simpler failure attribution, easier debugging and testing, lower upstream
load, and predictable SQLite write behavior. No asyncio orchestration, thread or
process pool, task queue, or worker was added. If a measured runtime later
proves unacceptable, concurrency should be proposed separately with those
measurements.

> **Update (issue #31):** a measured runtime did later prove worth addressing.
> `ingest_league_season` and this sequential design remain unchanged and
> available as the default and as a reference/debug path. A second,
> bounded-concurrency entry point, `ingest_league_season_async`, was added
> alongside it. See `docs/async-league-ingestion.md` for that design; this
> section is left as-is because it accurately describes why the sequential
> path exists and was correct at the time it was written.

## 5. Coverage semantics

### What "covered" means

A season's coverage is `COMPLETE` when, in a single league-wide run, **both**
of these held:

1. every MLB team discovered for that season was attempted and successfully
   ingested; and
2. for each of those teams, every completed regular-season schedule game was
   represented in the normalized hitting game log.

The second condition is enforced inside the existing team-season path, not by
the league service. `get_team_game_batting_lines` compares the set of completed
schedule games — `codedGameState` in `{"F", "O"}`, after the existing `gamePk`
deduplication that folds a postponed row into its made-up one — against the set
of games it normalized. A completed game with no batting line raises
`TeamGameDataError` naming the team, the season, and the missing `gamePk`s, so
that club fails and the league run cannot be `COMPLETE`.

This means a club counted as succeeded is a club whose stored season has no
known hole in it. Missing games are never filled with zeros and a short season
is never returned as if it were whole.

Coverage is **not** inferred from any of: the number of stored rows, thirty team
ids being present, 4,860 team-game records existing, 162 games per club, or the
process exit code alone. It is recorded from what the run actually did. Adding
the per-game check does not change that — it strengthens what "successfully
ingested" means for one club, without introducing an expected game count.

The counts a run reports satisfy, and the schemas enforce:

```text
teams_discovered = teams_succeeded + teams_failed
team_game_records_fetched = inserted + updated + unchanged
```

and per team, the existing `fetched = inserted + updated + unchanged` invariant
is preserved.

### Ingestion coverage is not season finality

These are two different things, and only the first is what Milestone 4
establishes:

| Concept | Meaning | Established here? |
| --- | --- | --- |
| League ingestion coverage | Every team discovered for the season was successfully refreshed by this run | Yes |
| Season finality | The regular season itself has finished being played | No |

`COMPLETE` describes the refresh, not the season. A league-wide ingestion of an
in-progress season can legitimately reach `COMPLETE` while every club still has
games left to play — the run covered every team, and every team's stored games
are current as of that run.

This is why the CLI prints `Ingestion coverage: COMPLETE` rather than
`Status: COMPLETE`, and why the enum's docstring says so explicitly. For a
finished historical season such as 2025, `COMPLETE` coverage does support
treating the stored season as a trustworthy complete dataset — but that
conclusion comes from the season being over, which the application does not
itself assert, plus the coverage the application does assert.

No separate season-state infrastructure was invented for this distinction. It is
carried by naming, wording, and this document.

Milestone 5 is the first reader of this state. `COMPLETE` is the only value
that lets the team hits page describe a number as an MLB-wide average, and its
wording repeats the distinction above rather than relaxing it. See
`docs/team-vs-mlb-comparison.md`.

## 6. Persistence

### Schema

Table: `league_season_ingestions` (`app/database/models.py::LeagueSeasonIngestionRecord`)

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `season` | Unique — one row per season |
| `status` | `RUNNING`, `COMPLETE`, or `INCOMPLETE` |
| `expected_team_count` | Teams discovered for the season |
| `successful_team_count` | Teams ingested successfully |
| `failed_team_count` | Teams that failed |
| `started_at` | When the run began |
| `completed_at` | Null while `RUNNING` |

Operational league-import metadata is deliberately **not** stored on
`team_game_batting_lines` rows. It is a property of a run, not of a batting
line: putting it there would repeat one run's status across thousands of game
rows and leave nowhere to record a club that failed before writing any row at
all.

### Record semantics, and why

The table holds **current state for one season**, not an attempt log. A rerun
overwrites the season's row.

That is the simplest model supporting everything the milestone needs:

- knowing whether the most recent league-wide ingestion of a season succeeded;
- knowing how many teams were expected, succeeded, and failed;
- retrying an incomplete ingestion (just run it again);
- letting future league analytics decide whether a season's coverage is
  trustworthy.

Import history and per-attempt auditing were left out on purpose. Nothing in the
application reads them, and adding them would mean maintaining a growing table
with no reader — speculative infrastructure the root `AGENTS.md` warns against.
If a real need for history appears later, an attempts table can be added then.

Starting a run resets the season's row to `RUNNING` before any team is fetched.
A season's coverage is only ever as good as its most recent league-wide
ingestion, so a new run invalidates the old answer the moment it begins, rather
than leaving a stale `COMPLETE` readable while teams are being re-fetched.

### Constraints

The CHECK constraints keep the coverage claim honest at the database level, not
only in application code:

- `status` must be one of the three known values;
- `completed_at` is set if and only if the run is no longer `RUNNING`;
- for a finished run, `successful + failed = expected`;
- `COMPLETE` requires `failed_team_count = 0` **and** `expected_team_count > 0`.

The last one is the important one: a partial import cannot be recorded as
complete even if application code is later changed carelessly.

### Migration

Alembic revision `7f2c4b8e91d3` (`create_league_season_ingestions`), whose
`down_revision` is the Milestone 3.5 strikeouts revision `94dec6973c80`.

The revision only creates a new table. It does not read, write, or rebuild
`team_game_batting_lines`, so existing team-game records and the Milestone 3.5
`strikeouts` column and its history are untouched. Migration tests build a
database at the pre-Milestone-4 revision, insert real game rows including both a
row with a recorded strikeout total and a row whose total is still `NULL`,
upgrade, and assert every row, the query index, and the existing check
constraints survive. Downgrade drops only the new table and is tested to leave
game data intact, including an upgrade → downgrade → upgrade round trip.

There is no `Base.metadata.create_all()` anywhere in application startup, and no
migration path deletes or rebuilds a user's database.

## 7. Transaction boundaries

No database transaction is ever held open across an MLB network request:

```text
discover teams                      network only, no transaction
record RUNNING                      short transaction, committed
for each team:
    fetch team-season from MLB      network only, no transaction
    persist team-season             short transaction, committed
build and validate the result       no transaction
record COMPLETE / INCOMPLETE        short transaction, committed
```

The last two steps are in that order deliberately. `LeagueSeasonIngestionResult`
enforces invariants the coverage row cannot express on its own — each discovered
team appearing exactly once, aggregates matching the summed per-team counts,
status agreeing with the failure count. Recording coverage first would let the
database claim `COMPLETE` for a run the domain model then rejects. So the
validated result is the *precondition* for writing coverage, not a description
of what was already written.

If that validation fails, the error propagates and the season's row stays
`RUNNING` — the honest state, meaning the run did not establish trustworthy
coverage. Teams already ingested stay committed; only the coverage claim is
withheld.

Each team-season commits on its own, using the existing team ingestion
transaction semantics unchanged: MLB retrieval completes before the transaction
begins, and rows missing from the latest fetch are not deleted.

### Crash behavior

If the process dies mid-run, the season's row is left `RUNNING` with no
`completed_at`. That row is a statement about the past, not a lock: it means the
previous run never finished, so that season's coverage is unknown and must not
be trusted. The next invocation overwrites it and proceeds normally.

There is no lease, heartbeat, or resume checkpoint. Idempotent full reruns make
them unnecessary.

## 8. Partial failure

A partial league import is never marked complete. A run where one club fails
records:

```text
Teams discovered: 30
Teams succeeded: 29
Teams failed:    1
Ingestion coverage: INCOMPLETE
```

The 29 successful team imports **stay committed**. A whole league is not rolled
back because the thirtieth club failed after twenty-nine successful team-season
transactions — that would throw away good data and make the rerun more
expensive, for no integrity gain.

Each failed team keeps its id, its name, and its error message in the result and
in the CLI output, so an operator knows exactly which club is missing and why.

Failure boundaries are kept distinct rather than flattened into "league import
failed":

| Failure | Error | Effect |
| --- | --- | --- |
| Invalid season input | `InvalidSeasonError` | Nothing requested, no state written |
| Team discovery failed upstream | `MlbTeamDiscoveryError` | Nothing attempted, no state written |
| Zero eligible teams discovered | `NoMlbTeamsDiscoveredError` | Nothing attempted, no state written |
| Same team id discovered twice | `MlbTeamDiscoveryError` | Nothing attempted, no state written |
| A club's season is missing a completed game | `TeamGameDataError`, recorded per team | Other teams continue; run is `INCOMPLETE` |
| One team's MLB fetch or persistence failed | Recorded per team | Other teams continue; run is `INCOMPLETE` |
| Coverage metadata could not be persisted | `LeagueIngestionStateError` | Raised; coverage is unknown |

Wrapped exceptions preserve chaining (`raise ... from exc`) so the underlying
cause is never lost. Only the ingestion path's own errors are absorbed into a
per-team result; an unexpected error propagates rather than being reported as an
ordinary missing team.

Season validation rejects anything outside `1876` (MLB's first National League
season) through next year. Next year is allowed because a schedule is published
before a season starts; a typo such as `20255` still fails immediately rather
than after thirty upstream requests.

## 9. Rerun and idempotency

A rerun re-attempts every discovered team. There is no checkpoint or resume
machinery, because the existing upsert already makes a full rerun safe:

- rows identical to the latest MLB response are left untouched
  (`updated_at` does not change);
- changed rows are updated;
- missing rows are inserted;
- rows already stored but absent from the latest response are not deleted.

So a second identical league import reports everything as `unchanged`, and a
rerun after a partial failure fills in the missing clubs and can move the season
from `INCOMPLETE` to `COMPLETE`.

Note that both clubs in a game are stored under the same `game_pk`, one row
each, keyed by the existing `(team_id, game_pk)` unique constraint. League-wide
storage is exactly the case that key was designed for.

## 10. CLI

`scripts/import_league_season.py` is a thin operational adapter: argument
parsing, dependency construction, service invocation, result formatting, and
exit-code selection. It contains no ingestion logic.

```bash
poetry run alembic upgrade head
poetry run python scripts/import_league_season.py --season 2025
poetry run python scripts/import_league_season.py --season 2025 --format json
```

Table output reports each club as it finishes, then the totals:

```text
MLB League Import — 2025
Teams discovered: 30
[ 1/30] Athletics ................. unchanged
[ 2/30] Los Angeles Angels ........ updated
...
[30/30] Washington Nationals ...... inserted
Season: 2025
Teams discovered: 30
Teams succeeded: 30
Teams failed: 0
Team-game records fetched: 4860
Inserted: 0
Updated: 4860
Unchanged: 0
Ingestion coverage: COMPLETE
```

Those numbers are illustrative. Real counts come from the service result.

Failed clubs are listed with their errors under a `Failures:` heading. Raw
tracebacks are never printed as normal output; operational errors go to stderr
as `error: ...`.

With `--format json`, stdout carries only the serialized result — no progress
prose — so it stays parseable by a future scheduler or deployment tool.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Every discovered team was ingested (`COMPLETE`) |
| `1` | The run could not be carried out: invalid season, discovery failure, or coverage state could not be persisted |
| `2` | The run finished but at least one club failed (`INCOMPLETE`) |

`scripts/import_team_season.py` is unchanged and still useful for targeted
repair, debugging, single historical imports, and local development. Both
scripts call reusable application services.

## 11. The web application is not involved

League ingestion is not connected to browser requests, and no admin ingestion
route was added in this milestone.

```text
browser ─► FastAPI ─► SQLite ─► analytics ─► Plotly      (correct, unchanged)
browser ─► FastAPI ─► MLB API                            (still wrong)
```

Loading `/` or `/strikeouts` does not trigger an import. A regression test
asserts that no web route reaches league ingestion or team discovery.

## 12. Why league analytics are deferred

Milestone 4 establishes the trustworthy data foundation and stops there. MLB
average chart lines, league ranks, percentiles, team-vs-league cards, and
hits- or strikeouts-vs-league comparisons belong to Milestone 5.

The reason is the ordering the root `AGENTS.md` requires: authoritative league
statistics must not be calculated from incomplete league data. A league average
computed over 27 of 30 clubs is wrong in a way that renders perfectly. Now that
coverage is explicit and persisted, Milestone 5 can check it before drawing
anything — which is only possible because this milestone shipped first.

League data existing is not itself a reason to visualize it.
