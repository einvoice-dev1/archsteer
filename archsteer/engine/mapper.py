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

from archsteer.engine.model import ArchitectureModel, DependencyEdge
from archsteer.engine.parser import CodeParserFacade

try:  # stdlib on 3.11+; on 3.10 we fall back to a scoped regex
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
    tomllib = None  # type: ignore[assignment]

IGNORE_DIRS = {
    ".git", ".archsteer", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".next", ".turbo", "coverage", ".pytest_cache", ".mypy_cache",
}
SOURCE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java", ".cls", ".trigger"}

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
    """Declared RUNTIME dependency names from package.json / pyproject.toml.

    devDependencies and dev extras are deliberately excluded — tooling churn
    (linters, test runners) would flood ADR decision detection with
    meaningless "new dependency" entries.
    """
    deps: set[str] = set()
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            for key in ("dependencies", "peerDependencies"):
                deps.update((data.get(key) or {}).keys())
        except (json.JSONDecodeError, OSError):
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        specs: List[str] = []
        if tomllib is not None:
            try:
                specs = list((tomllib.loads(text).get("project") or {}).get("dependencies") or [])
            except tomllib.TOMLDecodeError:
                pass
        else:  # Python 3.10: no stdlib TOML parser; scope the regex to dep arrays
            for block in re.finditer(r"^dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL | re.MULTILINE):
                specs += re.findall(r"""["']([^"']+)["']""", block.group(1))
        for spec in specs:  # PEP 508: name is the leading token
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)", spec)
            if m:
                deps.add(m.group(1))
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


def _resolve_python(
    importer_rel: str, target: str, symbols: List[str], components: dict, root_name: str
) -> List[str]:
    """Resolve a Python import to component keys (possibly several).

    Handles the shapes the JS resolver can't:
    - relative imports: ``from .utils import x`` / ``from ..pkg.mod import y``
      (dotted prefix = directory levels up from the importer)
    - same-package absolute imports: ``from fastapi import routing`` resolves
      whether the x-ray root is the repo (components keyed ``fastapi/...``) or
      the package dir itself (components keyed ``routing.py`` — first segment
      matches the root dir name and is stripped).
    - submodule imports through a package: ``from pkg import mod_a, mod_b``
      resolves to each symbol that is itself a module file, so the real edges
      aren't collapsed onto ``pkg/__init__.py``.

    A stdlib/third-party name shadowed by a local module resolves to the local
    file, mirroring how Python's own import system can shadow.
    """
    def probe(mod_path: str) -> Optional[str]:
        mod_path = os.path.normpath(mod_path).replace(os.sep, "/")
        if mod_path in (".", ""):
            mod_path = "__init__"  # `from . import x` in a root-level module
        for cand in (f"{mod_path}.py", f"{mod_path}/__init__.py"):
            if cand in components:
                return cand
        return None

    if target.startswith("."):
        dots = len(target) - len(target.lstrip("."))
        rest = target.lstrip(".")
        base = Path(importer_rel).parent
        for _ in range(dots - 1):
            base = base.parent
        base_path = (base / rest.replace(".", "/")).as_posix() if rest else base.as_posix()
    else:
        parts = target.split(".")
        if probe("/".join(parts)) is None and parts[0] == root_name:
            parts = parts[1:]
        base_path = "/".join(parts)

    hit = probe(base_path)
    if hit is None:
        return []
    if not hit.endswith("__init__.py"):
        return [hit]
    # Package import: symbols may be submodules — resolve each that is one.
    pkg_dir = hit[: -len("__init__.py")]
    sub_hits = [h for s in symbols if (h := probe(f"{pkg_dir}{s}"))]
    return sub_hits or [hit]


def _java_class_index(components: dict) -> dict:
    """Simple-class-name -> [component keys] for every .java file.

    Java convention puts class ``com.foo.Bar`` at ``<source root>/com/foo/Bar.java``
    with a variable prefix (``src/main/java/``, module dirs, …), so an import
    resolves by matching the FQN-derived path suffix; indexing by class name
    first keeps the suffix scan away from O(edges × components).
    """
    idx: dict = {}
    for key, comp in components.items():
        if comp.language == "java":
            idx.setdefault(Path(key).stem, []).append(key)
    return idx


def _resolve_java(target: str, class_index: dict) -> Optional[str]:
    simple = target.rsplit(".", 1)[-1]
    suffix = "/" + target.replace(".", "/") + ".java"
    for key in class_index.get(simple, []):
        if key.endswith(suffix) or key == suffix[1:]:
            return key
    return None


def _apex_name_index(components: dict) -> dict:
    """Apex classes share one org-wide namespace: file stem == class name."""
    return {
        Path(key).stem: key
        for key, comp in components.items()
        if comp.language == "apex" and key.endswith(".cls")
    }


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
        # The parser may have already set a layer from in-source signals
        # (Spring stereotype annotations, Apex naming conventions) — those
        # beat path heuristics, which vary wildly across build layouts.
        comp.layer = comp.layer or _infer_layer(comp.file_path)
        model.components[comp.file_path] = comp

    # Second pass: resolve internal edges now that all components are known.
    # Python deps are probed even when the parser flagged them external:
    # `from mypkg import mod` is textually indistinguishable from a third-party
    # import, so resolution against the actual component set is the arbiter.
    # Java FQN imports and Apex type references likewise resolve against the
    # actual component set.
    java_index = _java_class_index(model.components)
    apex_index = _apex_name_index(model.components)
    for rel, comp in model.components.items():
        extra: List[DependencyEdge] = []
        keep: List[DependencyEdge] = []
        for dep in comp.dependencies:
            if comp.language == "python":
                hits = _resolve_python(rel, dep.target, dep.symbols, model.components, root.name)
            elif comp.language == "java":
                one = _resolve_java(dep.target, java_index)
                hits = [one] if one else []
                if not hits:
                    head = dep.target.split(".", 1)[0]
                    if head in ("java", "javax", "jakarta"):
                        continue  # JDK/EE stdlib: not a third-party dependency
                    # Collapse third-party FQNs to their group (org.springframework,
                    # com.fasterxml, …) so the external-dependency list stays readable.
                    dep.target = ".".join(dep.target.split(".")[:2])
            elif comp.language == "apex":
                one = apex_index.get(dep.target)
                if one is None or one == rel:
                    # Unresolved Apex type refs are System/builtin types, not
                    # third-party packages — dropping them keeps the external-
                    # dependency count meaningful. Self-references (a class
                    # calling its own statics by name) aren't edges either.
                    continue
                hits = [one]
            elif dep.external:
                keep.append(dep)
                continue
            else:
                one = _resolve_internal(rel, dep.target, model.components)
                hits = [one] if one else []
            if hits:
                dep.target = hits[0]
                dep.external = False
                # `from pkg import mod_a, mod_b`: one statement, several real edges.
                extra += [
                    dep.model_copy(update={"target": h, "symbols": []}) for h in hits[1:]
                ]
            # else: keep as-is (unresolved relative imports stay internal,
            # unresolved absolute imports stay external)
            keep.append(dep)
        comp.dependencies = keep + extra
    return model
