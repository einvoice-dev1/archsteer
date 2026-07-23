"""Declared architectural intent — ``.archsteer/architecture.yaml``.

The architect declares the *desired* architecture once: layers, forbidden/required
patterns, and an optional migration target. Conformance is evaluated against this.
A migration is just intent with a target the codebase hasn't fully reached yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.default_flow_style = False


class Rule(BaseModel):
    """A single fitness function.

    Supported ``type`` values:
      - ``required_layer_for_data_access`` — data access (optionally filtered by
        ``operations``) is only allowed in ``allowed_layers``.
      - ``forbidden_import`` — components in scope must not import a target whose
        path matches ``pattern`` (regex). Scope via ``scope_layer`` or ``scope`` glob.
      - ``forbidden_data_access`` — components in scope must not perform the given
        ``operations``.
      - ``forbidden_layer_edge`` — components in ``from_layer`` must not depend on
        components in ``to_layer``.
      - ``forbidden_security_finding`` — components must have no detected
        security findings (hardcoded secrets). Narrow with ``pattern`` (regex
        against the finding's kind/detail).
      - ``required_layer_for_external_call`` — outbound third-party calls
        (HTTP/SDK) are only allowed in ``allowed_layers``.
    """

    id: str
    type: str
    description: str = ""
    severity: str = Field("error", description="error | warn")
    # selectors / params (only the relevant ones are used per type)
    allowed_layers: List[str] = Field(default_factory=list)
    operations: List[str] = Field(default_factory=list)
    scope_layer: Optional[str] = None
    scope: Optional[str] = None
    pattern: Optional[str] = None
    from_layer: Optional[str] = None
    to_layer: Optional[str] = None
    adr: Optional[str] = Field(None, description="ADR file backing this rule")
    steer: Optional[str] = Field(None, description="Directive shown to AI agents")


class Intent(BaseModel):
    version: int = 1
    target: Optional[str] = Field(None, description="One-line description of the goal")
    layers: List[str] = Field(default_factory=list)
    rules: List[Rule] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Intent":
        with Path(path).open("r", encoding="utf-8") as f:
            data = _yaml.load(f) or {}
        return cls.model_validate(data)

    @classmethod
    def load_if_exists(cls, path: Path) -> Optional["Intent"]:
        p = Path(path)
        return cls.load(p) if p.exists() else None
