#!/usr/bin/env python3
"""
.github/scripts/generate_lint_report.py

Reads log artifacts from /tmp/lint-logs, job results from JOB_RESULTS env var,
and GitHub context env vars — then renders a self-contained HTML report to
/tmp/lint-report/index.html.
"""

import os
import sys
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# ── GitHub context ─────────────────────────────────────────────────────────────
run_id      = os.environ.get("GITHUB_RUN_ID", "unknown")
run_number  = os.environ.get("GITHUB_RUN_NUMBER", "?")
repo        = os.environ.get("GITHUB_REPOSITORY", "unknown/repo")
sha         = os.environ.get("GITHUB_SHA", "")
sha_short   = sha[:7] if sha else "unknown"
ref         = os.environ.get("GITHUB_REF_NAME", "unknown")
event       = os.environ.get("GITHUB_EVENT_NAME", "unknown")
actor       = os.environ.get("GITHUB_ACTOR", "unknown")
server_url  = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
timestamp   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
run_url     = f"{server_url}/{repo}/actions/runs/{run_id}"

# ── Job results ────────────────────────────────────────────────────────────────
try:
    job_results = json.loads(os.environ.get("JOB_RESULTS", "{}"))
except Exception:
    job_results = {}

# ── Log file map: display name → (artifact subpath, result key) ───────────────
LOG_DIR = Path("/tmp/lint-logs")
log_map = {
    "YAML Lint":       ("lint-log-yamllint/yamllint.log",          "yamllint"),
    "ArgoCD App Lint": ("lint-log-argocd-manifest-app/argocd-app-lint.log", "argocd-app-lint"),
    "Manifest App Lint": ("lint-log-argocd-manifest-app/manifests-lint.log", "manifests-app-lint"),
    "Helm Lint":       ("lint-log-helm/helm-lint.log",             "helm-lint"),
}


def read_log(rel_path: str) -> str | None:
    full = LOG_DIR / rel_path
    return full.read_text(errors="replace") if full.exists() else None


def parse_kubeconform_stats(text: str) -> dict | None:
    """Extract resource/invalid/error counts from a kubeconform summary line."""
    if not text:
        return None
    m = re.search(
        r"Summary:\s+(\d+)\s+resources?\s+found.*?(\d+)\s+invalid.*?(\d+)\s+errors?",
        text, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return {"resources": int(m.group(1)), "invalid": int(m.group(2)), "errors": int(m.group(3))}
    return None


def line_css_class(line: str) -> str:
    lo = line.lower()
    if any(x in lo for x in ["✗", "failed", " error", "invalid", "err:"]):
        return "log-error"
    if any(x in lo for x in ["✓ ok", "passed", "no issues found", "valid"]):
        return "log-ok"
    if any(x in lo for x in ["warning", "warn"]):
        return "log-warn"
    if any(x in lo for x in ["▶", "━", "building", "linting", "checking", "templating"]):
        return "log-section"
    if "summary:" in lo:
        return "log-summary"
    return ""


def render_log_html(text: str | None) -> str:
    if not text:
        return '<div class="log-empty">No output captured for this job.</div>'
    parts = []
    for line in text.splitlines():
        cls = line_css_class(line)
        esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f'<span class="{cls}">{esc}</span>' if cls else esc)
    return "<pre class='log-block'>" + "\n".join(parts) + "</pre>"


# ── Build job data ─────────────────────────────────────────────────────────────
jobs = []
overall_pass = True

for job_name, (log_path, result_key) in log_map.items():
    log_text = read_log(log_path)
    result   = job_results.get(result_key, "skipped")
    if result == "failure":
        overall_pass = False

    jobs.append({
        "name":     job_name,
        "result":   result,
        "log_html": render_log_html(log_text),
        "stats":    parse_kubeconform_stats(log_text) if log_text else None,
    })

total   = len([j for j in jobs if j["result"] != "skipped"])
passed  = len([j for j in jobs if j["result"] == "success"])
failed  = len([j for j in jobs if j["result"] == "failure"])
skipped = len([j for j in jobs if j["result"] == "skipped"])

overall_label = "PASSED" if overall_pass else "FAILED"
overall_class = "overall-pass" if overall_pass else "overall-fail"


