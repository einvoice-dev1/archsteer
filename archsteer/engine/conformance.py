"""Evaluate declared intent against the model — fitness functions + drift score.

Produces stable-fingerprinted violations (line-number independent, so cosmetic
edits don't churn the ratchet baseline) and a per-rule migration/conformance %.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from typing import Dict, List, Optional

from pydantic import BaseModel

from archsteer.engine.intent import Intent, Rule
from archsteer.engine.model import ArchitectureComponent, ArchitectureModel


class Violation(BaseModel):
    rule_id: str
    severity: str
    file: str
    message: str
    loc: int = 0
    fingerprint: str = ""

    def with_fingerprint(self) -> "Violation":
        # Line-independent identity: rule + file + message-shape.
        key = f"{self.rule_id}|{self.file}|{self.message}".encode("utf-8")
        self.fingerprint = hashlib.sha1(key).hexdigest()[:12]
        return self


class RuleResult(BaseModel):
    rule_id: str
    description: str
    severity: str
    scoped: int
    compliant: int
    violations: List[Violation] = []

    @property
    def progress(self) -> float:
        if self.scoped == 0:
            return 100.0
        return round(100.0 * self.compliant / self.scoped, 1)


class ConformanceReport(BaseModel):
    target: Optional[str] = None
    results: List[RuleResult] = []

    @property
    def all_violations(self) -> List[Violation]:
        return [v for r in self.results for v in r.violations]

    @property
    def conformance_score(self) -> float:
        """Overall % of (rule, component) checks that pass."""
        scoped = sum(r.scoped for r in self.results)
        compliant = sum(r.compliant for r in self.results)
        if scoped == 0:
            return 100.0
        return round(100.0 * compliant / scoped, 1)

    @property
    def drift_score(self) -> float:
        return round(100.0 - self.conformance_score, 1)


def _in_scope(rule: Rule, comp: ArchitectureComponent) -> bool:
    if rule.scope_layer is not None:
        return comp.layer == rule.scope_layer
    if rule.scope:
        return fnmatch.fnmatch(comp.file_path, rule.scope)
    return True  # whole repo


def _eval_rule(rule: Rule, model: ArchitectureModel) -> RuleResult:
    scoped = 0
    compliant = 0
    violations: List[Violation] = []

    for comp in model.components.values():
        if not _in_scope(rule, comp):
            continue
        scoped += 1
        v = _violations_for(rule, comp, model)
        if v:
            violations.extend(v)
        else:
            compliant += 1

    return RuleResult(
        rule_id=rule.id,
        description=rule.description,
        severity=rule.severity,
        scoped=scoped,
        compliant=compliant,
        violations=violations,
    )


def _violations_for(
    rule: Rule, comp: ArchitectureComponent, model: ArchitectureModel
) -> List[Violation]:
    out: List[Violation] = []
    ops = {o.upper() for o in rule.operations}

    if rule.type == "required_layer_for_data_access":
        if comp.layer in rule.allowed_layers:
            return out
        for da in comp.data_access:
            if not ops or (da.operations & ops):
                out.append(
                    Violation(
                        rule_id=rule.id, severity=rule.severity, file=comp.file_path,
                        loc=da.loc,
                        message=f"data access to '{da.entity}' ({'/'.join(sorted(da.operations))}) outside allowed layers {rule.allowed_layers}",
                    ).with_fingerprint()
                )

    elif rule.type == "forbidden_data_access":
        for da in comp.data_access:
            if not ops or (da.operations & ops):
                out.append(
                    Violation(
                        rule_id=rule.id, severity=rule.severity, file=comp.file_path,
                        loc=da.loc,
                        message=f"forbidden data access to '{da.entity}' ({'/'.join(sorted(da.operations))})",
                    ).with_fingerprint()
                )

    elif rule.type == "forbidden_import" and rule.pattern:
        rx = re.compile(rule.pattern)
        for dep in comp.dependencies:
            if rx.search(dep.target):
                out.append(
                    Violation(
                        rule_id=rule.id, severity=rule.severity, file=comp.file_path,
                        loc=dep.loc,
                        message=f"forbidden import '{dep.target}' (matches /{rule.pattern}/)",
                    ).with_fingerprint()
                )

    elif rule.type == "forbidden_layer_edge":
        for dep in comp.dependencies:
            tgt = model.components.get(dep.target)
            if tgt and comp.layer == rule.from_layer and tgt.layer == rule.to_layer:
                out.append(
                    Violation(
                        rule_id=rule.id, severity=rule.severity, file=comp.file_path,
                        loc=dep.loc,
                        message=f"{rule.from_layer} -> {rule.to_layer} dependency on '{dep.target}' is forbidden",
                    ).with_fingerprint()
                )

    return out


def evaluate(model: ArchitectureModel, intent: Intent) -> ConformanceReport:
    return ConformanceReport(
        target=intent.target,
        results=[_eval_rule(rule, model) for rule in intent.rules],
    )
