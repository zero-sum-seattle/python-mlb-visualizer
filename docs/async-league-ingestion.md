# Async league ingestion (issue #31)

This document describes the bounded-concurrency league ingestion path added
in issue #31, once `python-mlb-statsapi` 1.1.0 shipped `AsyncMlb` as a public,
released API. It supplements `docs/league-season-ingestion.md`, which
describes Milestone 4's original sequential design and is left unchanged; see
its "Sequential by design" section for why that design was correct at the
time and remains available today.

## 1. What changed, and what did not

A prototype on an earlier branch measured roughly a 3x average speedup for
league ingestion by fetching several teams from MLB concurrently instead of
one at a time. This issue re-evaluates that idea against the current
codebase and adds the smallest version of it that fits the existing
architecture, rather than porting the prototype's design wholesale.

Unchanged:

- normalization, request parameters, response validation, schedule joins,
  and completed-game checks in `app/services/team_game_logs.py` and
  `app/services/league_teams.py`;
- persistence semantics in `app/database/repositories.py` — the upsert rules,
  idempotency, and the atomic batting+pitching transaction in
  `persist_team_season`;
- failure semantics, coverage recording, and the `LeagueSeasonIngestionResult`
  / `LeagueSeasonIngestionState` invariants described in
  `docs/league-season-ingestion.md`;
- `ingest_league_season`, the sequential entry point, kept as the default and
  as a simple reference/debug path.

New:

- `app.services.team_game_logs.get_team_game_lines_async` and its supporting
  `AsyncMlbGameDataClient`-shaped fetch helpers;
- `app.services.league_teams.discover_mlb_teams_async`;
- `app.services.team_season_ingestion.ingest_team_season_async` and the
  shared `persist_team_season` helper it and the sync path both call;
- `app.services.league_season_ingestion.ingest_league_season_async`, the
  bounded-concurrency league entry point;
- `--async` / `--concurrency` on `scripts/import_league_season.py`.

## 2. Why the async transport reuses the sync baseball logic

`AsyncMlb`'s methods on the 1.1.0 release are structurally identical to
`Mlb`'s: same method names, same parameter names, same response shapes —
only awaited. Given that, the async fetch functions in `team_game_logs.py`
and `league_teams.py` are thin `await`-shaped siblings of the sync fetch
functions; the actual response validation (team lookup, schedule presence,
game-log split extraction) was extracted into small shared, pure functions
(`_validate_fetched_team`, `_validate_fetched_schedule`,
`_hitting_splits_from_stat_groups`, `_pitching_splits_from_stat_groups`,
`_finish_discovery`) that both transports call. Normalization
(`_normalize_batting_log`, `_normalize_pitching_log`, schedule indexing, the
completed-game check) was not touched at all — the async path calls the
exact same private functions the sync path always has.

The result: there is one implementation of what a team-season or a season's
club set *means*, and two implementations of how the bytes get here.

## 3. Concurrency model

```text
ingest_league_season_async
├─ discover_mlb_teams_async (one request, over the shared AsyncMlb client)
├─ record RUNNING
├─ for each discovered team, concurrently, bounded by `concurrency`:
│    ├─ fetch team + schedule + hitting log + pitching log  (await, no lock)
│    └─ persist the team-season                              (write_lock held)
├─ build and validate the result (same as the sequential path)
└─ record COMPLETE / INCOMPLETE
```

- **Bounded, not unbounded.** An `asyncio.Semaphore(concurrency)` limits how
  many teams may be fetching from MLB at once. Concurrency happens across
  teams, not by launching every individual request (team, schedule, hitting
  log, pitching log) for every team all at once — a single team's four
  requests are still awaited one at a time, in the sync path's order.
  `concurrency` defaults to `DEFAULT_LEAGUE_CONCURRENCY` (4) and must be at
  least 1; the CLI and the service both reject anything less before making a
  request.
- **One shared client.** `ingest_league_season_async` opens exactly one
  `AsyncMlb` client for the whole run (or reuses an injected one), the same
  way the sequential path opens exactly one `Mlb` client. It is passed to
  discovery and to every team's fetch, so the underlying HTTP connection pool
  is reused across the run rather than opened per team.
- **Writes are serialized.** An `asyncio.Lock` (`write_lock`) wraps the call
  to `persist_team_season`, so only one already-fetched team-season is ever
  being written at a time, even while several other teams are still
  mid-fetch. `persist_team_season` contains no `await` — it is the same
  synchronous, single-transaction SQLAlchemy code the sequential path uses —
  so once a coroutine enters it, nothing else can run until it finishes; the
  lock makes that guarantee explicit rather than incidental, so it keeps
  holding even if the persistence code changes later. No async SQLAlchemy is
  used anywhere, and SQLite is never written to from more than one place at
  once.
