"""Assemble the DuckDB warehouse from the ingested sources."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from ..config import WAREHOUSE_PATH, load_sources
from ..fetch import Session
from ..ingest import census, geography, worldbank
from ..ingest.catalog import Catalog
from ..quality.checks import CheckResult

PIPELINE_VERSION = "1.0.0"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Indicators sourced from the national statistics office rather than the World
# Bank. Declared here rather than in `sources.yml` because they are derived from
# the shape of the published tables, not configurable.
DISTRICT_INDICATORS = [
    ("POP_TOTAL", "Population, total", "persons", "INE (CNPV 2022)"),
    ("POP_MALE", "Population, male", "persons", "INE (CNPV 2022)"),
    ("POP_FEMALE", "Population, female", "persons", "INE (CNPV 2022)"),
    ("AGE_MEDIAN", "Median age", "years", "INE (CNPV 2022)"),
    ("NBI_HOUSEHOLDS", "Households", "households", "INE (NBI 2022)"),
    ("NBI_ANY_PCT", "Households with at least one unmet basic need", "%", "INE (NBI 2022)"),
    ("NBI_HOUSING_PCT", "Unmet basic need: housing quality", "%", "INE (NBI 2022)"),
    ("NBI_SANITATION_PCT", "Unmet basic need: sanitation", "%", "INE (NBI 2022)"),
    ("NBI_EDUCATION_PCT", "Unmet basic need: access to education", "%", "INE (NBI 2022)"),
    ("NBI_SUBSISTENCE_PCT", "Unmet basic need: subsistence capacity", "%", "INE (NBI 2022)"),
]


@dataclass
class BuildResult:
    run_id: str
    connection: duckdb.DuckDBPyConnection
    catalogue_audit: dict
    match_rates: dict[str, float]
    unmatched: list[tuple[str, str]]
    empty_indicators: list[str]


def build(
    *,
    warehouse_path: Path | None = None,
    reuse_cache: bool = False,
    audit_catalogue: bool = True,
) -> BuildResult:
    """Run the full ingest and load a fresh warehouse.

    The warehouse is rebuilt from scratch on every run. These sources are small
    and republished wholesale rather than incrementally, so an incremental load
    would add machinery that could drift out of step with the source for no gain.
    """
    sources = load_sources()
    run_id = uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc)
    session = Session(reuse_cache=reuse_cache)

    path = warehouse_path or WAREHOUSE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = duckdb.connect(str(path))
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))

    # ---- ingest -----------------------------------------------------------
    cfg = sources["datos_gov_py"]
    catalog = Catalog(session, cfg["api_base"])

    districts = geography.build_dimension(session, catalog, sources)
    scoped = geography.build_scoped_name_index(districts)
    unscoped = geography.build_name_index(districts)

    population = census.ingest_population(
        session, catalog, cfg["census_population_2022"], districts, scoped, unscoped
    )
    nbi = census.ingest_nbi(
        session, catalog, cfg["nbi_2022"], districts, scoped, unscoped
    )
    wb = worldbank.ingest(session, sources["worldbank"])

    audit = catalog.audit_catalogue() if audit_catalogue else {}

    # ---- load -------------------------------------------------------------
    # One transaction for the whole load. Without it DuckDB commits and syncs
    # per row, which turns a few thousand inserts into minutes, and it leaves a
    # half-loaded warehouse behind if anything fails partway through.
    conn.execute("BEGIN TRANSACTION")

    conn.execute(
        "INSERT INTO dim_run VALUES (?, ?, NULL, ?, ?)",
        [run_id, started, PIPELINE_VERSION, reuse_cache],
    )

    conn.executemany(
        "INSERT INTO dim_geography VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [
                d.geo_key, d.department_code, d.department_name, d.district_name,
                d.district_name_normalised, d.vintage_2012_key, d.name_2012,
                d.name_changed_since_2012, d.created_after_2012,
                ",".join(d.encoding_damage),
            ]
            for d in districts
        ],
    )

    conn.executemany(
        "INSERT INTO dim_indicator VALUES (?, ?, ?, ?, ?, ?)",
        [
            [m.code, m.name, m.unit, "national", "World Bank (WDI)",
             m.expect_complete_from]
            for m in wb.indicators
        ]
        + [
            [code, name, unit, "district", publisher, 2022]
            for code, name, unit, publisher in DISTRICT_INDICATORS
        ],
    )

    conn.executemany(
        "INSERT INTO fact_indicator_national VALUES (?, ?, ?, ?, ?)",
        [[o.indicator_code, o.year, o.value, run_id, o.source_key]
         for o in wb.observations],
    )

    district_rows = population.observations + nbi.observations
    conn.executemany(
        "INSERT INTO fact_indicator_district VALUES (?, ?, ?, ?, ?, ?)",
        [[o.geo_key, o.indicator_code, o.year, o.value, run_id, o.source_key]
         for o in district_rows],
    )

    conn.executemany(
        "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [
                f"{run_id}-{i:04d}", run_id, p.source_key, p.url, p.fetched_at,
                p.http_status, p.content_type, p.content_length, p.sha256,
                p.declared_format, p.served_format, p.format_matches_declared,
                p.from_cache,
            ]
            for i, p in enumerate(session.records)
        ],
    )

    conn.execute(
        "UPDATE dim_run SET finished_at = ? WHERE run_id = ?",
        [datetime.now(timezone.utc), run_id],
    )
    conn.execute("COMMIT")

    return BuildResult(
        run_id=run_id,
        connection=conn,
        catalogue_audit=audit,
        match_rates={
            "census_population": population.match_rate,
            "nbi": nbi.match_rate,
        },
        unmatched=population.unmatched + nbi.unmatched,
        empty_indicators=wb.empty_indicators,
    )


def record_checks(conn, run_id: str, results: list[CheckResult]) -> None:
    """Persist check results as facts."""
    conn.execute("BEGIN TRANSACTION")
    conn.executemany(
        "INSERT INTO fact_quality_check VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [
                run_id, r.check_id, r.check_family, r.target_table, r.target_column,
                r.severity, r.status, r.expected, r.observed, r.failing_rows,
                r.detail, r.checked_at,
            ]
            for r in results
        ],
    )
    conn.execute("COMMIT")
