# ADR 0003: Security baseline — no hardcoded secrets, callouts in the service layer

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Hardcoded credentials committed to source are a standing breach risk that persists
in git history even after rotation. Separately, HTTP callouts scattered across
triggers, controllers, and selectors make outbound org traffic hard to audit or
govern under Salesforce's callout limits. AI agents tend to inline whichever
pattern already dominates nearby code, so both problems compound quickly.

## Decision
No string literal may look like a credential (password, API key, token) anywhere
in Apex — use Named Credentials or Custom Metadata, never a literal. All HTTP
callouts to external systems MUST originate from a `*Service` class, never
directly from a trigger, controller, or selector.

## Consequences
- Secrets stay out of git history and code review diffs.
- Outbound callout traffic has one auditable choke point per integration.
- Enforced by ArchSteer rules `no-hardcoded-secrets` and `external-calls-only-in-service`.
