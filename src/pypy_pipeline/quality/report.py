"""Render the quality report as a self-contained HTML page.

The report is generated from `fact_quality_check` rather than from the in-memory
results, so what a reader sees is exactly what the warehouse recorded. It is a
single file with no external assets so it can be opened from disk or served from
GitHub Pages without a build step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data quality report - Paraguay open data warehouse</title>
<style>
  :root {
    --bg: #ffffff; --fg: #16191d; --muted: #5c6672; --line: #e3e7ec;
    --pass: #17795e; --pass-bg: #e6f4ef;
    --warn: #8a5a00; --warn-bg: #fdf3e0;
    --fail: #b02a1f; --fail-bg: #fdecea;
    --accent: #1f4e79;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14171a; --fg: #e8ecf1; --muted: #9aa5b1; --line: #2a2f36;
      --pass: #4cc79f; --pass-bg: #16302a;
      --warn: #e0a33c; --warn-bg: #33280f;
      --fail: #f1786c; --fail-bg: #3a1d1a;
      --accent: #7fb2e5;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--fg);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          Helvetica, Arial, sans-serif;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
  h2 { font-size: 1.05rem; margin: 2.5rem 0 .75rem; letter-spacing: -.005em; }
  .sub { color: var(--muted); margin: 0 0 2rem; font-size: .9rem; }
  .cards { display: grid; gap: .75rem;
           grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
  .card { border: 1px solid var(--line); border-radius: 8px; padding: .9rem 1rem; }
  .card .n { font-size: 1.7rem; font-weight: 650; line-height: 1.1; }
  .card .l { color: var(--muted); font-size: .78rem; text-transform: uppercase;
             letter-spacing: .04em; margin-top: .2rem; }
  .card.ok .n { color: var(--pass); }
  .card.warn .n { color: var(--warn); }
  .card.bad .n { color: var(--fail); }
  .scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
  table { border-collapse: collapse; width: 100%; font-size: .87rem; }
  th, td { text-align: left; padding: .55rem .7rem; border-bottom: 1px solid var(--line);
           vertical-align: top; }
  th { font-weight: 600; color: var(--muted); font-size: .74rem;
       text-transform: uppercase; letter-spacing: .04em; white-space: nowrap; }
  tbody tr:last-child td { border-bottom: none; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         font-size: .93em; }
  .pill { display: inline-block; padding: .1rem .45rem; border-radius: 4px;
          font-size: .74rem; font-weight: 650; letter-spacing: .02em; }
  .pill.pass { color: var(--pass); background: var(--pass-bg); }
  .pill.fail-error { color: var(--fail); background: var(--fail-bg); }
  .pill.fail-warning { color: var(--warn); background: var(--warn-bg); }
  .pill.fail-info { color: var(--muted); background: var(--line); }
  .note { border-left: 3px solid var(--accent); padding: .6rem 0 .6rem .9rem;
          color: var(--muted); margin: 1rem 0; font-size: .9rem; }
  .muted { color: var(--muted); }
  footer { margin-top: 3rem; color: var(--muted); font-size: .8rem;
           border-top: 1px solid var(--line); padding-top: 1rem; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Data quality report</h1>
  <p class="sub">
    Paraguay open data warehouse &middot; run <code>{{ run_id }}</code> &middot;
    generated {{ generated_at }}
  </p>

  <div class="cards">
    <div class="card"><div class="n">{{ summary.total }}</div><div class="l">Checks run</div></div>
    <div class="card ok"><div class="n">{{ summary.passed }}</div><div class="l">Passed</div></div>
    <div class="card {{ 'bad' if summary.failed else 'ok' }}">
      <div class="n">{{ summary.failed }}</div><div class="l">Failed</div></div>
    <div class="card {{ 'bad' if summary.blocking else 'ok' }}">
      <div class="n">{{ summary.blocking }}</div><div class="l">Blocking</div></div>
    <div class="card"><div class="n">{{ rows.district }}</div><div class="l">District facts</div></div>
    <div class="card"><div class="n">{{ rows.national }}</div><div class="l">National facts</div></div>
  </div>

  <div class="note">
    Checks that fail at <strong>warning</strong> severity are defects present in
    the published sources. They are recorded rather than repaired so the state of
    the source data stays visible; only <strong>error</strong> severity blocks a run.
  </div>

  <h2>Checks by family</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Family</th><th>Run</th><th>Failed</th></tr></thead>
    <tbody>
    {% for family, stat in summary.by_family.items() %}
      <tr><td>{{ family }}</td><td>{{ stat.total }}</td>
          <td>{% if stat.failed %}<span class="pill fail-error">{{ stat.failed }}</span>
              {% else %}<span class="pill pass">0</span>{% endif %}</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>

  <h2>All checks</h2>
  <div class="scroll">
  <table>
    <thead>
      <tr><th>Status</th><th>Check</th><th>Target</th><th>Expected</th>
          <th>Observed</th><th>Rows</th></tr>
    </thead>
    <tbody>
    {% for r in results %}
      <tr>
        <td>{% if r.status == 'pass' %}<span class="pill pass">pass</span>
            {% else %}<span class="pill fail-{{ r.severity }}">{{ r.severity }}</span>{% endif %}</td>
        <td><code>{{ r.check_id }}</code>
            {% if r.detail %}<div class="muted">{{ r.detail }}</div>{% endif %}</td>
        <td><code>{{ r.target_table }}</code>{% if r.target_column %}
            <div class="muted"><code>{{ r.target_column }}</code></div>{% endif %}</td>
        <td>{{ r.expected }}</td>
        <td>{{ r.observed }}</td>
        <td>{{ r.failing_rows }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>

  <h2>Source integration</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Measure</th><th>Value</th><th>Meaning</th></tr></thead>
    <tbody>
      {% for m in integration %}
      <tr><td>{{ m.label }}</td><td><strong>{{ m.value }}</strong></td>
          <td class="muted">{{ m.meaning }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
  </div>

  {% if catalogue %}
  <h2>National catalogue audit</h2>
  <p class="sub">Measured live against the datos.gov.py DKAN API during this run.</p>
  <div class="scroll">
  <table>
    <thead><tr><th>Measure</th><th>Value</th></tr></thead>
    <tbody>
      <tr><td>Datasets listed</td><td>{{ catalogue.packages }}</td></tr>
      <tr><td>Resources listed</td><td>{{ catalogue.resources }}</td></tr>
      <tr><td>Datasets with no resource</td><td>{{ catalogue.packages_without_resources }}</td></tr>
      <tr><td>Resources with no URL</td><td>{{ catalogue.resources_without_url }}</td></tr>
      <tr><td>Datasets with no declared publisher</td>
          <td>{{ catalogue.packages_without_publisher }}
              ({{ catalogue.publisher_gap_pct }})</td></tr>
      <tr><td>Distinct declared formats</td><td>{{ catalogue.formats | length }}</td></tr>
    </tbody>
  </table>
  </div>
  {% endif %}

  <h2>Sources retrieved</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Source</th><th>Status</th><th>Declared</th><th>Served</th>
               <th>Bytes</th><th>SHA-256</th></tr></thead>
    <tbody>
    {% for s in sources %}
      <tr>
        <td><code>{{ s.source_key }}</code></td>
        <td>{{ s.http_status }}</td>
        <td>{{ s.declared_format or '-' }}</td>
        <td>{{ s.served_format or '-' }}
            {% if s.format_matches is false %}
            <span class="pill fail-warning">mismatch</span>{% endif %}</td>
        <td>{{ '{:,}'.format(s.content_length or 0) }}</td>
        <td><code class="muted">{{ s.sha256[:12] }}</code></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>

  <footer>
    Generated by paraguay-open-data-pipeline. Every figure on this page is
    computed from the warehouse built by this run; nothing is hard-coded.
  </footer>
</div>
</body>
</html>
"""


