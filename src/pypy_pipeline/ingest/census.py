"""Ingest district-level statistics from the 2022 census and the NBI table.

These files are the hard half of the integration problem. None of them carry a
district code -- only a district name, in a spelling that matches neither of the
two official code registers exactly. Attaching them to `dim_geography`
therefore means matching on a normalised name, and the share of rows that match
is itself a published quality metric rather than an implementation detail.

Matching is scoped to a department wherever the source states one, because
district names are not unique nationally: several departments contain a district
that shares the department's own name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..fetch import Session
from ..parse import ParseError, parse_number, read_indented_csv
from ..text import normalise_name
from .catalog import Catalog
from .geography import District

# Indentation depths observed in the NBI table. Country total and departments
# sit at one depth, districts at a deeper one; nothing else in the row
# distinguishes them.
_NBI_DEPARTMENT_INDENT = 5
_NBI_DISTRICT_INDENT = 10

_DEPT_IN_TITLE_RE = re.compile(r"Departamento\s+(.+?)\.\s", re.IGNORECASE)


@dataclass(frozen=True)
class DistrictObservation:
    """One value of one indicator for one district in one year."""

    geo_key: str
    year: int
    indicator_code: str
    value: float
    source_key: str


@dataclass
class IngestOutcome:
    """Observations plus the names that could not be attached to a district."""

    observations: list[DistrictObservation]
    unmatched: list[tuple[str, str]]  # (source label, unmatched district name)
    rows_read: int = 0

    @property
    def match_rate(self) -> float:
        matched = self.rows_read - len(self.unmatched)
        return matched / self.rows_read if self.rows_read else 0.0


def _department_code_index(districts: list[District]) -> dict[str, str]:
    """Map a normalised department name to its two-digit code."""
    index: dict[str, str] = {}
    for district in districts:
        key = normalise_name(district.department_name)
        if key:
            index.setdefault(key, district.department_code)
    return index


def _resolve(
    scoped: dict[tuple[str, str], str],
    unscoped: dict[str, str],
    department_code: str | None,
    name: str,
) -> str | None:
    """Look a district name up, preferring the department-scoped index."""
    key = normalise_name(name)
    if not key:
        return None
    if department_code is not None:
        hit = scoped.get((department_code, key))
        if hit:
            return hit
    return unscoped.get(key)


# Column label -> indicator code, for the per-department population tables.
_POPULATION_INDICATORS = {
    "Total": "POP_TOTAL",
    "Sexo - Hombres": "POP_MALE",
    "Sexo - Mujeres": "POP_FEMALE",
    "Edad mediana": "AGE_MEDIAN",
}


def ingest_population(
    session: Session,
    catalog: Catalog,
    cfg: dict,
    districts: list[District],
    scoped: dict[tuple[str, str], str],
    unscoped: dict[str, str],
) -> IngestOutcome:
    """Read the 17 per-department population tables of the 2022 census."""
    resources = catalog.resources(
        cfg["dataset"], cfg["resource_pattern"], cfg.get("format")
    )
    dept_index = _department_code_index(districts)
    year = int(cfg["year"])

    observations: list[DistrictObservation] = []
    unmatched: list[tuple[str, str]] = []
    rows_read = 0

    for resource in resources:
        match = _DEPT_IN_TITLE_RE.search(resource.name)
        department_code = (
            dept_index.get(normalise_name(match.group(1))) if match else None
        )
        payload = session.fetch(
            f"census_population:{resource.name[:40]}",
            resource.url,
            declared_format=resource.declared_format,
        )
        header, rows = read_indented_csv(
            payload,
            encoding=cfg.get("encoding", "utf-8"),
            delimiter=cfg.get("delimiter", ";"),
        )
        # Header labels carry stray double spaces in some files ("Sexo  y edad").
        columns = [re.sub(r"\s+", " ", h).strip() for h in header[1:]]

        for row in rows:
            if row.is_total:
                continue
            rows_read += 1
            geo_key = _resolve(scoped, unscoped, department_code, row.label)
            if geo_key is None:
                unmatched.append((resource.name, row.label))
                continue
            for column, raw in zip(columns, row.values):
                code = _POPULATION_INDICATORS.get(column)
                if code is None:
                    continue
                try:
                    value = parse_number(raw)
                except ParseError:
                    continue
                if value is None:
                    continue
                observations.append(
                    DistrictObservation(
                        geo_key=geo_key,
                        year=year,
                        indicator_code=code,
                        value=value,
                        source_key=f"census_population:{resource.name[:40]}",
                    )
                )

    # Asuncion is a district (key 0000) as well as a department, but the census
    # publishes it broken down by barrio rather than as a district row, so the
    # 17 per-department tables leave it with no population at all. Its totals
    # come from the national table instead.
    observations.extend(
        _ingest_asuncion(session, catalog, cfg, unscoped, year, unmatched)
    )
    rows_read += 1

    return IngestOutcome(observations, unmatched, rows_read)


def _ingest_asuncion(
    session: Session,
    catalog: Catalog,
    cfg: dict,
    unscoped: dict[str, str],
    year: int,
    unmatched: list[tuple[str, str]],
) -> list[DistrictObservation]:
    """Take Asuncion's population from the national by-department table."""
    resource = catalog.one_resource(
        cfg["dataset"], cfg["national_resource_pattern"], cfg.get("format")
    )
    payload = session.fetch(
        "census_population:national",
        resource.url,
        declared_format=resource.declared_format,
    )
    header, rows = read_indented_csv(
        payload,
        encoding=cfg.get("encoding", "utf-8"),
        delimiter=cfg.get("delimiter", ";"),
    )
    columns = [re.sub(r"\s+", " ", h).strip() for h in header[1:]]

    for row in rows:
        if normalise_name(row.label) != "ASUNCION":
            continue
        geo_key = unscoped.get("ASUNCION")
        if geo_key is None:
            unmatched.append((resource.name, row.label))
            return []
        out = []
        for column, raw in zip(columns, row.values):
            code = _POPULATION_INDICATORS.get(column)
            if code is None:
                continue
            try:
                value = parse_number(raw)
            except ParseError:
                continue
            if value is not None:
                out.append(
                    DistrictObservation(
                        geo_key=geo_key,
                        year=year,
                        indicator_code=code,
                        value=value,
                        source_key="census_population:national",
                    )
                )
        return out

    unmatched.append((resource.name, "Asuncion (row absent from national table)"))
    return []


