"""Local MCP server — the live model + declared intent, queryable mid-edit.

Runs over stdio, spawned by the agent's own editor/CLI (Claude Code, Cursor,
etc.), never by us. It reads only what `archsteer init` / `map` / `govern`
already produced in `.archsteer/` on disk — no network call, no auth, nothing
leaves the machine.

Responses are deliberately scoped (never the whole model), matching the same
context-window discipline as `archsteer steer` — see archsteer/steer.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from archsteer.engine.conformance import ConformanceReport, evaluate
from archsteer.engine.intent import Intent
from archsteer.engine.model import ArchitectureModel
from archsteer.steer import _rule_applies_to_files
from archsteer.workspace import Workspace

mcp = FastMCP("archsteer")

_root = Path(".")


def _workspace() -> Workspace:
    return Workspace(_root)


def _normalize(file_path: str) -> str:
    return file_path.replace("\\", "/").lstrip("./")


def _load() -> tuple[ArchitectureModel, Optional[Intent]]:
    ws = _workspace()
    model = ArchitectureModel.load_if_exists(ws.model)
    if model is None:
        raise RuntimeError(
            f"No model found at {ws.model}. Run `archsteer map` in this repo first."
        )
    intent = Intent.load_if_exists(ws.intent)
    return model, intent


def _report(model: ArchitectureModel, intent: Intent) -> ConformanceReport:
    return evaluate(model, intent)


@mcp.tool()
def current_architecture() -> dict:
    """Summary of the current, code-derived architecture: component/layer
    counts, conformance and drift scores, and the declared migration target
    (if any). Call this once at the start of a task to orient yourself —
    it is not the full component catalog."""
    model, intent = _load()
    report = _report(model, intent) if intent else None
    return {
        "repo": model.repo_name,
        "commit": model.commit_sha,
        "components": len(model.components),
        "layers": sorted(model.get_layers()),
        "governed": intent is not None,
        "target": intent.target if intent else None,
        "conformance_score": report.conformance_score if report else None,
        "drift_score": report.drift_score if report else None,
        "open_violations": len(report.all_violations) if report else None,
    }


@mcp.tool()
def get_target_pattern(file_path: Optional[str] = None) -> dict:
    """The declared target architecture and the specific invariants that
    apply to `file_path` (all rules, if omitted). Call this BEFORE writing
    or editing code so you conform to the intended pattern instead of
    copying whatever an adjacent legacy file happens to do."""
    model, intent = _load()
    if intent is None:
        return {
            "governed": False,
            "message": "No intent declared yet — run `archsteer init` to declare a target.",
        }
    files = [_normalize(file_path)] if file_path else []
    applicable = [r for r in intent.rules if _rule_applies_to_files(r, files, model)]
    return {
        "governed": True,
        "target": intent.target,
        "rules": [
            {
                "id": r.id,
                "severity": r.severity,
                "directive": r.steer or r.description or r.id,
                "adr": r.adr,
            }
            for r in applicable
        ],
    }


@mcp.tool()
def check_file(file_path: str) -> dict:
    """Check a single file against declared architectural intent. Call this
    AFTER editing a file to verify you didn't introduce a violation, without
    waiting for `archsteer check` in CI."""
    model, intent = _load()
    if intent is None:
        return {
            "governed": False,
            "message": "No intent declared yet — run `archsteer init` to declare a target.",
        }
    report = _report(model, intent)
    target = _normalize(file_path)
    violations = [v for v in report.all_violations if v.file == target]
    return {
        "governed": True,
        "file": target,
        "compliant": not violations,
        "violations": [
            {"rule_id": v.rule_id, "severity": v.severity, "message": v.message}
            for v in violations
        ],
    }


def run(path: Optional[str] = None) -> None:
    global _root
    _root = Path(path or os.environ.get("ARCHSTEER_ROOT") or ".").resolve()
    mcp.run()
