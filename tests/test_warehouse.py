"""Assertions against a built warehouse.

Every figure the README quotes is checked here. If a source changes upstream and
a number in the README stops being true, these tests fail rather than the claim
quietly going stale.

The suite is skipped when no warehouse has been built, so `pytest` works on a
fresh clone with no network. Build one first with:

    python -m pypy_pipeline build
"""

from __future__ import annotations

import duckdb
import pytest

from pypy_pipeline.config import WAREHOUSE_PATH

pytestmark = pytest.mark.skipif(
    not WAREHOUSE_PATH.exists(),
    reason="no warehouse built; run `python -m pypy_pipeline build` first",
)


@pytest.fixture(scope="module")
def conn():
    connection = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    yield connection
    connection.close()


def scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


class TestGeographyClaims:
    def test_263_districts(self, conn):
        assert scalar(conn, "SELECT count(*) FROM dim_geography") == 263

    def test_250_reconcile_to_the_2012_register(self, conn):
        assert scalar(
            conn,
            "SELECT count(*) FROM dim_geography WHERE vintage_2012_key IS NOT NULL",
        ) == 250

    def test_13_districts_created_after_2012(self, conn):
        assert scalar(
            conn, "SELECT count(*) FROM dim_geography WHERE created_after_2012"
        ) == 13

    def test_every_key_is_zero_padded_to_four_digits(self, conn):
        assert scalar(
            conn,
            "SELECT count(*) FROM dim_geography "
            "WHERE NOT regexp_matches(geo_key, '^\\d{4}$')",
        ) == 0

    def test_most_published_2012_keys_are_not_joinable_as_published(self, conn):
        # 160 of the 250 shared districts publish a 2012 key shorter than four
        # digits. This is the defect the crosswalk exists to absorb.
        unpadded = scalar(
            conn,
            "SELECT count(*) FROM dim_geography "
            "WHERE vintage_2012_key IS NOT NULL "
            "  AND NOT regexp_matches(vintage_2012_key, '^\\d{4}$')",
        )
        assert unpadded == 160

    def test_three_district_names_arrived_with_encoding_damage(self, conn):
        assert scalar(
            conn, "SELECT count(*) FROM dim_geography WHERE encoding_damage <> ''"
        ) == 3


class TestDistrictFactClaims:
    def test_every_district_has_a_population(self, conn):
        # The claim behind "100% name match rate": all 263 districts of the
        # 2022 register carry a population figure, despite no source file
        # containing a district code.
        assert scalar(
            conn,
            "SELECT count(DISTINCT geo_key) FROM fact_indicator_district "
            "WHERE indicator_code = 'POP_TOTAL'",
        ) == 263

    def test_every_district_has_an_nbi_figure(self, conn):
        assert scalar(
            conn,
            "SELECT count(DISTINCT geo_key) FROM fact_indicator_district "
            "WHERE indicator_code = 'NBI_ANY_PCT'",
        ) == 263

    def test_no_orphan_geography_keys(self, conn):
        assert scalar(
            conn,
            "SELECT count(*) FROM fact_indicator_district f "
            "LEFT JOIN dim_geography d USING (geo_key) "
            "WHERE d.geo_key IS NULL",
        ) == 0

    def test_sexes_reconstruct_the_total_population(self, conn):
        # Guards against a thousands-separator misparse, which would leave the
        # values plausible individually but inconsistent with each other.
        assert scalar(
            conn,
            "WITH p AS (SELECT geo_key,"
            " max(value) FILTER (WHERE indicator_code='POP_TOTAL') t,"
            " max(value) FILTER (WHERE indicator_code='POP_MALE') m,"
            " max(value) FILTER (WHERE indicator_code='POP_FEMALE') f"
            " FROM fact_indicator_district GROUP BY 1)"
            " SELECT count(*) FROM p WHERE abs(t-(m+f)) > 0.5",
        ) == 0

    def test_national_population_matches_the_published_census_total(self, conn):
        # 6,109,903 is the figure the INE publishes for Paraguay in 2022.
        # Summing the districts must reproduce it exactly.
        total = scalar(
            conn,
            "SELECT sum(value) FROM fact_indicator_district "
            "WHERE indicator_code = 'POP_TOTAL'",
        )
        assert int(total) == 6_109_903


class TestNationalFactClaims:
    def test_series_reach_at_least_2024(self, conn):
        assert scalar(conn, "SELECT max(year) FROM fact_indicator_national") >= 2024

    def test_gini_is_the_sparse_series(self, conn):
        span = conn.execute(
            "SELECT max(year)-min(year)+1, count(*) FROM fact_indicator_national "
            "WHERE indicator_code = 'SI.POV.GINI'"
        ).fetchone()
        assert span[0] - span[1] == 7  # seven missing years

    def test_central_government_debt_has_no_observations(self, conn):
        # Configured, listed by the World Bank for Paraguay, and null for every
        # year. Kept in the warehouse's dimension precisely so this is visible.
        assert scalar(
            conn,
            "SELECT count(*) FROM fact_indicator_national "
            "WHERE indicator_code = 'GC.DOD.TOTL.GD.ZS'",
        ) == 0
        assert scalar(
            conn,
            "SELECT count(*) FROM dim_indicator "
            "WHERE indicator_code = 'GC.DOD.TOTL.GD.ZS'",
        ) == 1


class TestQualityFacts:
    def test_the_suite_ran_and_was_recorded(self, conn):
        assert scalar(conn, "SELECT count(*) FROM fact_quality_check") >= 25

    def test_no_blocking_failure_in_the_latest_run(self, conn):
        assert scalar(
            conn,
            "SELECT count(*) FROM fact_quality_check q "
            "JOIN (SELECT run_id FROM dim_run ORDER BY started_at DESC LIMIT 1) r "
            "  USING (run_id) "
            "WHERE q.status = 'fail' AND q.severity = 'error'",
        ) == 0

    def test_every_check_family_is_exercised(self, conn):
        families = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT check_family FROM fact_quality_check"
            ).fetchall()
        }
        assert {
            "completeness", "uniqueness", "range",
            "referential", "freshness", "conformity",
        } <= families

    def test_provenance_is_recorded_for_every_source(self, conn):
        assert scalar(conn, "SELECT count(*) FROM dim_source") > 20
        assert scalar(
            conn, "SELECT count(*) FROM dim_source WHERE sha256 IS NULL"
        ) == 0
