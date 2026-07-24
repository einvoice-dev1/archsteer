"""End-to-end tests over the ArchSteer engine using throwaway repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from archsteer.cli import app
from archsteer.docs import render_architecture_md
from archsteer.engine.baseline import Baseline
from archsteer.engine.conformance import evaluate
from archsteer.engine.decisions import DecisionEngine
from archsteer.engine.intent import Intent
from archsteer.engine.mapper import build_model
from archsteer.engine.security import scan_source
from archsteer.steer import START_MARKER, AgentSteeringEngine

PACK = Path(__file__).resolve().parent.parent / "archsteer" / "packs" / "express_to_next"


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _legacy_repo(root: Path) -> None:
    _write(root, "package.json", '{"dependencies":{"express":"^4","pg":"^8"}}')
    _write(root, "src/db/client.js", "const {Pool}=require('pg');module.exports={pool:new Pool()};")
    _write(
        root, "src/controllers/payment_controller.js",
        "const {pool}=require('../db/client');\n"
        "async function charge(){return pool.query('INSERT INTO payments (a) VALUES ($1)',[1]);}\n"
        "module.exports={charge};",
    )
    _write(
        root, "src/repositories/user_repository.js",
        "const {pool}=require('../db/client');\n"
        "async function find(id){return pool.query('SELECT * FROM users WHERE id=$1',[id]);}\n"
        "module.exports={find};",
    )
    _write(
        root, "src/services/user_service.js",
        "const {find}=require('../repositories/user_repository');\n"
        "module.exports={get:(id)=>find(id)};",
    )


def _intent() -> Intent:
    return Intent.load(PACK / "architecture.yaml")


def test_model_build_and_layers(tmp_path: Path):
    _legacy_repo(tmp_path)
    model = build_model(tmp_path)
    assert "src/controllers/payment_controller.js" in model.components
    layers = model.get_layers()
    assert {"controller", "repository", "service"} <= layers
    # internal edge service -> repository resolves through the ../ path
    assert ("src/services/user_service.js", "src/repositories/user_repository.js") in model.internal_edges()


def _java_repo(root: Path) -> None:
    _write(root, "pom.xml", "<project/>")
    _write(
        root, "src/main/java/com/acme/web/OrderController.java",
        "package com.acme.web;\n"
        "import com.acme.core.OrderService;\n"
        "import org.springframework.web.bind.annotation.RestController;\n"
        "import java.util.List;\n"
        "@RestController\npublic class OrderController { private final OrderService s; }\n",
    )
    _write(
        root, "src/main/java/com/acme/core/OrderService.java",
        "package com.acme.core;\n"
        "import org.springframework.stereotype.Service;\n"
        "@Service\npublic class OrderService {\n"
        "  public int c() { return jdbcTemplate.queryForObject(\"SELECT count(*) FROM orders\", Integer.class); }\n"
        "}\n",
    )


def _sf_repo(root: Path) -> None:
    _write(root, "sfdx-project.json", "{}")
    _write(
        root, "force-app/main/default/classes/AccountService.cls",
        "public with sharing class AccountService {\n"
        "  public static void run() { List<Account> a = AccountSelector.selectAll(); update a; }\n"
        "}\n",
    )
    _write(
        root, "force-app/main/default/classes/AccountSelector.cls",
        "public with sharing class AccountSelector {\n"
        "  public static List<Account> selectAll() { return [SELECT Id FROM Account]; }\n"
        "}\n",
    )
    _write(
        root, "force-app/main/default/triggers/AccountTrigger.trigger",
        "trigger AccountTrigger on Account (before insert) { AccountService.run(); }\n",
    )


def test_java_layers_edges_and_external_grouping(tmp_path: Path):
    _java_repo(tmp_path)
    model = build_model(tmp_path)
    ctrl = model.components["src/main/java/com/acme/web/OrderController.java"]
    svc = model.components["src/main/java/com/acme/core/OrderService.java"]
    # Layer comes from the Spring stereotype annotation, not the path.
    assert ctrl.layer == "controller" and svc.layer == "service"
    # FQN import resolves to the actual file across differing package dirs.
    assert ("src/main/java/com/acme/web/OrderController.java",
            "src/main/java/com/acme/core/OrderService.java") in model.internal_edges()
    ext = {d.target for c in model.components.values() for d in c.dependencies if d.external}
    # Third-party FQNs collapse to their group; JDK stdlib is dropped entirely.
    assert "org.springframework" in ext
    assert not any(e.startswith("java.") for e in ext)
    # JdbcTemplate call registers as raw data access.
    assert any("RAW" in d.operations for d in svc.data_access)


def test_apex_typerefs_soql_dml_and_layers(tmp_path: Path):
    _sf_repo(tmp_path)
    model = build_model(tmp_path)
    svc = model.components["force-app/main/default/classes/AccountService.cls"]
    sel = model.components["force-app/main/default/classes/AccountSelector.cls"]
    trg = model.components["force-app/main/default/triggers/AccountTrigger.trigger"]
    # Layers from naming conventions; triggers get their own layer.
    assert svc.layer == "service" and sel.layer == "repository" and trg.layer == "trigger"
    # Type references resolve to internal edges (Apex has no imports).
    edges = model.internal_edges()
    assert ("force-app/main/default/classes/AccountService.cls",
            "force-app/main/default/classes/AccountSelector.cls") in edges
    assert ("force-app/main/default/triggers/AccountTrigger.trigger",
            "force-app/main/default/classes/AccountService.cls") in edges
    # Unresolved builtins (List, Account ctor refs…) are dropped, not "external deps".
    assert not any(d.external for c in model.components.values() for d in c.dependencies)
    # SOQL is READ data access on the entity; DML registers as WRITE.
    assert any(d.entity == "Account" and "READ" in d.operations for d in sel.data_access)
    assert any("WRITE" in d.operations for d in svc.data_access)


def test_init_pack_autodetect(tmp_path: Path):
    from archsteer.cli import _detect_pack
    _write(tmp_path, "pom.xml", "<project/>")
    assert _detect_pack(tmp_path) == "java-spring"
    # Salesforce markers outrank others even when package.json coexists.
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, "sfdx-project.json", "{}")
    assert _detect_pack(tmp_path) == "salesforce"


def test_conformance_flags_raw_sql_outside_repository(tmp_path: Path):
    _legacy_repo(tmp_path)
    report = evaluate(build_model(tmp_path), _intent())
    raw = next(r for r in report.results if r.rule_id == "no-raw-sql-outside-repository")
    files = {v.file for v in raw.violations}
    assert "src/controllers/payment_controller.js" in files
    # repository raw SQL is allowed -> not flagged
    assert "src/repositories/user_repository.js" not in files
    assert 0 < report.conformance_score < 100


def test_security_scan_detects_secrets_and_ignores_env_and_placeholders():
    positive = "\n".join([
        'const password = "Sup3rSecretPass!";',
        'AWS_KEY = "AKIAABCDEFGHIJKLMNOP"',
        'let token = "abcd1234efgh5678";',
    ])
    findings = scan_source("app.js", positive)
    assert len(findings) == 3
    assert all(f.kind == "hardcoded-secret" for f in findings)

    negative = "\n".join([
        "# api_key = \"changeme\"",  # comment line: skipped entirely
        'secret: "${SECRET_KEY}"',  # env-style interpolation, not a literal
        'const password = "changeme";',  # placeholder
        'const token = "short";',  # below length floor
    ])
    assert scan_source("app.py", negative) == []


def test_conformance_flags_hardcoded_secret_and_misplaced_external_call(tmp_path: Path):
    _write(tmp_path, "package.json", '{"dependencies":{"express":"^4"}}')
    _write(
        tmp_path, "src/controllers/payment_controller.js",
        'const password = "Sup3rSecretPass!";\n'
        "async function charge(){return fetch('https://api.example.com/charge');}\n"
        "module.exports={charge};",
    )
    _write(
        tmp_path, "src/services/notify_service.js",
        "async function notify(){return fetch('https://api.example.com/notify');}\n"
        "module.exports={notify};",
    )
    report = evaluate(build_model(tmp_path), _intent())

    secrets = next(r for r in report.results if r.rule_id == "no-hardcoded-secrets")
    assert {v.file for v in secrets.violations} == {"src/controllers/payment_controller.js"}

    calls = next(r for r in report.results if r.rule_id == "external-calls-only-in-service")
    files = {v.file for v in calls.violations}
    assert "src/controllers/payment_controller.js" in files
    # the service-layer call is allowed -> not flagged
    assert "src/services/notify_service.js" not in files


def test_ratchet_blocks_only_net_new(tmp_path: Path):
    _legacy_repo(tmp_path)
    intent = _intent()
    base_report = evaluate(build_model(tmp_path), intent)
    baseline = Baseline.from_report(base_report)
    # No net-new yet.
    assert baseline.net_new(base_report) == []
    # Add a brand-new raw-SQL service (a regression).
    _write(
        tmp_path, "src/services/report_service.js",
        "const {pool}=require('../db/client');\n"
        "module.exports={t:()=>pool.query('SELECT 1 FROM payments')};",
    )
    new_report = evaluate(build_model(tmp_path), intent)
    net_new = baseline.net_new(new_report)
    assert len(net_new) == 1
    assert net_new[0].file == "src/services/report_service.js"


def test_decision_detection_is_boundary_only_and_idempotent(tmp_path: Path):
    _legacy_repo(tmp_path)
    old = build_model(tmp_path)
    # Internal reshuffle (rename a service) must NOT trigger a decision.
    (tmp_path / "src/services/user_service.js").rename(tmp_path / "src/services/profile_service.js")
    # Boundary change: add a new third-party dependency.
    _write(tmp_path, "package.json", '{"dependencies":{"express":"^4","pg":"^8","stripe":"^15"}}')
    new = build_model(tmp_path)

    engine = DecisionEngine(tmp_path / "adr")
    drafts = engine.analyze_diff(old, new)
    titles = " ".join(d.title for d in drafts)
    assert "stripe" in titles
    assert "profile_service" not in titles  # internal move ignored
    written = engine.write_drafts(drafts)
    assert written
    # Re-running writes nothing new (idempotent).
    assert engine.write_drafts(engine.analyze_diff(old, new)) == []


def _multi_violation_repo(root: Path) -> None:
    _write(root, "package.json", '{"dependencies":{"express":"^4","pg":"^8"}}')
    _write(root, "src/db/client.js", "const {Pool}=require('pg');module.exports={pool:new Pool()};")
    for name in ("payment", "invoice", "refund"):
        _write(
            root, f"src/controllers/{name}_controller.js",
            "const {pool}=require('../db/client');\n"
            f"async function run(){{return pool.query('SELECT * FROM {name}s');}}\n"
            "module.exports={run};",
        )


def test_violation_pattern_drafted_when_widespread_and_idempotent(tmp_path: Path):
    _multi_violation_repo(tmp_path)
    intent = _intent()  # express_to_next: every shipped rule already has an ADR
    report = evaluate(build_model(tmp_path), intent)
    engine = DecisionEngine(tmp_path / "adr")
    drafts = engine.analyze_violation_patterns(report, intent)
    # Both no-raw-sql-outside-repository AND controller-no-direct-db-client trip
    # here (every controller imports src/db/client.js directly) — each is its
    # own decision, so both get drafted.
    by_rule = {d.title: d for d in drafts}
    draft = by_rule["Review rule 'no-raw-sql-outside-repository': widespread violations found"]
    assert "controller-no-direct-db-client" in " ".join(by_rule)
    assert "already documented in" in draft.context
    assert "payment_controller.js" in draft.context
    written = engine.write_drafts(drafts)
    assert written

    # Re-running with a different violation count must NOT duplicate the file —
    # the slug is derived from the rule id, not the count.
    _write(
        tmp_path, "src/controllers/extra_controller.js",
        "const {pool}=require('../db/client');\n"
        "async function run(){return pool.query('SELECT * FROM extras');}\n"
        "module.exports={run};",
    )
    report2 = evaluate(build_model(tmp_path), intent)
    drafts2 = engine.analyze_violation_patterns(report2, intent)
    draft2 = {d.title: d for d in drafts2}["Review rule 'no-raw-sql-outside-repository': widespread violations found"]
    assert draft2.context != draft.context  # count really did change ("extra" now included)
    assert engine.write_drafts(drafts2) == []  # but nothing new gets written to disk


def test_violation_pattern_below_threshold_is_silent(tmp_path: Path):
    _legacy_repo(tmp_path)  # only one file violates no-raw-sql-outside-repository
    report = evaluate(build_model(tmp_path), _intent())
    drafts = DecisionEngine(tmp_path / "adr").analyze_violation_patterns(report, _intent())
    assert drafts == []


def test_violation_pattern_without_backing_adr(tmp_path: Path):
    _multi_violation_repo(tmp_path)
    intent = _intent()
    for rule in intent.rules:
        rule.adr = None  # simulate a hand-written rule nobody has ratified yet
    report = evaluate(build_model(tmp_path), intent)
    drafts = DecisionEngine(tmp_path / "adr").analyze_violation_patterns(report, intent)
    assert drafts  # every rule.adr is None -> the "no backing ADR" branch for all of them
    for draft in drafts:
        assert "no backing ADR" in draft.context
        assert "already documented" not in draft.context


def test_violation_pattern_needs_intent_and_ignores_ungoverned_xray(tmp_path: Path):
    from archsteer.engine.conformance import ConformanceReport
    _multi_violation_repo(tmp_path)
    build_model(tmp_path)
    # X-ray mode: no declared intent -> empty report -> nothing to review.
    assert DecisionEngine(tmp_path / "adr").analyze_violation_patterns(ConformanceReport(), None) == []


def test_steer_is_idempotent(tmp_path: Path):
    _legacy_repo(tmp_path)
    model = build_model(tmp_path)
    engine = AgentSteeringEngine(tmp_path)
    payload = engine.synthesize(_intent(), model, files=["src/controllers/payment_controller.js"])
    target = tmp_path / "CLAUDE.md"
    engine._inject(target, payload)
    engine._inject(target, engine.synthesize(_intent(), model, files=["src/controllers/payment_controller.js"]))
    assert target.read_text().count(START_MARKER) == 1


def test_steer_writes_cursor_mdc_with_frontmatter(tmp_path: Path):
    # Real repos already have .cursor/rules as a directory (Cursor's own
    # convention) — writing must target a file inside it, not the dir itself.
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    _legacy_repo(tmp_path)
    model = build_model(tmp_path)
    engine = AgentSteeringEngine(tmp_path)
    payload = engine.synthesize(_intent(), model, files=["src/controllers/payment_controller.js"])
    written = engine.write(payload)
    mdc = tmp_path / ".cursor" / "rules" / "archsteer.mdc"
    assert mdc in written
    text = mdc.read_text()
    assert text.startswith("---\nalwaysApply: true\n---")
    assert START_MARKER in text
    # Idempotent: re-running doesn't duplicate the marker or the frontmatter.
    engine.write(engine.synthesize(_intent(), model, files=["src/controllers/payment_controller.js"]))
    text2 = mdc.read_text()
    assert text2.count(START_MARKER) == 1
    assert text2.count("alwaysApply: true") == 1


def test_docs_render_is_deterministic(tmp_path: Path):
    _legacy_repo(tmp_path)
    model = build_model(tmp_path)
    assert render_architecture_md(model) == render_architecture_md(model)


def test_first_map_has_no_decisions(tmp_path: Path):
    _legacy_repo(tmp_path)
    model = build_model(tmp_path)
    assert DecisionEngine(tmp_path / "adr").analyze_diff(None, model) == []


def test_history_dedupes_identical_snapshots(tmp_path: Path):
    from archsteer.engine.evolution import History

    _legacy_repo(tmp_path)
    hist = History(tmp_path / "history")
    _, rec1 = hist.record(build_model(tmp_path))
    _, rec2 = hist.record(build_model(tmp_path))  # unchanged structure
    assert rec1 is True and rec2 is False
    assert len(hist.metas()) == 1


def test_evolution_feed_reports_new_dependency(tmp_path: Path):
    from archsteer.engine.evolution import compute_feed

    _legacy_repo(tmp_path)
    old = build_model(tmp_path)
    _write(tmp_path, "package.json", '{"dependencies":{"express":"^4","pg":"^8","stripe":"^15"}}')
    new = build_model(tmp_path)
    feed = compute_feed(old, new)
    kinds = {c.kind for c in feed.changes}
    assert "dependency_added" in kinds
    assert any("stripe" in c.text for c in feed.changes)


def test_first_feed_is_baseline(tmp_path: Path):
    from archsteer.engine.evolution import compute_feed

    _legacy_repo(tmp_path)
    feed = compute_feed(None, build_model(tmp_path))
    assert feed.is_first and feed.changes == []


def test_python_import_resolution(tmp_path: Path) -> None:
    """Relative and same-package absolute Python imports resolve to internal edges."""
    _write(tmp_path, "mypkg/__init__.py", "")
    _write(tmp_path, "mypkg/routing.py", "from .utils import helper\nclass Router: pass\n")
    _write(tmp_path, "mypkg/utils.py", "import json\ndef helper(): pass\n")
    _write(tmp_path, "mypkg/sub/deep.py", "from ..routing import Router\nfrom mypkg import utils\n")
    model = build_model(tmp_path)
    edges = set(model.internal_edges())
    assert ("mypkg/routing.py", "mypkg/utils.py") in edges          # relative .utils
    assert ("mypkg/sub/deep.py", "mypkg/routing.py") in edges       # relative ..routing
    assert ("mypkg/sub/deep.py", "mypkg/utils.py") in edges         # absolute mypkg.utils
    # stdlib import stays external
    utils = model.components["mypkg/utils.py"]
    assert all(d.external for d in utils.dependencies if d.target == "json")


def test_ts_path_alias_resolves_to_internal_edge(tmp_path: Path) -> None:
    """`@/lib/x`-style imports are the default alias in every create-next-app
    project; without tsconfig-aware resolution they're indistinguishable from
    a third-party package and the whole diagram comes out edge-less."""
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}')
    _write(tmp_path, "app/dashboard/page.tsx", 'import { listRepos } from "@/lib/store";\n')
    _write(tmp_path, "lib/store.ts", "export function listRepos(){ return []; }\n")
    model = build_model(tmp_path)
    assert ("app/dashboard/page.tsx", "lib/store.ts") in set(model.internal_edges())
    # a genuine third-party package must NOT be swept up by the alias probe
    dep = model.components["app/dashboard/page.tsx"]
    assert all(d.target != "react" or d.external for d in dep.dependencies)


def test_ts_path_alias_tolerates_jsonc_comments_and_missing_tsconfig(tmp_path: Path) -> None:
    _write(
        tmp_path, "tsconfig.json",
        "{\n"
        "  // path aliases\n"
        '  "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["./*"] } } /* trailing */\n'
        "}\n",
    )
    _write(tmp_path, "app/page.tsx", 'import { x } from "@/lib/util";\n')
    _write(tmp_path, "lib/util.ts", "export const x = 1;\n")
    model = build_model(tmp_path)
    assert ("app/page.tsx", "lib/util.ts") in set(model.internal_edges())

    # No tsconfig at all: an @/ import stays external, no crash.
    (tmp_path / "tsconfig.json").unlink()
    model2 = build_model(tmp_path)
    dep = model2.components["app/page.tsx"].dependencies[0]
    assert dep.external and dep.target == "@/lib/util"


def test_python_resolution_at_package_root(tmp_path: Path) -> None:
    """X-raying the package dir itself resolves pkg-prefixed absolute imports."""
    pkg = tmp_path / "mypkg"
    _write(tmp_path, "mypkg/__init__.py", "")
    _write(tmp_path, "mypkg/a.py", "from mypkg.b import thing\n")
    _write(tmp_path, "mypkg/b.py", "def thing(): pass\n")
    model = build_model(pkg)
    assert ("a.py", "b.py") in set(model.internal_edges())


def test_manifest_deps_runtime_only(tmp_path: Path) -> None:
    """Manifest reader takes runtime deps only, not dev tooling or stray strings."""
    _write(tmp_path, "package.json",
           '{"dependencies":{"express":"^4"},"devDependencies":{"eslint":"^9"},"peerDependencies":{"react":"^19"}}')
    _write(tmp_path, "pyproject.toml",
           '[project]\nname = "x"\nlicense = "BSD-3-Clause"\n'
           'dependencies = ["pydantic>=2.7.0", "rich>=13"]\n'
           '[project.optional-dependencies]\ndev = ["pytest>=8"]\n'
           '[tool.pytest.ini_options]\naddopts = "--strict-markers"\n')
    deps = set(build_model(tmp_path).manifest_dependencies)
    assert {"express", "react", "pydantic", "rich"} <= deps
    assert not {"eslint", "pytest", "BSD-3-Clause", "--strict-markers"} & deps


def test_build_model_cache_skips_unchanged_files(tmp_path: Path, monkeypatch) -> None:
    _legacy_repo(tmp_path)
    cache_path = tmp_path / ".archsteer" / "parse_cache.json"
    build_model(tmp_path, cache_path=cache_path)
    assert cache_path.exists()

    from archsteer.engine import parser as parser_module

    calls: list[Path] = []
    original = parser_module.CodeParserFacade.parse_file

    def spy(self, path, root):  # noqa: ANN001
        calls.append(Path(path).resolve())
        return original(self, path, root)

    monkeypatch.setattr(parser_module.CodeParserFacade, "parse_file", spy)

    # Nothing changed since the first build -> every file is a cache hit.
    model = build_model(tmp_path, cache_path=cache_path)
    assert calls == []
    assert "src/controllers/payment_controller.js" in model.components

    # Touch exactly one file -> only that file gets re-parsed.
    _write(
        tmp_path, "src/services/user_service.js",
        "const {find}=require('../repositories/user_repository');\n"
        "module.exports={get:(id)=>find(id),extra:true};",
    )
    calls.clear()
    build_model(tmp_path, cache_path=cache_path)
    assert calls == [(tmp_path / "src/services/user_service.js").resolve()]


def test_check_prints_conformance_score_line(tmp_path: Path) -> None:
    _legacy_repo(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--path", str(tmp_path)]).exit_code == 0
    result = runner.invoke(app, ["check", "--path", str(tmp_path)])
    assert "Architecture conformance:" in result.output


def test_install_hooks_writes_blocks_foreign_and_uninstalls(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    runner = CliRunner()
    hook = tmp_path / ".git" / "hooks" / "pre-push"

    result = runner.invoke(app, ["install-hooks", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "archsteer check" in hook.read_text(encoding="utf-8")
    assert hook.stat().st_mode & 0o111  # executable

    # A foreign hook is never silently clobbered.
    hook.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    blocked = runner.invoke(app, ["install-hooks", "--path", str(tmp_path)])
    assert blocked.exit_code != 0
    assert "echo custom" in hook.read_text(encoding="utf-8")

    forced = runner.invoke(app, ["install-hooks", "--path", str(tmp_path), "--force"])
    assert forced.exit_code == 0
    assert "archsteer check" in hook.read_text(encoding="utf-8")

    uninstalled = runner.invoke(app, ["install-hooks", "--path", str(tmp_path), "--uninstall"])
    assert uninstalled.exit_code == 0
    assert not hook.exists()


NEXTJS_PACK = Path(__file__).resolve().parent.parent / "archsteer" / "packs" / "nextjs_app_router"


def _nextjs_intent() -> Intent:
    return Intent.load(NEXTJS_PACK / "architecture.yaml")


def test_fetch_same_origin_excluded_absolute_url_gets_host_label(tmp_path: Path) -> None:
    _write(
        tmp_path, "web/components/ContactForm.tsx",
        "async function submit(){ return fetch('/api/contact', {method:'POST'}); }\n"
        "async function track(){ return fetch('https://analytics.example.com/collect'); }\n"
        "async function unknown(url){ return fetch(url); }\n",
    )
    model = build_model(tmp_path)
    comp = model.components["web/components/ContactForm.tsx"]
    destinations = [e.destination for e in comp.external_calls]
    # same-origin /api/contact is excluded entirely -> only 2 of the 3 fetches remain
    assert len(comp.external_calls) == 2
    assert "analytics.example.com" in destinations
    assert "HTTP_CLIENT_CALL" in destinations  # non-literal fetch(url): kept, conservative


def test_supabase_query_detected_single_and_multiline_chain(tmp_path: Path) -> None:
    _write(
        tmp_path, "web/lib/store.ts",
        'export async function listRepos(){ return sb.from("repo_snapshots").select("*"); }\n'
        'export async function saveScore(v: number){\n'
        '  return sb\n'
        '    .from("repo_snapshots")\n'
        '    .insert({ v });\n'
        '}\n',
    )
    model = build_model(tmp_path)
    comp = model.components["web/lib/store.ts"]
    assert {d.entity for d in comp.data_access} == {"repo_snapshots"}
    ops = {op for d in comp.data_access for op in d.operations}
    assert {"READ", "WRITE"} <= ops


def test_app_router_reserved_filenames_get_layers_regardless_of_directory() -> None:
    from archsteer.engine.mapper import _infer_layer
    assert _infer_layer("app/blog/[slug]/page.tsx") == "page"
    assert _infer_layer("app/layout.tsx") == "layout"
    assert _infer_layer("app/api/contact/route.ts") == "api"
    assert _infer_layer("app/webhooks/stripe/route.ts") == "api"  # route.ts outside any "api" dir


def test_nextjs_app_router_pack_autodetected_over_express_migration_pack(tmp_path: Path) -> None:
    from archsteer.cli import _detect_pack
    _write(tmp_path, "package.json", '{"dependencies":{"next":"15.0.0","react":"19.0.0"}}')
    _write(tmp_path, "app/page.tsx", "export default function Home(){ return null; }\n")
    assert _detect_pack(tmp_path) == "nextjs-app-router"

    # An Express repo migrating TO Next still gets the migration pack, not the
    # app-router pack, even once "next" appears as a dependency.
    _write(tmp_path, "package.json", '{"dependencies":{"express":"^4","next":"15.0.0"}}')
    assert _detect_pack(tmp_path) == "express-to-next"


def test_nextjs_pack_flags_data_access_and_external_calls_outside_lib(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"dependencies":{"next":"15.0.0"}}')
    _write(
        tmp_path, "app/dashboard/page.tsx",
        'export default async function Dashboard(){\n'
        '  const rows = await sb.from("repo_snapshots").select("*");\n'
        '  const r = await fetch("https://api.stripe.com/v1/charges");\n'
        '  return null;\n'
        '}\n',
    )
    _write(
        tmp_path, "lib/store.ts",
        'export async function listRepos(){ return sb.from("repo_snapshots").select("*"); }\n',
    )
    report = evaluate(build_model(tmp_path), _nextjs_intent())

    da = next(r for r in report.results if r.rule_id == "no-data-access-outside-lib")
    assert {v.file for v in da.violations} == {"app/dashboard/page.tsx"}

    ext = next(r for r in report.results if r.rule_id == "external-calls-only-in-lib")
    assert {v.file for v in ext.violations} == {"app/dashboard/page.tsx"}


def test_agent_worktree_dirs_are_not_scanned(tmp_path: Path) -> None:
    """A subagent's git worktree under .claude/ is a full duplicate checkout of
    the repo — scanning it double-counts every component. Regression guard:
    tooling dirs that can hold duplicate checkouts must be skipped entirely."""
    _legacy_repo(tmp_path)  # the real repo
    # A phantom duplicate copy sitting inside .claude/worktrees/<id>/
    _write(
        tmp_path, ".claude/worktrees/abc123/src/controllers/payment_controller.js",
        "async function charge(){ return db.query('INSERT INTO payments (a) VALUES (1)'); }\n",
    )
    _write(tmp_path, ".cursor/rules/whatever.js", "const x = require('pg');\n")
    model = build_model(tmp_path)
    assert not any(p.startswith(".claude/") for p in model.components)
    assert not any(p.startswith(".cursor/") for p in model.components)
    # the real controller is still there
    assert "src/controllers/payment_controller.js" in model.components
