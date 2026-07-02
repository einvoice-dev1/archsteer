"""Walks a repo and assembles the :class:`ArchitectureModel` from parsed files.

Resolves relative imports to internal component keys, infers a layer per
component from path conventions (overridable by intent later), and records the
declared third-party manifest dependencies that ADR decision detection watches.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from archsteer.engine.model import ArchitectureModel
from archsteer.engine.parser import CodeParserFacade

IGNORE_DIRS = {
    ".git", ".archsteer", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".next", ".turbo", "coverage", ".pytest_cache", ".mypy_cache",
}
SOURCE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"}

# Path-segment -> layer. First match wins. Architects refine this in intent.
LAYER_HINTS = [
    ("repositories", "repository"),
    ("repository", "repository"),
    ("controllers", "controller"),
    ("controller", "controller"),
    ("routes", "route"),
    ("routers", "route"),
    ("services", "service"),
    ("service", "service"),
    ("models", "model"),
    ("entities", "model"),
    ("middleware", "middleware"),
    ("handlers", "handler"),
    ("api", "api"),
    ("lib", "lib"),
    ("utils", "util"),
    ("components", "ui"),
]
_CANDIDATE_EXTS = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"]


def _infer_layer(rel_path: str) -> Optional[str]:
    segments = {s.lower() for s in Path(rel_path).parts}
    for hint, layer in LAYER_HINTS:
        if hint in segments:
            return layer
    return None


def _git_sha(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _read_manifest_deps(root: Path) -> List[str]:
    deps: set[str] = set()
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps.update((data.get(key) or {}).keys())
        except (json.JSONDecodeError, OSError):
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        # Lightweight extraction; avoids a hard tomli dependency on 3.10.
        text = pyproject.read_text(encoding="utf-8")
        for m in re.finditer(r"""["']([A-Za-z0-9_.\-]+)\s*(?:[><=!~]|["'])""", text):
            name = m.group(1)
            if name and not name.startswith("."):
                deps.add(name)
    return sorted(deps)


def _resolve_internal(importer_rel: str, target: str, components: dict) -> Optional[str]:
    """Resolve a relative JS/TS import (``./x``) to a component key, or None."""
    if not target.startswith("."):
        return None
    raw = (Path(importer_rel).parent / target).as_posix()
    base_posix = os.path.normpath(raw).replace(os.sep, "/")  # collapse ../ segments
    for ext in _CANDIDATE_EXTS:
        cand = f"{base_posix}{ext}"
        if cand in components:
            return cand
        idx = f"{base_posix}/index{ext}" if ext else None
        if idx and idx in components:
            return idx
    return None


def build_model(root_dir: str | Path) -> ArchitectureModel:
    root = Path(root_dir).resolve()
    parser = CodeParserFacade()
    model = ArchitectureModel(
        repo_name=root.name,
        commit_sha=_git_sha(root),
        manifest_dependencies=_read_manifest_deps(root),
    )

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_EXTS:
            continue
        if any(part in IGNORE_DIRS for part in path.relative_to(root).parts):
            continue
        comp = parser.parse_file(path, root)
        comp.layer = _infer_layer(comp.file_path)
        model.components[comp.file_path] = comp

    # Second pass: resolve internal edges now that all components are known.
    for rel, comp in model.components.items():
        for dep in comp.dependencies:
            if dep.external:
                continue
            resolved = _resolve_internal(rel, dep.target, model.components)
            if resolved:
                dep.target = resolved
            else:
                # relative import we couldn't resolve to a file: keep as-is, internal
                pass
    return model
