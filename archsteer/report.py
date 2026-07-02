"""Self-contained local dashboard (report.html) — the demo/architect closer.

Renders the live map, conformance/drift score, per-rule migration progress, and
pending decisions into a single standalone HTML file (no cloud, no build step).
"""

from __future__ import annotations

import html
from typing import List, Optional

from archsteer.docs import _mermaid
from archsteer.engine.conformance import ConformanceReport
from archsteer.engine.evolution import EvolutionFeed, SnapshotMeta
from archsteer.engine.model import ArchitectureModel

_CSS = """
:root{color-scheme:light dark}
body{font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#0d1117;color:#e6edf3}
.wrap{max-width:960px;margin:0 auto;padding:32px}
h1{font-size:24px;margin:0 0 4px} .sub{color:#8b949e;margin:0 0 24px}
.cards{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:28px}
.card{flex:1;min-width:160px;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px}
.card .n{font-size:30px;font-weight:700} .card .l{color:#8b949e;font-size:13px}
.bar{height:9px;background:#30363d;border-radius:5px;overflow:hidden;margin-top:6px}
.bar>span{display:block;height:100%;background:linear-gradient(90deg,#2ea043,#56d364)}
.rule{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px;margin-bottom:10px}
.rule .top{display:flex;justify-content:space-between;font-weight:600}
.pill{font-size:11px;padding:2px 8px;border-radius:20px;background:#1f6feb33;color:#79c0ff}
.pill.err{background:#f8514933;color:#ff7b72}
.adr{border-left:3px solid #d29922;padding:6px 12px;margin:6px 0;background:#161b22}
.muted{color:#8b949e} table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid #21262d;padding:6px 8px;text-align:left}
code{background:#161b22;padding:1px 5px;border-radius:4px}
.feed{list-style:none;padding:0;margin:0}
.feed li{padding:8px 12px;border-left:3px solid #30363d;background:#161b22;margin:6px 0;border-radius:0 8px 8px 0}
.feed li.positive{border-left-color:#2ea043}
.feed li.negative{border-left-color:#f85149}
.tag{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.04em}
.footer{margin-top:44px;padding-top:16px;border-top:1px solid #21262d;color:#8b949e;font-size:13px;text-align:center}
.footer a{color:#79c0ff;text-decoration:none}
"""


def _sparkline(values: List[float], width: int = 220, height: int = 44) -> str:
    if len(values) < 2:
        return '<span class="muted">Not enough history yet — run map again over time.</span>'
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    step = width / (len(values) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - (v - lo) / rng * (height - 8) - 4:.1f}"
        for i, v in enumerate(values)
    )
    last = values[-1]
    color = "#2ea043" if last >= values[0] else "#f85149"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/></svg>'
    )


def _mermaid_html(model: ArchitectureModel) -> str:
    body = _mermaid(model).replace("```mermaid", "").replace("```", "").strip()
    return f'<div class="mermaid">{html.escape(body)}</div>'


