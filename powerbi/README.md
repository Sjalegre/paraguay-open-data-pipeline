# Power BI model

The pipeline does not ship a `.pbix`. A `.pbix` is a binary Power BI Desktop
artefact, it cannot be generated from Python, and a checked-in binary that
nobody can diff is the opposite of what the rest of this repository is for.

What is here instead is everything needed to build the model in about ten
minutes, plus the exports it reads. The star schema is already the shape Power
BI wants, so there is no reshaping to do.

## 1. Produce the exports

```bash
python -m pypy_pipeline build
python -m pypy_pipeline export
```

This writes `data/exports/` as both Parquet and CSV:

| File | Role | Rows |
| --- | --- | --- |
| `dim_geography` | district dimension, with the 2012 crosswalk | 263 |
| `dim_indicator` | indicator dimension, both grains | 22 |
| `dim_source` | one row per retrieval: URL, checksum, formats | ~78 |
| `dim_run` | one row per pipeline execution | 1 per run |
| `fact_indicator_national` | indicator x year | ~407 |
| `fact_indicator_district` | district x indicator x year | ~2,630 |
| `fact_quality_check` | run x check x target | ~30 per run |

## 2. Load

**Get Data → Parquet**, and point at the files in `data/exports/`. Prefer the
Parquet over the CSV: it carries types, so `year` arrives as an integer and
`value` as a decimal without a type-conversion step, and it avoids any question
about how the CSV writer quoted the district names.

## 3. Relationships

All single-direction, many-to-one from fact to dimension:

```
fact_indicator_district [geo_key]        -> dim_geography  [geo_key]
fact_indicator_district [indicator_code] -> dim_indicator  [indicator_code]
fact_indicator_national [indicator_code] -> dim_indicator  [indicator_code]
fact_quality_check      [run_id]         -> dim_run        [run_id]
dim_source              [run_id]         -> dim_run        [run_id]
```

`dim_geography[geo_key]` is text, not a number. Keep it that way — the leading
zero is the whole point, and letting Power BI type it as a whole number
reintroduces exactly the defect the pipeline exists to fix.

Mark `dim_geography[district_name]` with **Data category → Place** and
`department_name` likewise, so the map visuals resolve without a latitude
column.

## 4. Measures

The fact tables are in long format, so most measures filter on
`indicator_code`. Written this way rather than as a pivoted table because the
indicator list grows without a schema change.

```dax
Population 2022 =
CALCULATE(
    SUM( fact_indicator_district[value] ),
    dim_indicator[indicator_code] = "POP_TOTAL"
)

Households =
CALCULATE(
    SUM( fact_indicator_district[value] ),
    dim_indicator[indicator_code] = "NBI_HOUSEHOLDS"
)

NBI rate =
CALCULATE(
    AVERAGE( fact_indicator_district[value] ),
    dim_indicator[indicator_code] = "NBI_ANY_PCT"
)

-- Population-weighted, so a department is not the unweighted mean of its
-- districts. Ciudad del Este and a Chaco district of 900 people should not
-- carry the same weight.
NBI rate (weighted) =
DIVIDE(
    SUMX( VALUES( dim_geography[geo_key] ), [Households] * [NBI rate] ),
    SUMX( VALUES( dim_geography[geo_key] ), [Households] )
)

Households affected =
SUMX(
    VALUES( dim_geography[geo_key] ),
    [Households] * [NBI rate] / 100
)

Checks failed =
CALCULATE(
    COUNTROWS( fact_quality_check ),
    fact_quality_check[status] = "fail"
)

Blocking failures =
CALCULATE(
    COUNTROWS( fact_quality_check ),
    fact_quality_check[status] = "fail",
    fact_quality_check[severity] = "error"
)

Districts reconciled to 2012 =
CALCULATE(
    COUNTROWS( dim_geography ),
    NOT ISBLANK( dim_geography[vintage_2012_key] )
)
```

## 5. Pages

Three pages, matching the three questions in the README:

1. **Macro** — line chart of `fact_indicator_national` by year, sliced by
   `dim_indicator[indicator_name]`. Add a card for the latest year per series so
   the gaps in the Gini index are visible rather than interpolated away.
2. **Territory** — filled map on `dim_geography[district_name]` coloured by
   `[NBI rate]`, with a table ranked by `[Households affected]`. Slice by
   `department_name`.
3. **Quality** — `fact_quality_check` as a matrix of `check_family` against
   `status`, with `[Blocking failures]` as a KPI card, and a table of the failing
   checks showing `expected` against `observed`.

The third page is the one worth keeping. A dashboard that shows the data without
showing what was verified about it is asking to be trusted rather than earning
it.

## Refreshing

Re-run `build` then `export` and hit **Refresh** in Power BI. Because
`fact_quality_check` is keyed by `run_id` and `dim_run` keeps one row per
execution, the quality page shows the trend across runs rather than only the
latest state — provided the exports are appended rather than overwritten, which
is a change to `EXPORT_TABLES` handling in `cli.py` and is not implemented.
Today, each export overwrites the last.