# The NBI table's column labels are long and repeat a common prefix. Matching on
# the distinctive tail keeps the mapping readable and survives the prefix being
# reworded upstream.
_NBI_INDICATORS = [
    ("Total de hogares", "NBI_HOUSEHOLDS"),
    ("Al menos una NBI", "NBI_ANY_PCT"),
    ("Calidad de la vivienda", "NBI_HOUSING_PCT"),
    ("Infraestructura sanitaria", "NBI_SANITATION_PCT"),
    ("Acceso a la educacion", "NBI_EDUCATION_PCT"),
    ("Capacidad de subsistencia", "NBI_SUBSISTENCE_PCT"),
]


def ingest_nbi(
    session: Session,
    catalog: Catalog,
    cfg: dict,
    districts: list[District],
    scoped: dict[tuple[str, str], str],
    unscoped: dict[str, str],
) -> IngestOutcome:
    """Read the 2022 unsatisfied-basic-needs table.

    The file nests country > department > district using indentation alone, and
    `Concepcion` appears at both the department and the district level. The
    department context is tracked so district rows resolve within it.
    """
    resource = catalog.one_resource(
        cfg["dataset"], cfg["resource_pattern"], cfg.get("format")
    )
    payload = session.fetch(
        "nbi_2022",
        resource.url,
        declared_format=resource.declared_format,
    )
    header, rows = read_indented_csv(
        payload,
        encoding=cfg.get("encoding", "utf-8"),
        delimiter=cfg.get("delimiter", ";"),
    )
    dept_index = _department_code_index(districts)
    year = int(cfg["year"])

    # Map each data column to an indicator code by looking for its distinctive
    # tail in the published header.
    column_codes: list[str | None] = []
    for label in header[1:]:
        code = None
        for needle, candidate in _NBI_INDICATORS:
            if needle.lower() in label.lower():
                code = candidate
                break
        column_codes.append(code)

    observations: list[DistrictObservation] = []
    unmatched: list[tuple[str, str]] = []
    rows_read = 0
    department_code: str | None = None

    for row in rows:
        label = row.label
        if row.indent <= _NBI_DEPARTMENT_INDENT:
            # A department header, or the country total. Either way it sets the
            # context for the district rows that follow; neither is an
            # observation at district grain.
            stripped = re.sub(r"^Departamento\s+", "", label, flags=re.IGNORECASE)
            department_code = dept_index.get(normalise_name(stripped))
            if normalise_name(label).startswith("TOTAL"):
                # Asuncion is listed as a district directly under the country
                # total, with no department header of its own.
                department_code = dept_index.get("ASUNCION")
            continue

        if row.indent < _NBI_DISTRICT_INDENT:
            continue

        rows_read += 1
        geo_key = _resolve(scoped, unscoped, department_code, label)
        if geo_key is None:
            unmatched.append((resource.name, label))
            continue
        for code, raw in zip(column_codes, row.values):
            if code is None:
                continue
            try:
                value = parse_number(raw)
            except ParseError:
                continue
            if value is None:
                continue
            observations.append(
                DistrictObservation(
                    geo_key=geo_key,
                    year=year,
                    indicator_code=code,
                    value=value,
                    source_key="nbi_2022",
                )
            )

    return IngestOutcome(observations, unmatched, rows_read)
