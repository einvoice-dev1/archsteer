# ADR 0003: No hardcoded secrets

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Hardcoded credentials committed to source are a standing breach risk that persists
in git history even after rotation. AI agents tend to inline a placeholder-looking
literal when scaffolding new integration code, and it's easy for that to slip
through review as "just a stub."

## Decision
No string literal may look like a credential (password, API key, token) anywhere
in source — read secrets from environment variables (`process.env`) instead.

## Consequences
- Secrets stay out of git history and code review diffs.
- Enforced by ArchSteer rule `no-hardcoded-secrets`.
