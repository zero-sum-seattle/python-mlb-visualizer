# Concurrent league-season ingestion

This document describes the optional concurrent path for a league-wide import:
what it changes, what it deliberately does not change, and how the tests
establish that the two paths agree.

It builds on [`docs/league-season-ingestion.md`](league-season-ingestion.md),
which defines coverage semantics, persistence, and failure handling. None of
that changed. Read that document first; this one only describes the difference.

## 1. The problem this solves

A league import is roughly four MLB requests per club across about thirty
clubs: a team lookup, a season schedule, a hitting game log, and a pitching
game log. Almost none of that time is spent computing anything. It is spent
waiting for MLB to answer, one request at a time, with the process idle.

Section 4 of the Milestone 4 document said concurrency was not worth adding and
that it should be proposed separately if a measured runtime later proved
unacceptable. That section remains accurate as a record of the decision taken
then, and is deliberately left as written. This is the separate proposal: the
sequential path stays exactly as it was and remains the default, and a
concurrent path is added beside it so the two can be compared.

## 2. What is concurrent, and what is not

Being precise about this matters more than the feature does.

| | Concurrent? |
| --- | --- |
| MLB fetches for **different clubs** | Yes, up to `--concurrency` at once |
| The four MLB requests **within one club** | No — awaited one at a time |
| Team discovery | No — it is a single request |
| Normalization, validation, the schedule join | No — one thread, between awaits |
| Database writes | No — one connection, one transaction at a time |
| The database driver | Unchanged, synchronous SQLAlchemy |

So this does not make ingestion faster. It makes the *waiting* overlap. The
saving available is bounded by how much of a run is network wait and by the
concurrency bound, and nothing about the per-club work changed: every club is
still fetched once, normalized once, and written once.

### Why the concurrency is at the fan-out over clubs

The other place it could have gone is inside a single team-season, overlapping
that club's four requests. That was rejected:

- its ceiling is 4x, against roughly 30x available across clubs;
- `team_game_logs` would have to hold four in-flight requests and decide what
  to do when one answer invalidates another, which is a lot of complication in
  the module that owns the baseball rules;
- it changes the order failures surface in. Today a club that is not a Major
  League team is refused before its schedule is even requested. Overlapped,
  that club's other three requests are already in flight when the answer that
  refuses it arrives, and which error a broken club reports starts depending on
  which response landed first.

`async_team_game_logs` therefore awaits the four requests in the same order the
synchronous path makes them, one at a time. A test asserts that order, and
asserts that a single-club import never has more than one request in flight.

## 3. Architecture

```text
CLI --async ─────┐
scheduler ───────┼──► ingest_league_season_concurrently ─► MLB / database
admin operation ─┘
```

Three modules were added, each with one job:

| Module | Responsibility |
| --- | --- |
| `app/services/async_league_teams.py` | The discovery request, awaited |
| `app/services/async_team_game_logs.py` | One club's four requests, awaited in order |
| `app/services/concurrent_league_season_ingestion.py` | The fan-out over clubs, and persistence |

The first two are transport and nothing else. Neither decides anything about
baseball data.

They are separate modules rather than functions added to the synchronous ones
for one concrete reason beyond size: importing `AsyncMlb` pulls in `httpx`, and
`team_game_logs` is imported on every web request path. Keeping the async
client behind its own modules keeps that dependency where async is actually
used.

## 4. Not writing the ingestion rules twice

The constraint that shaped this change is that there must be exactly one
implementation of every rule about baseball data. A second, subtly different
normalizer living in a transport module is the failure mode worth designing
against — it would render perfectly and be wrong.

So the preceding commit separated, in each existing module, *asking MLB
something* from *deciding what the answer means*, and gave the second kind
public names. Nothing moved between modules, and no rule left the module that
owns it.

| Module | Shared under a public name |
| --- | --- |
| `team_game_logs` | `team_game_log_request`, `team_schedule_request`, `translating_team_lookup_failure`, `translating_game_data_failure`, `require_mlb_team`, `require_team_schedule`, `require_hitting_game_log`, `require_pitching_game_log`, `index_schedule_games`, `normalize_batting_log`, `normalize_pitching_log` |
| `league_teams` | `translating_discovery_failure`, `normalize_discovered_teams` |
| `team_season_ingestion` | `persist_team_season` |
| `league_season_ingestion` | `validate_season`, `record_run_started`, `build_league_result`, `record_run_finished`, `league_team_failure`, `discard_failed_team_transaction`, `LEAGUE_TEAM_INGESTION_ERRORS` |

That refactor is its own commit and changed no behavior: the full suite passed
unchanged before and after it.

The concurrent service calls all of the above. It contributes exactly one
thing of its own — the order in which clubs are fetched. Even the request
parameters are shared, because which stat type, which stat group, the
regular-season-only game type, and the season-wide schedule window are
decisions about which baseball data this application ingests, not about
transport.