def render(
    conn,
    run_id: str,
    summary: dict,
    output_path: Path,
    *,
    catalogue: dict | None = None,
    match_rates: dict[str, float] | None = None,
    unmatched: list | None = None,
) -> Path:
    """Write the HTML quality report and return its path."""
    results = [
        dict(zip(
            ("check_id", "check_family", "target_table", "target_column",
             "severity", "status", "expected", "observed", "failing_rows", "detail"),
            row,
        ))
        for row in conn.execute(
            "SELECT check_id, check_family, target_table, target_column, severity,"
            "       status, expected, observed, failing_rows, detail "
            "FROM fact_quality_check WHERE run_id = ? "
            "ORDER BY status DESC, severity, check_id",
            [run_id],
        ).fetchall()
    ]

    sources = [
        dict(zip(
            ("source_key", "http_status", "declared_format", "served_format",
             "format_matches", "content_length", "sha256"),
            row,
        ))
        for row in conn.execute(
            "SELECT source_key, http_status, declared_format, served_format,"
            "       format_matches, content_length, sha256 "
            "FROM dim_source WHERE run_id = ? ORDER BY source_key",
            [run_id],
        ).fetchall()
    ]

    rows = {
        "district": f"{conn.execute('SELECT count(*) FROM fact_indicator_district').fetchone()[0]:,}",
        "national": f"{conn.execute('SELECT count(*) FROM fact_indicator_national').fetchone()[0]:,}",
    }

    integration = _integration_metrics(conn, match_rates or {}, unmatched or [])

    if catalogue:
        catalogue = dict(catalogue)
        pct = (
            catalogue["packages_without_publisher"] / catalogue["packages"]
            if catalogue.get("packages") else 0
        )
        catalogue["publisher_gap_pct"] = f"{pct:.1%}"

    html = Environment(autoescape=True).from_string(_TEMPLATE).render(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        summary=summary,
        results=results,
        sources=sources,
        rows=rows,
        integration=integration,
        catalogue=catalogue,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _integration_metrics(conn, match_rates: dict, unmatched: list) -> list[dict]:
    """The measurements that answer the question the project set out to ask."""
    q = conn.execute

    districts = q("SELECT count(*) FROM dim_geography").fetchone()[0]
    crosswalked = q(
        "SELECT count(*) FROM dim_geography WHERE vintage_2012_key IS NOT NULL"
    ).fetchone()[0]
    new_districts = q(
        "SELECT count(*) FROM dim_geography WHERE created_after_2012"
    ).fetchone()[0]
    renamed = q(
        "SELECT count(*) FROM dim_geography WHERE name_changed_since_2012"
    ).fetchone()[0]
    damaged = q(
        "SELECT count(*) FROM dim_geography WHERE encoding_damage <> ''"
    ).fetchone()[0]
    unpadded = q(
        "SELECT count(*) FROM dim_geography "
        "WHERE vintage_2012_key IS NOT NULL "
        "  AND NOT regexp_matches(vintage_2012_key, '^\\d{4}$')"
    ).fetchone()[0]

    metrics = [
        {
            "label": "Districts in the 2022 register",
            "value": districts,
            "meaning": "CNPV 2022 GeoJSON, the authoritative vintage for this warehouse",
        },
        {
            "label": "Reconciled to the 2012 register",
            "value": f"{crosswalked} of {districts}",
            "meaning": "after zero-padding both keys to DDdd",
        },
        {
            "label": "2012 keys published unpadded",
            "value": f"{unpadded} of {crosswalked}",
            "meaning": "a join on the keys as published matches these on nothing",
        },
        {
            "label": "Districts created after 2012",
            "value": new_districts,
            "meaning": "a real change in the territory, not a join failure",
        },
        {
            "label": "Districts renamed since 2012",
            "value": renamed,
            "meaning": "same code, different official name across vintages",
        },
        {
            "label": "Names carrying encoding damage",
            "value": damaged,
            "meaning": "present in the published source before ingestion",
        },
    ]
    for name, rate in match_rates.items():
        metrics.append({
            "label": f"Name match rate: {name}",
            "value": f"{rate:.1%}",
            "meaning": "district rows attached to the dimension by normalised name",
        })
    metrics.append({
        "label": "Unmatched district names",
        "value": len(unmatched),
        "meaning": "rows that could not be attached and were not loaded",
    })
    return metrics
