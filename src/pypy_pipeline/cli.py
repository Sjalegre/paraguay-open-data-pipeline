"""Command-line entry point.

    python -m pypy_pipeline build      # ingest, load, check, report
    python -m pypy_pipeline check      # re-run checks against an existing warehouse
    python -m pypy_pipeline export     # write Parquet/CSV for Power BI
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import duckdb

from .config import DOCS_DIR, EXPORT_DIR, REPORT_DIR, WAREHOUSE_PATH, ensure_dirs
from .quality import report as quality_report
from .quality.suite import build_suite, run_suite, summarise
from .warehouse.build import build, record_checks

# Tables exported for the Power BI model. The quality facts are exported too:
# the point of storing them as facts is that the dashboard can show data and
# data quality side by side.
EXPORT_TABLES = [
    "dim_geography",
    "dim_indicator",
    "dim_source",
    "dim_run",
    "fact_indicator_national",
    "fact_indicator_district",
    "fact_quality_check",
]


def _print_summary(summary: dict, results) -> None:
    print(
        f"\nchecks: {summary['total']} run, {summary['passed']} passed, "
        f"{summary['failed']} failed ({summary['blocking']} blocking)"
    )
    for r in results:
        if r.failed:
            print(f"  [{r.severity:7}] {r.check_id}")
            print(f"            expected {r.expected}; observed {r.observed}")


def cmd_build(args: argparse.Namespace) -> int:
    ensure_dirs()
    print("building warehouse ...")
    result = build(
        reuse_cache=args.reuse_cache,
        audit_catalogue=not args.skip_catalogue_audit,
    )
    conn = result.connection

    for table in ("dim_geography", "fact_indicator_national", "fact_indicator_district"):
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"  {table:28} {count:>8,} rows")
    for name, rate in result.match_rates.items():
        print(f"  name match rate ({name}): {rate:.2%}")
    if result.unmatched:
        print(f"  {len(result.unmatched)} district names did not match the dimension")
    if result.empty_indicators:
        print(f"  World Bank series with no data for Paraguay: "
              f"{', '.join(result.empty_indicators)}")

    results = run_suite(conn, build_suite(reference_year=args.reference_year))
    record_checks(conn, result.run_id, results)
    _print_summary(summarise(results), results)

    path = quality_report.render(
        conn, result.run_id, summarise(results),
        REPORT_DIR / "quality-report.html",
        catalogue=result.catalogue_audit or None,
        match_rates=result.match_rates,
        unmatched=result.unmatched,
    )
    print(f"\nreport: {path}")
    if args.publish_docs:
        # `data/` is git-ignored, so the generated report is invisible to
        # anyone browsing the repository. Copying it to `docs/` gives GitHub
        # Pages something to serve and the README a link that works without
        # cloning and running anything.
        published = DOCS_DIR / "index.html"
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"published: {published}")
    conn.close()

    blocking = summarise(results)["blocking"]
    if blocking:
        print(f"\nFAILED: {blocking} blocking check(s)", file=sys.stderr)
    return 1 if blocking else 0


def cmd_check(args: argparse.Namespace) -> int:
    ensure_dirs()
    conn = duckdb.connect(str(WAREHOUSE_PATH))
    run_id = conn.execute(
        "SELECT run_id FROM dim_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()[0]
    results = run_suite(conn, build_suite(reference_year=args.reference_year))
    conn.execute("DELETE FROM fact_quality_check WHERE run_id = ?", [run_id])
    record_checks(conn, run_id, results)
    summary = summarise(results)
    _print_summary(summary, results)
    path = quality_report.render(
        conn, run_id, summary, REPORT_DIR / "quality-report.html"
    )
    print(f"\nreport: {path}")
    conn.close()
    return 1 if summary["blocking"] else 0


def cmd_export(args: argparse.Namespace) -> int:
    ensure_dirs()
    conn = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    for table in EXPORT_TABLES:
        for fmt in args.formats:
            target = EXPORT_DIR / f"{table}.{fmt}"
            if fmt == "parquet":
                conn.execute(f"COPY {table} TO '{target}' (FORMAT PARQUET)")
            else:
                conn.execute(
                    f"COPY {table} TO '{target}' (FORMAT CSV, HEADER, DELIMITER ',')"
                )
            print(f"  wrote {target}")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pypy_pipeline")
    parser.add_argument(
        "--reference-year", type=int, default=date.today().year,
        help="year the freshness checks measure against (default: current year)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="ingest, load, check and report")
    build_parser.add_argument(
        "--reuse-cache", action="store_true",
        help="re-read previously downloaded payloads instead of the network",
    )
    build_parser.add_argument(
        "--skip-catalogue-audit", action="store_true",
        help="skip the full walk of the national catalogue (about 5 requests)",
    )
    build_parser.add_argument(
        "--publish-docs", action="store_true",
        help="also write the report to docs/index.html for GitHub Pages",
    )
    build_parser.set_defaults(func=cmd_build)

    check_parser = sub.add_parser("check", help="re-run checks on the existing warehouse")
    check_parser.set_defaults(func=cmd_check)

    export_parser = sub.add_parser("export", help="write tables for Power BI")
    export_parser.add_argument(
        "--formats", nargs="+", default=["parquet", "csv"],
        choices=["parquet", "csv"],
    )
    export_parser.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
