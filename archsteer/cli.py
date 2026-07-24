"""ArchSteer CLI — every command is a projection of the one shared model.

    archsteer init     scaffold .archsteer/ + seed intent
    archsteer map      build model.json from source
    archsteer docs     regenerate living architecture.md
    archsteer adr      detect structural decisions -> draft ADRs
    archsteer govern   show conformance / drift
    archsteer baseline snapshot accepted violations (the ratchet)
    archsteer check    fail on NET-NEW violations only (CI / pre-commit)
    archsteer install-hooks   wire the same check into a local git pre-push hook
    archsteer steer    write agent guardrails into CLAUDE.md / AGENTS.md
    archsteer mcp      local MCP server: agents query the model + intent mid-edit
    archsteer report   build the self-contained report.html
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from archsteer import __version__
from archsteer.docs import render_architecture_md
from archsteer.engine.baseline import Baseline
from archsteer.engine.conformance import ConformanceReport, evaluate
from archsteer.engine.decisions import DecisionEngine, DraftADR
from archsteer.engine.evolution import History, compute_feed
from archsteer.engine.intent import Intent
from archsteer.engine.mapper import build_model
from archsteer.engine.model import ArchitectureModel
from archsteer.report import render_report_html
from archsteer.steer import AgentSteeringEngine
from archsteer.workspace import Workspace

app = typer.Typer(add_completion=False, help="ArchSteer — Living Architecture Control Plane.")
console = Console()
PACKS_DIR = Path(__file__).parent / "packs"
PACK_DIR = PACKS_DIR / "express_to_next"  # kept for backward compat

# Starter packs: pack name -> (one-line label shown at init).
PACKS = {
    "java-spring": "Layered Spring (controller → service → repository → model)",
    "salesforce": "Salesforce enterprise patterns (logic-less triggers, SOQL in selectors)",
    "python-service": "Layered Python service (api → service → repository)",
    "nextjs-app-router": "Next.js App Router (page/layout/api/lib) — data access + secrets baseline",
    "express-to-next": "Express → Next.js migration + repository pattern",
}


def _package_deps(root: Path) -> dict:
    try:
        return json.loads((root / "package.json").read_text(encoding="utf-8")).get("dependencies", {}) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _detect_pack(root: Path) -> str:
    """Pick a starter pack from the repo's build manifests. Order matters:
    Salesforce first (an sfdx repo may also carry a package.json for tooling)."""
    if (root / "sfdx-project.json").exists() or (root / "force-app").is_dir():
        return "salesforce"
    if any((root / f).exists() for f in ("pom.xml", "build.gradle", "build.gradle.kts")):
        return "java-spring"
    if any((root / f).exists() for f in ("pyproject.toml", "setup.py", "requirements.txt")):
        return "python-service"
    if (root / "package.json").exists():
        deps = _package_deps(root)
        # A real App Router app, not a legacy Express repo migrating TO Next —
        # the latter gets the migration-flavored pack instead.
        if "next" in deps and "express" not in deps and (root / "app").is_dir():
            return "nextjs-app-router"
    return "express-to-next"


def _pack_dir(name: str) -> Path:
    return PACKS_DIR / name.replace("-", "_")


def _ws(path: Optional[str]) -> Workspace:
    return Workspace(Path(path or "."))


def _require_init(ws: Workspace) -> None:
    if not ws.initialized:
        console.print("[red]Not initialized.[/red] Run [bold]archsteer init[/bold] first.")
        raise typer.Exit(1)


def _load_model(ws: Workspace) -> ArchitectureModel:
    model = ArchitectureModel.load_if_exists(ws.model)
    if model is None:
        console.print("[yellow]No model found — run [bold]archsteer map[/bold] first.[/yellow]")
        raise typer.Exit(1)
    return model


def _conformance(ws: Workspace, model: ArchitectureModel) -> ConformanceReport:
    """Evaluate intent if present; otherwise return an empty report (X-ray mode)."""
    intent = Intent.load_if_exists(ws.intent)
    if intent is None:
        return ConformanceReport()
    return evaluate(model, intent)


def _all_drafts(
    ws: Workspace, model: ArchitectureModel, report: ConformanceReport
) -> List[DraftADR]:
    """Every draft ADR source: structural change since the last snapshot, plus
    any rule violated across several components in the current one."""
    prev = ArchitectureModel.load_if_exists(ws.model_prev)
    intent = Intent.load_if_exists(ws.intent)
    engine = DecisionEngine(ws.adr_dir)
    return engine.analyze_diff(prev, model) + engine.analyze_violation_patterns(report, intent)


def _record_snapshot(ws: Workspace, model: ArchitectureModel) -> None:
    conf = _conformance(ws, model)
    has_intent = ws.intent.exists()
    History(ws.history_dir).record(
        model,
        conformance_score=conf.conformance_score if has_intent else None,
        drift_score=conf.drift_score if has_intent else None,
        open_violations=len(conf.all_violations) if has_intent else None,
    )


@app.command()
def version() -> None:
    """Print the ArchSteer version."""
    console.print(f"ArchSteer {__version__}")


@app.command()
def init(
    path: Optional[str] = typer.Option(None, help="Repo root (default: cwd)."),
    pack: Optional[str] = typer.Option(
        None, help=f"Starter pack: {', '.join(PACKS)}. Auto-detected from the repo when omitted."
    ),
) -> None:
    """Scaffold .archsteer/ and seed a starter intent + ADRs matched to your stack."""
    ws = _ws(path)
    if ws.initialized:
        console.print(f"[yellow]Already initialized at {ws.dir}[/yellow]")
        raise typer.Exit(0)
    if pack is not None and pack not in PACKS:
        console.print(f"[red]Unknown pack '{pack}'.[/red] Available: {', '.join(PACKS)}")
        raise typer.Exit(1)
    chosen = pack or _detect_pack(ws.root)
    pack_dir = _pack_dir(chosen)
    ws.dir.mkdir(parents=True, exist_ok=True)
    ws.adr_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pack_dir / "architecture.yaml", ws.intent)
    for adr in (pack_dir / "adr").glob("*.md"):
        shutil.copyfile(adr, ws.adr_dir / adr.name)
    console.print(f"[green]✓[/green] Initialized [bold]{ws.dir}[/bold]")
    detected = " (auto-detected)" if pack is None else ""
    console.print(f"  • starter pack: [cyan]{chosen}[/cyan]{detected} — {PACKS[chosen]}")
    console.print("  • seeded [cyan]architecture.yaml[/cyan] — baseline rules, edit to match your conventions")
    console.print("  • seeded baseline ADRs in [cyan].archsteer/adr/[/cyan]")
    if pack is None:
        console.print(f"  [dim]Wrong guess? Re-run with --pack <name> ({', '.join(PACKS)})[/dim]")
    console.print("\nNext: [bold]archsteer map[/bold] then [bold]archsteer report[/bold]")


@app.command()
def map(path: Optional[str] = typer.Option(None, help="Repo root (default: cwd).")) -> None:
    """Build the architecture model (.archsteer/model.json) from source."""
    ws = _ws(path)
    _require_init(ws)
    if ws.model.exists():
        shutil.copyfile(ws.model, ws.model_prev)  # keep prior snapshot for `adr`
    model = build_model(ws.root, cache_path=ws.parse_cache)
    model.save(ws.model)
    _record_snapshot(ws, model)
    console.print(
        f"[green]✓[/green] Mapped [bold]{len(model.components)}[/bold] components · "
        f"layers: {', '.join(sorted(model.get_layers())) or '—'} · "
        f"data stores: {', '.join(sorted(model.get_all_data_stores())) or '—'}"
    )


@app.command()
def docs(path: Optional[str] = typer.Option(None)) -> None:
    """Regenerate living architecture docs (deterministic)."""
    ws = _ws(path)
    _require_init(ws)
    model = _load_model(ws)
    ws.architecture_md.write_text(render_architecture_md(model), encoding="utf-8")
    console.print(f"[green]✓[/green] Wrote [bold]{ws.architecture_md}[/bold]")


@app.command()
def adr(path: Optional[str] = typer.Option(None)) -> None:
    """Detect structural decisions + widespread rule violations and draft ADRs."""
    ws = _ws(path)
    _require_init(ws)
    model = _load_model(ws)
    report = _conformance(ws, model)
    drafts = _all_drafts(ws, model, report)
    written = DecisionEngine(ws.adr_dir).write_drafts(drafts)
    if not written:
        console.print("[green]✓[/green] No new architectural decisions to record.")
        return
    console.print(f"[yellow]📝 {len(written)} draft ADR(s) need architect review:[/yellow]")
    for p in written:
        console.print(f"  • {p.relative_to(ws.root)}")


@app.command()
def govern(path: Optional[str] = typer.Option(None)) -> None:
    """Show conformance and drift against declared intent."""
    ws = _ws(path)
    _require_init(ws)
    model = _load_model(ws)
    report = _conformance(ws, model)
    table = Table(title=f"Conformance — {report.target or ws.root.name}")
    table.add_column("Rule"); table.add_column("Sev"); table.add_column("Progress", justify="right")
    table.add_column("Open", justify="right")
    for r in report.results:
        table.add_row(r.rule_id, r.severity, f"{r.progress}%", str(len(r.violations)))
    console.print(table)
    console.print(
        f"Overall conformance: [bold]{report.conformance_score}%[/bold] · "
        f"drift: [bold]{report.drift_score}%[/bold]"
    )


@app.command()
def baseline(path: Optional[str] = typer.Option(None)) -> None:
    """Snapshot current violations as accepted debt (the ratchet)."""
    ws = _ws(path)
    _require_init(ws)
    model = _load_model(ws)
    report = _conformance(ws, model)
    bl = Baseline.from_report(report)
    bl.save(ws.baseline)
    console.print(f"[green]✓[/green] Baselined [bold]{len(bl.fingerprints)}[/bold] existing violation(s).")
    console.print("New violations will now be blocked by [bold]archsteer check[/bold].")


def _score_line(ws: Workspace, report: ConformanceReport, net_new_count: int) -> str:
    """The one-line quality-score readout `check` prints — the whole point of
    running it before a push: a number you see every time, not just on failure."""
    metas = History(ws.history_dir).metas()
    prev = metas[-1].conformance_score if metas else None
    delta = ""
    if prev is not None:
        d = round(report.conformance_score - prev, 1)
        delta = f" ({'+' if d >= 0 else ''}{d} pts)"
    return (
        f"\n[bold]Architecture conformance: {report.conformance_score}%{delta}[/bold] · "
        f"{len(report.all_violations)} open violation(s), {net_new_count} net-new"
    )


@app.command()
def check(
    path: Optional[str] = typer.Option(None),
    remap: bool = typer.Option(True, help="Rebuild the model before checking."),
) -> None:
    """Fail (exit 1) on NET-NEW violations only — for CI / pre-commit."""
    ws = _ws(path)
    _require_init(ws)
    if remap:
        model = build_model(ws.root, cache_path=ws.parse_cache)
        model.save(ws.model)
    else:
        model = _load_model(ws)
    report = _conformance(ws, model)
    bl = Baseline.load_if_exists(ws.baseline)
    if bl is None:
        console.print("[yellow]No baseline — treating ALL violations as net-new.[/yellow]")
        net_new = report.all_violations
        fixed = 0
    else:
        net_new = bl.net_new(report)
        fixed = bl.fixed(report)
    if fixed:
        console.print(f"[green]↑ {fixed} baselined violation(s) resolved — nice.[/green]")
    blocking = [v for v in net_new if v.severity == "error"]
    for v in net_new:
        tag = "[red]✗[/red]" if v.severity == "error" else "[yellow]△[/yellow]"
        console.print(f"{tag} {v.file}:{v.loc} [dim]{v.rule_id}[/dim] — {v.message}")
    if ws.intent.exists():
        console.print(_score_line(ws, report, len(net_new)))
    if blocking:
        console.print(f"\n[red]✗ {len(blocking)} net-new error violation(s) block this change.[/red]")
        raise typer.Exit(1)
    console.print("\n[green]✓ No net-new blocking violations.[/green]")


_HOOK_MARKER = "# archsteer:pre-push"
_HOOK_SCRIPT = f"""#!/bin/sh
{_HOOK_MARKER}
# Installed by `archsteer install-hooks`. Shows the architecture conformance
# score before every push and blocks only NET-NEW error violations — the same
# ratchet `archsteer check` already applies in CI. If CI would reject this
# push, this just says so before you wait for CI to.
if ! command -v archsteer >/dev/null 2>&1; then
  exit 0
