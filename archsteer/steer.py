"""Agent steering: write sharp, model-grounded directives for AI coding agents.

Risk mitigation #3 (context-window eviction): we never dump the full model into
the agent's context. We inject only the directives relevant to the files being
touched, with pointers to the specific ADRs. Deep structural queries are delegated
to the (Phase 3) MCP server, not the prompt. Writes are idempotent — managed
markers are replaced in place, never duplicated.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import List, Optional

from archsteer.engine.intent import Intent, Rule
from archsteer.engine.model import ArchitectureModel

START_MARKER = "<!-- BEGIN ARCHSTEER GUARDRAILS (auto-generated; do not edit) -->"
END_MARKER = "<!-- END ARCHSTEER GUARDRAILS -->"

DEFAULT_TARGETS = ["CLAUDE.md", "AGENTS.md", ".cursor/rules/archsteer.mdc"]
# Files ArchSteer owns outright and will create on first run. AGENTS.md is a
# shared multi-tool convention we only append to if the project already has one.
_AUTO_CREATE = ("CLAUDE.md", ".cursor/rules/archsteer.mdc")


def _rule_applies_to_files(rule: Rule, files: List[str], model: ArchitectureModel) -> bool:
    if not files:
        return True
    for f in files:
        comp = model.components.get(f)
        if rule.scope and fnmatch.fnmatch(f, rule.scope):
            return True
        if rule.scope_layer and comp is not None and comp.layer == rule.scope_layer:
            return True
        if rule.scope is None and rule.scope_layer is None:
            return True
    return False


class AgentSteeringEngine:
    def __init__(self, root_dir: Path):
        self.root = Path(root_dir)

    def synthesize(
        self,
        intent: Intent,
        model: ArchitectureModel,
        files: Optional[List[str]] = None,
        task: Optional[str] = None,
    ) -> str:
        files = files or []
        applicable = [r for r in intent.rules if _rule_applies_to_files(r, files, model)]

        lines: List[str] = [
            START_MARKER,
            "## 🧭 ArchSteer architectural guardrails",
        ]
        if intent.target:
            lines.append(f"**Target architecture:** {intent.target}")
        if task:
            lines.append(f"**Current task:** {task}")
        if files:
            lines.append(f"**Files in scope:** {', '.join(files)}")
        lines.append(
            "\nDo NOT copy patterns from adjacent legacy files. Conform to the rules below. "
            "When unsure of the target pattern, consult the referenced ADR — do not guess "
            "from surrounding code."
        )
        lines.append("\n### Invariants you must satisfy")
        if not applicable:
            lines.append("- (No specific rules for these files — follow the target architecture.)")
        for r in applicable:
            directive = r.steer or r.description or r.id
            tail = f" See `{r.adr}`." if r.adr else ""
            lines.append(f"- **[{r.severity}] {r.id}:** {directive}{tail}")
        lines.append(
            "\n_New violations of these invariants are blocked in CI (`archsteer check`)._"
        )
        lines.append(END_MARKER)
        return "\n".join(lines)

    def write(self, payload: str, targets: Optional[List[str]] = None) -> List[Path]:
        written: List[Path] = []
        for target in (targets or DEFAULT_TARGETS):
            path = self.root / target
            if not path.exists() and target not in _AUTO_CREATE:
                # Respect files/dirs the project doesn't already use, except
                # for the files ArchSteer owns outright (see _AUTO_CREATE).
                if not path.parent.exists():
                    continue
            written.append(self._inject(path, payload))
        return written

    @staticmethod
    def _inject(path: Path, payload: str) -> Path:
        # `.cursor/rules` is a directory by Cursor convention (may already
        # exist as one in real repos) — never write to it directly. We only
        # ever target a specific file inside it (see DEFAULT_TARGETS).
        if path.is_dir():
            raise ValueError(f"steer target must be a file, not a directory: {path}")
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        if path.suffix == ".mdc" and not content.lstrip().startswith("---"):
            # Cursor rule frontmatter: alwaysApply=true keeps architecture
            # guardrails in every agent session, matching CLAUDE.md/AGENTS.md.
            content = f"---\nalwaysApply: true\n---\n\n{content}" if content else "---\nalwaysApply: true\n---\n"
        pattern = re.compile(
            re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL
        )
        if pattern.search(content):
            updated = pattern.sub(payload, content)
        else:
            updated = f"{content.rstrip()}\n\n{payload}\n" if content.strip() else f"{content}{payload}\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated.strip() + "\n", encoding="utf-8")
        return path