`record_run_finished` takes an already-constructed `LeagueSeasonIngestionResult`
rather than loose counts. That makes the Milestone 4 ordering rule structural:
the validated result is the precondition for writing coverage, so neither path
can record COMPLETE for a run the domain model would reject.

## 5. Fetching is concurrent; the database is not

No async SQLAlchemy driver was introduced, no thread pool, and no repository or
schema change. The session is the same synchronous session the sequential path
uses, on one thread.

```text
discover teams                         one request, no transaction
record RUNNING                         short transaction, committed
fan out N fetches, bounded             network only, no transaction
as each club's fetch finishes:
    persist that club                  short transaction, committed
build and validate the result          no transaction
record COMPLETE / INCOMPLETE           short transaction, committed
```

Fetch tasks touch MLB and nothing else — no session, no transaction, no
repository. Persistence happens in the orchestrating coroutine, one club at a
time, and `_persist_fetched_team_season` is a plain `def` containing no
`await`. A transaction therefore cannot be open across a suspension point.

Making that function `async` would remove the guarantee even without adding an
`await` today, because a later edit could then add one inside the transaction
without anything failing.

### How that is actually asserted

A comment claiming this would be worth nothing, so it is tested.

While a concurrent import runs, a spinner task loops on `await asyncio.sleep(0)`
— a scheduling yield, not a wait — counting turns of the event loop. SQLAlchemy
`after_begin` and `after_commit` events record that count at each transaction's
two ends. In a single-threaded event loop, another task can only run when the
current coroutine suspends, so:

> the turn count being identical at begin and at commit *is* the statement that
> no `await` happened inside the transaction.

Every transaction in a run satisfies it. A second test guards against the first
passing vacuously: it asserts the loop demonstrably did turn during the run, and
turned between one transaction committing and the next beginning, which is
where the fetches are. A third records the thread at each boundary and asserts
it is the calling thread, which is what rules out a thread pool.

No sleeps of any length and no wall-clock measurement are involved in any of
this, so it is deterministic and stays offline.

### Why not an async database driver

Because there is no problem it would solve here. The writes are small, they are
already a rounding error next to the network wait, and SQLite serializes them
anyway. It would mean a second driver, a second session type, and repository
changes, in exchange for overlapping work that is not the bottleneck.

## 6. Ordering

Clubs finish in whatever order MLB answers. That order is not discovery order
and is not reproducible between runs. Three things could depend on it, and they
were decided separately.

