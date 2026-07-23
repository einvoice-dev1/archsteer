# ADR 0003: Security baseline — no hardcoded secrets, outbound calls in the service layer

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Hardcoded credentials committed to source are a standing breach risk that persists
in git history even after rotation. Separately, outbound HTTP/SDK calls scattered
across controllers and repositories make third-party traffic hard to audit, retry,
or rate-limit consistently. AI agents tend to inline whichever pattern already
dominates nearby code, so both problems compound quickly.

## Decision
No string literal may look like a credential (password, API key, token) anywhere
in source — bind secrets through Spring configuration properties backed by
environment variables or a secrets manager. All outbound calls to third-party
APIs/SDKs MUST originate from a `@Service` class, never directly from a
controller or repository.

## Consequences
- Secrets stay out of git history and code review diffs.
- Outbound third-party traffic has one auditable choke point per service.
- Enforced by ArchSteer rules `no-hardcoded-secrets` and `external-calls-only-in-service`.