fi
archsteer check
"""


@app.command(name="install-hooks")
def install_hooks(
    path: Optional[str] = typer.Option(None),
    uninstall: bool = typer.Option(False, help="Remove a previously installed archsteer pre-push hook."),
    force: bool = typer.Option(False, help="Overwrite an existing pre-push hook not written by archsteer."),
) -> None:
    """Wire `archsteer check` into a local git pre-push hook — a quality score on every push."""
    ws = _ws(path)
    hook_path = ws.root / ".git" / "hooks" / "pre-push"

    if uninstall:
        if hook_path.exists() and _HOOK_MARKER in hook_path.read_text(encoding="utf-8"):
            hook_path.unlink()
            console.print(f"[green]✓[/green] Removed [bold]{hook_path}[/bold]")
        else:
            console.print("[yellow]No archsteer pre-push hook found.[/yellow]")
        return

    if not (ws.root / ".git").is_dir():
        console.print(f"[red]Not a git repository[/red] (no .git directory) at {ws.root}")
        raise typer.Exit(1)
    if hook_path.exists() and _HOOK_MARKER not in hook_path.read_text(encoding="utf-8") and not force:
        console.print(
            f"[red]A pre-push hook already exists at {hook_path} and wasn't written by archsteer.[/red]\n"
            "Re-run with --force to overwrite it, or add `archsteer check` to it by hand."
        )
        raise typer.Exit(1)

    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(_HOOK_SCRIPT, encoding="utf-8")
    hook_path.chmod(0o755)
    console.print(f"[green]✓[/green] Installed a pre-push hook at [bold]{hook_path}[/bold]")
    console.print("  Every push now runs [bold]archsteer check[/bold] first and prints the conformance score.")
    console.print("  Already using husky / pre-commit / lefthook? See the README to wire `archsteer check` into that instead.")


@app.command()
def steer(
    path: Optional[str] = typer.Option(None),
    files: Optional[List[str]] = typer.Option(None, "--files", "-f", help="Files in scope."),
    task: Optional[str] = typer.Option(None, "--task", "-t", help="What the agent is about to do."),
    targets: Optional[List[str]] = typer.Option(None, "--target", help="Agent files to write."),
) -> None:
    """Inject sharp, model-grounded guardrails into agent context files."""
    ws = _ws(path)
    _require_init(ws)
    model = _load_model(ws)
    intent = Intent.load(ws.intent)
    engine = AgentSteeringEngine(ws.root)
    payload = engine.synthesize(intent, model, files=files, task=task)
    written = engine.write(payload, targets=targets)
    console.print(f"[green]✓[/green] Steered: {', '.join(str(p.relative_to(ws.root)) for p in written) or '(no targets)'}")


@app.command(name="mcp")
def mcp_cmd(
    path: Optional[str] = typer.Option(None, help="Repo root (default: cwd)."),
) -> None:
    """Start a local MCP server so agents query the model + intent mid-edit."""
    ws = _ws(path)
    _require_init(ws)
    _load_model(ws)
    try:
        from archsteer.mcp_server import run as run_mcp
    except ImportError:
        console.print(
            "[red]The MCP server needs an extra dependency.[/red] Install it with:\n"
            "  [bold]pipx inject archsteer mcp[/bold]        (if installed via pipx)\n"
            '  [bold]pip install "archsteer[mcp]"[/bold]  (if installed via pip)'
        )
        raise typer.Exit(1)
    # stdout is reserved entirely for the MCP JSON-RPC stream once run_mcp()
    # starts — any human-readable output must go to stderr, never stdout.
    Console(stderr=True).print(f"[green]✓[/green] ArchSteer MCP server starting for [bold]{ws.root}[/bold] (stdio)…")
    run_mcp(str(ws.root))


def _render_report(ws: Workspace, model: ArchitectureModel) -> None:
    conf = _conformance(ws, model)
    governed = ws.intent.exists()
    pending = [d.title for d in _all_drafts(ws, model, conf)]
    bl = Baseline.load_if_exists(ws.baseline)
    fixed = bl.fixed(conf) if bl else 0
    hist = History(ws.history_dir)
    metas = hist.metas()
    old_meta, new_meta = hist.latest_two()
    old_model = hist.load_model(old_meta) if old_meta else None
    feed = compute_feed(old_model, model, old_meta, new_meta)
    ws.report_html.write_text(
        render_report_html(
            model, conf, pending, fixed_count=fixed,
            feed=feed, history=metas, governed=governed,
        ),
        encoding="utf-8",
    )


@app.command()
def report(path: Optional[str] = typer.Option(None)) -> None:
    """Build the self-contained report.html (map + evolution + conformance + decisions)."""
    ws = _ws(path)
    _require_init(ws)
    _render_report(ws, _load_model(ws))
    console.print(f"[green]✓[/green] Wrote [bold]{ws.report_html}[/bold] — open it in a browser.")


@app.command()
def xray(path: Optional[str] = typer.Option(None, help="Repo root (default: cwd).")) -> None:
    """Zero-config read-only X-ray: map + docs + evolution + report, no intent needed.

    The universal wedge — point it at ANY repo and instantly see what the
    architecture is and how it changed, without declaring any rules.
    """
    ws = _ws(path)
    ws.dir.mkdir(parents=True, exist_ok=True)
    if ws.model.exists():
        shutil.copyfile(ws.model, ws.model_prev)
    model = build_model(ws.root, cache_path=ws.parse_cache)
    model.save(ws.model)
    _record_snapshot(ws, model)
    ws.architecture_md.write_text(render_architecture_md(model), encoding="utf-8")
    # Draft ADRs for structural change since last snapshot, and for any rule
    # violated across several components right now (architect-in-the-loop).
    DecisionEngine(ws.adr_dir).write_drafts(_all_drafts(ws, model, _conformance(ws, model)))
    _render_report(ws, model)
    old_meta, new_meta = History(ws.history_dir).latest_two()
    feed = compute_feed(
        History(ws.history_dir).load_model(old_meta) if old_meta else None,
        model, old_meta, new_meta,
    )
    console.print(
        f"[green]✓[/green] X-ray of [bold]{ws.root.name}[/bold]: "
        f"{len(model.components)} components, {len(model.get_layers())} layers."
    )
    console.print(f"  {feed.summary()}")
    console.print(f"  → [bold]{ws.architecture_md.relative_to(ws.root)}[/bold] and "
                  f"[bold]{ws.report_html.relative_to(ws.root)}[/bold] (open in a browser)")


def _detect_repo_url(root: Path) -> Optional[str]:
    """Best-effort public URL of the repo, from `git remote origin`.

    Normalizes SSH/HTTPS git remotes to a browsable https URL so the situation
    room can deep-link to the committed living docs. Returns None if unavailable.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5,
        )
        remote = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return None
    if not remote:
        return None
    # git@host:org/repo(.git) -> https://host/org/repo
    m = re.match(r"^[\w.+-]+@([^:]+):(.+?)(?:\.git)?/?$", remote)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    # ssh://git@host/org/repo(.git) or https://host/org/repo(.git)
    m = re.match(r"^(?:ssh://)?(?:[\w.+-]+@)?(?:https?://)?([^/]+)/(.+?)(?:\.git)?/?$", remote)
    if m and "." in m.group(1):
        return f"https://{m.group(1)}/{m.group(2)}"
    return None


