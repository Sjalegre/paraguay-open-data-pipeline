"""Readers for the shapes the national statistical files actually come in.

Two conventions in these files defeat a default CSV reader:

  * Numbers are formatted es-PY. `1.770.755` is one million seven hundred
    thousand, not 1.77; `28,3` is 28.3. Reading either with a C-locale parser
    produces a plausible, wrong number rather than an error, which is the
    dangerous kind of defect.
  * Hierarchy is encoded as leading whitespace. In the NBI table, a row indented
    five spaces is a department (or the country total) and a row indented ten is
    a district. Nothing else distinguishes them, and `Concepcion` appears at both
    levels. Reading the file without tracking indentation silently double-counts
    every department into the district grain.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

_THOUSANDS_RE = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
_DECIMAL_RE = re.compile(r"^-?\d+,\d+$")
_INT_RE = re.compile(r"^-?\d+$")

# Values the sources use to mean "no data". Kept explicit so that a genuinely
# new sentinel shows up as a parse failure instead of being coerced to zero.
NULL_TOKENS = {"", "-", "--", "...", "..", "n/d", "nd", "s/d", "*"}


class ParseError(ValueError):
    """Raised when a value cannot be interpreted under the es-PY conventions."""


def parse_number(raw: str | None) -> float | None:
    """Parse an es-PY formatted number.

    Returns `None` for the documented null sentinels. Raises `ParseError` for
    anything else it cannot interpret, rather than returning `None`, so that an
    unexpected format surfaces during ingestion instead of becoming a silent gap
    in the warehouse.

    >>> parse_number("1.770.755")
    1770755.0
    >>> parse_number("28,3")
    28.3
    >>> parse_number("5")
    5.0
    >>> parse_number("-") is None
    True
    """
    if raw is None:
        return None
    text = str(raw).strip().replace("\xa0", "").replace(" ", "")
    if text.lower() in NULL_TOKENS:
        return None
    if _INT_RE.match(text):
        return float(text)
    if _THOUSANDS_RE.match(text):
        return float(text.replace(".", ""))
    if _DECIMAL_RE.match(text):
        return float(text.replace(",", "."))
    # Mixed form: thousands separators and a decimal comma together.
    if re.match(r"^-?\d{1,3}(\.\d{3})+,\d+$", text):
        return float(text.replace(".", "").replace(",", "."))
    raise ParseError(f"unrecognised numeric format: {raw!r}")


@dataclass(frozen=True)
class IndentedRow:
    """One row of a hierarchical statistical table."""

    indent: int
    label: str
    values: list[str]

    @property
    def is_total(self) -> bool:
        """Whether this row is a subtotal rather than an observation."""
        head = self.label.strip().lower()
        return head.startswith("total") or head.startswith("departamento ")


def read_indented_csv(
    payload: bytes,
    *,
    encoding: str,
    delimiter: str = ";",
) -> tuple[list[str], list[IndentedRow]]:
    """Read a table whose first column carries hierarchy as leading whitespace.

    Returns the header row and the data rows, each tagged with its indentation
    depth. The caller decides what each depth means, because it differs between
    tables: the NBI table nests country > department > district, while the
    per-department population tables nest total > district only.
    """
    text = payload.decode(encoding)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = [r for r in reader if r and any(cell.strip() for cell in r)]
    if not rows:
        raise ParseError("empty table")

    header = [cell.strip() for cell in rows[0]]
    out: list[IndentedRow] = []
    for row in rows[1:]:
        label_cell = row[0]
        indent = len(label_cell) - len(label_cell.lstrip(" \t"))
        out.append(
            IndentedRow(
                indent=indent,
                label=label_cell.strip(),
                values=[cell.strip() for cell in row[1:]],
            )
        )
    return header, out


def read_csv_dicts(
    payload: bytes,
    *,
    encoding: str,
    delimiter: str = ";",
) -> list[dict[str, str]]:
    """Read a flat CSV into dicts, stripping keys and values.

    Header keys are stripped because the 2012 district table ships headers with
    inconsistent spelling and stray characters (`Descripcin de Distrito`), and
    the callers match on position-independent, pre-agreed names.
    """
    text = payload.decode(encoding)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    out = []
    for row in reader:
        out.append(
            {
                (k or "").strip(): (v or "").strip()
                for k, v in row.items()
                if k is not None
            }
        )
    return out
