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
    ".java": "java",
    ".cls": "apex",
    ".trigger": "apex",
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
# `fetch(` specifically needs a same-origin carve-out: a Next.js/React component
# calling its own `/api/...` route handler via fetch('/api/x') is not an
# external call, and flagging it produces exactly the false positives an
# architect stops trusting the tool over. Other HTTP clients (axios, got, a
# direct https.request) are kept as unconditionally external — those are
# never how you'd call your own same-origin route.
_FETCH_LITERAL_ARG = re.compile(r"""\bfetch\s*\(\s*[`'"](?P<url>[^`'"]*)[`'"]""")
_ABS_URL_HOST = re.compile(r"""^(?:[a-z][a-z0-9+.\-]*:)?//(?P<host>[A-Za-z0-9.\-]+)""", re.IGNORECASE)

# Python ---------------------------------------------------------------------
_PY_IMPORT = re.compile(r"""^\s*import\s+(?P<mod>[A-Za-z0-9_.]+)""", re.MULTILINE)
_PY_FROM = re.compile(
    r"""^\s*from\s+(?P<mod>[A-Za-z0-9_.]+)\s+import\s+(?P<sym>.+)""", re.MULTILINE
)
_PY_EXPORT = re.compile(r"""^(?:async\s+)?(?:def|class)\s+(?P<name>[A-Za-z0-9_]+)""", re.MULTILINE)
_PY_HTTP = re.compile(r"""\b(?:requests|httpx|aiohttp|urllib)\b\.""")

# Java -------------------------------------------------------------------------
_JAVA_IMPORT = re.compile(
    r"""^\s*import\s+(?:static\s+)?(?P<fqn>[a-zA-Z_][\w.]*?)(?:\.\*)?\s*;""", re.MULTILINE
)
_JAVA_TYPE = re.compile(
    r"""^\s*(?:@\w+(?:\([^)]*\))?\s+)*public\s+(?:final\s+|abstract\s+)*(?:class|interface|enum|record)\s+(?P<name>\w+)""",
    re.MULTILINE,
)
# Spring stereotype annotations are the most reliable layer signal in Java —
# far better than directory names, which vary wildly across build layouts.
_JAVA_LAYER_ANNOTATIONS = [
    ("@RestController", "controller"),
    ("@Controller", "controller"),
    ("@Service", "service"),
    ("@Repository", "repository"),
    ("@Entity", "model"),
]
_JAVA_ORM = re.compile(
    r"""(?:entityManager|em)\.(?:createQuery|createNativeQuery|persist|merge|remove|find)\s*\(|jdbcTemplate\.(?:query|queryForObject|queryForList|update|batchUpdate|execute)\s*\("""
)
# Spring Data repositories are interfaces with no stereotype annotation — the
# superinterface is the layer signal.
_JAVA_SPRING_DATA = re.compile(
    r"""interface\s+\w+[\w\s,<>]*?extends\s+[\w\s,<>.]*?(?:JpaRepository|CrudRepository|PagingAndSortingRepository|ListCrudRepository|Repository)\s*<"""
)
# Name-suffix fallback (FooController, FooService, …) for classes with neither
# a stereotype annotation nor a conventional directory.
_JAVA_NAME_LAYERS = [
    ("Controller", "controller"),
    ("Service", "service"),
    ("Repository", "repository"),
    ("Dao", "repository"),
    ("Entity", "model"),
]
_JAVA_HTTP = re.compile(
    r"""\b(?:restTemplate|webClient|RestClient|HttpClient)\b\s*\.|\bWebClient\.(?:create|builder)\b"""
)