@app.command()
def push(
    path: Optional[str] = typer.Option(None),
    url: Optional[str] = typer.Option(None, envvar="ARCHSTEER_URL", help="Ingest endpoint."),
    token: Optional[str] = typer.Option(None, envvar="ARCHSTEER_TOKEN", help="Org API token."),
    org: Optional[str] = typer.Option(None, envvar="ARCHSTEER_ORG", help="Organization slug."),
    repo_url: Optional[str] = typer.Option(
        None, envvar="ARCHSTEER_REPO_URL",
        help="Public repo URL for docs deep-links (auto-detected from git remote).",
    ),
) -> None:
    """Push the latest snapshot + conformance to the cloud situation room."""
    ws = _ws(path)
    _require_init(ws)
    model = _load_model(ws)
    conf = _conformance(ws, model)
    governed = ws.intent.exists()
    pending = len(_all_drafts(ws, model, conf))
    hist = History(ws.history_dir)
    old_meta, new_meta = hist.latest_two()
    feed = compute_feed(hist.load_model(old_meta) if old_meta else None, model, old_meta, new_meta)

    payload = {
        "repo": model.repo_name,
        "org": org,
        "repo_url": repo_url or _detect_repo_url(ws.root),
        "commit": model.commit_sha,
        "timestamp": model.timestamp,
        "components": len(model.components),
        "layers": sorted(model.get_layers()),
        "external_dependencies": len(model.get_all_external_dependencies()),
        "data_stores": len([s for s in model.get_all_data_stores() if s != "raw_sql"]),
        "conformance_score": conf.conformance_score if governed else None,
        "drift_score": conf.drift_score if governed else None,
        "open_violations": len(conf.all_violations) if governed else None,
        "pending_decisions": pending,
        "changes": [c.model_dump() for c in feed.changes],
    }
    endpoint = url or "https://www.archsteer.com/api/ingest"
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        console.print(
            f"[green]✓[/green] Pushed [bold]{model.repo_name}[/bold] → situation room "
            f"({endpoint}) · {body.get('snapshots', '?')} snapshot(s)."
        )
    except urllib.error.HTTPError as e:
        console.print(f"[red]✗ Push failed ({e.code}): {e.reason}[/red]")
        raise typer.Exit(1)
    except urllib.error.URLError as e:
        console.print(f"[red]✗ Could not reach {endpoint}: {e.reason}[/red]")
        raise typer.Exit(1)


