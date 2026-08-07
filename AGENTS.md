# AGENTS.md

## Purpose

This repository is intentionally designed to remain modular, maintainable, explicit, and easy for another Python engineer to understand.

Do not treat this repository as a prototype or vibe-coded project.

Prefer:

* boring code over clever code
* explicit behavior over hidden behavior
* small focused modules over large abstractions
* correctness over making a feature appear to work
* maintainability over short-term implementation speed

Before completing a change, ask:

> Could another Python engineer open this repository six months from now, predict where this code belongs, and understand why it exists?

If not, simplify the design.

---

# Architecture

Preserve the existing dependency direction:

```text
MLB API
    ↓
services / normalization
    ↓
domain schemas
    ↓
database / repositories
    ↓
analytics
    ↓
web / charts / templates
```

Do not introduce dependencies in the opposite direction without a clear architectural reason.

---

## Domain schemas

Location:

```text
app/schemas/
```

Responsibilities:

* Define typed contracts between layers.
* Represent normalized application data.
* Validate domain invariants.
* Keep external API representations out of downstream code.

Rules:

* Prefer Pydantic models over unstructured dictionaries for domain data.
* Do not carry raw MLB response objects beyond normalization.
* Do not expose SQLAlchemy ORM objects as domain contracts.
* Schemas must not depend on FastAPI, Jinja, Plotly, or web presentation code.
* Avoid `Any` when a meaningful domain type can reasonably be defined.

---

## Services

Location:

```text
app/services/
```

Responsibilities:

* MLB API interaction.
* External-data normalization.
* Cross-source validation.
* Converting upstream models into application domain models.

Rules:

* Services may depend on MLB client models and domain schemas.
* Services must not contain FastAPI route behavior.
* Services must not contain Jinja presentation logic.
* Services must not build Plotly figures.
* Services should not contain database persistence logic unless explicitly designed as orchestration around a repository.
* Missing or contradictory upstream data should raise explicit data-integrity errors.

Never silently fabricate source data.

---

# Operational entry points

CLI scripts, scheduled jobs, admin operations, and future background tasks are
entry points into reusable application services.

They are not the application architecture themselves.

Code under `scripts/` should remain thin.

A script may:

- parse command-line arguments
- construct configuration and dependencies
- invoke an application service
- format results for stdout/stderr
- select an appropriate process exit code

A script should not contain substantial:

- MLB normalization logic
- database business rules
- ingestion algorithms
- analytics
- completeness rules
- application-domain decisions

Prefer:

```text
CLI script ─────────┐
scheduled job ──────┼──► reusable service ─► repositories / MLB
admin operation ────┘
## Database and repositories

Location:

```text
app/database/
```

Responsibilities:

* SQLAlchemy ORM models.
* Engine/session construction.
* Persistence mapping.
* Repository queries.
* Alembic-backed schema evolution.

Rules:

* Repositories own persistence and database querying only.
* Repository functions should return domain models or focused typed result models where practical.
* Do not leak ORM records into analytics.
* Do not place charting or HTTP logic in repositories.
* Do not place baseball analytics calculations in repositories.

---

## Analytics

Location:

```text
app/analytics/
```

Responsibilities:

* Baseball/statistical calculations.
* Rolling averages.
* Comparisons.
* Derived analytical models.

Rules:

* Analytics accepts normalized domain models.
* Analytics must not query SQLAlchemy.
* Analytics must not call MLB.
* Analytics must not depend on FastAPI.
* Analytics must not depend on Jinja.
* Analytics must not construct Plotly figures.
* Keep calculation precision internally.
* Round values only in the presentation layer unless the statistic itself requires rounding.

Analytics functions should be usable and testable without a web server or database.

---

## Web and presentation

Location:

```text
app/web/
```

Responsibilities:

* HTTP request handling.
* Dependency orchestration.
* Templates.
* Presentation formatting.
* Plotly figure construction.
* Browser-facing error states.

Routes should remain thin.

A route should conceptually look like:

```python
games = list_team_season(...)
analysis = build_analysis(games, ...)
figure = build_figure(analysis)

