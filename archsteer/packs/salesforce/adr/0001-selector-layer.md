# ADR 0001: SOQL only in Selector classes

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
SOQL scattered across controllers, services, and triggers causes duplicate queries,
inconsistent field lists (breaking downstream code that expects a field), and
governor-limit surprises. AI agents copy whatever query pattern is nearest.

## Decision
Every SOQL query lives in a per-object `*Selector` class (fflib selector layer).
Callers get records only through selector methods with explicit field lists.

## Consequences
- One place to audit field lists, sharing, and query limits per object.
- Services own transactions; controllers orchestrate UI concerns only.
- Enforced by ArchSteer rules `soql-only-in-selectors` and `no-dml-in-controllers`.
