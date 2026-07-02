"""Language-agnostic static extraction.

Design choice (risk mitigation #1 — the tree-sitter cross-compilation trap):
the *reliable default* is a deterministic, dependency-free regex extractor that
works on any machine. Tree-sitter, when installed via the optional ``treesitter``
extra, is used only as an accelerator/validator. ``build_model`` never hard-errors
on a contributor's box.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from archsteer.engine.model import (
    ArchitectureComponent,
    DataAccessPoint,
    DependencyEdge,
    ExternalCall,
)

# ---------------------------------------------------------------------------
# tree-sitter is optional. We probe for it but the regex path is authoritative.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - environment dependent
    from tree_sitter_languages import get_parser as _ts_get_parser  # noqa: F401

    TREE_SITTER_AVAILABLE = True
except Exception:  # noqa: BLE001
    TREE_SITTER_AVAILABLE = False


_EXT_LANG = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
}

# JS/TS ----------------------------------------------------------------------
_JS_IMPORT_FROM = re.compile(r"""import\s+(?P<sym>[^;'"]+?)\s+from\s+['"](?P<src>[^'"]+)['"]""")
_JS_IMPORT_BARE = re.compile(r"""(?<![\w.])import\s+['"](?P<src>[^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""require\(\s*['"](?P<src>[^'"]+)['"]\s*\)""")
_JS_DYNAMIC = re.compile(r"""import\(\s*['"](?P<src>[^'"]+)['"]\s*\)""")
_JS_EXPORT = re.compile(
    r"""export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(?P<name>[A-Za-z0-9_$]+)"""
)
_JS_HTTP = re.compile(r"""\b(?:fetch|axios|got|superagent|ky)\b\s*[.(]|\bhttps?\.\w+\s*\(""")

# Python ---------------------------------------------------------------------
_PY_IMPORT = re.compile(r"""^\s*import\s+(?P<mod>[A-Za-z0-9_.]+)""", re.MULTILINE)
_PY_FROM = re.compile(
    r"""^\s*from\s+(?P<mod>[A-Za-z0-9_.]+)\s+import\s+(?P<sym>.+)""", re.MULTILINE
)
_PY_EXPORT = re.compile(r"""^(?:async\s+)?(?:def|class)\s+(?P<name>[A-Za-z0-9_]+)""", re.MULTILINE)
_PY_HTTP = re.compile(r"""\b(?:requests|httpx|aiohttp|urllib)\b\.""")

# Data access (language-shared heuristics) -----------------------------------
_SQL_LITERAL = re.compile(
    r"""(?P<op>SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?P<rest>[`"']?[A-Za-z0-9_."]+)?""",
    re.IGNORECASE,
)
_RAW_EXEC = re.compile(
    r"""\b(?:execute|executeRaw|queryRaw|raw)\s*\(|\b(?:db|cursor|conn|connection)\.(?:query|execute)\s*\("""
)
_PRISMA = re.compile(
    r"""prisma\.(?P<entity>[A-Za-z0-9_]+)\.(?P<op>findMany|findUnique|findFirst|create|update|delete|upsert|count)"""
)
_SQLALCHEMY = re.compile(r"""session\.query\(\s*(?P<entity>[A-Za-z0-9_]+)""")

_SQL_OP_MAP = {
    "select": "READ",
    "insert into": "WRITE",
    "update": "WRITE",
    "delete from": "DELETE",
}


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


class CodeParserFacade:
    """Extracts an :class:`ArchitectureComponent` per source file."""

    def __init__(self) -> None:
        self.tree_sitter = TREE_SITTER_AVAILABLE

    @staticmethod
    def supported(ext: str) -> bool:
        return ext in _EXT_LANG

    def parse_file(self, file_path: Path, root_dir: Path) -> ArchitectureComponent:
        file_path = Path(file_path)
        root_dir = Path(root_dir)
        ext = file_path.suffix
        rel = file_path.relative_to(root_dir).as_posix()
        comp = ArchitectureComponent(
            name=file_path.name,
            file_path=rel,
            language=_EXT_LANG.get(ext),
        )
        try:
            source = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return comp

        comp.loc = source.count("\n") + 1
        if ext == ".py":
            self._extract_python(source, comp)
        elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            self._extract_javascript(source, comp)
        self._extract_data_access(source, comp, rel)
        return comp

    # -- JS/TS ----------------------------------------------------------------
    def _extract_javascript(self, source: str, comp: ArchitectureComponent) -> None:
        for m in _JS_IMPORT_FROM.finditer(source):
            comp.dependencies.append(
                DependencyEdge(
                    target=m.group("src"),
                    edge_type="import",
                    symbols=self._split_js_symbols(m.group("sym")),
                    loc=_line_of(source, m.start()),
                    external=not m.group("src").startswith("."),
                )
            )
        for rx, etype in ((_JS_IMPORT_BARE, "import"), (_JS_REQUIRE, "import"), (_JS_DYNAMIC, "dynamic")):
            for m in rx.finditer(source):
                src = m.group("src")
                comp.dependencies.append(
                    DependencyEdge(
                        target=src,
                        edge_type=etype,
                        loc=_line_of(source, m.start()),
                        external=not src.startswith("."),
                    )
                )
        for m in _JS_EXPORT.finditer(source):
            comp.exported_apis.append(m.group("name"))
        if "module.exports" in source or "export default" in source:
            comp.exported_apis.append("default")
        for m in _JS_HTTP.finditer(source):
            comp.external_calls.append(
                ExternalCall(
                    destination="HTTP_CLIENT_CALL",
                    context=source[m.start():m.start() + 40].splitlines()[0].strip(),
                    loc=_line_of(source, m.start()),
                )
            )

    @staticmethod
    def _split_js_symbols(raw: str) -> List[str]:
        raw = raw.strip().strip("{}").replace("* as", "").replace("type ", "")
        return [s.strip() for s in raw.split(",") if s.strip()]

    # -- Python ---------------------------------------------------------------
    def _extract_python(self, source: str, comp: ArchitectureComponent) -> None:
        for m in _PY_IMPORT.finditer(source):
            mod = m.group("mod")
            comp.dependencies.append(
                DependencyEdge(
                    target=mod,
                    edge_type="import",
                    loc=_line_of(source, m.start()),
                    external=not mod.startswith("."),
                )
            )
        for m in _PY_FROM.finditer(source):
            mod = m.group("mod")
            comp.dependencies.append(
                DependencyEdge(
                    target=mod,
                    edge_type="import",
                    symbols=[s.strip() for s in m.group("sym").split(",") if s.strip()],
                    loc=_line_of(source, m.start()),
                    external=not mod.startswith("."),
                )
            )
        for m in _PY_EXPORT.finditer(source):
            comp.exported_apis.append(m.group("name"))
        for m in _PY_HTTP.finditer(source):
            comp.external_calls.append(
                ExternalCall(
                    destination="HTTP_CLIENT_CALL",
                    context=source[m.start():m.start() + 40].splitlines()[0].strip(),
                    loc=_line_of(source, m.start()),
                )
            )

    # -- data access (shared) -------------------------------------------------
    def _extract_data_access(self, source: str, comp: ArchitectureComponent, rel: str) -> None:
        for m in _PRISMA.finditer(source):
            op = "READ" if m.group("op").startswith("find") or m.group("op") == "count" else (
                "DELETE" if m.group("op") == "delete" else "WRITE"
            )
            comp.data_access.append(
                DataAccessPoint(
                    entity=m.group("entity"),
                    operations={op},
                    file_path=rel,
                    loc=_line_of(source, m.start()),
                )
            )
        for m in _SQLALCHEMY.finditer(source):
            comp.data_access.append(
                DataAccessPoint(
                    entity=m.group("entity"),
                    operations={"READ"},
                    file_path=rel,
                    loc=_line_of(source, m.start()),
                )
            )
        for m in _SQL_LITERAL.finditer(source):
            op = _SQL_OP_MAP.get(re.sub(r"\s+", " ", m.group("op").lower()), "RAW")
            entity = self._clean_entity(m.group("rest")) if m.group("rest") else "raw_sql"
            comp.data_access.append(
                DataAccessPoint(
                    entity=entity,
                    operations={op, "RAW"},
                    file_path=rel,
                    loc=_line_of(source, m.start()),
                )
            )
        for m in _RAW_EXEC.finditer(source):
            comp.data_access.append(
                DataAccessPoint(
                    entity="raw_sql",
                    operations={"RAW"},
                    file_path=rel,
                    loc=_line_of(source, m.start()),
                )
            )

    @staticmethod
    def _clean_entity(raw: Optional[str]) -> str:
        if not raw:
            return "raw_sql"
        return raw.strip().strip("`\"'").split(".")[0] or "raw_sql"