return render_template(...)
```

If a route contains substantial:

* SQL construction
* baseball calculations
* rolling-average logic
* external API normalization
* data-integrity rules

move that responsibility to the appropriate layer.

Web requests should read persisted data and should not call the MLB API unless a future architectural decision explicitly changes this rule.

---

# Module ownership

Do not create junk-drawer modules such as:

```text
utils.py
helpers.py
common.py
misc.py
```

Functions should live in the module that owns their responsibility.

Examples:

```text
app/web/formatting.py
app/analytics/team_hitting.py
app/database/repositories.py
app/services/team_game_logs.py
```

Module names should make ownership obvious.

---

# Abstractions

Do not introduce abstractions merely because two implementations look similar.

Prefer a small amount of obvious duplication over a premature or incorrect abstraction.

For example, separate implementations for:

* team hits
* batting strikeouts
* runs
* future metrics

are acceptable until repeated real-world usage reveals a stable abstraction.

Do not introduce designs such as:

```text
GenericMetricEngine
MetricProcessorFactory
AbstractVisualizationService
GenericBaseballAnalyticsFramework
```

without demonstrated need.

Refactor repeated behavior only after the repeated pattern is understood.

Avoid speculative architecture for hypothetical future requirements.

---

# Git and branching workflow

Never develop directly on `main`.

Before starting significant work:

1. Confirm the working tree is clean.
2. Update local `main` from the remote.
3. Create a feature branch from the latest `main`.
4. Run the existing test suite.
5. Record the baseline test state.

Use descriptive branch names.

Preferred examples:

```text
milestone-3.5-team-strikeouts-visualization
milestone-4-league-ingestion
fix-team-season-selector
```

Avoid generated names such as:

```text
cursor/*
codex/*
agent/*
feature-random-suffix
```

Unless explicitly instructed otherwise:

> One milestone or coherent feature should correspond to one branch and one pull request.

Do not:

* commit directly to `main`
* merge without explicit user instruction
* force-push shared history
* rewrite published history without explicit instruction
* rename an existing branch without explicit instruction
* mix unrelated work into the current branch
* create a replacement branch merely because a new agent session started

If work depends on another unmerged branch, report that dependency rather than silently creating a complicated branch stack.

---

# Commits

Keep commits coherent and understandable.

Prefer commit messages such as:

```text
feat: add team batting strikeout analytics
db: persist batting strikeouts
test: cover strikeout migration behavior
docs: document strikeout visualization
fix: preserve selected season when changing teams
```

Avoid vague commit messages such as:

```text
updates
changes
fixes
stuff
final
final final
cursor changes
```

Do not perform repository-wide cleanup or formatting unrelated files as part of a focused feature.

A commit should represent a change that can be explained clearly.

---

# Pull requests

A pull request should contain one coherent:

* feature
* fix
* milestone
* refactor

Do not mix unrelated work into the same PR.

Before declaring a PR ready:

* run formatting
* run linting
* run tests
* verify database migrations when applicable
* perform relevant manual validation
* document important design decisions
* report known limitations

Do not merge a pull request unless explicitly instructed.

---

# Scope discipline

Keep milestones focused.

A milestone should answer one clear product or analytical question.

Do not add unrelated:

* infrastructure
* metrics
* refactors
* dependencies
* visualizations
* frameworks
* abstractions

because they seem potentially useful.

Future ideas belong in planning or documentation until explicitly included in a milestone.

When implementing a milestone:

1. Preserve existing architecture.
2. Make the smallest coherent change.
3. Add focused tests.
4. Update relevant documentation.
5. Leave unrelated working code alone.

---

# Dependencies

Do not add a dependency when the Python standard library or an existing project dependency reasonably solves the problem.

Before adding a runtime dependency:

1. Identify the problem it solves.
2. Confirm existing dependencies do not already provide the capability.
3. Prefer established and maintained libraries.
4. Add it through Poetry.
5. Commit the corresponding lockfile change.

Do not introduce major infrastructure merely for convenience.

Examples that require explicit milestone scope include:

* frontend frameworks
* new databases
* Redis
* task queues
* workers
* dataframe frameworks
* caching infrastructure
* distributed systems components

A dependency should solve a demonstrated problem, not a hypothetical future one.

---

# Database migrations

Database schema changes require Alembic migrations.

Never use:

```python
Base.metadata.create_all()
```

as a substitute for schema migration in application startup.

Do not:

* delete a database to avoid writing a migration
* recreate user data as a migration strategy
* silently destroy existing records
* fabricate historical values

Existing data should survive upgrades whenever reasonably possible.

Unknown historical values should remain unknown, usually:

```text
NULL
```

until real source data can populate them.

Never convert an unknown historical statistic into `0` simply because the database needs a value.

Migration behavior should be tested against the previous schema revision when the migration affects existing data.

---

# Data integrity

Correctness is more important than making a visualization render.

Do not:

* silently replace missing baseball statistics with zero
* silently drop incomplete records and present the remaining data as complete
* fabricate historical values
* calculate authoritative league statistics from incomplete league data
* describe partial stored data as a complete season
* hide contradictory upstream data
* swallow integrity errors solely to keep the page working

When the available data cannot support a calculation, expose the limitation clearly.

---

# Statistical integrity

Every statistic or visualization should begin with a clear analytical question.

Before implementing a new statistic, identify:

1. What baseball question are we asking?
2. What data measures that concept?
3. What mathematical transformation is being applied?
4. What assumptions does the calculation make?
5. What can legitimately be concluded?
6. What cannot legitimately be concluded?

Prefer understandable statistics before sophisticated statistics.

Examples:

* Rolling averages describe trends.
* Normalized indexes compare relative movement against a baseline.
* Scatter plots show relationships between variables.
* Correlation measures linear association.
* Correlation does not establish causation.
* Per-game statistics and rate statistics answer different questions.

Do not add statistical complexity merely because a library makes the calculation easy.

Do not invent composite metrics without a clear mathematical and baseball interpretation.

---

# Visualization semantics

Visualization labels must accurately describe the underlying measurement.

Prefer precise labels such as:

```text
Batting Strikeouts
Batting Strikeouts per Game
Season-to-Date Average
Average Across Stored Completed Games
```

rather than ambiguous labels.

Do not imply database completeness when completeness has not been established.

A normalized index above `100` means:

> the statistic is above its baseline

It does not automatically mean:

> performance is better

Directionality depends on the statistic.

For example:

* More hits may generally be favorable.
* More batting strikeouts may generally be unfavorable.

Do not automatically assign positive or negative visual semantics unless that interpretation has been explicitly defined.

---

# Error handling

Use specific domain errors for expected failure modes.

Avoid broad exception catches unless the architectural boundary genuinely requires them.

Do not translate unrelated failures into misleading user-facing errors.

When wrapping lower-level exceptions:

* preserve exception chaining
* provide useful context
* distinguish data-integrity failures from infrastructure failures

Errors should make clear:

* what operation failed
* what entity/data was involved
* what the developer or user can reasonably do next

Never expose raw tracebacks in normal browser responses.

Do not silently swallow unexpected failures.

---

# Configuration

Runtime configuration belongs in Pydantic Settings and environment variables.

Do not hardcode:

* credentials
* machine-specific paths
* secrets
* tokens
* environment-specific configuration

Never commit:

* API keys
* passwords
* access tokens
* private credentials
* secret-bearing `.env` files

Tests should use isolated test configuration.

---

# Type safety

Use modern Python type hints consistently.

Prefer:

```python
TeamGameBattingLine
TeamHitsAnalysis
TeamStrikeoutsAnalysis
```

over:

```python
dict[str, Any]
```

for application/domain boundaries.

Do not weaken existing typing merely because doing so makes an implementation easier.

Use `Any` only when interacting with genuinely dynamic or untyped boundaries where a stronger representation is unreasonable.

---

# Testing philosophy

Tests are part of the repository contract.

Tests should protect meaningful behavior rather than maximize the number of tests.

Prioritize tests for:

* domain invariants
* MLB normalization
* analytics calculations
* persistence behavior
* idempotency
* Alembic migrations
* data-integrity failures
* HTTP contracts
* important regressions

Ask:

> Would this test catch a bug we care about?

Do not optimize for test count alone.

Coverage is useful as a signal, not a goal by itself.

Do not create large numbers of brittle tests for implementation details.

---

# Existing tests

Do not modify or remove an existing test merely because a new implementation fails it.

If the existing behavior is still correct:

> fix the implementation.

If intended product behavior genuinely changes:

* update the test
* make the reason for the behavior change clear
* update relevant documentation if needed

Do not:

* disable tests to make CI green
* weaken assertions without justification
* delete regression tests because they are inconvenient
* mock away the behavior actually being tested

---

# Offline tests

Automated tests should remain offline unless a task explicitly requires integration testing.

Unit and application tests should not depend on:

* live MLB availability
* external network connectivity
* mutable remote data

Use captured fixtures and normalized test records.

Manual integration validation may use the live MLB API when appropriate.

---

# Agent behavior

Before making substantial changes:

1. Read this `AGENTS.md`.
2. Inspect the existing implementation.
3. Inspect related tests.
4. Inspect relevant documentation.
5. Run the baseline test suite.

Do not assume repository architecture solely from the task prompt.

Prefer extending existing patterns over inventing parallel architecture.

If a requirement conflicts with:

* existing architecture
* data integrity
* database history
* another explicit project rule

stop and explain the conflict rather than silently working around it.

Do not:

* fabricate data
* weaken validation for convenience
* swallow exceptions without justification
* disable linting
* disable tests
* remove type safety
* modify CI merely to make incorrect code pass
* redesign unrelated code while implementing a feature
* add speculative infrastructure

When a test exposes a genuine regression, fix the implementation.

---

# Refactoring

Refactoring should have a demonstrated reason.

Good reasons include:

* repeated code has developed a clearly stable shared abstraction
* a module has accumulated multiple unrelated responsibilities
* dependency direction has become incorrect
* testing is difficult because responsibilities are coupled
* a bug repeatedly occurs because ownership is unclear

Do not perform broad refactors because code could theoretically be “cleaner.”

Do not combine a large unrelated refactor with a feature unless explicitly requested.

Prefer behavior-preserving refactors with focused tests.

---

# File and module size

There is no arbitrary line-count limit.

However, investigate a module when it begins handling several unrelated responsibilities.

Split modules based on responsibility, not simply because they are long.

A large cohesive module may be healthier than several tiny poorly named modules.

Do not create excessive micro-modules solely to reduce file length.

---

# Documentation

Document important decisions and tradeoffs future maintainers could otherwise accidentally reverse.

Useful documentation explains things such as:

* why a database field is nullable
* why a league calculation is deferred
* why a statistic uses one denominator rather than another
* why the web application reads persisted data instead of calling MLB
* why an abstraction was intentionally deferred
* what assumptions an analytical calculation makes

Do not produce documentation merely to restate obvious code.

Prefer documenting:

> why

over:

> what the code visibly does

---

# Repository history

Historical milestone documentation should remain historically accurate.

Do not rewrite old milestone documents to make them appear as though later architecture already existed.

When architecture evolves:

* document the new decision
* preserve useful historical context
* link related documents where appropriate

---

# Manual validation

Automated tests are necessary but may not fully validate:

* browser behavior
* interactive chart behavior
* migrations against realistic local data
* real MLB ingestion
* responsive layout

Perform manual validation when it materially matters to the change.

Document what was manually verified.

Do not claim manual validation occurred if it did not.

---

# Definition of done

A change is not complete merely because the code runs.

Before reporting completion:

1. The implementation follows this architecture.
2. Relevant tests pass.
3. Ruff linting passes.
4. Ruff formatting passes.
5. Required migrations have been tested.
6. Existing unrelated behavior still works.
7. Relevant documentation is updated.
8. Manual validation has been performed when appropriate.
9. No unrelated files were changed unnecessarily.
10. Known limitations are reported.
11. No fabricated or misleading data was introduced.
12. The current milestone scope was not expanded.
13. The branch has not been merged unless explicitly requested.

---

# Final principle

This project should remain understandable without requiring the reader to know which AI tool, coding agent, or developer created a particular piece of code.

The repository itself should explain its architecture through:

* clear module ownership
* strong types
* explicit data flow
* focused tests
* safe migrations
* accurate documentation
* disciplined Git history

When choosing between cleverness and clarity:

> choose clarity.
