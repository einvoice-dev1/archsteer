# ADR 0002: Third-party API calls confined to lib/ and route handlers

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Third-party API calls (Stripe, an email provider, an external data source) scattered
across pages and components make outbound traffic hard to audit, retry, or
rate-limit consistently, and duplicate the same fetch/error-handling logic in
every place that needs it. This is distinct from a component calling its own
`/api/...` route handler via `fetch` — that's same-origin, not a third-party
dependency, and is explicitly not what this rule targets.

## Decision
Calls to third-party APIs/SDKs MUST originate from a lib/ function or a route
handler, never directly from a page, layout, or component.

## Consequences
- Outbound third-party traffic has one auditable choke point per integration.
- Retry/error-handling logic for a given API lives in one place, not copy-pasted.
- Enforced by ArchSteer rule `external-calls-only-in-lib`.