- **`asyncio.gather` preserves discovery order.** Even though teams complete
  in whatever order their fetches finish, `team_results` is built in the same
  discovery order the sequential path produces, because `gather` returns
  results in argument order regardless of completion order. The
  `on_team_complete` progress callback's `position` argument does reflect
  completion order, not discovery order — documented on
  `ingest_league_season_async` — since that is the only order concurrent
  completions actually have.
- **Task ownership is explicit.** Each team's coroutine is wrapped in its own
  `asyncio.Task` up front, rather than left for `asyncio.gather` to wrap
  internally, so the run can act on all of them if one fails unexpectedly.
  See §5 for what that failure path guarantees.

## 4. Transaction boundaries

Identical shape to the sequential path (see
`docs/league-season-ingestion.md` §7), plus the write lock described above.
No transaction is ever held open across an `await`. `persist_team_season` is
the single place either path commits a team-season, so "batting and pitching
commit atomically, together" holds for both transports because it is the
same code.

## 5. Failure semantics

Unchanged from the sequential path:

- a team's fetch failure (`TeamGameLogError` and its subclasses —
  `TeamNotFoundError`, `TeamGameDataError`) becomes a `FAILED` per-team result
  without aborting the run;
- a team's persistence failure (`TeamSeasonIngestionError`) is handled the
  same way, and rolls back only that team's own transaction;
- an unexpected exception (anything else) propagates out of
  `ingest_league_season_async` rather than being reported as a missing team.
  The team coroutines are owned as explicit `asyncio.Task`s; on an unexpected
  exception — including one raised by `on_team_complete`, which is
  intentionally never absorbed — every other team's task is cancelled and
  awaited to completion before the exception is allowed to leave the
  function, so no sibling task can still be mid-fetch, queued behind the
  concurrency semaphore, or waiting on the write lock once the caller sees
  the error. This does not depend on `asyncio.run`'s shutdown behavior, so it
  holds even when the function is awaited inside a longer-lived event loop;
- the coverage row is only ever recorded `COMPLETE` after the same validated
  `LeagueSeasonIngestionResult` the sequential path builds; a run that raises
  before that point leaves the row `RUNNING`;
- idempotent reruns and INCOMPLETE-to-COMPLETE recovery work identically,
  because they depend on the shared `persist_team_season` / upsert behavior,
  not on which transport fetched the data.

## 6. CLI

```bash
poetry run python scripts/import_league_season.py --season 2025
poetry run python scripts/import_league_season.py --season 2025 --async
poetry run python scripts/import_league_season.py --season 2025 --async --concurrency 8
```

`--async` selects `ingest_league_season_async`; without it, the default
remains the sequential `ingest_league_season`. `--concurrency` requires
`--async` and must be at least 1 — both are validated with `parser.error()`
(exit code 2) before any MLB request is made, rather than silently ignored
or silently accepted. Error handling, exit codes, and output formatting are
otherwise unchanged and shared between both modes, since both raise the same
exception types.

## 7. Testing

Tests are offline and deterministic, following the existing fake-client
pattern: `AsyncFakeMlb` / `AsyncFakeLeagueMlb` wrap the existing sync fakes
(`FakeMlb`, `FakeLeagueMlb`) so both transports are driven by the same
fixture data. An artificial `asyncio.sleep` delay is used only to force
fetches to genuinely overlap in tests that check the concurrency bound and
write serialization — never real network I/O.

See:

- `tests/test_team_game_logs_async.py` — fetch parity, request-parameter
  parity, and error-translation parity with the sync path.
- `tests/test_league_teams_async.py` — discovery parity.
- `tests/test_team_season_ingestion_async.py` — atomic persistence,
  idempotency, and sync/async row-for-row parity.
- `tests/test_league_season_ingestion_async.py` — bounded concurrency
  (`max_in_flight` never exceeds the configured bound), single shared client,
  no-overlapping-writes (a patched `persist_team_season` asserts at most one
  active writer at a time), COMPLETE/INCOMPLETE and per-team failure
  semantics, unexpected-error propagation, idempotent reruns, and sequential
  vs. concurrent persisted-row parity.
- `tests/test_import_league_season.py` — `--async` / `--concurrency`
  argument parsing and validation, including the invalid combination
  (`--concurrency` without `--async`) and an invalid bound (`< 1`).
