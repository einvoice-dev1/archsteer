"""Continuous change analysis → prefilled draft ADRs (architect-in-the-loop).

Risk mitigation #2 (ADR noise): only structural changes that alter *external
architecture boundaries* trigger a draft — a new third-party manifest dependency,
a new persistence entity, or a newly introduced layer. Internal file splits and
util reshuffles produce nothing. We only ever draft; we never auto-commit a
decision. Drafts are idempotent (re-running won't duplicate an existing ADR).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from archsteer.engine.model import ArchitectureModel


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


class DraftADR(BaseModel):
    title: str
    date: str = Field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    status: str = "Draft"
    context: str = ""
    impact: List[str] = Field(default_factory=list)
    kind: str = "change"  # dependency | datastore | layer

    def slug(self) -> str:
        return _slug(self.title)


class DecisionEngine:
    def __init__(self, adr_dir: Path):
        self.adr_dir = Path(adr_dir)

    def analyze_diff(
        self, old: Optional[ArchitectureModel], new: ArchitectureModel
    ) -> List[DraftADR]:
        drafts: List[DraftADR] = []
        if old is None:
            return drafts  # first map establishes the baseline; nothing to decide yet

        for dep in sorted(set(new.manifest_dependencies) - set(old.manifest_dependencies)):
            drafts.append(DraftADR(
                kind="dependency",
                title=f"Introduce external dependency on {dep}",
                context=(
                    f"ArchSteer detected a new third-party dependency '{dep}' added to the "
                    f"project manifest since the last snapshot. New external dependencies "
                    f"expand the system's runtime and supply-chain surface."
                ),
                impact=[
                    "Adds an external runtime/supply-chain boundary.",
                    "May require new configuration, secrets, or version policy.",
                ],
            ))

        for store in sorted(new.get_all_data_stores() - old.get_all_data_stores()):
            if store in ("raw_sql",):
                continue
            drafts.append(DraftADR(
                kind="datastore",
                title=f"Adopt persistence entity '{store}'",
                context=(
                    f"New persistence access to entity '{store}' appeared in the codebase. "
                    f"This alters the data layer and its integrity/migration scope."
                ),
                impact=[
                    "Alters transactional/data-integrity scope.",
                    "Requires a database migration + ownership decision.",
                ],
            ))

        for layer in sorted(new.get_layers() - old.get_layers()):
            drafts.append(DraftADR(
                kind="layer",
                title=f"Introduce architectural layer '{layer}'",
                context=(
                    f"A new architectural layer '{layer}' was introduced. Layer additions "
                    f"change allowed dependency directions and should be ratified."
                ),
                impact=[
                    "Changes allowed dependency directions across the system.",
                    "Conformance rules should be updated to govern the new layer.",
                ],
            ))

        return drafts

    # -- writing --------------------------------------------------------------
    def _next_index(self) -> int:
        if not self.adr_dir.exists():
            return 1
        nums = [
            int(m.group(1))
            for f in self.adr_dir.glob("*.md")
            if (m := re.match(r"(\d{4})-", f.name))
        ]
        return (max(nums) + 1) if nums else 1

    def _exists(self, slug: str) -> bool:
        return self.adr_dir.exists() and any(
            slug in f.name for f in self.adr_dir.glob("*.md")
        )

    def write_drafts(self, drafts: List[DraftADR]) -> List[Path]:
        """Write only drafts that don't already have an ADR. Returns new paths."""
        written: List[Path] = []
        self.adr_dir.mkdir(parents=True, exist_ok=True)
        idx = self._next_index()
        for draft in drafts:
            if self._exists(draft.slug()):
                continue
            path = self.adr_dir / f"{idx:04d}-{draft.slug()}.md"
            path.write_text(self._render(idx, draft), encoding="utf-8")
            written.append(path)
            idx += 1
        return written

    @staticmethod
    def _render(idx: int, adr: DraftADR) -> str:
        consequences = "\n".join(f"- {i}" for i in adr.impact)
        return (
            f"# ADR {idx:04d}: {adr.title}\n\n"
            f"- **Status:** {adr.status}\n"
            f"- **Date:** {adr.date}\n"
            f"- **Generated by:** ArchSteer continuous change engine ({adr.kind})\n\n"
            f"## Context\n{adr.context}\n\n"
            f"## Decision\n_Architect action required: record the rationale behind this change._\n\n"
            f"## Consequences\n{consequences}\n"
        )