**The progress callback fires in completion order.** Its `position` counts clubs
finished so far, not the club's place in the discovery list. An operator
watching a long import wants to know what has actually happened; replaying
progress in discovery order would mean holding a finished club back to preserve
a sequence the run is not following, and would hide a slow club behind one that
has not been reported yet. The CLI's mode line says `progress is completion
order` so the column is not misread, and a caller must not read `position` as
identifying a club.

**`team_results` is restored to discovery order.** That collection is the record
of the run: it is serialized by `--format json`, it is what coverage is built
from, and it is what a concurrent run is compared against a sequential one
field by field. Ordering it by whichever club answered first would make two
identical imports produce different records for no reason. Discovery order is
already defined as stable — clubs sorted by name then id — so this restores an
order the sequential path also produces.

**Persistence order is completion order, and is deliberately not restored.**
Holding a finished club's rows back until an earlier club committed would
reintroduce exactly the serialization the feature removes. It is safe to leave
because the upsert is keyed by `(team_id, game_pk)` and is idempotent, so the
stored rows do not depend on which club committed first. The only thing that
does depend on it is the autoincrement row id, which records write order and
carries no baseball data.

## 7. Coverage semantics are unchanged

Every guarantee in `docs/league-season-ingestion.md` section 5 still holds, and
is covered by tests against the concurrent path specifically:

- `RUNNING` is written before any club is fetched, and a season's row is reset
  by a new run rather than left stale;
- each club commits on its own;
- one club's failure does not undo the clubs that finished before it, and does
  not stop the clubs still in flight;
- a run with any failure is recorded `INCOMPLETE`, never `COMPLETE`;
- a failed club keeps its id, its name, and its error message;
- a rerun re-attempts every club and can move a season from `INCOMPLETE` to
  `COMPLETE`;
- a rerun of an unchanged season reports everything `unchanged`.

An unexpected error — anything outside a club's own ingestion failures — still
propagates and ends the run, as it does sequentially. Outstanding fetches are
cancelled and drained so a club nobody will persist stops calling MLB, and so a
failure from a fetch that was never consumed cannot surface on top of the error
that actually stopped the run.

## 8. How the tests establish parity

The claim to establish is that the concurrent service is a different order of
fetching and nothing else. The tests reuse the captured 2025 fixtures and the
fake clients the sequential tests already use; `AsyncFakeLeagueMlb` is a thin
async facade over the same fake, not a second one, so both paths are driven by
identical data.

**The parity test.** A sequential import, then a concurrent one on the same
database, asserting the concurrent run reports every team-game record
`unchanged`, nothing inserted, nothing updated, and every stored column of
every row byte-identical to before. Then the same thing with the order
reversed, so neither path is privileged.

That test is sharp because of how the upsert works: a row is reported `updated`
exactly when some column differs. A single normalized statistic differing by
one in either path would show up as an update and as a row difference. That was
confirmed by perturbing one hit in the async path and watching the assertion
fail, so it is known not to be vacuous.

Alongside it:

- two empty databases, one import each, compared row for row with only `id`,
  `created_at` and `updated_at` excluded — all three record how and when a row
  was written, not what happened in the game;
- the same broken club under both paths, asserting the two results are equal
  field for field including the per-club error message;
- `team_results` in the same order from both paths;
- at the team-season level, the async transport producing records equal to the
  synchronous one from the same fixtures, sending equal request parameters, and
  raising equal errors with equal messages at the same point in the sequence.

**Overlap and its bound** are established by counting, not by timing. The async
fake records the high-water mark of requests suspended simultaneously. Because
one club's requests are sequential, that number is also the number of clubs in
flight. The tests assert it exceeds one (the fetches genuinely overlap), never
exceeds the bound (genuinely bounded), and actually reaches the bound — a bound
nothing ever reaches would satisfy the ceiling assertion too. With one club it
is one, which is what proves the four requests within a club are not
overlapped.

Nothing in the suite sleeps for any length of time, measures wall-clock time,
or touches the network.

## 9. The dependency

`AsyncMlb` ships on the client library's `release/1.1.0` branch and is not
published to PyPI, so `pyproject.toml` points at the branch and requests the
`async` extra, which brings in `httpx`:

```toml
python-mlb-statsapi = { git = "https://github.com/zero-sum-seattle/python-mlb-statsapi.git", branch = "release/1.1.0", extras = ["async"] }
# Local alternative, for changing the client and this repository together.
# Swap the two lines and re-run `poetry lock`.
# python-mlb-statsapi = { path = "../python-mlb-statsapi", develop = true, extras = ["async"] }
```

`poetry lock` was re-run, because the lockfile does not carry a git source
otherwise. That branch is also a major version step from the previously pinned
`^0.8.0`; the existing suite passes against it unchanged.

`AsyncMlb` mirrors `Mlb`: the same method names, the same parameters, the same
models, and the same exception hierarchy rooted at `TheMlbStatsApiException`.
That is what lets the shared failure-translation and response-check functions
serve both transports without knowing which client called them.

## 10. Measuring it

`scripts/benchmark_league_import.py` times a sequential import against one or
more concurrent ones:

```bash
poetry run python scripts/benchmark_league_import.py --season 2025
poetry run python scripts/benchmark_league_import.py --season 2025 --concurrency 4 8 16
poetry run python scripts/benchmark_league_import.py --season 2025 --concurrency 8 --reverse
```

Each run gets its own throwaway SQLite database, migrated to Alembic head, in a
temporary directory. The configured application database is never opened.
Because each run starts empty, every run does the same inserting work rather
than one inserting and the next finding everything unchanged.

`--reverse` runs the concurrent imports first. Running it both ways is the
cheapest check that run order — a warming DNS or connection cache — is not what
produced the difference. If the two orders disagree, believe neither.

It is a live benchmark against a public API. Every number it prints measures one
moment of MLB's load, this machine's network path, and whatever caching sits in
between. One pair of numbers is an anecdote, not a result, and should not be
quoted as the speedup of this feature.

### The default bound

`DEFAULT_CONCURRENCY` is 8. That is a politeness-and-uncertainty choice, not a
measured optimum: MLB's API belongs to someone else, a league import is not
latency sensitive, and no measurement has established a better value. Raise it
deliberately, with the benchmark.

## 11. What was not verified

**No live MLB request of any kind was made while implementing this.** Outbound
access to `statsapi.mlb.com` is denied by the network policy of the environment
this work was done in, the same limitation recorded in
`docs/league-season-ingestion.md`. Specifically:

- **The benchmark has never been run against MLB.** No timing exists. The
  claim in this document is that waiting is overlapped, which the tests
  establish; the claim that a real league import gets faster, and by how much,
  is unmeasured. Run the benchmark before repeating any number.
- The concurrent path has never fetched a real club. It has only been exercised
  against captured fixtures — the same fixtures the sequential path is tested
  against, which is what makes the parity claim meaningful, but they are not
  MLB.
- How MLB responds to eight concurrent requests from one client is unknown
  here: whether it rate-limits, throttles, or resets connections under that
  load, and therefore whether a higher bound helps, does nothing, or makes
  things worse.
- `AsyncMlb` itself has not been exercised against a live endpoint from this
  repository. It has been read, and the tests substitute for it at the
  application's own boundary, which proves how this application uses it and not
  what it does over a real socket.

The first real league import run with `--async` should be treated as a
validation of all four, and compared against a sequential import of the same
season — which is what the benchmark is for, and why the sequential path
remains the default and the reference.
