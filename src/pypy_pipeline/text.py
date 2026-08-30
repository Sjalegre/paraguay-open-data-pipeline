"""Name normalisation and encoding-damage detection.

This module is the core of the integration problem. Paraguayan district data is
published under three different naming conventions across three official
sources, and none of the district-level statistical files carry a district code
-- only a name. Joining them therefore means normalising names, and normalising
names means first knowing which characters are genuine and which are damage.

Three distinct kinds of encoding damage were observed in the sources, and they
need different handling:

  1. Recoverable mojibake. Text whose cp1252 rendering, re-read as UTF-8, yields
     the intended character. `YASY KAÃ‘Y` is `YASY KAÑY`
     round-tripped through the wrong codec, and the original is recoverable.
  2. Unrecoverable substitution. `MAYOR JULIO DIONISIO OTA??O` -- the `Ñ`
     was replaced by literal `?` (0x3F) upstream. The byte is gone; no decoder
     brings it back. These are flagged, never guessed.
  3. Codec mismatch at the file level. The 2012 code tables are cp1252 served
     as `text/csv` with no charset parameter. Handled at read time, not here.

All three appear in the same corpus, and the second appears in the same file as
the first.
"""

from __future__ import annotations

import re
import unicodedata

# cp1252 bytes 0x80-0x9F map to printable characters that are not Latin-1.
# Their presence immediately after a capital A-tilde or A-circumflex is the
# fingerprint of UTF-8 bytes decoded as cp1252 and then re-encoded as UTF-8.
_MOJIBAKE_LEAD = "ÃÂ"
_MOJIBAKE_TRAIL = (
    "-ÿ"
    "‘’‚“”„†‡…‰"
    "€ŒœŠšŽžŸƒˆ˜"
    "‹›–—™"
)
_MOJIBAKE_RE = re.compile(f"[{_MOJIBAKE_LEAD}][{_MOJIBAKE_TRAIL}]")

# A literal question mark sitting inside a word is not punctuation; it is a
# character that was destroyed on the way out of some upstream system.
_LOST_CHAR_RE = re.compile(r"(?<=[A-Za-z])\?+(?=[A-Za-z]|\s|$)")

_REPLACEMENT_RE = re.compile("�")


def find_encoding_damage(value: str) -> list[str]:
    """Return the kinds of encoding damage present in `value`.

    Returns an empty list for clean text. Used both to populate the
    `has_encoding_damage` column on `dim_geography` and to drive the
    conformity checks, so the warehouse can report damage rather than
    quietly normalising it away.
    """
    if not value:
        return []
    damage = []
    if _MOJIBAKE_RE.search(value):
        damage.append("mojibake")
    if _LOST_CHAR_RE.search(value):
        damage.append("lost_character")
    if _REPLACEMENT_RE.search(value):
        damage.append("replacement_char")
    return damage


def repair_mojibake(value: str) -> str:
    """Undo one round of UTF-8-read-as-cp1252 damage, if that is what happened.

    Returns `value` unchanged when the text is clean or when the round trip is
    not exactly reversible. Never guesses: a string that cannot be repaired
    losslessly is left alone for `find_encoding_damage` to flag.
    """
    if not value or not _MOJIBAKE_RE.search(value):
        return value
    try:
        repaired = value.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    # Only accept a repair that actually removed the fingerprint. A string that
    # still looks damaged after the round trip was damaged some other way.
    return repaired if not _MOJIBAKE_RE.search(repaired) else value


