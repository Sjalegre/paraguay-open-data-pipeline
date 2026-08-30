"""Build the geographic dimension and the 2012-to-2022 crosswalk.

Paraguay's district register exists in two official vintages that do not join to
each other as published:

  * DGEEC 2012 code table (CSV, cp1252): 250 districts, key published as an
    unpadded integer -- Asuncion is `0`, Belen is `102`.
  * CNPV 2022 GeoJSON: 263 districts, key published as a zero-padded string --
    Asuncion is `0000`, Belen is `0102`.

A direct join on the published keys matches nothing at all. After padding both
to `DDdd` the two vintages reconcile fully on the 250 districts they share, and
the 13 districts created after 2012 stand out as genuinely new rather than as
join failures. That distinction -- a real change in the territory versus a
defect in the key format -- is the whole point of doing this deliberately.

The 2022 vintage is authoritative for `dim_geography` because it is the one the
current census tables are published against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..fetch import Session
from ..parse import read_csv_dicts
from ..text import find_encoding_damage, normalise_name, repair_mojibake
from .catalog import Catalog

# Column names in the 2012 district table, reproduced exactly as published --
# including the missing accent in `Descripcin`, which is upstream's typo and not
# one of ours.
_COL_DEPT_CODE = "Codigo de Departamento"
_COL_DEPT_NAME = "Descripcion de Departamento"
_COL_DIST_CODE = "Codigo de Distrito"
_COL_DIST_NAME = "Descripcin de Distrito"


@dataclass
class District:
    """One row of `dim_geography`."""

    geo_key: str
    department_code: str
    department_name: str
    district_name: str
    district_name_normalised: str
    vintage_2012_key: str | None = None
    name_2012: str | None = None
    name_changed_since_2012: bool = False
    created_after_2012: bool = False
    encoding_damage: list[str] = field(default_factory=list)

    @property
    def has_encoding_damage(self) -> bool:
        return bool(self.encoding_damage)


def _pad_key(department: str | int, district: str | int) -> str:
    """Render a department/district pair as the canonical `DDdd` key."""
    return f"{int(department):02d}{int(district):02d}"


def load_2022_districts(session: Session, catalog: Catalog, cfg: dict) -> list[District]:
    """Read the CNPV 2022 district GeoJSON, discarding geometry."""
    resource = catalog.one_resource(
        cfg["dataset"], cfg["resources"]["districts"]
    )
    payload = session.fetch(
        "geography_2022:districts",
        resource.url,
        declared_format=resource.declared_format,
    )
    document = json.loads(payload.decode(cfg.get("encoding", "utf-8")))

    districts: list[District] = []
    for feature in document.get("features", []):
        props = feature.get("properties") or {}
        raw_name = props.get("DIST_DESC_") or ""
        raw_dept = props.get("DPTO_DESC") or ""
        # `repair_mojibake` fixes the recoverable cases; the damage list keeps a
        # record of every case, recoverable or not, so the warehouse can report
        # on it instead of pretending the source was clean.
        damage = find_encoding_damage(raw_name) + find_encoding_damage(raw_dept)
        districts.append(
            District(
                geo_key=str(props.get("CLAVE") or "").strip(),
                department_code=str(props.get("DPTO") or "").strip(),
                department_name=repair_mojibake(raw_dept).strip(),
                district_name=repair_mojibake(raw_name).strip(),
                district_name_normalised=normalise_name(raw_name),
                encoding_damage=sorted(set(damage)),
            )
        )
    return districts


def load_2012_districts(session: Session, catalog: Catalog, cfg: dict) -> dict[str, dict]:
    """Read the DGEEC 2012 code table, keyed by the padded `DDdd` key."""
    resource = catalog.one_resource(
        cfg["dataset"], cfg["resources"]["districts"]
    )
    payload = session.fetch(
        "geography_2012:districts",
        resource.url,
        declared_format=resource.declared_format,
    )
    rows = read_csv_dicts(
        payload,
        encoding=cfg.get("encoding", "cp1252"),
        delimiter=cfg.get("delimiter", ";"),
    )
    out: dict[str, dict] = {}
    for row in rows:
        try:
            key = _pad_key(row[_COL_DEPT_CODE], row[_COL_DIST_CODE])
        except (KeyError, ValueError):
            continue
        out[key] = {
            "name": row.get(_COL_DIST_NAME, "").strip(),
            "department": row.get(_COL_DEPT_NAME, "").strip(),
            "published_key": row.get("Codigo concatenado", "").strip(),
        }
    return out


def build_dimension(session: Session, catalog: Catalog, sources: dict) -> list[District]:
    """Assemble `dim_geography` from both vintages."""
    cfg = sources["datos_gov_py"]
    districts = load_2022_districts(session, catalog, cfg["geography_2022"])
    vintage_2012 = load_2012_districts(session, catalog, cfg["geography_2012"])

    for district in districts:
        legacy = vintage_2012.get(district.geo_key)
        if legacy is None:
            district.created_after_2012 = True
            continue
        district.vintage_2012_key = legacy["published_key"]
        district.name_2012 = legacy["name"]
        district.name_changed_since_2012 = (
            normalise_name(legacy["name"]) != district.district_name_normalised
        )
    return districts


def build_name_index(districts: list[District]) -> dict[str, str]:
    """Map every known spelling of a district to its `geo_key`.

    Statistical tables published at district level carry no code, only a name,
    so this index is the only way to attach them to the dimension. It accepts
    the 2022 spelling and the 2012 spelling of each district, and deliberately
    refuses to accept anything else: an unrecognised name is reported as a
    referential-integrity failure rather than fuzzy-matched into whichever
    district happens to look closest.
    """
    index: dict[str, str] = {}
    collisions: dict[str, set[str]] = {}

    def register(name: str | None, geo_key: str) -> None:
        if not name:
            return
        key = normalise_name(name)
        if key:
            existing = index.get(key)
            if existing is not None and existing != geo_key:
                collisions.setdefault(key, {existing}).add(geo_key)
                return
            index[key] = geo_key

    for district in districts:
        register(district.district_name, district.geo_key)
        register(district.name_2012, district.geo_key)

    # District names are not unique nationally -- several departments contain a
    # district sharing the department's own name. Collisions are dropped from
    # the index rather than resolved arbitrarily; callers that know the
    # department disambiguate with `build_scoped_name_index`.
    for key in collisions:
        index.pop(key, None)
    return index


def build_scoped_name_index(districts: list[District]) -> dict[tuple[str, str], str]:
    """Map (department code, normalised district name) to `geo_key`.

    Preferred over `build_name_index` wherever the source file states which
    department it covers, which is the case for every per-department census
    table.
    """
    index: dict[tuple[str, str], str] = {}
    for district in districts:
        for name in (district.district_name, district.name_2012):
            if not name:
                continue
            key = normalise_name(name)
            if key:
                index.setdefault((district.department_code, key), district.geo_key)
    return index