def render_report_html(
    model: ArchitectureModel,
    report: ConformanceReport,
    pending_adrs: List[str],
    fixed_count: int = 0,
    feed: Optional[EvolutionFeed] = None,
    history: Optional[List[SnapshotMeta]] = None,
    governed: bool = True,
) -> str:
    score = report.conformance_score
    rule_cards = []
    for r in sorted(report.results, key=lambda x: x.progress):
        sev = "err" if r.severity == "error" else ""
        rule_cards.append(
            f'<div class="rule"><div class="top"><span>{html.escape(r.rule_id)} '
            f'<span class="pill {sev}">{r.severity}</span></span><span>{r.progress}%</span></div>'
            f'<div class="muted">{html.escape(r.description)}</div>'
            f'<div class="bar"><span style="width:{r.progress}%"></span></div>'
            f'<div class="muted" style="margin-top:6px">{len(r.violations)} open · {r.compliant}/{r.scoped} compliant</div></div>'
        )

    adr_html = "".join(
        f'<div class="adr">📝 {html.escape(a)}</div>' for a in pending_adrs
    ) or '<p class="muted">No pending decisions — architecture is fully ratified.</p>'

    catalog = "".join(
        f"<tr><td><code>{html.escape(p)}</code></td><td>{html.escape(c.layer or '—')}</td>"
        f"<td>{len(c.data_access)}</td><td>{len(c.external_calls)}</td></tr>"
        for p, c in sorted(model.components.items())
    )

    # Evolution feed
    if feed is None or feed.is_first:
        feed_html = '<p class="muted">First snapshot — the evolution baseline is set. Run <code>archsteer map</code> again later to see how the architecture changed.</p>'
    elif not feed.changes:
        feed_html = '<p class="muted">No structural change since the last snapshot.</p>'
    else:
        feed_html = '<ul class="feed">' + "".join(
            f'<li class="{c.direction}"><span class="tag">{html.escape(c.kind.replace("_", " "))}</span><br>{html.escape(c.text)}</li>'
            for c in feed.changes
        ) + "</ul>"

    # Drift Index trend (governed mode only)
    trend_html = ""
    if governed and history:
        series = [m.drift_score for m in history if m.drift_score is not None]
        if len(series) >= 2:
            trend_html = (
                f'<h2>Drift Index trend</h2><div class="card" style="max-width:260px">'
                f'{_sparkline(series)}<div class="l">Drift over {len(series)} snapshots '
                f'(now {series[-1]}%)</div></div>'
            )

    score_cards = (
        f'<div class="card"><div class="n">{score}%</div><div class="l">Conformance</div><div class="bar"><span style="width:{score}%"></span></div></div>'
        f'<div class="card"><div class="n">{report.drift_score}%</div><div class="l">Drift from target</div></div>'
        if governed else ""
    )
    violation_card = (
        f'<div class="card"><div class="n">{len(report.all_violations)}</div><div class="l">Open violations</div></div>'
        f'<div class="card"><div class="n">{fixed_count}</div><div class="l">Resolved since baseline</div></div>'
        if governed else ""
    )

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>ArchSteer — {html.escape(model.repo_name)}</title><style>{_CSS}</style>
<script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';mermaid.initialize({{startOnLoad:true,theme:'dark'}});</script>
</head><body><div class="wrap">
<h1>🧭 ArchSteer — {html.escape(model.repo_name)}</h1>
<p class="sub">{'Living architecture control plane' if governed else 'Architecture X-ray (read-only)'}{f" · commit {html.escape(model.commit_sha)}" if model.commit_sha else ""}</p>
<div class="cards">
  {score_cards}
  <div class="card"><div class="n">{len(model.components)}</div><div class="l">Components</div></div>
  <div class="card"><div class="n">{len(model.get_layers())}</div><div class="l">Layers</div></div>
  <div class="card"><div class="n">{len([s for s in model.get_all_data_stores() if s != 'raw_sql'])}</div><div class="l">Data stores</div></div>
  {violation_card}
</div>
<h2>What changed</h2>{feed_html}
{trend_html}
<h2>Layer map</h2>{_mermaid_html(model)}
{('<h2>Conformance by rule</h2>' + (''.join(rule_cards) or '<p class="muted">No rules declared.</p>')) if governed else ''}
<h2>Pending decisions (draft ADRs)</h2>{adr_html}
<h2>Component catalog</h2>
<table><thead><tr><th>Component</th><th>Layer</th><th>Data access</th><th>External calls</th></tr></thead>
<tbody>{catalog}</tbody></table>
<div class="footer">🧭 Generated by <a href="https://www.archsteer.com" target="_blank" rel="noopener">ArchSteer</a> — the living architecture control plane · <a href="https://www.archsteer.com/dashboard" target="_blank" rel="noopener">watch drift across every repo in the situation room →</a></div>
</div></body></html>"""
