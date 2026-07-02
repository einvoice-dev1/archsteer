# ADR 0001: Repository pattern for data access

- **Status:** Accepted
- **Date:** 2026-06-22

## Context
Data access is currently scattered as raw SQL inside controllers and routes, making
the codebase hard to test and migrate. AI agents replicate this pattern because it is
the dominant local example.

## Decision
All persistence access MUST go through repository modules under `src/repositories/`.
No raw SQL outside the repository layer.

## Consequences
- Controllers/services depend on repository interfaces, not on DB drivers.
- Queries are centralized, testable, and swappable (raw SQL → ORM later).
- Enforced by ArchSteer rule `no-raw-sql-outside-repository`.
