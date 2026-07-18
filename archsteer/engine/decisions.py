"""Continuous change analysis → prefilled draft ADRs (architect-in-the-loop).

Risk mitigation #2 (ADR noise): only structural changes that alter *external
architecture boundaries* trigger a draft — a new third-party manifest dependency,
a new persistence entity, or a newly introduced layer. Internal file splits and
util reshuffles produce nothing. We only ever draft; we never auto-commit a
decision. Drafts are idempotent (re-running won't duplicate an existing ADR).

A second, complementary source (`analyze_violation_patterns`) looks sideways
rather than backwards in time: a rule violated across many components in a
single snapshot is a standing architectural question, not a bug report — is
the rule wrong for this codebase, or is this debt that needs a remediation
plan? `archsteer check`/`govern` already list each violation individually;
this groups them into the one decision an architect actually has to make.
A single violation isn't a pattern — those are left to check/govern.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from archsteer.engine.conformance import ConformanceReport
from archsteer.engine.intent import Intent
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

    def analyze_violation_patterns(
        self,
        report: ConformanceReport,
        intent: Optional[Intent],
        min_files: int = 3,
    ) -> List[DraftADR]:
        """A rule violated across >= min_files components in one snapshot.

        Title deliberately excludes the occurrence count so the slug — and
        therefore idempotency — is stable even as the count drifts run to run.
        """
        drafts: List[DraftADR] = []
        adr_by_rule = {r.id: r.adr for r in (intent.rules if intent else [])}

        for result in report.results:
            files = sorted({v.file for v in result.violations})
            if len(files) < min_files:
                continue
            sample = files[:8]
            more = f" (+{len(files) - len(sample)} more)" if len(files) > len(sample) else ""
            existing_adr = adr_by_rule.get(result.rule_id)
            if existing_adr:
                context = (
                    f"Rule '{result.rule_id}' ({result.description}) is already documented in "
                    f"{existing_adr}, but is violated in {len(files)} file(s): "
                    f"{', '.join(sample)}{more}. This many occurrences is a pattern, not a "
                    f"one-off — decide whether to commit to remediation (track the debt with "
                    f"`archsteer baseline`) or the rule no longer matches reality and should be "
                    f"relaxed or scoped, with the reasoning recorded here."
                )
            else:
                context = (
                    f"Rule '{result.rule_id}' ({result.description}) has no backing ADR and is "
                    f"violated in {len(files)} file(s): {', '.join(sample)}{more}. This many "
                    f"occurrences is a pattern, not a mistake — decide whether to ratify the "
                    f"rule (fix the violations, or accept them as debt with `archsteer baseline`) "
                    f"or the rule is wrong for this codebase and should be relaxed or dropped."
                )
            drafts.append(DraftADR(
                kind="violation-pattern",
                title=f"Review rule '{result.rule_id}': widespread violations found",
                context=context,
                impact=[
                    f"Severity: {result.severity} · {len(files)} of {result.scoped} "
                    f"in-scope component(s) violate this rule.",
                    "Ratify as debt: run `archsteer baseline` to accept current violations "
                    "and block only new ones.",
                    "Relax the rule: edit `.archsteer/architecture.yaml` and record the "
                    "reasoning in this ADR's Decision section.",
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
