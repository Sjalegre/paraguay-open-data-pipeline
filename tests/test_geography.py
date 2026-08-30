"""Tests for the geographic dimension and the two-vintage crosswalk.

These run offline against fixtures reproduced from the published sources, so
the reconciliation logic is testable without depending on the national portal
being up.
"""

from __future__ import annotations

from pypy_pipeline.ingest.geography import (
    District,
    _pad_key,
    build_name_index,
    build_scoped_name_index,
)


class TestKeyPadding:
    def test_pads_both_components_to_two_digits(self):
        assert _pad_key(1, 2) == "0102"
        assert _pad_key(0, 0) == "0000"
        assert _pad_key(15, 7) == "1507"

    def test_accepts_the_string_form_the_geojson_publishes(self):
        assert _pad_key("07", "21") == "0721"

    def test_published_keys_do_not_join_without_padding(self):
        # The whole reason the crosswalk exists. The 2012 register publishes
        # Belen as '102' and the 2022 register as '0102'; comparing them as
        # published matches nothing.
        published_2012 = "102"
        published_2022 = "0102"
        assert published_2012 != published_2022
        assert _pad_key(1, 2) == published_2022


def _district(key, dept, dept_name, name, name_2012=None, **kw):
    from pypy_pipeline.text import normalise_name

    return District(
        geo_key=key,
        department_code=dept,
        department_name=dept_name,
        district_name=name,
        district_name_normalised=normalise_name(name),
        name_2012=name_2012,
        **kw,
    )


# A slice of the real dimension covering each interesting case.
FIXTURE = [
    _district("0000", "00", "ASUNCION", "ASUNCION", "ASUNCION"),
    _district("0101", "01", "CONCEPCION", "CONCEPCION", "CONCEPCION"),
    _district("0108", "01", "CONCEPCION", "AZOTE'Y", "AZOTE'Y"),
    _district("0112", "01", "CONCEPCION", "ARROYITO", None, created_after_2012=True),
    _district("0407", "04", "GUAIRA", "COLONIA INDEPENDENCIA", "INDEPENDENCIA"),
    _district("1007", "10", "ALTO PARANA", "JUAN E. O'LEARY", "JUAN E. O'LEARY"),
    # Same code, different official name in each vintage.
    _district("1210", "12", "CENTRAL", "MAYOR JOSE DE JESUS MARTINEZ",
              "MAYOR JOSE DEJESUS MARTINEZ"),
    # Department and district share a name; the district is in San Pedro.
    _district("0201", "02", "SAN PEDRO", "SAN PEDRO", "SAN PEDRO"),
]


class TestNameIndex:
    def test_indexes_the_current_official_spelling(self):
        index = build_name_index(FIXTURE)
        assert index["COLONIA INDEPENDENCIA"] == "0407"

    def test_indexes_the_2012_spelling_as_well(self):
        # This is what lets a census table that still says 'Independencia'
        # attach to the district the 2022 register calls
        # 'Colonia Independencia'.
        index = build_name_index(FIXTURE)
        assert index["INDEPENDENCIA"] == "0407"

    def test_both_vintages_resolve_a_renamed_district_to_one_key(self):
        index = build_name_index(FIXTURE)
        assert index["MAYOR JOSE DE JESUS MARTINEZ"] == "1210"
        assert index["MAYOR JOSE DEJESUS MARTINEZ"] == "1210"

    def test_apostrophe_variants_resolve(self):
        index = build_name_index(FIXTURE)
        assert index["AZOTEY"] == "0108"
        assert index["JUAN E OLEARY"] == "1007"

    def test_a_district_created_after_2012_is_indexed_under_its_only_name(self):
        index = build_name_index(FIXTURE)
        assert index["ARROYITO"] == "0112"

    def test_unknown_names_are_not_guessed_at(self):
        # An unrecognised name must fall through to a referential-integrity
        # failure rather than be fuzzy-matched to the nearest district.
        index = build_name_index(FIXTURE)
        assert index.get("ARROYITOS") is None
        assert index.get("SAN PEDRE") is None


class TestScopedIndex:
    def test_disambiguates_by_department(self):
        scoped = build_scoped_name_index(FIXTURE)
        assert scoped[("01", "CONCEPCION")] == "0101"
        assert scoped[("02", "SAN PEDRO")] == "0201"

    def test_does_not_resolve_a_district_outside_its_department(self):
        scoped = build_scoped_name_index(FIXTURE)
        assert scoped.get(("02", "AZOTEY")) is None


class TestCrosswalkShape:
    def test_districts_without_a_2012_key_are_flagged_as_new(self):
        new = [d for d in FIXTURE if d.created_after_2012]
        assert [d.geo_key for d in new] == ["0112"]
        assert all(d.name_2012 is None for d in new)
