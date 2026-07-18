<!-- mcp-name: io.github.einvoice-dev1/archsteer -->

# ArchSteer

[![ArchSteer conformance](https://img.shields.io/endpoint?url=https%3A%2F%2Fwww.archsteer.com%2Fapi%2Fbadge%2Farchsteer)](https://www.archsteer.com)
[![PyPI](https://img.shields.io/pypi/v/archsteer)](https://pypi.org/project/archsteer/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

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
pip install archsteer                 # regex engine + the local MCP server, zero native deps
pip install "archsteer[treesitter]"   # optional native acceleration
```

(Since 0.4.1 the MCP server ships in the base install; `pip install "archsteer[mcp]"` still
works as a no-op alias.)

**Languages:** JavaScript / TypeScript, Python, **Java** (Spring-aware), and
**Salesforce Apex** (SOQL/DML + trigger/handler/selector conventions). Layer
detection uses in-source signals first — Spring stereotype annotations, Apex
class-name conventions — then directory names.

## Quickstart

```bash
archsteer init      # scaffold .archsteer/ + a starter rule pack auto-matched to your stack
archsteer map       # build model.json from source
archsteer docs      # regenerate .archsteer/architecture.md (deterministic, Mermaid)
archsteer govern    # conformance + drift score by rule
archsteer adr       # draft ADRs: new structural decisions + widespread rule violations
archsteer baseline  # accept current debt — the ratchet
archsteer steer -f src/controllers/payment.js -t "add refund endpoint"
archsteer check     # CI/pre-commit: fail on NET-NEW violations only
archsteer report    # self-contained .archsteer/report.html
```

`init` auto-detects your stack and seeds a matching baseline rule pack — edit
`.archsteer/architecture.yaml` to fit your conventions, or pick one explicitly:

| Pack | Detected by | Baseline rules |
|---|---|---|
| `java-spring` | pom.xml / build.gradle | persistence only in repositories; controllers never touch repositories |
| `salesforce` | sfdx-project.json / force-app | SOQL only in selectors; logic-less triggers; no DML in controllers |
| `python-service` | pyproject.toml / requirements.txt | persistence behind repositories; thin API handlers |
| `express-to-next` | package.json | repository pattern; Express → Next.js migration |

```bash
archsteer init --pack salesforce   # override the auto-detection
```

## The three design guarantees

1. **Ratchet, not freeze.** `archsteer check` blocks only *net-new* violations against a
   baseline — teams keep shipping features while debt can only shrink.
2. **Conservative, architect-in-the-loop ADRs.** Two narrow sources, both opt-in review —
   never auto-committed. Across time: external-boundary changes (new dependency, new
   datastore, new layer) — never internal reshuffles. Within a snapshot: a rule violated
   in 3+ components — a genuine pattern worth ratifying or relaxing, not a one-off left to
   `check`/`govern`. Drafts are idempotent; re-running never duplicates one already on disk.
3. **Sharp agent steering.** Guardrails injected into `CLAUDE.md`, `AGENTS.md`, and
   `.cursor/rules/archsteer.mdc` (an always-on Cursor rule) are scoped to the files in play and
   point at the governing ADR — they don't dump the whole model into the context window.

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

Add it to Cursor with one click:
[**Install in Cursor →**](cursor://anysphere.cursor-deeplink/mcp/install?name=archsteer&config=eyJjb21tYW5kIjoiYXJjaHN0ZWVyIiwiYXJncyI6WyJtY3AiXX0%3D)

Or to any MCP-compatible client's config:

```json
{ "mcpServers": { "archsteer": { "command": "archsteer", "args": ["mcp"] } } }
```

Also published to the [official MCP registry](https://registry.modelcontextprotocol.io) as
`io.github.einvoice-dev1/archsteer` (runnable via `uvx archsteer mcp`).

## CI / pre-commit

- GitHub Action: `.github/workflows/archsteer.yml` (maps, drafts ADRs, runs the net-new gate,
  uploads `report.html`).
- Git hook: `cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.

## Conformance badge

If your repo pushes snapshots to the situation room (`archsteer push`), its latest
conformance score is a live badge — the one at the top of this README is this repo
governing itself:

```markdown
[![ArchSteer conformance](https://img.shields.io/endpoint?url=https%3A%2F%2Fwww.archsteer.com%2Fapi%2Fbadge%2FYOUR-REPO)](https://www.archsteer.com)
```

Replace `YOUR-REPO` with the repo name `archsteer push` reports. Green at ≥90%, grey while
you're still x-ray-only (no `architecture.yaml` declared yet).

## Try the demo

```bash
cd examples/demo-repo
archsteer init && archsteer map && archsteer report   # open .archsteer/report.html
```

## Roadmap

- **Shipped** — cloud control plane (Next.js + Supabase): multi-repo situation room with
  drift/decision time-series. `archsteer mcp`: a local MCP server so agents query the live
  model + intent mid-edit. An org-wide, hosted MCP server (Team tier) so agents can ask
  cross-repo questions against the situation room — "what's our drift index," "which repos
  have pending ADRs" — the same data as the dashboard, over MCP.
- **Later** — auth, org/repo model, billing.

## Development

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```
