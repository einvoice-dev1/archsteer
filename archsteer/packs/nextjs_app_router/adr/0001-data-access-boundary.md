# ADR 0001: Data access confined to lib/ and route handlers

- **Status:** Accepted
- **Date:** (set when adopting)

## Context
Pages and layouts in the App Router are the easiest place to reach for a database
call — the data is needed right there, in the component that renders it. But a
Supabase/Prisma/raw-SQL call inlined into a page makes that query untestable in
isolation, undiscoverable from anywhere else that needs the same data, and risky
to change (a schema tweak now means grepping every page for inline queries).
AI agents replicate whatever data-access pattern already dominates nearby code,
so one inlined query becomes the template for the next ten.

## Decision
Database/ORM access (Supabase, Prisma, raw SQL) MUST go through a function in
lib/, or happen inside a route handler. Pages, layouts, and components MUST NOT
import a database/ORM client directly.

## Consequences
- Queries are centralized, testable, and reusable across pages.
- A schema or query change touches lib/, not every page that happens to need the data.
- Enforced by ArchSteer rules `no-data-access-outside-lib` and `no-direct-db-import-in-ui`.
