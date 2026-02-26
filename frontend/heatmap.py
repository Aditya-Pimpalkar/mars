"""
frontend/heatmap.py
────────────────────
MARS Evidence Heatmap — Streamlit app

Run: streamlit run frontend/heatmap.py

Shows the full claim × source matrix for any session,
with live conflict highlighting and click-to-expand evidence.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from es_client import get_client
from claim_ledger.ledger import ClaimLedger

# ── Page config ────────────────────────────────────────────────
st.set_page_config(
    page_title="MARS — Evidence Heatmap",
    page_icon="🔴",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Fetch sessions ─────────────────────────────────────────────
@st.cache_resource
def get_es():
    return get_client()

def get_sessions(es) -> list[str]:
    try:
        resp = es.search(
            index="claim-ledger-mars",
            body={
                "size": 0,
                "aggs": {
                    "sessions": {
                        "terms": {"field": "session_id", "size": 50}
                    }
                }
            }
        )
        buckets = resp["aggregations"]["sessions"]["buckets"]
        return [b["key"] for b in sorted(buckets, key=lambda x: x["key"], reverse=True)]
    except Exception:
        return []

def get_claims(es, session_id: str) -> list[dict]:
    try:
        resp = es.search(
            index="claim-ledger-mars",
            body={
                "query": {"term": {"session_id": session_id}},
                "size": 200,
                "sort": [{"created_at": "asc"}]
            }
        )
        return [h["_source"] for h in resp["hits"]["hits"]]
    except Exception:
        return []

# ── Support level logic ────────────────────────────────────────
def get_support_level(claim: dict, source_type: str) -> dict:
    """Return {level: 0-4, note: str} for a claim × source cell."""
    c_type  = claim.get("source_type", "unknown")
    status  = claim.get("status", "pending")
    conf    = claim.get("confidence", 0.0)
    text    = claim.get("claim_text", "")
    evidence = claim.get("evidence_summary", "")

    if c_type != source_type:
        return {"level": 0, "note": "Not retrieved from this source"}

    if status == "contradicted":
        return {
            "level": 1,
            "note": f"⚡ CONTRADICTED — {claim.get('resolution_reasoning', '')[:120]}"
        }
    if conf >= 0.90:
        return {"level": 4, "note": f"Strong evidence ({conf:.0%})\n{evidence}"}
    if conf >= 0.75:
        return {"level": 3, "note": f"Moderate evidence ({conf:.0%})\n{evidence}"}
    if conf >= 0.60:
        return {"level": 2, "note": f"Weak evidence ({conf:.0%})\n{evidence}"}
    return {"level": 2, "note": f"Low confidence ({conf:.0%})\n{evidence}"}

# ── Build heatmap HTML ─────────────────────────────────────────
def build_heatmap_html(claims: list[dict], summary: dict) -> str:
    sources = [
        {"id": "esql_data",    "label": "ES|QL Data",   "icon": "⚡"},
        {"id": "internal_doc", "label": "Internal Docs", "icon": "📖"},
        {"id": "web",          "label": "Web Scout",     "icon": "🌐"},
    ]

    # Build matrix data
    matrix = []
    for claim in claims:
        row = {
            "claim_id":   claim.get("claim_id", ""),
            "claim_text": claim.get("claim_text", "")[:80] + "...",
            "full_text":  claim.get("claim_text", ""),
            "status":     claim.get("status", "pending"),
            "confidence": claim.get("confidence", 0),
            "follow_up":  claim.get("follow_up_status", "idle"),
            "source_type": claim.get("source_type", "unknown"),
            "cells": []
        }
        for src in sources:
            support = get_support_level(claim, src["id"])
            row["cells"].append({
                "source": src["id"],
                "level":  support["level"],
                "note":   support["note"],
            })
        matrix.append(row)

    matrix_json  = json.dumps(matrix)
    sources_json = json.dumps(sources)
    summary_json = json.dumps(summary)

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:        #080c12;
    --surface:   #0d1420;
    --surface2:  #111827;
    --border:    #1e2d42;
    --accent:    #00d4ff;
    --teal:      #00bfb3;
    --red:       #ff4444;
    --amber:     #f59e0b;
    --green4:    #059669;
    --green3:    #10b981;
    --green2:    #6ee7b7;
    --gray:      #374151;
    --text:      #e2e8f0;
    --muted:     #64748b;
    --mono:      'Space Mono', monospace;
    --display:   'Syne', sans-serif;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    min-height: 100vh;
    padding: 24px;
  }}

  .header {{
    display: flex;
    align-items: baseline;
    gap: 16px;
    margin-bottom: 28px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }}
  .header h1 {{
    font-family: var(--display);
    font-size: 28px;
    font-weight: 800;
    color: var(--accent);
    letter-spacing: -0.5px;
  }}
  .header .sub {{
    color: var(--muted);
    font-size: 11px;
  }}

  /* ── Stats bar ── */
  .stats {{
    display: flex;
    gap: 12px;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }}
  .stat {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 16px;
    min-width: 120px;
  }}
  .stat-value {{
    font-family: var(--display);
    font-size: 22px;
    font-weight: 800;
    color: var(--accent);
  }}
  .stat-value.red   {{ color: var(--red); }}
  .stat-value.amber {{ color: var(--amber); }}
  .stat-value.green {{ color: var(--green3); }}
  .stat-label {{
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 2px;
  }}

  /* ── Legend ── */
  .legend {{
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
    align-items: center;
    flex-wrap: wrap;
  }}
  .legend-label {{ color: var(--muted); font-size: 10px; text-transform: uppercase; }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    color: var(--muted);
  }}
  .legend-cell {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
  }}

  /* ── Main grid ── */
  .grid-wrapper {{
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}

  /* Column headers */
  thead th {{
    padding: 12px 10px;
    text-align: center;
    font-family: var(--display);
    font-size: 11px;
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    border-bottom: 1px solid var(--border);
    background: var(--surface2);
  }}
  thead th.claim-col {{
    text-align: left;
    width: 340px;
    padding-left: 16px;
  }}
  thead th.source-col {{ width: 120px; }}
  thead th.conf-col   {{ width: 80px; }}

  /* Rows */
  tbody tr {{
    border-bottom: 1px solid var(--border);
    transition: background 0.15s;
  }}
  tbody tr:hover {{ background: rgba(0,212,255,0.03); }}
  tbody tr:last-child {{ border-bottom: none; }}

  /* Claim label cell */
  .claim-cell {{
    padding: 10px 10px 10px 16px;
    max-width: 340px;
    vertical-align: middle;
  }}
  .claim-id {{
    font-size: 9px;
    color: var(--muted);
    font-family: var(--mono);
    margin-bottom: 2px;
  }}
  .claim-text {{
    font-size: 11px;
    color: var(--text);
    line-height: 1.4;
    cursor: pointer;
  }}
  .claim-text:hover {{ color: var(--accent); }}

  .follow-up-badge {{
    display: inline-block;
    font-size: 9px;
    padding: 1px 6px;
    border-radius: 3px;
    margin-left: 6px;
    vertical-align: middle;
    background: rgba(245,158,11,0.15);
    color: var(--amber);
    border: 1px solid rgba(245,158,11,0.3);
  }}
  .follow-up-badge.resolved {{
    background: rgba(16,185,129,0.12);
    color: var(--green3);
    border-color: rgba(16,185,129,0.3);
  }}

  /* Evidence cells */
  .cell {{
    padding: 6px;
    text-align: center;
    vertical-align: middle;
    cursor: pointer;
    position: relative;
  }}

  .cell-inner {{
    width: 52px;
    height: 40px;
    border-radius: 6px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    transition: transform 0.15s, box-shadow 0.15s;
    position: relative;
    overflow: hidden;
  }}
  .cell-inner:hover {{
    transform: scale(1.12);
    box-shadow: 0 0 12px rgba(0,212,255,0.3);
    z-index: 10;
  }}

  /* Level colours */
  .lvl-0 {{ background: rgba(30,45,66,0.4); border: 1px solid rgba(30,45,66,0.8); }}
  .lvl-1 {{
    background: rgba(255,68,68,0.18);
    border: 1px solid rgba(255,68,68,0.5);
    animation: pulse-red 1.8s ease-in-out infinite;
  }}
  .lvl-2 {{ background: rgba(245,158,11,0.18); border: 1px solid rgba(245,158,11,0.45); }}
  .lvl-3 {{ background: rgba(16,185,129,0.22); border: 1px solid rgba(16,185,129,0.5); }}
  .lvl-4 {{ background: rgba(5,150,105,0.30);  border: 1px solid rgba(5,150,105,0.7); box-shadow: 0 0 8px rgba(5,150,105,0.2); }}

  @keyframes pulse-red {{
    0%, 100% {{ box-shadow: 0 0 0 0 rgba(255,68,68,0.4); }}
    50%       {{ box-shadow: 0 0 0 4px rgba(255,68,68,0); }}
  }}

  /* Confidence column */
  .conf-cell {{
    padding: 6px 10px;
    text-align: center;
    vertical-align: middle;
  }}
  .conf-bar-wrap {{
    width: 56px;
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    margin: 0 auto 3px;
  }}
  .conf-bar {{
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
  }}
  .conf-pct {{ font-size: 10px; color: var(--muted); }}

  /* Tooltip */
  .tooltip {{
    display: none;
    position: fixed;
    z-index: 999;
    background: #111827;
    border: 1px solid var(--accent);
    border-radius: 8px;
    padding: 12px 14px;
    max-width: 320px;
    font-size: 11px;
    line-height: 1.6;
    color: var(--text);
    pointer-events: none;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6);
  }}
  .tooltip.show {{ display: block; }}
  .tooltip-title {{
    font-family: var(--display);
    font-size: 12px;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 6px;
  }}
  .tooltip-conflict {{
    color: var(--red);
    font-weight: bold;
    margin-top: 6px;
  }}

  /* Summary row */
  tfoot td {{
    padding: 8px 6px;
    text-align: center;
    border-top: 2px solid var(--border);
    background: var(--surface2);
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }}
  tfoot td.claim-col {{ text-align: left; padding-left: 16px; }}

  /* Claim detail panel */
  .detail-panel {{
    margin-top: 20px;
    background: var(--surface);
    border: 1px solid var(--accent);
    border-radius: 10px;
    padding: 20px;
    display: none;
  }}
  .detail-panel.show {{ display: block; animation: fadeIn 0.2s ease; }}
  @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  .detail-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
  }}
  .detail-id {{ font-size: 10px; color: var(--muted); }}
  .detail-text {{
    font-size: 13px;
    color: var(--text);
    line-height: 1.6;
    margin-bottom: 14px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border);
  }}
  .detail-meta {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 10px;
  }}
  .meta-item {{ }}
  .meta-key  {{ font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; }}
  .meta-val  {{ font-size: 11px; color: var(--text); margin-top: 2px; }}
  .meta-val.conflict {{ color: var(--red); }}
  .meta-val.supported {{ color: var(--green3); }}
  .close-btn {{
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    border-radius: 4px;
    padding: 3px 10px;
    cursor: pointer;
    font-family: var(--mono);
    font-size: 11px;
  }}
  .close-btn:hover {{ border-color: var(--red); color: var(--red); }}
</style>
</head>
<body>

<div class="header">
  <h1>MARS</h1>
  <span class="sub">Evidence Heatmap — Multi-Agent Research Synthesizer</span>
</div>

<div class="stats" id="stats"></div>

<div class="legend">
  <span class="legend-label">Support level →</span>
  <div class="legend-item"><div class="legend-cell lvl-4" style="background:rgba(5,150,105,0.30);border:1px solid rgba(5,150,105,0.7)"></div> Strong (≥90%)</div>
  <div class="legend-item"><div class="legend-cell lvl-3" style="background:rgba(16,185,129,0.22);border:1px solid rgba(16,185,129,0.5)"></div> Moderate (≥75%)</div>
  <div class="legend-item"><div class="legend-cell lvl-2" style="background:rgba(245,158,11,0.18);border:1px solid rgba(245,158,11,0.45)"></div> Weak (≥60%)</div>
  <div class="legend-item"><div class="legend-cell lvl-1" style="background:rgba(255,68,68,0.18);border:1px solid rgba(255,68,68,0.5)"></div> Contradicted</div>
  <div class="legend-item"><div class="legend-cell lvl-0" style="background:rgba(30,45,66,0.4);border:1px solid rgba(30,45,66,0.8)"></div> No evidence</div>
</div>

<div class="grid-wrapper">
  <table id="heatmap-table"></table>
</div>

<div class="detail-panel" id="detail-panel">
  <div class="detail-header">
    <div>
      <div class="detail-id" id="detail-id"></div>
    </div>
    <button class="close-btn" onclick="closeDetail()">✕ close</button>
  </div>
  <div class="detail-text" id="detail-text"></div>
  <div class="detail-meta" id="detail-meta"></div>
</div>

<div class="tooltip" id="tooltip"></div>

<script>
const MATRIX  = {matrix_json};
const SOURCES = {sources_json};
const SUMMARY = {summary_json};

const LEVEL_ICONS = ['·', '⚡', '◒', '◕', '●'];
const LEVEL_LABELS = ['No evidence', 'Contradicted', 'Weak', 'Moderate', 'Strong'];

function confColor(c) {{
  if (c >= 0.90) return '#059669';
  if (c >= 0.75) return '#10b981';
  if (c >= 0.60) return '#f59e0b';
  return '#ef4444';
}}

// ── Stats bar ──────────────────────────────────────────────
function renderStats() {{
  const el = document.getElementById('stats');
  const breakdown = SUMMARY.status_breakdown || {{}};
  const stats = [
    {{ label: 'Total Claims',     value: SUMMARY.total_claims,     cls: '' }},
    {{ label: 'Verified',         value: breakdown.supported || 0, cls: 'green' }},
    {{ label: 'Conflicts Found',  value: SUMMARY.conflicts_found,  cls: SUMMARY.conflicts_found > 0 ? 'red' : '' }},
    {{ label: 'Follow-ups Fired', value: SUMMARY.follow_ups_fired, cls: 'amber' }},
    {{ label: 'Avg Confidence',   value: (SUMMARY.avg_confidence * 100).toFixed(0) + '%', cls: 'green' }},
  ];
  el.innerHTML = stats.map(s => `
    <div class="stat">
      <div class="stat-value ${{s.cls}}">${{s.value}}</div>
      <div class="stat-label">${{s.label}}</div>
    </div>
  `).join('');
}}

// ── Heatmap table ──────────────────────────────────────────
function renderTable() {{
  const table = document.getElementById('heatmap-table');

  // Header
  let html = `<thead><tr>
    <th class="claim-col">Claim</th>
    ${{SOURCES.map(s => `<th class="source-col">${{s.icon}} ${{s.label}}</th>`).join('')}}
    <th class="conf-col">Confidence</th>
  </tr></thead>`;

  // Body
  html += '<tbody>';
  MATRIX.forEach((row, ri) => {{
    const isConflict = row.status === 'contradicted';
    const hasFollowup = row.follow_up !== 'idle';

    let badge = '';
    if (row.follow_up === 'querying')  badge = `<span class="follow-up-badge">🔄 querying</span>`;
    if (row.follow_up === 'resolved')  badge = `<span class="follow-up-badge resolved">✓ followed-up</span>`;
    if (row.follow_up === 'exhausted') badge = `<span class="follow-up-badge">⚠ exhausted</span>`;

    html += `<tr>
      <td class="claim-cell">
        <div class="claim-id">${{row.claim_id}}</div>
        <div class="claim-text" onclick="showDetail(${{ri}})">${{row.claim_text}}${{badge}}</div>
      </td>`;

    row.cells.forEach((cell, ci) => {{
      const icon = LEVEL_ICONS[cell.level];
      html += `<td class="cell"
        onmouseenter="showTooltip(event, ${{ri}}, ${{ci}})"
        onmouseleave="hideTooltip()"
        onclick="showDetail(${{ri}})">
        <div class="cell-inner lvl-${{cell.level}}">${{icon}}</div>
      </td>`;
    }});

    const conf = row.confidence;
    const barColor = confColor(conf);
    html += `<td class="conf-cell">
      <div class="conf-bar-wrap">
        <div class="conf-bar" style="width:${{(conf*100).toFixed(0)}}%;background:${{barColor}}"></div>
      </div>
      <div class="conf-pct">${{(conf*100).toFixed(0)}}%</div>
    </td>`;

    html += '</tr>';
  }});
  html += '</tbody>';

  // Footer — source coverage scores
  html += '<tfoot><tr><td class="claim-col">Source coverage</td>';
  SOURCES.forEach((src, ci) => {{
    const total    = MATRIX.length;
    const covered  = MATRIX.filter(r => r.cells[ci].level >= 2).length;
    const pct      = total > 0 ? Math.round(covered / total * 100) : 0;
    html += `<td>${{covered}}/${{total}} (${{pct}}%)</td>`;
  }});
  html += '<td>—</td></tr></tfoot>';

  table.innerHTML = html;
}}

// ── Tooltip ────────────────────────────────────────────────
function showTooltip(e, ri, ci) {{
  const row  = MATRIX[ri];
  const cell = row.cells[ci];
  const src  = SOURCES[ci];
  const tip  = document.getElementById('tooltip');

  const levelLabel = LEVEL_LABELS[cell.level];
  const isConflict = cell.level === 1;

  tip.innerHTML = `
    <div class="tooltip-title">${{src.icon}} ${{src.label}} → ${{row.claim_id}}</div>
    <div><b>Level:</b> ${{levelLabel}}</div>
    <div style="margin-top:6px;color:#94a3b8">${{cell.note}}</div>
    ${{isConflict ? `<div class="tooltip-conflict">⚡ This claim was overridden by ES|QL data</div>` : ''}}
  `;

  tip.classList.add('show');
  positionTooltip(e);
}}

function hideTooltip() {{
  document.getElementById('tooltip').classList.remove('show');
}}

function positionTooltip(e) {{
  const tip = document.getElementById('tooltip');
  const x = e.clientX + 14;
  const y = e.clientY - 10;
  const maxX = window.innerWidth  - 340;
  const maxY = window.innerHeight - 160;
  tip.style.left = Math.min(x, maxX) + 'px';
  tip.style.top  = Math.min(y, maxY) + 'px';
}}

document.addEventListener('mousemove', e => {{
  const tip = document.getElementById('tooltip');
  if (tip.classList.contains('show')) positionTooltip(e);
}});

// ── Detail panel ───────────────────────────────────────────
function showDetail(ri) {{
  const row = MATRIX[ri];
  document.getElementById('detail-id').textContent   = row.claim_id;
  document.getElementById('detail-text').textContent = row.full_text;

  const statusCls = row.status === 'contradicted' ? 'conflict'
                  : row.status === 'supported'    ? 'supported' : '';

  const metaItems = [
    {{ key: 'Source Type',   val: row.source_type,                          cls: '' }},
    {{ key: 'Status',        val: row.status,                               cls: statusCls }},
    {{ key: 'Confidence',    val: (row.confidence * 100).toFixed(0) + '%',  cls: '' }},
    {{ key: 'Follow-up',     val: row.follow_up,                            cls: '' }},
  ];

  document.getElementById('detail-meta').innerHTML = metaItems.map(m => `
    <div class="meta-item">
      <div class="meta-key">${{m.key}}</div>
      <div class="meta-val ${{m.cls}}">${{m.val}}</div>
    </div>
  `).join('');

  document.getElementById('detail-panel').classList.add('show');
  document.getElementById('detail-panel').scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

function closeDetail() {{
  document.getElementById('detail-panel').classList.remove('show');
}}

// ── Init ───────────────────────────────────────────────────
renderStats();
renderTable();
</script>
</body>
</html>
"""

