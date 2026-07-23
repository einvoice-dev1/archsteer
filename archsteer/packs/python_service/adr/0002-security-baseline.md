# ADR 0002: Security baseline — no hardcoded secrets, outbound calls in the service layer

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Hardcoded credentials committed to source are a standing breach risk that persists
in git history even after rotation. Separately, third-party HTTP/SDK calls scattered
across handlers and repositories make outbound traffic hard to audit, retry, or
rate-limit consistently. AI agents tend to inline whichever pattern already
dominates nearby code, so both problems compound quickly.

## Decision
No string literal may look like a credential (password, API key, token) anywhere
in source — read secrets from environment variables or a secrets manager. All
outbound calls to third-party APIs/SDKs MUST originate from the service layer,
never directly from API handlers or repositories.

## Consequences
- Secrets stay out of git history and code review diffs.
- Outbound third-party traffic has one auditable choke point per service.
- Enforced by ArchSteer rules `no-hardcoded-secrets` and `external-calls-only-in-service`.
