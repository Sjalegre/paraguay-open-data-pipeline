# Paraguay open data pipeline

An integration and data-quality pipeline for Paraguayan public statistics. It
pulls from a clean international API and a messy national catalogue, reconciles
them against the official geographic register, loads a star schema in DuckDB,
and runs 30 automated quality checks whose results are stored as facts and
published as a report.

The quality framework is the point of the project, not an accessory to it.
Anyone can move rows from A to B; the harder claim is that the rows are worth
believing, and that claim needs evidence a reader can check.

## Why this exists

I published an article in *Marketdata* called *"Desafíos en la Gestión
Estadística Paraguaya: Integrando los datos gubernamentales para un desarrollo
transparente"* — on the cost of Paraguay's fragmented public statistics. This
repository is that article executed. I argued the problem existed; here it is,
measured, with the integration actually performed.

## What it found

Everything below is produced by the pipeline on every run and asserted by the
test suite. Nothing is quoted from a one-off analysis.

**The two official district registers do not join to each other.** The 2012
register publishes its district key as an unpadded integer (`0`, `101`); the
2022 census publishes it zero-padded (`0000`, `0101`). A join on the keys as
published matches **nothing**. 160 of the 250 shared districts carry a key
shorter than four characters.

**After normalising the key form, the territory reconciles and the real changes
become visible:** 250 of 263 districts match, 13 were created after 2012, and 6
share a code but not a name across vintages.

**The 2022 geographic file contains three incompatible renderings of the letter
`Ñ`.** In the same file: one name where it survived as recoverable mojibake, and
two where it was replaced upstream by literal `?` characters and is gone for
good. The pipeline repairs what is recoverable and flags what is not — it never
guesses.

**No district-level statistical file carries a district code.** Population and
unmet-basic-needs tables identify districts by name only, in spellings that
match neither register exactly. All 263 districts attach to the dimension by
normalised name, with **no per-district exceptions and no fuzzy matching**.

**Even the well-behaved source has holes.** The World Bank lists central
government debt for Paraguay and has no observation for it in any year; the Gini
index is missing 7 of its 35. Both are loaded and reported rather than filtered
out.

**Half the national catalogue does not say who published it.** 229 of 434
datasets (52.8%) declare no publisher; the statistics office appears under two
different spellings of its own name.

Full detail, including the sources that turned out to be unusable, is in
[`docs/source-audit.md`](docs/source-audit.md).

## Architecture

```
                 World Bank WDI API            datos.gov.py (DKAN API)
                 12 indicators, PRY            434 datasets, 3,615 resources
                        │                                │
                        │                        ┌───────┴────────┐
                        │                   geography         statistics
                        │                 2012 CSV (cp1252)   census + NBI
                        │                 2022 GeoJSON        (names, no codes)
                        ▼                        ▼                ▼
                 ┌──────────────────────────────────────────────────────┐
                 │  fetch: provenance recorded per retrieval            │
                 │  URL · timestamp · HTTP status · SHA-256 ·           │
                 │  declared format vs served content type              │
                 └──────────────────────────────────────────────────────┘
                                          │
                 ┌──────────────────────────────────────────────────────┐
                 │  normalise: key padding · encoding repair ·          │
                 │  name folding · es-PY numbers · indentation depth    │
                 └──────────────────────────────────────────────────────┘
                                          │
                 ┌──────────────────────────────────────────────────────┐
                 │  DuckDB star schema                                  │
                 │                                                      │
                 │   dim_geography ─┐                                   │
                 │   dim_indicator ─┼─ fact_indicator_district          │
                 │   dim_source   ──┤                                   │
                 │   dim_run      ──┼─ fact_indicator_national          │
                 │                  └─ fact_quality_check               │
                 └──────────────────────────────────────────────────────┘
                                          │
                        ┌─────────────────┴─────────────────┐
                   HTML quality report              Parquet / CSV → Power BI
```

`fact_quality_check` is a fact table with its own grain — one run, one check, one
target — not a log file. Quality is queried the same way the data is, and the
history survives across runs.

## Quick start

```bash
pip install -r requirements.txt
python -m pypy_pipeline build
```

That fetches every source live, loads `data/warehouse.duckdb`, runs the check
suite, and writes `data/reports/quality-report.html`. It takes about two minutes,
most of it downloading a 46 MB GeoJSON for five attribute fields.

```bash
python -m pypy_pipeline build --reuse-cache   # re-read data/raw, skip the network
python -m pypy_pipeline check                 # re-run checks on the existing warehouse
python -m pypy_pipeline export                # Parquet + CSV for Power BI
pytest                                        # 82 tests
```

`build` exits non-zero if any check fails at `error` severity, so it drops
straight into CI without a wrapper.

## The check suite

30 checks across six families. Each exists because of something observed in the
sources, not because it is generic good practice.

| Family | What it establishes | Examples |
| --- | --- | --- |
| **Completeness** | the expected rows are present | `dim_geography` holds exactly 263 districts; every configured indicator returned at least one observation |
| **Uniqueness** | the declared grain holds | no duplicate `(geo_key, indicator_code, year)` |
| **Range** | values are physically possible | NBI percentages in [0,100]; median age in [10,60] |
| **Referential** | every fact resolves to its dimension | every name-matched `geo_key` exists in `dim_geography` |
| **Freshness** | the data is as recent as its publication cycle allows | World Bank within 2 years; the census within 12, because it is decennial |
| **Conformity** | values match their declared shape | `geo_key` matches `^\d{4}$`; declared format matches served content type; no encoding damage |

