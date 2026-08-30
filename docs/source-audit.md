# Source audit

Every source below was probed directly on 2026-08-30 before a line of the
pipeline was written. This file records what was found, including the sources
that turned out to be unusable, so the decisions encoded in `config/sources.yml`
can be checked rather than taken on trust.

The measurements marked **live** are recomputed on every pipeline run and appear
in the generated quality report; the rest are one-off findings from the audit.

## Portals probed

| Source | Result | Verdict |
| --- | --- | --- |
| `api.worldbank.org` | HTTP 200, JSON | Used. WDI source, `lastupdated` 2026-07-13 |
| `www.datos.gov.py` | HTTP 200, DKAN action API | Used. 434 datasets, 3,615 resources |
| `www.ine.gov.py` | HTTP 200, HTML + XLSX/PDF | Used indirectly, via catalogue resource URLs |
| `www.bcp.gov.py` | **HTTP 403** | Unusable. Blocks automated access |
| `www.dnit.gov.py` | HTTP 200, no data links | Unusable. See below |
| `datos.ine.gov.py` | DNS failure | Does not resolve |
| `www.contrataciones.gov.py` | DNS failure from this network | Not pursued |

### datos.gov.py has an API

The portal runs **DKAN**, the Drupal implementation of CKAN, and exposes a
CKAN-v3-compatible action API at `/api/3/action/`. Not every CKAN action is
implemented — `package_search` and `organization_list` return HTML rather than
JSON — but `package_list`, `package_show` and
`current_package_list_with_resources` all work and are what this pipeline uses.
Scraping is unnecessary.

### DNIT publishes no machine-readable data

`dnit.gov.py/web/portal-institucional/datos-abiertos` returns HTTP 200 and 179 KB
of HTML containing **zero** links to data files: it is a Liferay portal whose
asset list is rendered client-side. DNIT also contributes **no datasets at all**
to the national catalogue. The tax and customs authority is therefore absent
from Paraguay's open-data infrastructure, which is itself a finding.

### A dataset that cannot be retrieved

The catalogue publishes `tipo-de-cambio-mensual-de-monedas` with its only
resource pointing at `bcp.gov.py`, which returns 403 to automated clients. The
dataset is listed, documented and structurally unreachable.

## Catalogue metadata (live)

Measured by walking `current_package_list_with_resources` on every run.

| Measure | Value on 2026-08-30 |
| --- | --- |
| Datasets listed | 434 |
| Resources listed | 3,615 |
| Datasets with no resource at all | 5 |
| Resources with an empty URL | 8 |
| Datasets with no declared publisher | 229 (52.8%) |
| Distinct declared formats | 25 |

Two metadata defects worth naming:

* **One format, three spellings.** The same file family is declared as `xlsx`
  (95 resources), `excel` (70) and `xls` (27). The pipeline folds these into one
  family when comparing declared against served format, because the split is a
  metadata defect rather than a content one.
* **One publisher, two identities.** The statistics office appears as both
  `Instituto Nacional de Estadistica` (12 datasets) and
  `Instituto Nacional de Estadística` (6). They are the same body.
* **Resource names are not unique.** Most tables are published twice under a
  byte-identical resource name, once as CSV and once as JSON. Selecting a
  resource by name alone returns two rows, which is why every entry in
  `config/sources.yml` also pins a format.

## Link liveness

A random sample of 250 of the 3,615 resources was retrieved with a ranged GET:

| Status | Count | Share |
| --- | --- | --- |
| 200 / 206 | 244 | 97.6% |
| 404 | 4 | 1.6% |
| 403 | 2 | 0.8% |

All 215 sampled resources hosted on `datos.gov.py` itself resolved. The failures
were on external hosts the catalogue links out to (`hacienda.gov.py`,
`mitic.gov.py`, `ine.gov.py`). Separately, the `mapa-de-pobreza-monetaria-distrital`
XLSX on `ine.gov.py` returns 404, which is why district poverty is not in the
warehouse — see *Limitations* in the README.

## The geographic register: two vintages

This is the integration problem the project exists to solve.

| | DGEEC 2012 | CNPV 2022 |
| --- | --- | --- |
| Format | CSV, `;` delimited, **cp1252** | GeoJSON, UTF-8 |
| Districts | 250 | 263 |
| Key field | `Codigo concatenado` | `CLAVE` |
| Key form | unpadded integer — `0`, `101`, `1304` | zero-padded string — `0000`, `0101`, `1304` |

A join on the keys **as published** matches nothing: 160 of the 250 shared
districts carry a 2012 key shorter than four characters. After padding both to
`DDdd`:

* 250 of 263 districts reconcile exactly,
* 13 districts exist only in 2022 — created after the previous census,
* 8 share a code but not a name across the two vintages.

Of those 8 name disagreements, normalisation resolves 2 (`GUAZU-CUA` /
`GUAZU CUA`, and `TTE. 1º` / `TTE 1RO`). The remaining 6 are handled by indexing
each district under **both** of its official spellings rather than by a
hand-written alias table.

### Encoding damage in the 2022 GeoJSON

The file is valid UTF-8 and contains three mutually incompatible renderings of
the letter `Ñ`:

| District | Bytes in the file | Diagnosis | Recoverable |
| --- | --- | --- | --- |
| `MAYOR JULIO DIONISIO OTA??O` | `0x3F 0x3F` | replaced by two literal `?` | No |
| `DR. RAUL PE?A` | `0x3F` | replaced by one literal `?` | No |
| `YASY KAÃ‘Y` | `0xC3 0x2018` | UTF-8 read as cp1252, re-encoded | Yes |

The pipeline repairs the third and flags all three. It never guesses at the
first two: the bytes are gone.

### The 2012 CSVs are cp1252 served as `text/csv`

No `charset` parameter is sent, so a client that assumes UTF-8 corrupts every
accented name. The department table renders `ASUNCIÓN` as byte `0xD3`, and its
header row spells `Descripcin de Distrito` — the accent is missing from the
column name itself.

## District statistics carry no codes

Neither the 2022 census population tables nor the NBI table contain a district
code. They identify districts by **name only**, in a spelling that matches
neither code register exactly:

* the code registers write `AZOTE'Y` and `JUAN E. O'LEARY`; the census writes
  `Azotey` and `Juan E. Oleary`,
* the 2022 GeoJSON writes `COLONIA INDEPENDENCIA`; the census writes
  `Independencia`,
* names are published without accents or the letter `ñ` — `Numi` for Ñumí,
  `Neembucu` for Ñeembucú.

Two further properties of these files defeat a default CSV reader:

* **Hierarchy is indentation.** In the NBI table a row indented five spaces is a
  department and a row indented ten is a district; `Concepcion` appears at both
  levels. Ignoring the indentation double-counts every department into the
  district grain.
* **Numbers are es-PY.** `1.770.755` is one and three-quarter million;
  `28,3` is 28.3. A C-locale parser reads the first as 1.77 and produces a
  plausible, wrong answer rather than an error.

## World Bank indicator coverage

Probed for `PRY` over 1990–2025.

| Coverage | Indicators |
| --- | --- |
| Complete, no gaps | GDP growth, GDP per capita, CPI inflation, exports %GDP, trade %GDP, agriculture %GDP, FDI %GDP, population, labour participation |
| Partial | Unemployment (35/36), tax revenue (2005–2024), **Gini index — 28 of 35 years, 7 interior gaps** |
| Empty | **Central government debt %GDP — listed for Paraguay, null in every year** |

Both defects are loaded into the warehouse rather than filtered out, and both
are reported by the check suite.
