# ADR 0002: One logic-less trigger per object

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Multiple triggers on one object fire in nondeterministic order; logic inside trigger
bodies can't be unit-tested or bypassed in bulk loads.

## Decision
Each object has exactly one trigger whose body is a single delegation to a
`*TriggerHandler` class. All logic, SOQL, and DML live in the handler/service layers.

## Consequences
- Deterministic execution, testable handlers, a bypass switch in one place.
- Enforced by ArchSteer rule `no-dml-in-triggers`.