Severity decides what happens, not just how a row is coloured:

* **error** — the warehouse is not trustworthy. The run fails.
* **warning** — a known defect in the published source. Recorded, run continues.
* **info** — a measurement worth tracking with no pass/fail meaning.

The current run passes 26 of 30. The four that fail are all genuine source
defects, all non-blocking, and all of them are the findings listed above.
**Warnings are not suppressed and are not repaired into invisibility** — a
report that showed 30 green checks would be hiding the most interesting thing
this repository has to say.

## What the warehouse answers

Queries in [`sql/analytics.sql`](sql/analytics.sql), all runnable directly:

1. How does Paraguay compare against its own 1990–2025 history, with each
   indicator expressed as a percentile of its own range?
2. Which districts concentrate deprivation, weighted by the number of households
   it actually affects rather than by percentage alone?
3. Which *dimension* of deprivation dominates in each department — the four NBI
   components do not move together, and averaging them hides that.
4. How much of the published district-level data is genuinely attachable to the
   official geographic register, and what stood in the way?

## Dashboard

`python -m pypy_pipeline export` writes the star schema as Parquet and CSV.
[`powerbi/README.md`](powerbi/README.md) has the relationship map, the DAX
measures, and the three-page layout.

There is deliberately no `.pbix` in the repository: it is an opaque binary that
cannot be generated from Python or reviewed in a diff. The HTML quality report
is the artefact that renders without installing anything.

## Limitations

Stated plainly, because the defects in Paraguay's public data are the finding
rather than something to hide.

* **District poverty is not in the warehouse.** The `mapa-de-pobreza-monetaria-distrital`
  XLSX returns **HTTP 404** from `ine.gov.py`. Monetary poverty is therefore
  absent at district grain, and NBI is the only deprivation measure loaded.
* **Two district names cannot be recovered.** `MAYOR JULIO DIONISIO OTA??O` and
  `DR. RAUL PE?A` reach the warehouse with their `Ñ` destroyed upstream. They are
  stored as published and flagged; inferring the correct spelling would be
  fabricating data.
* **The Banco Central del Paraguay is excluded.** `bcp.gov.py` returns HTTP 403
  to automated clients, so exchange rates, monetary aggregates and the national
  accounts are out of scope. The national catalogue lists a BCP dataset that is
  unreachable for the same reason.
* **DNIT contributes nothing.** Its open-data portal is rendered client-side and
  serves no data files, and it publishes no datasets to the national catalogue.
  A Playwright scrape could reach the rendered page; that is deliberately not
  done here, because a scrape that breaks on the next theme change is a worse
  foundation than an API.
* **Central government debt loads zero rows.** The World Bank lists the series
  for Paraguay with no data behind it. It is kept in `dim_indicator` so its
  absence is visible instead of looking like an indicator nobody configured.
* **The Gini index has 7 interior gaps** between 1990 and 2024. Nothing is
  interpolated; the gaps are reported.
* **District data is a single year.** The 2022 census is the only district-level
  source loaded, so `fact_indicator_district` has no time series. Trends exist
  only at national grain.
* **Names are matched, not codes.** All 263 districts resolve today. If a future
  publication invents a fourth spelling, the referential-integrity check fails
  loudly rather than silently mismatching — which is the intended behaviour, but
  it does mean the load is not immune to upstream renaming.
* **Each export overwrites the previous one.** `fact_quality_check` accumulates
  history inside DuckDB across runs, but the Parquet/CSV exports do not.

## Repository layout

```
config/sources.yml         declarative source registry; no URLs in code
src/pypy_pipeline/
  fetch.py                 HTTP with per-retrieval provenance
  text.py                  name folding and encoding-damage detection
  parse.py                 es-PY numbers, indentation-encoded hierarchy
  ingest/catalog.py        DKAN API client and catalogue audit
  ingest/geography.py      the dimension and the 2012<->2022 crosswalk
  ingest/census.py         district statistics, matched by name
  ingest/worldbank.py      national annual indicators
  quality/checks.py        check primitives
  quality/suite.py         the suite this warehouse is held to
  quality/report.py        self-contained HTML report
  warehouse/schema.sql     the star schema
  warehouse/build.py       ingest, load, record
sql/analytics.sql          the analytical queries
tests/                     82 tests, no network required
docs/source-audit.md       what every source actually looked like
powerbi/                   model spec, DAX measures, page layout
```

## Tests

```bash
pytest
```

82 tests. The unit tests run offline against fixtures reproduced from the real
files. `tests/test_warehouse.py` asserts every figure quoted in this README
against the built warehouse and skips itself when no warehouse exists — so if a
source changes upstream and a claim here stops being true, the suite fails
rather than the README quietly going stale.

Two of them are worth singling out:

* `test_apostrophes_are_intra_word_not_separators` guards a real bug. Folding
  the apostrophe in `AZOTE'Y` to a space produced a different key from the
  census file's `Azotey`, which silently dropped two districts from the fact
  table. It was found by measuring the match rate, not by reading the code.
* `test_national_population_matches_the_published_census_total` sums the loaded
  districts and requires exactly **6,109,903** — the figure the INE publishes
  for Paraguay in 2022. It is the end-to-end proof that name matching, es-PY
  number parsing and the indentation reader are all correct at once.

---

Built by Sergio Alegre. Data from the
[World Bank](https://data.worldbank.org/country/paraguay),
[datos.gov.py](https://www.datos.gov.py) and the
[Instituto Nacional de Estadística](https://www.ine.gov.py).
