# ADR 0001: Persistence access only through repositories

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Query logic scattered across API handlers and services makes the data layer
untestable and schema migrations risky. AI agents replicate whatever data-access
pattern dominates nearby code.

## Decision
All persistence access goes through functions/classes in the repositories package.
No raw SQL or ORM session usage in API handlers or services.

## Consequences
- Services depend on repository interfaces, not on the DB driver or ORM session.
- Queries are centralized, testable, and swappable.
- Enforced by ArchSteer rules `persistence-only-in-repository` and
  `api-must-not-touch-repository`.
