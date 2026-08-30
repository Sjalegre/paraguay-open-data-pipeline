-- Analytical queries over the warehouse.
--
-- Run any of these against data/warehouse.duckdb:
--     duckdb data/warehouse.duckdb -c ".read sql/analytics.sql"
-- or open the file in the DuckDB CLI and run them one at a time.
--
-- These are the queries the dashboard is built on. They are kept here rather
-- than embedded in the report generator so the SQL is reviewable on its own.


-- ---------------------------------------------------------------------------
-- 1. Macro trajectory: how Paraguay compares against its own history.
--    Each indicator is expressed as a percentile of its own 1990-2025 range,
--    which makes series in different units comparable on one axis.
-- ---------------------------------------------------------------------------
WITH bounds AS (
    SELECT indicator_code, min(value) AS lo, max(value) AS hi
    FROM fact_indicator_national
    GROUP BY 1
)
SELECT
    f.year,
    d.indicator_name,
    d.unit,
    round(f.value, 2) AS value,
    round(100.0 * (f.value - b.lo) / nullif(b.hi - b.lo, 0), 1) AS pct_of_own_range
FROM fact_indicator_national f
JOIN dim_indicator d USING (indicator_code)
JOIN bounds        b USING (indicator_code)
WHERE f.year >= 2015
ORDER BY d.indicator_name, f.year;


-- ---------------------------------------------------------------------------
-- 2. Where deprivation concentrates: the districts with the highest share of
--    households under at least one unmet basic need, weighted by how many
--    households that actually represents.
-- ---------------------------------------------------------------------------
SELECT
    g.department_name,
    g.district_name,
    round(nbi.value, 1)                              AS pct_households_with_nbi,
    cast(households.value AS BIGINT)                 AS households,
    cast(round(households.value * nbi.value / 100) AS BIGINT)
                                                     AS households_affected,
    cast(pop.value AS BIGINT)                        AS population_2022
FROM dim_geography g
JOIN fact_indicator_district nbi
  ON nbi.geo_key = g.geo_key AND nbi.indicator_code = 'NBI_ANY_PCT'
JOIN fact_indicator_district households
  ON households.geo_key = g.geo_key AND households.indicator_code = 'NBI_HOUSEHOLDS'
JOIN fact_indicator_district pop
  ON pop.geo_key = g.geo_key AND pop.indicator_code = 'POP_TOTAL'
ORDER BY households_affected DESC
LIMIT 25;


-- ---------------------------------------------------------------------------
-- 3. Which dimension of deprivation dominates in each department.
--    The four NBI components do not move together: a department can be poor
--    in sanitation and adequate in education, and averaging them hides that.
-- ---------------------------------------------------------------------------
SELECT
    g.department_name,
    count(*)                                                    AS districts,
    round(avg(CASE WHEN f.indicator_code = 'NBI_HOUSING_PCT'     THEN f.value END), 1) AS housing,
    round(avg(CASE WHEN f.indicator_code = 'NBI_SANITATION_PCT'  THEN f.value END), 1) AS sanitation,
    round(avg(CASE WHEN f.indicator_code = 'NBI_EDUCATION_PCT'   THEN f.value END), 1) AS education,
    round(avg(CASE WHEN f.indicator_code = 'NBI_SUBSISTENCE_PCT' THEN f.value END), 1) AS subsistence
FROM dim_geography g
JOIN fact_indicator_district f ON f.geo_key = g.geo_key
WHERE f.indicator_code LIKE 'NBI_%_PCT'
GROUP BY 1
ORDER BY sanitation DESC;


-- ---------------------------------------------------------------------------
-- 4. Demographic structure by department: median age against population, with
--    the sex ratio. The Chaco departments and Central sit at opposite ends.
-- ---------------------------------------------------------------------------
SELECT
    g.department_name,
    cast(sum(CASE WHEN f.indicator_code = 'POP_TOTAL'  THEN f.value END) AS BIGINT) AS population,
    round(sum(CASE WHEN f.indicator_code = 'POP_MALE'   THEN f.value END)
        / nullif(sum(CASE WHEN f.indicator_code = 'POP_FEMALE' THEN f.value END), 0), 3)
                                                                                    AS men_per_woman,
    round(avg(CASE WHEN f.indicator_code = 'AGE_MEDIAN' THEN f.value END), 1)        AS median_age_avg
FROM dim_geography g
JOIN fact_indicator_district f ON f.geo_key = g.geo_key
GROUP BY 1
ORDER BY population DESC;


-- ---------------------------------------------------------------------------
-- 5. The integration question the project set out to answer: how much of the
--    published district-level data is actually attachable to the official
--    geographic register, and what stood in the way.
-- ---------------------------------------------------------------------------
SELECT
    'districts in the 2022 register'      AS measure,
    count(*)                              AS value
FROM dim_geography
UNION ALL SELECT 'reconciled to the 2012 register',
    count(*) FROM dim_geography WHERE vintage_2012_key IS NOT NULL
UNION ALL SELECT '2012 keys published in a non-joinable form',
    count(*) FROM dim_geography
    WHERE vintage_2012_key IS NOT NULL
      AND NOT regexp_matches(vintage_2012_key, '^\d{4}$')
UNION ALL SELECT 'districts created after 2012',
    count(*) FROM dim_geography WHERE created_after_2012
UNION ALL SELECT 'districts renamed since 2012',
    count(*) FROM dim_geography WHERE name_changed_since_2012
UNION ALL SELECT 'district names carrying encoding damage',
    count(*) FROM dim_geography WHERE encoding_damage <> ''
UNION ALL SELECT 'districts with a population figure loaded',
    count(DISTINCT geo_key) FROM fact_indicator_district
    WHERE indicator_code = 'POP_TOTAL'
UNION ALL SELECT 'district facts orphaned from the dimension',
    count(*) FROM fact_indicator_district f
    LEFT JOIN dim_geography d USING (geo_key) WHERE d.geo_key IS NULL;


-- ---------------------------------------------------------------------------
-- 6. Quality over time. Because check results are a fact table rather than a
--    log, the trend is a plain aggregation -- this is what the dashboard's
--    quality page is built on.
-- ---------------------------------------------------------------------------
SELECT
    r.started_at,
    q.check_family,
    count(*)                                          AS checks,
    count(*) FILTER (WHERE q.status = 'fail')         AS failed,
    count(*) FILTER (WHERE q.status = 'fail'
                       AND q.severity = 'error')      AS blocking
FROM fact_quality_check q
JOIN dim_run r USING (run_id)
GROUP BY 1, 2
ORDER BY r.started_at DESC, q.check_family;


-- ---------------------------------------------------------------------------
-- 7. Full provenance for any figure: which URL it came from, when, and the
--    checksum of the payload it was parsed out of.
-- ---------------------------------------------------------------------------
SELECT DISTINCT
    f.indicator_code,
    s.source_key,
    s.url,
    s.fetched_at,
    s.declared_format,
    s.served_format,
    substr(s.sha256, 1, 16) AS sha256_prefix
FROM fact_indicator_district f
JOIN dim_source s ON s.source_key = f.source_key AND s.run_id = f.run_id
ORDER BY f.indicator_code, s.source_key;