# Apex / Salesforce ------------------------------------------------------------
# Apex has no import statements: every class shares one org-wide namespace.
# The parser records candidate type references; the mapper resolves them
# against the set of actual class files and DROPS the rest (unresolved refs
# are System/builtin types, not meaningful external dependencies).
_APEX_TYPEREF = re.compile(
    r"""\bnew\s+(?P<new>[A-Z]\w+)\s*\(|\bextends\s+(?P<ext>[A-Z]\w+)|\bimplements\s+(?P<impl>[A-Z][\w.]*)|\b(?P<call>[A-Z]\w+)\.\w+\s*\("""
)
_APEX_BUILTINS = {
    "System", "Database", "Test", "Schema", "Trigger", "String", "Integer", "Long",
    "Decimal", "Double", "Boolean", "Date", "Datetime", "Time", "Id", "Blob",
    "List", "Set", "Map", "SObject", "Math", "JSON", "Http", "HttpRequest",
    "HttpResponse", "ApexPages", "PageReference", "Messaging", "UserInfo",
    "Limits", "EncodingUtil", "Crypto", "Url", "Type", "Pattern", "Matcher",
    "Exception", "AuraHandledException", "DmlException", "QueryException",
    "Savepoint", "SObjectType", "SObjectField", "Label", "Site", "Network",
    "Comparable", "Queueable", "Schedulable", "Batchable", "InstallHandler",
}
_APEX_TYPE = re.compile(
    r"""^\s*(?:global|public|private)\s+(?:with\s+sharing\s+|without\s+sharing\s+|inherited\s+sharing\s+)?(?:virtual\s+|abstract\s+)?(?:class|interface|enum)\s+(?P<name>\w+)""",
    re.MULTILINE,
)
_SOQL = re.compile(
    r"""\[\s*SELECT\b.+?\bFROM\s+(?P<entity>\w+)""", re.IGNORECASE | re.DOTALL
)
_APEX_DML = re.compile(
    r"""(?:^|\s)(?P<op>insert|update|upsert|delete|undelete)\s+(?:new\s+(?P<entity>[A-Z]\w+)\s*\(|\w+\s*;)|Database\.(?P<dbop>insert|update|upsert|delete|undelete)\s*\(""",
    re.MULTILINE,
)
_APEX_CALLOUT = re.compile(r"""\bHttp\s*\(\s*\)|\bHttpRequest\s*\(""")
# fflib / Salesforce naming conventions: the class-name suffix is the layer.
_APEX_NAME_LAYERS = [
    ("TriggerHandler", "handler"),
    ("Handler", "handler"),
    ("Controller", "controller"),
    ("Service", "service"),
    ("Selector", "repository"),
    ("Repository", "repository"),
    ("Domain", "model"),
    ("Batch", "job"),
    ("Queueable", "job"),
    ("Schedulable", "job"),
    ("Scheduler", "job"),
    ("Test", "test"),
    ("Mock", "test"),
]

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
# Supabase JS SDK: `.from("table")` chained (often across lines/formatting) to
# an operation call — `sb.from("x").select(...)`, or
#   .from("x")
#   .insert({...})
# The lazy, negated-lookahead middle group stops at the next `.from(` so an
# unrelated later call in the same file doesn't get attributed to this entity.
# Known gap: `.rpc(...)` stored-procedure calls aren't recognized yet.
_SUPABASE = re.compile(
    r"""\.from\(\s*[`'"](?P<entity>[A-Za-z0-9_]+)[`'"]\s*\)"""
    r"""(?:(?!\.from\().)*?"""
    r"""\.(?P<op>select|insert|update|upsert|delete)\s*\(""",
    re.DOTALL,
)
_SUPABASE_OP_MAP = {"select": "READ", "insert": "WRITE", "update": "WRITE", "upsert": "WRITE", "delete": "DELETE"}

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
        elif ext == ".java":
            self._extract_java(source, comp)
        elif ext in (".cls", ".trigger"):
            self._extract_apex(source, comp, rel)
        if ext in (".cls", ".trigger"):
            # Apex data access is SOQL/DML, handled in _extract_apex — the
            # shared SQL-literal heuristics double-count SOQL as raw SQL.
            return comp
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
            destination = "HTTP_CLIENT_CALL"
            if m.group(0).lstrip().startswith("fetch"):
                arg = _FETCH_LITERAL_ARG.match(source, m.start())
                if arg is not None:
                    url = arg.group("url")
                    if url.startswith("/") and not url.startswith("//"):
                        continue  # same-origin call to our own route — not external
                    host = _ABS_URL_HOST.match(url)
                    if host:
                        destination = host.group("host")
            comp.external_calls.append(
                ExternalCall(
                    destination=destination,
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

    # -- Java -------------------------------------------------------------------
    def _extract_java(self, source: str, comp: ArchitectureComponent) -> None:
        for m in _JAVA_IMPORT.finditer(source):
            fqn = m.group("fqn")
            comp.dependencies.append(
                DependencyEdge(
                    target=fqn,
                    edge_type="import",
                    loc=_line_of(source, m.start()),
                    external=True,  # second pass resolves same-repo FQNs
                )
            )
        for m in _JAVA_TYPE.finditer(source):
            comp.exported_apis.append(m.group("name"))
        for annotation, layer in _JAVA_LAYER_ANNOTATIONS:
            if re.search(re.escape(annotation) + r"\b", source):
                comp.layer = layer
                break
        if comp.layer is None and _JAVA_SPRING_DATA.search(source):
            comp.layer = "repository"
        if comp.layer is None:
            stem = Path(comp.file_path).stem
            for suffix, layer in _JAVA_NAME_LAYERS:
                if stem.endswith(suffix):
                    comp.layer = layer
                    break
        for m in _JAVA_ORM.finditer(source):
            comp.data_access.append(
                DataAccessPoint(
                    entity="orm",
                    operations={"RAW"},
                    file_path=comp.file_path,
                    loc=_line_of(source, m.start()),
                )
            )
        for m in _JAVA_HTTP.finditer(source):
            comp.external_calls.append(
                ExternalCall(
                    destination="HTTP_CLIENT_CALL",
                    context=source[m.start():m.start() + 40].splitlines()[0].strip(),
                    loc=_line_of(source, m.start()),
                )
            )

    # -- Apex / Salesforce --------------------------------------------------------
    def _extract_apex(self, source: str, comp: ArchitectureComponent, rel: str) -> None:
        seen: set[str] = set()
        for m in _APEX_TYPEREF.finditer(source):
            name = m.group("new") or m.group("ext") or m.group("impl") or m.group("call")
            name = name.split(".")[0]
            if name in _APEX_BUILTINS or name in seen:
                continue
            seen.add(name)
            comp.dependencies.append(
                DependencyEdge(
                    target=name,
                    edge_type="typeref",
                    loc=_line_of(source, m.start()),
                    external=True,  # mapper resolves against real class files, drops the rest
                )
            )
        for m in _APEX_TYPE.finditer(source):
            comp.exported_apis.append(m.group("name"))
        stem = Path(rel).stem
        if rel.endswith(".trigger"):
            comp.layer = "trigger"
        else:
            for suffix, layer in _APEX_NAME_LAYERS:
                if stem.endswith(suffix):
                    comp.layer = layer
                    break
        for m in _SOQL.finditer(source):
            comp.data_access.append(
                DataAccessPoint(
                    entity=m.group("entity"),
                    operations={"READ"},
                    file_path=rel,
                    loc=_line_of(source, m.start()),
                )
            )
        for m in _APEX_DML.finditer(source):
            op = (m.group("op") or m.group("dbop")).lower()
            comp.data_access.append(
                DataAccessPoint(
                    entity=m.group("entity") or "sobject",
                    operations={"DELETE" if "delete" in op else "WRITE"},
                    file_path=rel,
                    loc=_line_of(source, m.start()),
                )
            )
        for m in _APEX_CALLOUT.finditer(source):
            comp.external_calls.append(
                ExternalCall(
                    destination="HTTP_CALLOUT",
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
        for m in _SUPABASE.finditer(source):
            comp.data_access.append(
                DataAccessPoint(
                    entity=m.group("entity"),
                    operations={_SUPABASE_OP_MAP[m.group("op")]},
                    file_path=rel,
                    loc=_line_of(source, m.start()),
                )
            )
        for m in _SQL_LITERAL.finditer(source):
            # Guard against prose ("// Update existing pet's properties"):
            # real SQL sits inside a string literal (a quote appears earlier on
            # the line) or is written in SQL-style ALL CAPS.
            line_start = source.rfind("\n", 0, m.start()) + 1
            prefix = source[line_start:m.start()]
            keyword = m.group("op")
            if not (any(q in prefix for q in "\"'`") or keyword == keyword.upper()):
                continue
            op = _SQL_OP_MAP.get(re.sub(r"\s+", " ", keyword.lower()), "RAW")
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
