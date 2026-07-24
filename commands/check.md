---
description: Fail on NET-NEW architecture violations only — the same ratchet ArchSteer runs in CI, run right now.
---

# ArchSteer check

Run `archsteer check` in the repo root.

- Requires `.archsteer/architecture.yaml` to exist (run `archsteer init` first
  if not — it auto-detects the stack and seeds a starter rule pack).
- Only NET-NEW error-severity violations against the repo's baseline block
  (exit code 1); pre-existing debt never blocks. The output includes a
  conformance-score line (percent + delta since the last snapshot).

If it fails, list the net-new violations shown (file, rule, message) and fix
the ones caused by the current change before finishing. If a violation looks
like a genuine, repo-wide pattern shift rather than a one-off, suggest running
`archsteer adr` instead of just patching around it.
