# MCP directory listing copy

Reusable submission copy for MCP directories that need a form (mcp.so, Cursor's
directory, PulseMCP's add-server form, etc.). The official registry entry is
published from `server.json` via `mcp-publisher` and cascades to most
aggregators automatically.

- **Name:** ArchSteer
- **Registry id:** `io.github.einvoice-dev1/archsteer`
- **One-liner:** Architecture governance for AI coding agents — query the live
  architecture model, get file-scoped target patterns, and check for net-new
  violations mid-edit.
- **Description:** ArchSteer derives a repo's real architecture from source
  (`archsteer xray`) and lets the architect declare intent as enforceable
  rules. The MCP server runs locally over stdio, reads only the `.archsteer/`
  artifacts already on disk (no network, nothing leaves the machine), and
  gives agents three tools so they build toward the target architecture
  instead of copying adjacent legacy code.
- **Tools:** `current_architecture`, `get_target_pattern`, `check_file`
- **Transport:** stdio (local)
- **Install:** `pip install archsteer` → `archsteer mcp` (or `uvx archsteer mcp`)
- **Category:** developer tools / code analysis
- **Homepage:** https://www.archsteer.com
- **Repo:** https://github.com/einvoice-dev1/archsteer (MIT)

## Submission checklist (form/account-based; owner action)

- [ ] mcp.so — submit form (or PR to chatmcp/mcpso)
- [ ] PulseMCP — check if auto-indexed from the official registry after ~a week; else use their add-server form
- [ ] Glama — claim the auto-indexed listing (glama.json is committed with the maintainer handle)
- [ ] Cursor directory — check docs.cursor.com/tools intake; the README deeplink works regardless
- [ ] Smithery — optional, lower priority (hosted/remote focus)
