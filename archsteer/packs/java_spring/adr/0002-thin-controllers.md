# ADR 0002: Thin controllers, layered dependencies

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Controllers that reach directly into repositories (or entities that orchestrate
services) collapse the layering, making business logic impossible to reuse outside
the web layer and hard to test.

## Decision
Dependency direction is strictly controller → service → repository → model.
Controllers never import repositories; entities never import services.

## Consequences
- Business logic lives in services, reusable from jobs/schedulers/other transports.
- Enforced by ArchSteer rules `controller-must-not-touch-repository` and
  `model-depends-on-nothing`.
