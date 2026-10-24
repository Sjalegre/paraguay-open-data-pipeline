# The SQL layer

A dbt project over the warehouse the Python pipeline builds. Seven models and
28 tests, all in SQL.

```bash
python -m pypy_pipeline build          # build the warehouse first
cd dbt
dbt deps  --profiles-dir .
dbt build --profiles-dir .             # 7 models, 28 tests
```

`--profiles-dir .` makes dbt read the `profiles.yml` in this folder rather than
one in your home directory; the path inside it points at
`../data/warehouse.duckdb`.

## Why there is a SQL layer at all

The Python pipeline owns **ingestion**: fetching, splicing keys, repairing
encodings, loading the star schema. dbt owns everything **derived** from it. The
split is deliberate — ingestion needs a real programming language because the
problems are string normalisation and HTTP, and the analytical layer does not,
because the problems are joins and aggregations.

dbt reads the pipeline's tables as sources and never writes to them.

## The tests are a second opinion

The 28 tests here restate constraints the Python suite in
`src/pypy_pipeline/quality/` already enforces. **That duplication is the
point.**

Two independent implementations of the same rule, written against different
representations of the data, are stronger evidence than one. If they ever
disagree, one of them is wrong, and finding out which is a result rather than a
maintenance cost. A single implementation can only tell you that it agrees with
itself.

Five of them are singular tests — assertions SQL can express and a generic test
cannot:

| Test | What it catches |
| --- | --- |
| `assert_population_matches_published_census` | Summing the districts must give **6,109,903**, the figure the INE published for 2022. Passes only if name matching, es-PY number parsing and the indentation reader are *all* correct. |
| `assert_sexes_reconcile` | Male + female must rebuild the total. Catches a thousands-separator misparse that every range check lets through, because `73.360` read as `73.36` is plausible on its own. |
| `assert_district_count` | Exactly 263 districts, as the 2022 census defines them. |
| `assert_geo_key_is_padded` | Every key is four digits. This is the defect the project exists to absorb; a regression in the padding would surface downstream as mysteriously missing districts. |
| `assert_department_totals_match_districts` | The department mart must sum to the district fact. An aggregation that silently drops rows is invisible in the output and shows up here. |

## The models

```
sources (owned by the Python pipeline)
   dim_geography · dim_indicator · dim_source · dim_run
   fact_indicator_district · fact_indicator_national · fact_quality_check
        │
staging (views — renaming and typing only)
   stg_geography · stg_district_indicators · stg_national_indicators
        │
marts (tables — what Power BI and the report read)
   fct_district_profile         one row per district per year, pivoted wide
   mart_department_summary      household-weighted department aggregates
   mart_deprivation_ranking     ranked by households affected and by rate
   mart_quality_history         check results per run, for the quality page
```

**`fct_district_profile`** pivots the long fact table wide. The warehouse stores
observations long so indicators can be added without a schema change; a reader
and a BI tool both want them wide. Keeping the pivot in the mart leaves the
fact table general.

**`mart_department_summary`** weights the NBI rate by households rather than
averaging across districts, and publishes both figures side by side so the gap
is visible. It is not small: in Ñeembucú the unweighted average is **43.3%** and
the household-weighted rate is **34.0%** — nine points, from giving a district
of 900 households the same say as one of 90,000.

**`mart_deprivation_ranking`** ranks districts two ways. Ciudad del Este is
**1st** by households affected and **199th** by rate. Neither ranking is wrong;
publishing only one of them is. It also names which of the four NBI components
binds in each district, because a department short on sanitation needs a
different answer from one short on education, and an average of the four hides
that.

## Output

The marts land in the same DuckDB file, so
`python -m pypy_pipeline export` picks them up for Power BI alongside the base
tables.
