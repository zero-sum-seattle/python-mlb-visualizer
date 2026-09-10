# Player season catalog

## Decision

Player discovery uses the bulk call:

```python
mlb.get_people(sport_id=1, season=season)
```

One response supplies the identities MLB associates with that Major League
season. Catalog ingestion does not make one request per player and does not
fetch hitting or pitching statistics.

The normalized persistence grain is deliberately small:

- `players` remains the single current identity row keyed by MLB person id;
- `player_seasons` stores only `(player_id, season)` directory membership;
- `player_season_hitting` remains the independently imported full-season
  hitting aggregate.

This avoids copying names and positions into every season while preserving the
season boundary needed by a future DB-only player selector. A catalog entry
does not claim that hitting statistics have been imported.

## Installed-library audit

The installed dependency is `python-mlb-statsapi` 1.1.0. Its
`Mlb.get_people(sport_id=1, **params)` implementation sends the request to
`sports/1/players` and passes `season` through as an endpoint parameter. The
method returns an empty list for a 4xx response, so this application treats an
empty directory as an explicit discovery failure rather than a valid empty MLB
season.

Live audit on 2026-09-09 used one shared `Mlb()` client for all calls:

| Request | People | Unique ids | Missing names | Missing positions |
| --- | ---: | ---: | ---: | ---: |
| `season=2025` | 1,470 | 1,470 | 0 | 0 |
| `season=2024` | 1,454 | 1,454 | 0 | 0 |
| `season=2001` | 1,220 | 1,220 | 0 | 0 |
| `season=2026` | 1,449 | 1,449 | 0 | 0 |

The 2024 and 2025 id sets were materially different: 344 ids appeared only in
2025 and 328 only in 2024. Every audited person was marked `isPlayer=true`, and
no record had a debut after its requested season. An unscoped request matched
the current 2026 set, while `season=9999` returned the library's empty-list
failure shape. Adding `gameType=R` did not change the 2025 set, so the catalog
does not add that redundant parameter.

These observations support season-scoped membership. They do **not** support
historical identity claims: fields such as current team and current age in a
2024 response reflected their 2026 values. Therefore the catalog persists
season membership separately and continues to treat name and primary position
as the current MLB identity attributes already modeled by `players`.

Exact counts are audit evidence, not application invariants. In-progress
seasons grow as players debut, and upstream corrections may change historical
responses.

## Ingestion behavior

```bash
poetry run alembic upgrade head
poetry run python scripts/import_player_catalog.py --season 2025
```

The network response is fully normalized and checked for empty results,
non-player records, missing required identity fields, and duplicate person ids
before persistence begins. All identities and memberships are then written in
one database transaction. A database failure rolls back the entire catalog
refresh.

Reruns are idempotent. New season memberships are inserted, changed current
identity fields are updated, and matching entries are unchanged. A successful
refresh does not delete memberships absent from a later response: without a
separate completeness/run-state model, destructive reconciliation would turn a
transient partial upstream response into silent local data loss. This is a
search/discovery catalog, not a claim that local season coverage exactly equals
MLB forever after the import.

The existing one-player season importer also records the corresponding
membership atomically. The migration backfills memberships for existing
`player_season_hitting` rows because those rows are direct evidence of the
player-season relationship.

There is no async catalog path. Discovery is already one bulk request, so
concurrency would add no useful work and no second client lifecycle is needed.

## Scope boundary

This foundation adds no Player UI, search route, charts, player game logs,
pitching ingestion, team-stint model, or browser-side MLB request. Future Player
pages must query persisted catalog and statistic rows only.