# ── Render job cards ───────────────────────────────────────────────────────────
def render_job_card(j: dict) -> str:
    r = j["result"]
    badge_cls = {"success": "badge-pass", "failure": "badge-fail", "skipped": "badge-skip"}.get(r, "badge-skip")
    badge_lbl = {"success": "PASS",       "failure": "FAIL",       "skipped": "SKIP"}.get(r, "SKIP")
    icon      = {"success": "✓",          "failure": "✗",          "skipped": "–"}.get(r, "–")
    card_cls  = {"success": "card-pass",  "failure": "card-fail",  "skipped": "card-skip"}.get(r, "card-skip")
    cid       = f"log-{j['name'].replace(' ', '-').lower()}"

    stats_html = ""
    if j["stats"]:
        s = j["stats"]
        stats_html = f"""
        <div class="stats-row">
          <span class="stat"><span class="stat-num">{s['resources']}</span> resources</span>
          <span class="stat stat-bad"><span class="stat-num">{s['invalid']}</span> invalid</span>
          <span class="stat stat-bad"><span class="stat-num">{s['errors']}</span> errors</span>
        </div>"""

    return f"""
    <div class="job-card {card_cls}">
      <div class="job-header" onclick="toggleLog('{cid}')">
        <span class="job-icon">{icon}</span>
        <span class="job-name">{j['name']}</span>
        <span class="badge {badge_cls}">{badge_lbl}</span>
        <span class="expand-hint" id="hint-{cid}">▼ show log</span>
      </div>
      {stats_html}
      <div class="log-wrap" id="{cid}" style="display:none">
        {j['log_html']}
      </div>
    </div>"""


cards_html = "\n".join(render_job_card(j) for j in jobs)

