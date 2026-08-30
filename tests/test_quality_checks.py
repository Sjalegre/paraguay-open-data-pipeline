"""Tests for the data-quality primitives.

Each check is exercised against a warehouse built in memory, in both the state
it should pass and the state it should fail, because a check that cannot fail is
worse than no check at all.
"""

from __future__ import annotations

import duckdb
import pytest

from pypy_pipeline.quality.checks import (
    ERROR,
    WARNING,
    Conformity,
    DimensionCoverage,
    Freshness,
    NotNull,
    Range,
    ReferentialIntegrity,
    RowCount,
    SqlCheck,
    Unique,
)
from pypy_pipeline.quality.suite import run_suite, summarise


@pytest.fixture
def conn():
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE dim_geography ("
        "  geo_key VARCHAR, district_name VARCHAR, vintage_2012_key VARCHAR)"
    )
    connection.execute(
        "INSERT INTO dim_geography VALUES"
        " ('0000', 'ASUNCION', '0'),"
        " ('0101', 'CONCEPCION', '101'),"
        " ('0112', 'ARROYITO', NULL)"
    )
    connection.execute(
        "CREATE TABLE facts ("
        "  geo_key VARCHAR, indicator_code VARCHAR, year INTEGER, value DOUBLE)"
    )
    connection.execute(
        "INSERT INTO facts VALUES"
        " ('0000', 'POP_TOTAL', 2022, 462241),"
        " ('0101', 'POP_TOTAL', 2022, 73360),"
        " ('0112', 'POP_TOTAL', 2022, 8292),"
        " ('0000', 'NBI_ANY_PCT', 2022, 17.4)"
    )
    yield connection
    connection.close()


class TestRowCount:
    def test_passes_on_the_expected_count(self, conn):
        result = RowCount("t", "dim_geography", expected=3).run(conn)
        assert result.status == "pass"
        assert result.observed == "3"

    def test_fails_and_reports_the_shortfall(self, conn):
        result = RowCount("t", "dim_geography", expected=263).run(conn)
        assert result.status == "fail"
        assert result.failing_rows == 260


class TestUnique:
    def test_passes_when_the_grain_holds(self, conn):
        assert Unique("t", "facts", ("geo_key", "indicator_code", "year")).run(
            conn
        ).status == "pass"

    def test_detects_a_duplicated_grain(self, conn):
        conn.execute("INSERT INTO facts VALUES ('0000', 'POP_TOTAL', 2022, 99)")
        result = Unique("t", "facts", ("geo_key", "indicator_code", "year")).run(conn)
        assert result.status == "fail"
        assert result.failing_rows == 1


class TestNotNull:
    def test_fails_when_nulls_exceed_the_threshold(self, conn):
        result = NotNull("t", "dim_geography", "vintage_2012_key").run(conn)
        assert result.status == "fail"
        assert result.failing_rows == 1

    def test_tolerates_nulls_under_the_threshold(self, conn):
        result = NotNull(
            "t", "dim_geography", "vintage_2012_key", max_null_ratio=0.5
        ).run(conn)
        assert result.status == "pass"


class TestReferentialIntegrity:
    def test_passes_when_every_key_resolves(self, conn):
        assert ReferentialIntegrity(
            "t", "facts", "geo_key", "dim_geography", "geo_key"
        ).run(conn).status == "pass"

    def test_catches_an_orphan_fact(self, conn):
        # This is the failure mode a name-matched load produces: a district
        # name that normalised to something the dimension does not contain.
        conn.execute("INSERT INTO facts VALUES ('9999', 'POP_TOTAL', 2022, 1)")
        result = ReferentialIntegrity(
            "t", "facts", "geo_key", "dim_geography", "geo_key"
        ).run(conn)
        assert result.status == "fail"
        assert result.failing_rows == 1


class TestDimensionCoverage:
    def test_passes_at_full_coverage(self, conn):
        assert DimensionCoverage(
            "t", "facts", "geo_key", "dim_geography", "geo_key",
            where="indicator_code = 'POP_TOTAL'",
        ).run(conn).status == "pass"

    def test_catches_a_partially_loaded_source(self, conn):
        # Referential integrity would pass here: every key present is valid.
        # Only coverage notices that two thirds of the country is missing.
        result = DimensionCoverage(
            "t", "facts", "geo_key", "dim_geography", "geo_key",
            where="indicator_code = 'NBI_ANY_PCT'",
        ).run(conn)
        assert result.status == "fail"
        assert result.observed.startswith("33.3%")


class TestRange:
    def test_passes_inside_the_interval(self, conn):
        assert Range(
            "t", "facts", "value", 0, 100, where="indicator_code = 'NBI_ANY_PCT'"
        ).run(conn).status == "pass"

    def test_catches_a_percentage_outside_bounds(self, conn):
        conn.execute("INSERT INTO facts VALUES ('0101', 'NBI_ANY_PCT', 2022, 130.0)")
        result = Range(
            "t", "facts", "value", 0, 100, where="indicator_code = 'NBI_ANY_PCT'"
        ).run(conn)
        assert result.status == "fail"
        assert result.failing_rows == 1


class TestConformity:
    def test_passes_on_the_canonical_key_format(self, conn):
        assert Conformity(
            "t", "dim_geography", "geo_key", r"^\d{4}$"
        ).run(conn).status == "pass"

    def test_catches_the_unpadded_published_key(self, conn):
        # The 2012 register publishes '0' and '101' where the canonical form is
        # '0000' and '0101'.
        result = Conformity(
            "t", "dim_geography", "vintage_2012_key", r"^\d{4}$"
        ).run(conn)
        assert result.status == "fail"
        assert result.failing_rows == 2


class TestFreshness:
    def test_tolerates_the_decennial_census_lag(self, conn):
        assert Freshness(
            "t", "facts", "year", max_age_years=12, reference_year=2026
        ).run(conn).status == "pass"

    def test_flags_a_series_that_stopped_updating(self, conn):
        result = Freshness(
            "t", "facts", "year", max_age_years=2, reference_year=2026
        ).run(conn)
        assert result.status == "fail"
        assert "4 years behind" in result.observed


class TestSuiteRunner:
    def test_a_check_that_raises_is_reported_not_propagated(self, conn):
        broken = SqlCheck(
            "broken", "conformity", "facts",
            sql="SELECT * FROM table_that_does_not_exist",
            expected="never",
        )
        good = RowCount("good", "facts", expected=4)
        results = run_suite(conn, [broken, good])
        assert len(results) == 2
        assert results[0].status == "fail"
        assert results[0].check_family == "internal"
        # The important half: the broken check did not stop the good one.
        assert results[1].status == "pass"

    def test_summary_counts_blocking_failures_separately(self, conn):
        results = run_suite(conn, [
            RowCount("hard", "facts", expected=999, severity=ERROR),
            RowCount("soft", "facts", expected=999, severity=WARNING),
        ])
        summary = summarise(results)
        assert summary["failed"] == 2
        assert summary["blocking"] == 1