@app.command()
def evolution(
    path: Optional[str] = typer.Option(None),
    limit: int = typer.Option(15, help="Max changes to show."),
) -> None:
    """Show the Architecture Evolution Feed between the two latest snapshots."""
    ws = _ws(path)
    hist = History(ws.history_dir)
    old_meta, new_meta = hist.latest_two()
    if new_meta is None:
        console.print("[yellow]No history yet — run [bold]archsteer map[/bold] or [bold]xray[/bold] first.[/yellow]")
        raise typer.Exit(1)
    old_model = hist.load_model(old_meta) if old_meta else None
    new_model = hist.load_model(new_meta)
    feed = compute_feed(old_model, new_model, old_meta, new_meta)
    console.print(f"[bold]Architecture Evolution[/bold] — {feed.summary()}")
    if feed.drift_delta is not None:
        arrow = "↓ improved" if feed.drift_delta < 0 else ("↑ worsened" if feed.drift_delta > 0 else "unchanged")
        console.print(f"Drift Index: {arrow} ({'+' if feed.drift_delta > 0 else ''}{feed.drift_delta} pts)")
    for c in feed.changes[:limit]:
        icon = {"positive": "[green]✓[/green]", "negative": "[red]✗[/red]"}.get(c.direction, "•")
        console.print(f"  {icon} {c.text}")


if __name__ == "__main__":
    app()
