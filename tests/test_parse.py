"""Tests for the es-PY number and hierarchical-table readers."""

from __future__ import annotations

import pytest

from pypy_pipeline.parse import ParseError, parse_number, read_indented_csv


class TestNumbers:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1.770.755", 1_770_755.0),  # NBI table, national household count
            ("206.181", 206_181.0),      # census, Concepcion population
            ("28,3", 28.3),              # NBI table, percentage
            ("5", 5.0),
            ("27", 27.0),                # census, median age
            ("1.234.567,89", 1_234_567.89),
        ],
    )
    def test_parses_es_py_formats(self, raw, expected):
        assert parse_number(raw) == expected

    def test_thousands_separator_is_not_read_as_a_decimal_point(self):
        # The failure this guards against is silent: a C-locale parser reads
        # '73.360' as 73.36 and produces a plausible, wrong population.
        assert parse_number("73.360") == 73_360.0

    @pytest.mark.parametrize("token", ["", "-", "...", "n/d", "s/d"])
    def test_null_sentinels_become_none(self, token):
        assert parse_number(token) is None

    def test_unknown_format_raises_rather_than_returning_none(self):
        # An unrecognised format must surface during ingestion instead of
        # becoming an indistinguishable gap in the warehouse.
        with pytest.raises(ParseError):
            parse_number("about 12k")


# Reproduced byte-for-byte from the published NBI table, including the
# indentation that carries the hierarchy and the repeated 'Concepcion' at two
# different levels.
NBI_SAMPLE = (
    "Departamento y distrito;Total de hogares;"
    "Hogares con Necesidades Basicas Insatisfechas (NBI) - % Al menos una NBI\n"
    "     Total Pais;1.770.755;28,3\n"
    "          Asuncion;133.600;17,4\n"
    "     Departamento Concepcion;56.837;34,3\n"
    "          Concepcion;19.196;29,6\n"
    "          Belen;3.021;30,1\n"
).encode("utf-8")


class TestIndentedTables:
    def test_reads_header_and_rows(self):
        header, rows = read_indented_csv(NBI_SAMPLE, encoding="utf-8")
        assert header[0] == "Departamento y distrito"
        assert len(rows) == 5

    def test_indentation_distinguishes_department_from_district(self):
        # 'Concepcion' appears as both a department and a district. Nothing but
        # the indentation separates them, and conflating them double-counts the
        # department into the district grain.
        _, rows = read_indented_csv(NBI_SAMPLE, encoding="utf-8")
        by_label = {(r.label, r.indent) for r in rows}
        assert ("Departamento Concepcion", 5) in by_label
        assert ("Concepcion", 10) in by_label

    def test_total_rows_are_identified(self):
        _, rows = read_indented_csv(NBI_SAMPLE, encoding="utf-8")
        totals = [r.label for r in rows if r.is_total]
        assert totals == ["Total Pais", "Departamento Concepcion"]

    def test_reads_cp1252_payloads(self):
        # The 2012 code tables are cp1252 served as text/csv with no charset.
        payload = "Codigo;Nombre\n0;ASUNCIÓN\n".encode("cp1252")
        _, rows = read_indented_csv(payload, encoding="cp1252")
        assert rows[0].values == ["ASUNCIÓN"]
