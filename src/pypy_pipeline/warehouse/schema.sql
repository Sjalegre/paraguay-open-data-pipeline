-- Star schema for the Paraguayan public-data warehouse.
--
-- Two fact tables carry observations at the two grains the sources publish at,
-- and a third carries the result of every quality check the pipeline runs. The
-- third is not instrumentation bolted on the side: it is a fact table with its
-- own grain (one run, one check, one target), so quality is queryable and
-- comparable across runs in exactly the same way the data is.

DROP TABLE IF EXISTS fact_indicator_district;
DROP TABLE IF EXISTS fact_indicator_national;
DROP TABLE IF EXISTS fact_quality_check;
DROP TABLE IF EXISTS dim_indicator;
DROP TABLE IF EXISTS dim_geography;
DROP TABLE IF EXISTS dim_source;
DROP TABLE IF EXISTS dim_run;

-- One row per pipeline execution. Everything else is dated against this.
CREATE TABLE dim_run (
    run_id           VARCHAR PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    pipeline_version VARCHAR NOT NULL,
    used_cache       BOOLEAN NOT NULL DEFAULT FALSE
);

-- One row per retrieval. `declared_format` is what the catalogue said the
-- resource was; `served_format` is what the server actually returned. Keeping
-- both is what makes the conformity check possible.
CREATE TABLE dim_source (
    source_id       VARCHAR PRIMARY KEY,
    run_id          VARCHAR NOT NULL,
    source_key      VARCHAR NOT NULL,
    url             VARCHAR NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL,
    http_status     INTEGER NOT NULL,
    content_type    VARCHAR,
    content_length  BIGINT,
    sha256          VARCHAR NOT NULL,
    declared_format VARCHAR,
    served_format   VARCHAR,
    format_matches  BOOLEAN,
    from_cache      BOOLEAN NOT NULL DEFAULT FALSE
);

-- The geographic dimension, at district grain, on the CNPV 2022 vintage.
-- The 2012 columns are the crosswalk, kept in the dimension rather than in a
-- separate bridge table because the relationship is one-to-one where it exists
-- at all.
CREATE TABLE dim_geography (
    geo_key                  VARCHAR PRIMARY KEY,  -- 'DDdd', zero-padded
    department_code          VARCHAR NOT NULL,
    department_name          VARCHAR NOT NULL,
    district_name            VARCHAR NOT NULL,     -- official 2022 spelling
    district_name_normalised VARCHAR NOT NULL,     -- join key for named sources
    vintage_2012_key         VARCHAR,              -- as published, unpadded
    name_2012                VARCHAR,
    name_changed_since_2012  BOOLEAN NOT NULL,
    created_after_2012       BOOLEAN NOT NULL,
    encoding_damage          VARCHAR               -- comma-separated, '' if clean
);

CREATE TABLE dim_indicator (
    indicator_code       VARCHAR PRIMARY KEY,
    indicator_name       VARCHAR NOT NULL,
    unit                 VARCHAR,
    grain                VARCHAR NOT NULL,   -- 'national' | 'district'
    publisher            VARCHAR NOT NULL,
    expect_complete_from INTEGER
);

CREATE TABLE fact_indicator_national (
    indicator_code VARCHAR NOT NULL,
    year           INTEGER NOT NULL,
    value          DOUBLE  NOT NULL,
    run_id         VARCHAR NOT NULL,
    source_key     VARCHAR NOT NULL,
    PRIMARY KEY (indicator_code, year)
);

CREATE TABLE fact_indicator_district (
    geo_key        VARCHAR NOT NULL,
    indicator_code VARCHAR NOT NULL,
    year           INTEGER NOT NULL,
    value          DOUBLE  NOT NULL,
    run_id         VARCHAR NOT NULL,
    source_key     VARCHAR NOT NULL,
    PRIMARY KEY (geo_key, indicator_code, year)
);

-- One row per check, per target, per run.
CREATE TABLE fact_quality_check (
    run_id        VARCHAR NOT NULL,
    check_id      VARCHAR NOT NULL,
    check_family  VARCHAR NOT NULL,   -- completeness | uniqueness | range |
                                      -- referential | freshness | conformity
    target_table  VARCHAR NOT NULL,
    target_column VARCHAR,
    severity      VARCHAR NOT NULL,   -- error | warning | info
    status        VARCHAR NOT NULL,   -- pass | fail
    expected      VARCHAR,
    observed      VARCHAR,
    failing_rows  BIGINT,
    detail        VARCHAR,
    checked_at    TIMESTAMPTZ NOT NULL
);
