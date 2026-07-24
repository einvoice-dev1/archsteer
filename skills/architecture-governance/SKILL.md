---
name: architecture-governance
description: Keep code changes conformant to a repo's real, declared architecture using ArchSteer. Use when starting work in an unfamiliar repo, before finishing/committing an architecturally significant change (new dependency, new data access, new external call, new layer), or when asked about a codebase's architecture, layers, or dependencies.
metadata:
  docs:
    - "https://www.archsteer.com"
    - "https://github.com/einvoice-dev1/archsteer#readme"
  bashPatterns:
    - '\barchsteer\s+(xray|init|map|docs|govern|check|adr|steer|report|install-hooks)\b'
  pathPatterns:
    - '.archsteer/architecture.yaml'
    - '.archsteer/model.json'
    - '.archsteer/adr/*.md'
---

# Architecture governance with ArchSteer

ArchSteer derives a repo's real architecture from source (components, layers,
dependencies, data access, external calls) and — once a target is declared —
enforces it as fitness functions with a net-new ratchet: only NEW violations
block, existing debt never freezes feature work.

## Is it set up in this repo?

Check for `.archsteer/model.json`. If it's missing, this repo hasn't been
mapped yet:

```bash
archsteer xray     # zero-config: map + living docs + evolution feed, no rules required
```

If `.archsteer/architecture.yaml` doesn't exist either, the repo has never
declared a target:

```bash
archsteer init     # auto-detects the stack, seeds a matching starter rule pack + ADRs
```

If neither `archsteer` nor `uvx` is on PATH, install with `pipx install archsteer`
or `pip install archsteer` (needs Python 3.10+), or run one-off via `uvx archsteer <command>`.

## When to use it during a task

**Starting work in an unfamiliar repo** — run `archsteer xray` (or, if the MCP
server is connected, call `current_architecture`) to see real component/layer
counts and the current conformance score before making assumptions about
structure from a partial read of the code.

**Before writing or editing a file that touches an architectural boundary**
(new dependency, new data-access call, new external/third-party call, a file
in a layer you haven't touched yet) — call the `get_target_pattern` MCP tool
with that file path (or read `.archsteer/architecture.yaml` directly) to see
which declared rules apply, and follow the `steer` directive on each one
instead of copying whatever an adjacent legacy file happens to do.

**After editing a file** — call the `check_file` MCP tool with that file path
(or run `archsteer check`) to verify the edit didn't introduce a violation,
without waiting for CI to say so.

**Before finishing/committing an architecturally significant change** — run
`archsteer check`. It fails only on NET-NEW error-severity violations against
the repo's baseline; pre-existing debt is never a blocker. If a repo has
`archsteer install-hooks` set up, this already runs automatically on `git push`.

**When asked to explain, review, or diagram the architecture** — run
`archsteer report` and read `.archsteer/report.html` (conformance, drift,
layer map, component catalog) rather than re-deriving it from a manual code
read; it's already computed from the same model.

**If a change genuinely alters an architectural boundary** (new third-party
dependency, new persistence entity, new layer) or a rule turns out to be
violated across many components — run `archsteer adr` and mention the drafted
ADR(s) under `.archsteer/adr/` to the user for ratification. Never fabricate
or hand-write an ADR yourself; only ArchSteer's own detection drafts them.

## MCP tools (if the bundled server is connected)

- `current_architecture()` — component/layer counts, conformance/drift, the
  declared target. Call once at the start of a task to orient.
- `get_target_pattern(file_path?)` — the declared rules that apply to a file
  (or all rules, if omitted), each with its `steer` directive. Call BEFORE
  writing/editing.
- `check_file(file_path)` — whether a specific file currently violates
  declared intent. Call AFTER editing.

All three read only what's already in `.archsteer/` on disk — no network
call. If a tool errors with "no model found," run `archsteer map` first.
