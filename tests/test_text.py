"""Tests for name normalisation and encoding-damage detection.

Every case below is a string that actually appears in one of the published
sources, not a synthetic example. The comment on each says where.
"""

from __future__ import annotations

import pytest

from pypy_pipeline.text import (
    find_encoding_damage,
    normalise_name,
    repair_mojibake,
)


class TestEncodingDamage:
    def test_detects_mojibake(self):
        # CNPV 2022 GeoJSON, district 1411: the enye of YASY KANY survived as
        # UTF-8 bytes decoded through cp1252 and re-encoded.
        damaged = "YASY KAÃ‘Y"
        assert find_encoding_damage(damaged) == ["mojibake"]

    def test_repairs_recoverable_mojibake(self):
        assert repair_mojibake("YASY KAÃ‘Y") == "YASY KAÑY"

    def test_detects_destroyed_character(self):
        # CNPV 2022 GeoJSON, district 0718. The enye was replaced upstream by
        # two literal question marks; the bytes are gone.
        assert find_encoding_damage("MAYOR JULIO DIONISIO OTA??O") == ["lost_character"]

    def test_does_not_invent_a_repair_for_destroyed_characters(self):
        # The important half of the contract: unrecoverable damage must be
        # reported, never guessed at.
        damaged = "MAYOR JULIO DIONISIO OTA??O"
        assert repair_mojibake(damaged) == damaged

    def test_clean_text_reports_no_damage(self):
        assert find_encoding_damage("SAN PEDRO DEL PARANA") == []
        assert find_encoding_damage("ÑEEMBUCU") == []

    def test_repair_leaves_clean_text_untouched(self):
        assert repair_mojibake("CONCEPCIÓN") == "CONCEPCIÓN"


class TestNormalisation:
    def test_folds_accents_and_case(self):
        assert normalise_name("Concepción") == "CONCEPCION"
        assert normalise_name("Itapúa") == "ITAPUA"

    @pytest.mark.parametrize(
        "a, b",
        [
            # 2012 code table vs CNPV 2022 GeoJSON, district 1206.
            ("GUAZU-CUA", "GUAZU CUA"),
            # 2012 code table vs GeoJSON vs census table, district 1507.
            ("TTE. 1º MANUEL IRALA FERNANDEZ", "TTE 1RO MANUEL IRALA FERNANDEZ"),
            ("Tte. 1ero Manuel Irala Fernandez", "TTE 1RO MANUEL IRALA FERNANDEZ"),
        ],
    )
    def test_reconciles_spellings_that_differ_between_sources(self, a, b):
        assert normalise_name(a) == normalise_name(b)

    def test_does_not_reconcile_a_missing_word_break(self):
        # District 1210 is 'MAYOR JOSE DEJESUS MARTINEZ' in the 2012 register
        # and 'MAYOR JOSE DE JESUS MARTINEZ' in the 2022 one. Normalisation
        # deliberately does not bridge this: inserting a word break would take a
        # dictionary, and a rule that guesses at word boundaries would start
        # merging districts that are genuinely different.
        #
        # The case is handled instead by indexing each district under both of
        # its official spellings -- see `ingest.geography.build_name_index` and
        # `test_geography.py`.
        assert normalise_name("MAYOR JOSE DE JESUS MARTINEZ") != normalise_name(
            "MAYOR JOSE DEJESUS MARTINEZ"
        )

    @pytest.mark.parametrize(
        "with_mark, without_mark",
        [
            # District 0108: the code registers write the Guarani puso, the
            # census tables drop it.
            ("Azote'y", "Azotey"),
            # District 1007: same disagreement, different origin of the mark.
            ("JUAN E. O'LEARY", "Juan E. Oleary"),
        ],
    )
    def test_apostrophes_are_intra_word_not_separators(self, with_mark, without_mark):
        # Regression guard. Folding the apostrophe to a space produced
        # 'AZOTE Y' and 'AZOTEY' as different keys, which silently dropped
        # two districts from the district fact table.
        assert normalise_name(with_mark) == normalise_name(without_mark)

    def test_expands_rank_abbreviations(self):
        assert normalise_name("Gral. Eugenio A. Garay").startswith("GENERAL ")
        assert normalise_name("Dr. Raul Pena").startswith("DOCTOR ")

    def test_distinct_districts_do_not_collide(self):
        # Normalisation must be aggressive without being lossy enough to merge
        # genuinely different places.
        assert normalise_name("SAN PEDRO DEL PARANA") != normalise_name("SAN PEDRO")
        assert normalise_name("SAN JOSE") != normalise_name("SAN JOSE DEL ROSARIO")

    def test_handles_empty_and_none(self):
        assert normalise_name("") == ""
        assert normalise_name(None) == ""

    def test_normalises_damaged_names_without_crashing(self):
        assert normalise_name("MAYOR JULIO DIONISIO OTA??O") == (
            "MAYOR JULIO DIONISIO OTA O"
        )
