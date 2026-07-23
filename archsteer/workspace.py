"""Canonical locations inside a repo's ``.archsteer/`` control directory."""

from __future__ import annotations

from pathlib import Path


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.dir = self.root / ".archsteer"

    @property
    def intent(self) -> Path:
        return self.dir / "architecture.yaml"

    @property
    def model(self) -> Path:
        return self.dir / "model.json"

    @property
    def model_prev(self) -> Path:
        return self.dir / "model.prev.json"

    @property
    def parse_cache(self) -> Path:
        return self.dir / "parse_cache.json"

    @property
    def baseline(self) -> Path:
        return self.dir / "baseline.json"

    @property
    def adr_dir(self) -> Path:
        return self.dir / "adr"

    @property
    def history_dir(self) -> Path:
        return self.dir / "history"

    @property
    def architecture_md(self) -> Path:
        return self.dir / "architecture.md"

    @property
    def report_html(self) -> Path:
        return self.dir / "report.html"

    @property
    def initialized(self) -> bool:
        return self.intent.exists()
