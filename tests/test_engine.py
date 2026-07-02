"""End-to-end tests over the ArchSteer engine using throwaway repos."""

from __future__ import annotations

from pathlib import Path

from archsteer.docs import render_architecture_md
from archsteer.engine.baseline import Baseline
from archsteer.engine.conformance import evaluate
from archsteer.engine.decisions import DecisionEngine
from archsteer.engine.intent import Intent
from archsteer.engine.mapper import build_model
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


def test_conformance_flags_raw_sql_outside_repository(tmp_path: Path):
    _legacy_repo(tmp_path)
    report = evaluate(build_model(tmp_path), _intent())
    raw = next(r for r in report.results if r.rule_id == "no-raw-sql-outside-repository")
    files = {v.file for v in raw.violations}
    assert "src/controllers/payment_controller.js" in files
    # repository raw SQL is allowed -> not flagged
    assert "src/repositories/user_repository.js" not in files
    assert 0 < report.conformance_score < 100


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


def test_steer_is_idempotent(tmp_path: Path):
    _legacy_repo(tmp_path)
    model = build_model(tmp_path)
    engine = AgentSteeringEngine(tmp_path)
    payload = engine.synthesize(_intent(), model, files=["src/controllers/payment_controller.js"])
    target = tmp_path / "CLAUDE.md"
    engine._inject(target, payload)
    engine._inject(target, engine.synthesize(_intent(), model, files=["src/controllers/payment_controller.js"]))
    assert target.read_text().count(START_MARKER) == 1


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
