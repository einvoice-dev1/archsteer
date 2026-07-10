# ADR 0001: Persistence access only through repositories

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
JdbcTemplate/EntityManager calls scattered across controllers and services make
queries untestable and schema changes risky. AI agents replicate whatever data-access
pattern dominates nearby code.

## Decision
All persistence access MUST go through classes annotated `@Repository` (or Spring
Data interfaces). No JDBC, JPA, or raw SQL in controllers or services.

## Consequences
- Services depend on repository interfaces, not on the persistence machinery.
- Queries are centralized, testable, and swappable.
- Enforced by ArchSteer rule `persistence-only-in-repository`.