# ── Full HTML document ─────────────────────────────────────────────────────────
HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ArgoCD Lint · {repo} #{run_number}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:ital,wght@0,400;0,600;0,700;1,400&family=Syne:wght@400;600;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg:          #0d0f14;
      --bg2:         #13161e;
      --bg3:         #1a1e2a;
      --border:      #252a38;
      --border2:     #333a52;
      --text:        #c8cfe8;
      --text-dim:    #5a6280;
      --text-hi:     #eef0fa;
      --pass:        #2dd4a0;
      --pass-bg:     #0b2820;
      --fail:        #f05372;
      --fail-bg:     #2a0f18;
      --skip:        #6577b0;
      --skip-bg:     #151b30;
      --accent:      #5b8af5;
      --warn:        #f4c430;
      --mono:        'JetBrains Mono', monospace;
      --sans:        'Syne', sans-serif;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      min-height: 100vh;
    }}

    /* ── Topbar ── */
    .topbar {{
      position: sticky; top: 0; z-index: 100;
      background: var(--bg2);
      border-bottom: 1px solid var(--border);
      padding: 0 2rem;
      height: 50px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .tb-brand {{
      font-family: var(--mono);
      font-size: 0.75rem;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--text-dim);
    }}
    .tb-brand em {{ color: var(--accent); font-style: normal; }}
    .tb-run {{
      font-family: var(--mono);
      font-size: 0.7rem;
      color: var(--text-dim);
    }}
    .tb-run a {{ color: var(--accent); text-decoration: none; }}
    .tb-run a:hover {{ text-decoration: underline; }}

    /* ── Layout ── */
    .wrap {{
      max-width: 920px;
      margin: 0 auto;
      padding: 0 1.5rem;
    }}

    /* ── Hero ── */
    .hero {{
      padding: 3rem 0 1.5rem;
    }}
    .hero-pill {{
      display: inline-flex;
      align-items: center;
      gap: 0.6rem;
      margin-bottom: 1.25rem;
      padding: 0.3em 0.8em 0.3em 0.5em;
      border-radius: 999px;
      border: 1px solid;
    }}
    .overall-pass .hero-pill {{ border-color: var(--pass); background: var(--pass-bg); }}
    .overall-fail .hero-pill {{ border-color: var(--fail); background: var(--fail-bg); }}
    .pill-dot {{
      width: 10px; height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .overall-pass .pill-dot {{ background: var(--pass); box-shadow: 0 0 8px var(--pass); }}
    .overall-fail .pill-dot {{ background: var(--fail); box-shadow: 0 0 8px var(--fail); }}
    .pill-lbl {{
      font-family: var(--mono);
      font-size: 0.65rem;
      font-weight: 700;
      letter-spacing: 0.2em;
      text-transform: uppercase;
    }}
    .overall-pass .pill-lbl {{ color: var(--pass); }}
    .overall-fail .pill-lbl {{ color: var(--fail); }}

    h1 {{
      font-size: clamp(1.7rem, 5vw, 2.6rem);
      font-weight: 800;
      color: var(--text-hi);
      line-height: 1.1;
      margin-bottom: 0.5rem;
      letter-spacing: -0.02em;
    }}
    .hero-meta {{
      font-family: var(--mono);
      font-size: 0.73rem;
      color: var(--text-dim);
      margin-bottom: 2rem;
    }}
    .hero-meta b {{ color: var(--text); font-weight: 400; }}

    /* ── Score tiles ── */
    .scores {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0.75rem;
      margin-bottom: 2.5rem;
    }}
    @media (max-width: 500px) {{ .scores {{ grid-template-columns: repeat(2, 1fr); }} }}
    .score-tile {{
      background: var(--bg3);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem 1.25rem;
    }}
    .score-tile .n {{
      font-family: var(--mono);
      font-size: 2.2rem;
      font-weight: 700;
      line-height: 1;
      margin-bottom: 0.3rem;
    }}
    .score-tile .l {{
      font-family: var(--mono);
      font-size: 0.62rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--text-dim);
    }}
    .st-total .n {{ color: var(--accent); }}
    .st-pass  .n {{ color: var(--pass); }}
    .st-fail  .n {{ color: var(--fail); }}
    .st-skip  .n {{ color: var(--skip); }}

    /* ── Job cards section ── */
    .section-hdr {{
      font-family: var(--mono);
      font-size: 0.62rem;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--text-dim);
      padding-bottom: 0.5rem;
      margin-bottom: 0.75rem;
      border-bottom: 1px solid var(--border);
    }}
    .jobs {{ margin-bottom: 3rem; }}

    .job-card {{
      border: 1px solid var(--border);
      border-radius: 10px;
      margin-bottom: 0.65rem;
      overflow: hidden;
      transition: border-color 0.15s;
    }}
    .job-card:hover {{ border-color: var(--border2); }}
    .card-pass {{ border-left: 3px solid var(--pass); }}
    .card-fail {{ border-left: 3px solid var(--fail); }}
    .card-skip {{ border-left: 3px solid var(--skip); opacity: 0.7; }}

    .job-header {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.8rem 1.2rem;
      background: var(--bg2);
      cursor: pointer;
      user-select: none;
      transition: background 0.12s;
    }}
    .job-header:hover {{ background: var(--bg3); }}

    .job-icon {{
      font-family: var(--mono);
      font-size: 1rem;
      font-weight: 700;
      width: 1.3em;
      text-align: center;
      flex-shrink: 0;
    }}
    .card-pass .job-icon {{ color: var(--pass); }}
    .card-fail .job-icon {{ color: var(--fail); }}
    .card-skip .job-icon {{ color: var(--skip); }}

    .job-name {{
      flex: 1;
      font-size: 0.88rem;
      font-weight: 600;
      color: var(--text-hi);
    }}
    .badge {{
      font-family: var(--mono);
      font-size: 0.58rem;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      padding: 0.22em 0.6em;
      border-radius: 4px;
      border: 1px solid;
    }}
    .badge-pass {{ color: var(--pass); border-color: var(--pass); background: var(--pass-bg); }}
    .badge-fail {{ color: var(--fail); border-color: var(--fail); background: var(--fail-bg); }}
    .badge-skip {{ color: var(--skip); border-color: var(--skip); background: var(--skip-bg); }}
    .expand-hint {{
      font-family: var(--mono);
      font-size: 0.62rem;
      color: var(--text-dim);
      flex-shrink: 0;
      transition: color 0.12s;
    }}
    .job-header:hover .expand-hint {{ color: var(--accent); }}

    .stats-row {{
      display: flex;
      gap: 1.5rem;
      padding: 0.35rem 1.2rem 0.4rem 3.25rem;
      background: var(--bg2);
      border-top: 1px solid var(--border);
      font-family: var(--mono);
      font-size: 0.68rem;
      color: var(--text-dim);
    }}
    .stat-num {{ color: var(--text); font-weight: 600; margin-right: 0.25em; }}
    .stat-bad .stat-num {{ color: var(--fail); }}

    .log-wrap {{
      background: #080a10;
      border-top: 1px solid var(--border);
      max-height: 600px;
      overflow-y: auto;
    }}
    pre.log-block {{
      font-family: var(--mono);
      font-size: 0.71rem;
      line-height: 1.7;
      padding: 1rem 1.5rem;
      white-space: pre-wrap;
      word-break: break-all;
      color: #7080a0;
    }}
    .log-error   {{ color: #f05372; }}
    .log-ok      {{ color: #2dd4a0; }}
    .log-warn    {{ color: #f4c430; }}
    .log-section {{ color: #5b8af5; font-weight: 600; }}
    .log-summary {{ color: var(--text-hi); font-weight: 600; }}
    .log-empty {{
      padding: 1rem 1.5rem;
      font-family: var(--mono);
      font-size: 0.7rem;
      color: var(--text-dim);
      font-style: italic;
    }}

    /* ── Meta grid ── */
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
      gap: 0.5rem;
      margin-bottom: 3rem;
    }}
    .meta-cell {{
      background: var(--bg2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.6rem 1rem;
    }}
    .mc-k {{
      font-family: var(--mono);
      font-size: 0.58rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--text-dim);
      margin-bottom: 0.2rem;
    }}
    .mc-v {{
      font-family: var(--mono);
      font-size: 0.75rem;
      color: var(--text-hi);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    footer {{
      border-top: 1px solid var(--border);
      text-align: center;
      padding: 1.25rem;
      font-family: var(--mono);
      font-size: 0.62rem;
      color: var(--text-dim);
    }}
  </style>
</head>
<body>

<div class="topbar">
  <div class="tb-brand">⎈ <em>ArgoCD</em> Lint Report</div>
  <div class="tb-run">
    <a href="{run_url}" target="_blank" rel="noopener">Run #{run_number}</a>
    &nbsp;·&nbsp; {timestamp}
  </div>
</div>

<div class="wrap">

  <div class="hero {overall_class}">
    <div class="hero-pill">
      <div class="pill-dot"></div>
      <div class="pill-lbl">{overall_label}</div>
    </div>
    <h1>{repo.split("/")[-1]}</h1>
    <div class="hero-meta">
      <b>{ref}</b> &nbsp;·&nbsp; <b>{sha_short}</b>
      &nbsp;·&nbsp; {actor} via {event}
    </div>

    <div class="scores">
      <div class="score-tile st-total"><div class="n">{total}</div><div class="l">Jobs run</div></div>
      <div class="score-tile st-pass"> <div class="n">{passed}</div><div class="l">Passed</div></div>
      <div class="score-tile st-fail"> <div class="n">{failed}</div><div class="l">Failed</div></div>
      <div class="score-tile st-skip"> <div class="n">{skipped}</div><div class="l">Skipped</div></div>
    </div>
  </div>

  <div class="jobs">
    <div class="section-hdr">Job Results</div>
    {cards_html}
  </div>

  <div class="section-hdr">Run Metadata</div>
  <div class="meta-grid">
    <div class="meta-cell"><div class="mc-k">Repository</div><div class="mc-v">{repo}</div></div>
    <div class="meta-cell"><div class="mc-k">Branch / Ref</div><div class="mc-v">{ref}</div></div>
    <div class="meta-cell"><div class="mc-k">Commit</div><div class="mc-v">{sha_short}</div></div>
    <div class="meta-cell"><div class="mc-k">Triggered by</div><div class="mc-v">{actor}</div></div>
    <div class="meta-cell"><div class="mc-k">Event</div><div class="mc-v">{event}</div></div>
    <div class="meta-cell"><div class="mc-k">Run ID</div><div class="mc-v">#{run_number} · {run_id}</div></div>
  </div>

</div>

<footer>Generated by ArgoCD Lint Workflow &nbsp;·&nbsp; {timestamp}</footer>

<script>
  function toggleLog(id) {{
    const el   = document.getElementById(id);
    const hint = document.getElementById('hint-' + id);
    const open = el.style.display !== 'none';
    el.style.display  = open ? 'none' : 'block';
    hint.textContent  = open ? '▼ show log' : '▲ hide log';
  }}
  // Auto-expand failed job logs on load
  document.querySelectorAll('.card-fail .log-wrap').forEach(el => {{
    el.style.display = 'block';
    const hint = document.getElementById('hint-' + el.id);
    if (hint) hint.textContent = '▲ hide log';
  }});
</script>

</body>
</html>"""

# ── Write output ───────────────────────────────────────────────────────────────
out = Path("/tmp/lint-report/index.html")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(HTML)
print(f"✓ Report written → {out}")

if not overall_pass:
    print("✗ One or more lint jobs failed.")
    sys.exit(1)