# Abbreviations that differ between the three official spellings of the same
# district. Each entry below is backed by an observed disagreement, not by
# guesswork; `docs/source-audit.md` lists where each one was seen.
_ABBREVIATIONS = {
    "TTE": "TENIENTE",
    "TTE.": "TENIENTE",
    "GRAL": "GENERAL",
    "GRAL.": "GENERAL",
    "CNEL": "CORONEL",
    "CNEL.": "CORONEL",
    "CAP": "CAPITAN",
    "CAP.": "CAPITAN",
    "DR": "DOCTOR",
    "DR.": "DOCTOR",
    "MCAL": "MARISCAL",
    "MCAL.": "MARISCAL",
    "STA": "SANTA",
    "STA.": "SANTA",
    "STO": "SANTO",
    "STO.": "SANTO",
    "1RO": "PRIMERO",
    "1ERO": "PRIMERO",
    "1": "PRIMERO",
    "1RA": "PRIMERA",
    "2DO": "SEGUNDO",
    "2DA": "SEGUNDA",
    "3RO": "TERCERO",
}

_ORDINAL_MARK_RE = re.compile("[º°ª]")
# An apostrophe inside a Paraguayan place name is part of the word, not a
# separator: the puso in Guarani names like AZOTE'Y, and the elision in
# O'LEARY. Sources disagree on whether to write it at all -- the code registers
# publish `AZOTE'Y` and `JUAN E. O'LEARY`, the census tables `Azotey` and
# `Juan E. Oleary` -- so it is deleted rather than turned into a space, which
# makes both spellings fold to the same key.
_INTRA_WORD_PUNCT_RE = re.compile(r"['’ʼ`´]")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]")
_WS_RE = re.compile(r"\s+")


def normalise_name(value: str) -> str:
    """Fold a place name to a comparable key.

    The transformation is deliberately aggressive and deliberately lossy: it
    exists only to decide whether two spellings denote the same place, never to
    produce a display name. `dim_geography` keeps the official spelling
    alongside this key.

    Applied in order: repair recoverable mojibake, strip ordinal marks, fold to
    ASCII, upper-case, drop punctuation, expand rank abbreviations, collapse
    whitespace.

    >>> normalise_name("TTE. 1\\u00ba MANUEL IRALA FERNANDEZ")
    'TENIENTE PRIMERO MANUEL IRALA FERNANDEZ'
    >>> normalise_name("TTE 1RO MANUEL IRALA FERNANDEZ")
    'TENIENTE PRIMERO MANUEL IRALA FERNANDEZ'
    >>> normalise_name("GUAZU-CUA") == normalise_name("GUAZU CUA")
    True
    >>> normalise_name("Azote'y") == normalise_name("Azotey")
    True
    >>> normalise_name("JUAN E. O'LEARY") == normalise_name("Juan E. Oleary")
    True
    """
    if value is None:
        return ""
    text = repair_mojibake(str(value))
    # Ordinal marks carry meaning (1o) but fold to nothing under NFKD, so they
    # are removed before folding rather than after.
    text = _ORDINAL_MARK_RE.sub("", text)
    text = _INTRA_WORD_PUNCT_RE.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.upper()
    # Expand abbreviations before punctuation is stripped, because the trailing
    # period is what distinguishes `STA.` from a word that happens to start
    # with those letters.
    tokens = [_ABBREVIATIONS.get(t, t) for t in _WS_RE.split(text.strip()) if t]
    text = " ".join(tokens)
    text = _NON_ALNUM_RE.sub(" ", text)
    tokens = [_ABBREVIATIONS.get(t, t) for t in _WS_RE.split(text.strip()) if t]
    return " ".join(tokens)


# Deliberately absent: a hand-written alias table.
#
# An earlier draft carried one. It turned out to be papering over a bug in this
# module -- apostrophes were being folded to spaces, so `AZOTE'Y` and `Azotey`
# produced different keys -- and over the fact that districts must be looked up
# under both of their official vintages, not just the current one. With the
# folding rules above and the dual-vintage index in `ingest.geography`, all 263
# districts of the 2022 census and all 263 rows of the NBI table attach to the
# dimension with no per-district exceptions at all.
#
# The distinction matters for a data-quality project: an alias table hides
# disagreement between sources, whereas a normalisation rule states what is
# being treated as equivalent and why, once, for every row.
