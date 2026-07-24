---
description: Zero-config X-ray of this repo's real architecture — map, living docs, and evolution feed. No rules required.
---

# ArchSteer X-ray

Run `archsteer xray` in the repo root.

- If the command isn't found, install first: `pipx install archsteer` (or
  `pip install archsteer`, needs Python 3.10+), or run one-off via
  `uvx archsteer xray`.
- If this is the first run in the repo, it scaffolds `.archsteer/` and writes
  `.archsteer/architecture.md` (living docs + a Mermaid layer diagram) and
  `.archsteer/report.html`.

Report back: component count, the detected layers, any architectural change(s)
since the last snapshot (the evolution feed), and point to the two generated
files. If `.archsteer/architecture.yaml` doesn't exist yet, mention that
`archsteer init` seeds a starter rule pack auto-matched to the repo's stack
(Java Spring, Salesforce, Python service, Next.js App Router, or Express→Next
migration) so conformance can be enforced going forward, not just observed.
