"""The single source of truth: a strictly typed, code-derived architecture model.

Every ArchSteer pillar (docs, ADRs, conformance, steering, report) is a pure
projection of an :class:`ArchitectureModel` serialized to ``.archsteer/model.json``.
Keeping one model is the discipline that keeps a broad product coherent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from pydantic import BaseModel, Field


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class DependencyEdge(BaseModel):
    """A directed dependency from one component to a target module/component."""

    target: str = Field(..., description="Target module path or package name")
    edge_type: str = Field("import", description="import | dynamic | call | inject")
    symbols: List[str] = Field(default_factory=list, description="Imported symbols")
    loc: int = Field(0, description="Source line where the edge originates")
    external: bool = Field(
        False, description="True if target is a third-party package, not local code"
    )


class DataAccessPoint(BaseModel):
    """A place where the component touches a persistence entity."""

    entity: str = Field(..., description="Table, collection, model, or queue name")
    operations: Set[str] = Field(
        default_factory=set, description="READ | WRITE | DELETE | RAW"
    )
    file_path: str = Field(...)
    loc: int = Field(0)


class ExternalCall(BaseModel):
    """An outbound call to a third-party API / SDK / network destination."""

    destination: str = Field(..., description="Endpoint, SDK client, or call kind")
    context: str = Field(..., description="Where the call originates")
    loc: int = Field(0)


class SecurityFinding(BaseModel):
    """A source-level security concern detected by the cross-language scanner."""

    kind: str = Field(..., description="hardcoded-secret | credential-pattern")
    detail: str = Field(..., description="Human-readable description of the match")
    file_path: str = Field(...)
    loc: int = Field(0)


class ArchitectureComponent(BaseModel):
    """A single unit of the system (typically one source file/module)."""

    name: str
    file_path: str
    layer: Optional[str] = Field(
        None, description="Resolved layer, e.g. controller/service/repository"
    )
    tags: List[str] = Field(default_factory=list)
    language: Optional[str] = None
    exported_apis: List[str] = Field(default_factory=list)
    dependencies: List[DependencyEdge] = Field(default_factory=list)
    data_access: List[DataAccessPoint] = Field(default_factory=list)
    external_calls: List[ExternalCall] = Field(default_factory=list)
    security_findings: List[SecurityFinding] = Field(default_factory=list)
    loc: int = Field(0, description="Total lines of code in the file")


class ArchitectureModel(BaseModel):
    """The whole system as derived from source, plus convenience rollups."""

    schema_version: str = "1.0.0"
    timestamp: str = Field(default_factory=_utcnow)
    repo_name: str = "unknown"
    commit_sha: Optional[str] = None
    manifest_dependencies: List[str] = Field(
        default_factory=list,
        description="Declared third-party deps (package.json / pyproject), sorted",
    )
    components: Dict[str, ArchitectureComponent] = Field(default_factory=dict)

    # ---- rollups consumed by decisions / conformance / docs ----
    def get_all_external_dependencies(self) -> Set[str]:
        return {
            call.destination
            for comp in self.components.values()
            for call in comp.external_calls
        }

    def get_all_data_stores(self) -> Set[str]:
        return {
            da.entity
            for comp in self.components.values()
            for da in comp.data_access
        }

    def get_layers(self) -> Set[str]:
        return {c.layer for c in self.components.values() if c.layer}

    def internal_edges(self) -> List[tuple[str, str]]:
        """(source_path, target_path) for resolved internal dependencies."""
        edges: List[tuple[str, str]] = []
        for path, comp in self.components.items():
            for dep in comp.dependencies:
                if not dep.external and dep.target in self.components:
                    edges.append((path, dep.target))
        return edges

    # ---- persistence ----
    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ArchitectureModel":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def load_if_exists(cls, path: Path) -> Optional["ArchitectureModel"]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            return cls.load(p)
        except (json.JSONDecodeError, ValueError):
            return None