# ── Streamlit UI ───────────────────────────────────────────────
def main():
    st.sidebar.markdown("## 🔴 MARS")
    st.sidebar.markdown("**Evidence Heatmap**")
    st.sidebar.markdown("---")

    es = get_es()

    # Session picker
    sessions = get_sessions(es)
    if not sessions:
        st.error("No sessions found in claim-ledger-mars. Run `python agents/reviewer.py` first.")
        return

    selected = st.sidebar.selectbox(
        "Session",
        sessions,
        format_func=lambda s: f"mars_{s[-8:]}" if len(s) > 8 else s
    )

    # Auto-refresh toggle
    auto_refresh = st.sidebar.toggle("Auto-refresh (5s)", value=False)

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Legend**")
    st.sidebar.markdown("🟢 Strong evidence (≥90%)")
    st.sidebar.markdown("🟩 Moderate evidence (≥75%)")
    st.sidebar.markdown("🟡 Weak evidence (≥60%)")
    st.sidebar.markdown("🔴 Contradicted — overridden")
    st.sidebar.markdown("⬛ No evidence from source")

    # Load claims
    claims  = get_claims(es, selected)
    ledger  = ClaimLedger(es)
    summary = ledger.session_summary(selected)

    if not claims:
        st.warning(f"No claims found for session `{selected}`")
        return

    # Render the heatmap
    html = build_heatmap_html(claims, summary)
    st.components.v1.html(html, height=120 + len(claims) * 58 + 200, scrolling=True)

    # Auto refresh
    if auto_refresh:
        time.sleep(5)
        st.rerun()

if __name__ == "__main__":
    main()
