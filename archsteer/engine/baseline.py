"""The ratchet: accept existing debt, block only net-new violations.

Teams mid-migration can't freeze features or fix all debt at once. ``baseline``
snapshots the currently-accepted violation fingerprints; ``check`` then fails only
on fingerprints not in that snapshot. Architecture can only improve from here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from archsteer.engine.conformance import ConformanceReport, Violation


class Baseline:
    def __init__(self, fingerprints: set[str], created: str | None = None):
        self.fingerprints = fingerprints
        self.created = created or datetime.now(timezone.utc).isoformat()

    @classmethod
    def from_report(cls, report: ConformanceReport) -> "Baseline":
        return cls({v.fingerprint for v in report.all_violations})

    @classmethod
    def load(cls, path: Path) -> "Baseline":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(set(data.get("fingerprints", [])), data.get("created"))

    @classmethod
    def load_if_exists(cls, path: Path) -> "Baseline | None":
        p = Path(path)
        return cls.load(p) if p.exists() else None

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(
                {"created": self.created, "fingerprints": sorted(self.fingerprints)},
                indent=2,
            ),
            encoding="utf-8",
        )

    def net_new(self, report: ConformanceReport) -> List[Violation]:
        return [v for v in report.all_violations if v.fingerprint not in self.fingerprints]

    def fixed(self, report: ConformanceReport) -> int:
        """Count of baselined violations that are now resolved (progress!)."""
        current = {v.fingerprint for v in report.all_violations}
        return len(self.fingerprints - current)
