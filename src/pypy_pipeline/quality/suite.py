"""The check suite this warehouse is held to, and the runner that executes it.

Each check below exists because of something observed in the sources, not
because it is a generic best practice. The comments say which.
"""

from __future__ import annotations

from datetime import date

from .checks import (
    COMPLETENESS,
    CONFORMITY,
    ERROR,
    INFO,
    WARNING,
    CheckResult,
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

# The 2022 census defines the territory as 263 districts. Anything else means
# the GeoJSON changed shape or the load truncated, and both are worth failing on.
EXPECTED_DISTRICTS = 263
# 250 of them existed in 2012; the other 13 were created afterwards.
EXPECTED_2012_CROSSWALK = 250

_PERCENT_INDICATORS = (
    "'NBI_ANY_PCT','NBI_HOUSING_PCT','NBI_SANITATION_PCT',"
    "'NBI_EDUCATION_PCT','NBI_SUBSISTENCE_PCT'"
)


def build_suite(reference_year: int | None = None) -> list:
    year = reference_year or date.today().year
    return [
        # ---- dim_geography --------------------------------------------------
        RowCount(
            "geo.row_count", "dim_geography", expected=EXPECTED_DISTRICTS,
            detail="districts defined by CNPV 2022",
        ),
        Unique("geo.key_unique", "dim_geography", ("geo_key",)),
        # The reason the two vintages do not join as published: the 2022 key is
        # zero-padded to four digits and the 2012 key is not. This check pins
        # the canonical form so a regression in `_pad_key` cannot pass silently.
        Conformity(
            "geo.key_format", "dim_geography", "geo_key", r"^\d{4}$",
            detail="canonical DDdd key, zero-padded",
        ),
        Conformity(
            "geo.department_code_format", "dim_geography", "department_code",
            r"^\d{2}$",
        ),
        NotNull("geo.district_name_present", "dim_geography", "district_name"),
        # Informational, not a failure: it measures how far the published 2012
        # key is from a joinable form. Most of them are 1 to 3 digits.
        SqlCheck(
            "geo.published_2012_key_unpadded", CONFORMITY, "dim_geography",
            sql=(
                "SELECT count(*) FILTER (WHERE NOT regexp_matches(vintage_2012_key, "
                "'^\\d{4}$')), "
                "concat(count(*) FILTER (WHERE NOT regexp_matches(vintage_2012_key, "
                "'^\\d{4}$')), ' of ', count(*), ' published unpadded') "
                "FROM dim_geography WHERE vintage_2012_key IS NOT NULL"
            ),
            expected="published 2012 keys already in DDdd form",
            severity=INFO, column="vintage_2012_key",
            detail="a direct join on the published keys matches nothing",
        ),
        SqlCheck(
            "geo.crosswalk_coverage", COMPLETENESS, "dim_geography",
            sql=(
                f"SELECT {EXPECTED_2012_CROSSWALK} - count(*) FILTER "
                "(WHERE vintage_2012_key IS NOT NULL), "
                "concat(count(*) FILTER (WHERE vintage_2012_key IS NOT NULL), "
                "' of ', count(*), ' districts reconcile to the 2012 register') "
                "FROM dim_geography"
            ),
            expected=f"{EXPECTED_2012_CROSSWALK} districts crosswalk to 2012",
            severity=ERROR,
        ),
        # Expected to FAIL. Three district names in the CNPV 2022 GeoJSON carry
        # encoding damage that arrived that way: one recoverable mojibake and
        # two whose enye was replaced upstream by a literal '?'. Recorded as a
        # warning so the run continues and the defect stays visible instead of
        # being silently normalised away.
        SqlCheck(
            "geo.encoding_damage", CONFORMITY, "dim_geography",
            sql=(
                "SELECT count(*), concat(count(*), ' district names arrived damaged') "
                "FROM dim_geography WHERE encoding_damage <> ''"
            ),
            expected="0 names with encoding damage",
            severity=WARNING, column="district_name",
            detail="known defect in the published GeoJSON, not introduced here",
        ),

        # ---- dim_source -----------------------------------------------------
        SqlCheck(
            "source.all_retrievals_ok", COMPLETENESS, "dim_source",
            sql=(
                "SELECT count(*), concat(count(*), ' non-200 retrievals') "
                "FROM dim_source WHERE http_status <> 200"
            ),
            expected="every source returned HTTP 200",
            severity=ERROR,
        ),
        # The catalogue spreads one format across `xlsx`, `excel` and `xls`, and
        # a handful of resources are served as HTML despite being declared as
        # data. Only the resources this pipeline actually reads are checked.
        SqlCheck(
            "source.declared_format_matches_served", CONFORMITY, "dim_source",
            sql=(
                "SELECT count(*), concat(count(*), ' resources served as something "
                "other than their declared format') "
                "FROM dim_source WHERE format_matches = FALSE"
            ),
            expected="declared format matches served content type",
            severity=WARNING, column="declared_format",
        ),

        # ---- fact_indicator_national ---------------------------------------
        Unique(
            "national.grain_unique", "fact_indicator_national",
            ("indicator_code", "year"),
        ),
        NotNull("national.value_present", "fact_indicator_national", "value"),
        ReferentialIntegrity(
            "national.indicator_fk", "fact_indicator_national", "indicator_code",
            "dim_indicator", "indicator_code",
        ),
        Range(
            "national.year_plausible", "fact_indicator_national", "year",
            1990, year + 1,
        ),
        Range(
            "national.unemployment_is_a_percentage", "fact_indicator_national",
            "value", 0, 100, where="indicator_code = 'SL.UEM.TOTL.ZS'",
        ),
        Freshness(
            "national.freshness", "fact_indicator_national", "year",
            max_age_years=2, reference_year=year,
        ),
        # Measures the gaps the World Bank leaves in series Paraguay is
        # nominally covered by -- the Gini index is missing 7 of its 35 years.
        SqlCheck(
            "national.series_gaps", COMPLETENESS, "fact_indicator_national",
            sql=(
                "WITH span AS ("
                "  SELECT f.indicator_code, min(f.year) lo, max(f.year) hi,"
                "         count(*) n"
                "  FROM fact_indicator_national f"
                "  JOIN dim_indicator d USING (indicator_code)"
                "  WHERE d.expect_complete_from IS NOT NULL"
                "  GROUP BY 1)"
                " SELECT coalesce(sum(hi - lo + 1 - n), 0),"
                "        concat(coalesce(sum(hi - lo + 1 - n), 0),"
                "               ' missing years across ', count(*), ' series')"
                " FROM span"
            ),
            expected="no interior gaps in series declared complete",
            severity=WARNING,
        ),
        # The counterpart measurement. The check above only polices series the
        # config declares dense; this one reports the gaps in every series,
        # including the ones known to be sparse, so the number is visible rather
        # than merely excused. The Gini index is the main contributor.
        SqlCheck(
            "national.sparse_series", COMPLETENESS, "fact_indicator_national",
            sql=(
                "WITH span AS ("
                "  SELECT indicator_code, min(year) lo, max(year) hi, count(*) n"
                "  FROM fact_indicator_national GROUP BY 1)"
                " SELECT count(*) FILTER (WHERE hi - lo + 1 > n),"
                "        concat(count(*) FILTER (WHERE hi - lo + 1 > n),"
                "               ' of ', count(*), ' series have interior gaps,"
                " totalling ', coalesce(sum(hi - lo + 1 - n), 0), ' missing years')"
                " FROM span"
            ),
            expected="every series dense between its first and last year",
            severity=INFO,
        ),
        # A series the World Bank lists for Paraguay but has no observation for
        # in any year. It loads as zero rows rather than as an error, so without
        # this check it would be indistinguishable from an indicator nobody
        # configured.
        SqlCheck(
            "national.indicators_with_no_data", COMPLETENESS, "dim_indicator",
            sql=(
                "SELECT count(*), concat(count(*), ' configured indicators returned"
                " no observations at all') "
                "FROM dim_indicator d WHERE d.grain = 'national' AND NOT EXISTS ("
                "  SELECT 1 FROM fact_indicator_national f"
                "  WHERE f.indicator_code = d.indicator_code)"
            ),
            expected="every configured indicator has at least one observation",
            severity=WARNING, column="indicator_code",
        ),

        # ---- fact_indicator_district ---------------------------------------
        Unique(
            "district.grain_unique", "fact_indicator_district",
            ("geo_key", "indicator_code", "year"),
        ),
        NotNull("district.value_present", "fact_indicator_district", "value"),
        # The headline check. Every district-level row was attached to the
        # dimension by matching a name, because none of the source files carry a
        # district code. If normalisation is wrong, this is where it shows.
        ReferentialIntegrity(
            "district.geography_fk", "fact_indicator_district", "geo_key",
            "dim_geography", "geo_key",
        ),
        ReferentialIntegrity(
            "district.indicator_fk", "fact_indicator_district", "indicator_code",
            "dim_indicator", "indicator_code",
        ),
        # The other direction: a name-matched load can be referentially valid
        # and still be missing half the country.
        DimensionCoverage(
            "district.population_covers_country", "fact_indicator_district",
            "geo_key", "dim_geography", "geo_key", min_ratio=1.0,
            where="indicator_code = 'POP_TOTAL'", severity=ERROR,
        ),
        DimensionCoverage(
            "district.nbi_covers_country", "fact_indicator_district",
            "geo_key", "dim_geography", "geo_key", min_ratio=1.0,
            where="indicator_code = 'NBI_ANY_PCT'", severity=ERROR,
        ),
        Range(
            "district.percentages_in_bounds", "fact_indicator_district", "value",
            0, 100, where=f"indicator_code IN ({_PERCENT_INDICATORS})",
        ),
        Range(
            "district.population_positive", "fact_indicator_district", "value",
            1, 2_000_000, where="indicator_code = 'POP_TOTAL'",
        ),
        Range(
            "district.median_age_plausible", "fact_indicator_district", "value",
            10, 60, where="indicator_code = 'AGE_MEDIAN'",
        ),
        # Internal consistency: the two sex counts must reconstruct the total.
        # This catches a thousands-separator misparse that every range check
        # above would happily let through.
        SqlCheck(
            "district.sexes_sum_to_total", "consistency", "fact_indicator_district",
            sql=(
                "WITH p AS ("
                "  SELECT geo_key,"
                "         max(value) FILTER (WHERE indicator_code = 'POP_TOTAL')  AS total,"
                "         max(value) FILTER (WHERE indicator_code = 'POP_MALE')   AS male,"
                "         max(value) FILTER (WHERE indicator_code = 'POP_FEMALE') AS female"
                "  FROM fact_indicator_district GROUP BY 1)"
                " SELECT count(*) FILTER (WHERE abs(total - (male + female)) > 0.5),"
                "        concat(count(*) FILTER (WHERE abs(total - (male + female)) > 0.5),"
                "               ' of ', count(*), ' districts do not reconcile')"
                " FROM p WHERE total IS NOT NULL AND male IS NOT NULL"
                "   AND female IS NOT NULL"
            ),
            expected="male + female = total for every district",
            severity=ERROR,
        ),
        # The census is decennial, so a decade of lag is correct, not stale.
        # Setting this to the same tolerance as the World Bank series would
        # produce a failure that means nothing.
        Freshness(
            "district.freshness", "fact_indicator_district", "year",
            max_age_years=12, reference_year=year,
        ),
    ]


def run_suite(conn, checks) -> list[CheckResult]:
    """Execute every check, letting each one's failure stay local.

    A check that raises is itself reported as a failure rather than aborting the
    suite: a broken check should not be able to hide the results of the others.
    """
    results: list[CheckResult] = []
    for check in checks:
        try:
            results.append(check.run(conn))
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            from datetime import datetime, timezone

            results.append(
                CheckResult(
                    check_id=getattr(check, "check_id", type(check).__name__),
                    check_family="internal",
                    target_table=getattr(check, "table", "?"),
                    target_column=None,
                    severity=ERROR,
                    status="fail",
                    expected="check executes",
                    observed=f"{type(exc).__name__}: {exc}",
                    failing_rows=1,
                    detail="the check itself failed to run",
                    checked_at=datetime.now(timezone.utc),
                )
            )
    return results


def summarise(results: list[CheckResult]) -> dict:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if not r.failed),
        "failed": sum(1 for r in results if r.failed),
        "blocking": sum(1 for r in results if r.blocking),
        "by_family": {
            family: {
                "total": sum(1 for r in results if r.check_family == family),
                "failed": sum(
                    1 for r in results if r.check_family == family and r.failed
                ),
            }
            for family in sorted({r.check_family for r in results})
        },
    }
