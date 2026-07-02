# ADR 0002: Next.js route handlers replace Express

- **Status:** Accepted
- **Date:** 2026-06-22

## Context
The API is served by Express. The target platform is Next.js (App Router). New code
keeps reaching for Express because legacy routers are the nearest example.

## Decision
New endpoints MUST be implemented as Next.js App Router route handlers under
`app/api/<route>/route.ts`. No new Express routers or middleware.

## Consequences
- Express dependencies shrink over time until removable.
- Routing/auth/middleware converge on the Next.js model.
- Enforced by ArchSteer rule `no-express-framework`.
