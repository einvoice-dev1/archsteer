"""Cross-language security scanning: hardcoded secrets in source text.

Unlike SQL/DML extraction, secret shapes don't vary by language, so this runs
as one regex pass over raw file text rather than per-language extraction —
it covers every file `parser.py` visits, including languages with no
dedicated extractor. Known limitation: comment-stripping is line-prefix based
(#, //, --, *) and does not understand block comments or string interpolation
beyond the placeholders listed below, so it favors precision over recall.
"""

from __future__ import annotations

import re
from typing import List

from archsteer.engine.model import SecurityFinding

# Variable-name-shaped secrets: NAME (= | : | :=) "literal" where NAME suggests
# a credential and the literal isn't an env lookup or an obvious placeholder.
_SECRET_VAR = re.compile(
    r"""(?i)\b(
        password|passwd|secret|token|api[_-]?key|access[_-]?key|
        private[_-]?key|client[_-]?secret|auth[_-]?token|signing[_-]?key
    )\b\s*[:=]{1,2}\s*["']([^"'\n]{8,})["']""",
    re.VERBOSE,
)

_ENV_LOOKUP = re.compile(
    r"os\.environ|process\.env|getenv|System\.getenv|ENV\[|Environment\.GetEnvironmentVariable"
)

_PLACEHOLDER = re.compile(
    r"""(?ix)^(
        changeme | change[_-]?me | your[_-].* | xxx+ | todo | fixme | dummy |
        fake | example | placeholder | test | sample | none | null | undefined |
        \$\{.*\} | %\{.*\} | <.*> | @\{.*\} | \{\{.*\}\} | 0+ | 1+
    )$"""
)

_COMMENT_PREFIX = re.compile(r"^\s*(#|//|--|\*)")

# High-confidence literal patterns regardless of surrounding variable name.
_CREDENTIAL_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("generic API secret key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |)?PRIVATE KEY-----")),
]


def scan_source(file_path: str, text: str) -> List[SecurityFinding]:
    """Scan one file's raw text for hardcoded secrets. Language-agnostic."""
    findings: List[SecurityFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _COMMENT_PREFIX.match(line):
            continue

        for m in _SECRET_VAR.finditer(line):
            name, value = m.group(1), m.group(2)
            if _ENV_LOOKUP.search(value) or _PLACEHOLDER.match(value.strip()):
                continue
            findings.append(
                SecurityFinding(
                    kind="hardcoded-secret",
                    detail=f"literal assigned to '{name}' looks like a credential",
                    file_path=file_path,
                    loc=lineno,
                )
            )

        for label, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SecurityFinding(
                        kind="hardcoded-secret",
                        detail=f"{label} literal in source",
                        file_path=file_path,
                        loc=lineno,
                    )
                )

    return findings
