"""The time axis: snapshot history + the Architecture Evolution Feed.

This is what turns ArchSteer from "a better linter" into the *system of record for
how software evolves*. Each ``map``/``xray`` persists a timestamped snapshot; diffing
consecutive snapshots yields a human-readable changelog (git-log for architecture)
and a Drift Index trend leadership can watch.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from archsteer.engine.model import ArchitectureModel


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def structural_fingerprint(model: ArchitectureModel) -> str:
    """Hash of structure (ignores timestamp) so identical maps don't spam history."""
    payload = {
        "components": sorted(model.components.keys()),
        "layers": sorted(model.get_layers()),
        "external": sorted(model.get_all_external_dependencies()),
        "stores": sorted(model.get_all_data_stores()),
        "manifest": sorted(model.manifest_dependencies),
        "edges": sorted(model.internal_edges()),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


class SnapshotMeta(BaseModel):
    id: str
    timestamp: str
    commit_sha: Optional[str] = None
    fingerprint: str = ""
    components: int = 0
    layers: List[str] = Field(default_factory=list)
    external_dependencies: int = 0
    data_stores: int = 0
    conformance_score: Optional[float] = None
    drift_score: Optional[float] = None
    open_violations: Optional[int] = None


class ChangeItem(BaseModel):
    kind: str
    text: str
    direction: str = "neutral"  # positive | negative | neutral


class EvolutionFeed(BaseModel):
    from_ts: Optional[str] = None
    to_ts: Optional[str] = None
    changes: List[ChangeItem] = Field(default_factory=list)
    drift_delta: Optional[float] = None
    conformance_delta: Optional[float] = None

    @property
    def is_first(self) -> bool:
        return self.from_ts is None

    def summary(self) -> str:
        if self.is_first:
            return "First snapshot recorded — the evolution baseline is set."
        if not self.changes:
            return "No structural change since the last snapshot."
        return f"{len(self.changes)} architectural change(s) since the last snapshot."


class History:
    """Persists snapshots under ``.archsteer/history/`` with an index.json."""

    def __init__(self, history_dir: Path):
        self.dir = Path(history_dir)
        self.index_path = self.dir / "index.json"

    def metas(self) -> List[SnapshotMeta]:
        if not self.index_path.exists():
            return []
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        return [SnapshotMeta.model_validate(m) for m in data]

    def _save_index(self, metas: List[SnapshotMeta]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps([m.model_dump() for m in metas], indent=2), encoding="utf-8"
        )

    def load_model(self, meta: SnapshotMeta) -> ArchitectureModel:
        return ArchitectureModel.load(self.dir / f"{meta.id}.json")

    def record(
        self,
        model: ArchitectureModel,
        conformance_score: Optional[float] = None,
        drift_score: Optional[float] = None,
        open_violations: Optional[int] = None,
    ) -> Tuple[SnapshotMeta, bool]:
        """Append a snapshot. Returns (meta, recorded). Skips identical structure."""
        metas = self.metas()
        fp = structural_fingerprint(model)
        if metas and metas[-1].fingerprint == fp and conformance_score == metas[-1].conformance_score:
            return metas[-1], False
        meta = SnapshotMeta(
            id=_now_id(),
            timestamp=model.timestamp,
            commit_sha=model.commit_sha,
            fingerprint=fp,
            components=len(model.components),
            layers=sorted(model.get_layers()),
            external_dependencies=len(model.get_all_external_dependencies()),
            data_stores=len([s for s in model.get_all_data_stores() if s != "raw_sql"]),
            conformance_score=conformance_score,
            drift_score=drift_score,
            open_violations=open_violations,
        )
        self.dir.mkdir(parents=True, exist_ok=True)
        model.save(self.dir / f"{meta.id}.json")
        metas.append(meta)
        self._save_index(metas)
        return meta, True

    def latest_two(self) -> Tuple[Optional[SnapshotMeta], Optional[SnapshotMeta]]:
        metas = self.metas()
        if not metas:
            return None, None
        if len(metas) == 1:
            return None, metas[-1]
        return metas[-2], metas[-1]


def compute_feed(
    old_model: Optional[ArchitectureModel],
    new_model: ArchitectureModel,
    old_meta: Optional[SnapshotMeta] = None,
    new_meta: Optional[SnapshotMeta] = None,
) -> EvolutionFeed:
    if old_model is None:
        return EvolutionFeed(to_ts=new_model.timestamp)

    feed = EvolutionFeed(from_ts=old_model.timestamp, to_ts=new_model.timestamp)

    def _diff(old: set, new: set, label: str, pos_on_add: bool, drop_raw: bool = False):
        added = sorted(new - old)
        removed = sorted(old - new)
        if drop_raw:
            added = [a for a in added if a != "raw_sql"]
            removed = [r for r in removed if r != "raw_sql"]
        for a in added:
            feed.changes.append(ChangeItem(
                kind=f"{label}_added", text=f"New {label}: {a}",
                direction="positive" if pos_on_add else "negative",
            ))
        for r in removed:
            feed.changes.append(ChangeItem(
                kind=f"{label}_removed", text=f"Removed {label}: {r}",
                direction="neutral",
            ))

    _diff(set(old_model.manifest_dependencies), set(new_model.manifest_dependencies),
          "dependency", pos_on_add=False)
    _diff(old_model.get_all_data_stores(), new_model.get_all_data_stores(),
          "data store", pos_on_add=False, drop_raw=True)
    _diff(old_model.get_layers(), new_model.get_layers(), "layer", pos_on_add=True)

    delta_comp = len(new_model.components) - len(old_model.components)
    if delta_comp:
        feed.changes.append(ChangeItem(
            kind="components", direction="neutral",
            text=f"{'+' if delta_comp > 0 else ''}{delta_comp} component(s) "
                 f"({len(new_model.components)} total)",
        ))

    if old_meta and new_meta and old_meta.conformance_score is not None and new_meta.conformance_score is not None:
        feed.conformance_delta = round(new_meta.conformance_score - old_meta.conformance_score, 1)
        feed.drift_delta = round(-feed.conformance_delta, 1)
        if feed.conformance_delta:
            up = feed.conformance_delta > 0
            feed.changes.append(ChangeItem(
                kind="conformance", direction="positive" if up else "negative",
                text=f"Conformance {old_meta.conformance_score}% → {new_meta.conformance_score}% "
                     f"({'+' if up else ''}{feed.conformance_delta} pts)",
            ))
    return feed
