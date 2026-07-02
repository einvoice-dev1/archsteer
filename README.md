# ArchSteer

**Living Architecture Control Plane for the AI-Dev Era.**

AI agents now write code faster than any architect can review, document, or govern it.
Docs rot instantly, the *real* architecture is invisible, structural decisions get made
silently, and intended architecture drifts with every edit. ArchSteer is the always-current
architecture **system of record + governance plane**: it derives the real architecture from
code, keeps living docs and ADRs auto-built, surfaces every major decision for the architect
to ratify, enforces declared intent as code-level fitness functions, and steers AI agents to
conform instead of replicating local slop.

Everything is a projection of one code-derived model — `.archsteer/model.json`.

```
                    .archsteer/model.json  (single source of truth)
                                 │
   MAP ──── DOCUMENT ──── GOVERN ──── STEER ──── EVOLVE
  model    living docs   fitness     agent     report.html
  from     + auto ADRs   functions   guardrails  (drift/
  source   + diagrams    + ratchet   + MCP       decisions)
```

## Install

```bash
pip install archsteer                 # regex engine, zero native deps
pip install "archsteer[treesitter]"   # optional native acceleration
pip install "archsteer[mcp]"          # optional: the local MCP server (below)
```

## Quickstart

```bash
archsteer init      # scaffold .archsteer/ + seed intent (Express → Next.js + repository)
archsteer map       # build model.json from source
archsteer docs      # regenerate .archsteer/architecture.md (deterministic, Mermaid)
archsteer govern    # conformance + drift score by rule
archsteer adr       # draft ADRs for new structural decisions (architect-in-the-loop)
archsteer baseline  # accept current debt — the ratchet
archsteer steer -f src/controllers/payment.js -t "add refund endpoint"
archsteer check     # CI/pre-commit: fail on NET-NEW violations only
archsteer report    # self-contained .archsteer/report.html
```

## The three design guarantees

1. **Ratchet, not freeze.** `archsteer check` blocks only *net-new* violations against a
   baseline — teams keep shipping features while debt can only shrink.
2. **Conservative, architect-in-the-loop ADRs.** Only external-boundary changes (new
   dependency, new datastore, new layer) draft an ADR — never internal reshuffles, never
   auto-committed.
3. **Sharp agent steering.** Guardrails injected into `CLAUDE.md` / `AGENTS.md` are scoped to
   the files in play and point at the governing ADR — they don't dump the whole model into the
   context window.

## Declaring intent — `.archsteer/architecture.yaml`

```yaml
target: "Migrate Express + raw SQL to Next.js route handlers + the repository pattern"
layers: [route, controller, service, repository, model]
rules:
  - id: no-raw-sql-outside-repository
    type: required_layer_for_data_access
    allowed_layers: [repository]
    operations: [RAW]
    severity: error
    adr: .archsteer/adr/0001-repository-pattern.md
    steer: "Wrap all queries in a repository under src/repositories/. No raw SQL elsewhere."
```

Rule types: `required_layer_for_data_access`, `forbidden_import`, `forbidden_data_access`,
`forbidden_layer_edge`.

## Using with AI agents (MCP)

`archsteer mcp` runs a local MCP server over stdio — spawned by your own editor/agent,
never hosted by us. It reads only what `init`/`map`/`govern` already wrote to `.archsteer/`
on disk, so there's no network call and nothing leaves your machine. It exposes three tools:

- `current_architecture` — component/layer counts, conformance/drift, the declared target.
- `get_target_pattern` — the invariants that apply to a file, *before* you write to it.
- `check_file` — whether a file you just edited conforms, without waiting for CI.

Add it to Claude Code:

```bash
claude mcp add archsteer -- archsteer mcp
```

Or to any MCP-compatible client's config (Cursor, etc.):

```json
{ "mcpServers": { "archsteer": { "command": "archsteer", "args": ["mcp"] } } }
```

## CI / pre-commit

- GitHub Action: `.github/workflows/archsteer.yml` (maps, drafts ADRs, runs the net-new gate,
  uploads `report.html`).
- Git hook: `cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.

## Try the demo

```bash
cd examples/demo-repo
archsteer init && archsteer map && archsteer report   # open .archsteer/report.html
```

## Roadmap

- **Shipped** — cloud control plane (Next.js + Supabase): multi-repo situation room with
  drift/decision time-series. `archsteer mcp`: a local MCP server so agents query the live
  model + intent mid-edit.
- **Next** — an org-wide, hosted MCP server (Team tier) so agents can ask cross-repo questions
  against the situation room — "what's our drift index," "which repos have pending ADRs" —
  the same data as the dashboard, over MCP.
- **Later** — auth, org/repo model, billing.

## Development

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```
