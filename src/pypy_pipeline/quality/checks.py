"""Data-quality check primitives.

Every check is a small object that knows how to interrogate the warehouse and
returns a `CheckResult` -- never a bare boolean, never a raised exception on
failure. A failing check is data, not an error: it gets written to
`fact_quality_check` alongside the passing ones so that quality is queryable
over time rather than being a pass/fail gate that leaves no trace.

Checks are grouped into six families. The families are not decoration; they are
what lets a reader of the report distinguish "the source is incomplete" from
"our join is wrong" from "the file was already corrupt when it arrived".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

# Severity drives what happens to the run, not just how the row is coloured.
ERROR = "error"      # the warehouse is not trustworthy; the run fails
WARNING = "warning"  # a known defect in the source; recorded, run continues
INFO = "info"        # a measurement worth tracking with no pass/fail meaning

COMPLETENESS = "completeness"
UNIQUENESS = "uniqueness"
RANGE = "range"
REFERENTIAL = "referential"
FRESHNESS = "freshness"
CONFORMITY = "conformity"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    check_family: str
    target_table: str
    target_column: str | None
    severity: str
    status: str  # 'pass' | 'fail'
    expected: str | None
    observed: str | None
    failing_rows: int
    detail: str | None
    checked_at: datetime

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    @property
    def blocking(self) -> bool:
        return self.failed and self.severity == ERROR


class Check(Protocol):
    check_id: str

    def run(self, conn) -> CheckResult: ...


def _result(
    check_id: str,
    family: str,
    table: str,
    column: str | None,
    severity: str,
    passed: bool,
    expected: str,
    observed: str,
    failing_rows: int = 0,
    detail: str | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        check_family=family,
        target_table=table,
        target_column=column,
        severity=severity,
        status="pass" if passed else "fail",
        expected=expected,
        observed=observed,
        failing_rows=failing_rows,
        detail=detail,
        checked_at=datetime.now(timezone.utc),
    )


@dataclass
class RowCount:
    """Assert a table holds exactly, or at least, a given number of rows."""

    check_id: str
    table: str
    expected: int | None = None
    minimum: int | None = None
    severity: str = ERROR
    detail: str | None = None

    def run(self, conn) -> CheckResult:
        observed = conn.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0]
        if self.expected is not None:
            passed = observed == self.expected
            expected_text = f"= {self.expected}"
        else:
            passed = observed >= (self.minimum or 0)
            expected_text = f">= {self.minimum}"
        return _result(
            self.check_id, COMPLETENESS, self.table, None, self.severity,
            passed, expected_text, str(observed),
            failing_rows=0 if passed else abs(observed - (self.expected or self.minimum or 0)),
            detail=self.detail,
        )


@dataclass
class NotNull:
    """Assert a column's null rate stays under a threshold."""

    check_id: str
    table: str
    column: str
    max_null_ratio: float = 0.0
    severity: str = ERROR

    def run(self, conn) -> CheckResult:
        total, nulls = conn.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {self.column} IS NULL) "
            f"FROM {self.table}"
        ).fetchone()
        ratio = (nulls / total) if total else 0.0
        passed = ratio <= self.max_null_ratio
        return _result(
            self.check_id, COMPLETENESS, self.table, self.column, self.severity,
            passed, f"null ratio <= {self.max_null_ratio:.1%}",
            f"{ratio:.1%} ({nulls}/{total})", failing_rows=nulls,
        )


@dataclass
class Unique:
    """Assert a column or column set has no duplicates."""

    check_id: str
    table: str
    columns: tuple[str, ...]
    severity: str = ERROR

    def run(self, conn) -> CheckResult:
        cols = ", ".join(self.columns)
        duplicates = conn.execute(
            f"SELECT count(*) FROM (SELECT {cols} FROM {self.table} "
            f"GROUP BY {cols} HAVING count(*) > 1)"
        ).fetchone()[0]
        return _result(
            self.check_id, UNIQUENESS, self.table, cols, self.severity,
            duplicates == 0, "0 duplicate keys", f"{duplicates} duplicate keys",
            failing_rows=duplicates,
        )


@dataclass
class Range:
    """Assert every value of a column falls inside a closed interval.

    `where` narrows the check to one indicator, because a warehouse in long
    format holds percentages and headcounts in the same `value` column and they
    do not share a plausible range.
    """

    check_id: str
    table: str
    column: str
    minimum: float
    maximum: float
    where: str | None = None
    severity: str = ERROR

    def run(self, conn) -> CheckResult:
        clause = f"WHERE {self.where}" if self.where else ""
        total, outside = conn.execute(
            f"SELECT count(*), count(*) FILTER "
            f"(WHERE {self.column} < {self.minimum} OR {self.column} > {self.maximum}) "
            f"FROM {self.table} {clause}"
        ).fetchone()
        return _result(
            self.check_id, RANGE, self.table, self.column, self.severity,
            outside == 0,
            f"all values in [{self.minimum}, {self.maximum}]",
            f"{outside} of {total} outside",
            failing_rows=outside,
            detail=self.where,
        )


@dataclass
class ReferentialIntegrity:
    """Assert every foreign key in a fact table resolves in its dimension."""

    check_id: str
    table: str
    column: str
    parent_table: str
    parent_column: str
    severity: str = ERROR

    def run(self, conn) -> CheckResult:
        orphans = conn.execute(
            f"SELECT count(*) FROM {self.table} f "
            f"LEFT JOIN {self.parent_table} d "
            f"  ON f.{self.column} = d.{self.parent_column} "
            f"WHERE d.{self.parent_column} IS NULL"
        ).fetchone()[0]
        return _result(
            self.check_id, REFERENTIAL, self.table, self.column, self.severity,
            orphans == 0,
            f"every {self.column} present in {self.parent_table}",
            f"{orphans} orphan rows",
            failing_rows=orphans,
        )


@dataclass
class DimensionCoverage:
    """Assert a fact table covers a minimum share of its dimension.

    Referential integrity only proves that what is there is valid. This proves
    that what should be there is not missing -- the direction of the check that
    catches a partially loaded source.
    """

    check_id: str
    table: str
    column: str
    parent_table: str
    parent_column: str
    min_ratio: float = 1.0
    where: str | None = None
    severity: str = WARNING

    def run(self, conn) -> CheckResult:
        clause = f"WHERE {self.where}" if self.where else ""
        covered = conn.execute(
            f"SELECT count(DISTINCT {self.column}) FROM {self.table} {clause}"
        ).fetchone()[0]
        total = conn.execute(
            f"SELECT count(*) FROM {self.parent_table}"
        ).fetchone()[0]
        ratio = covered / total if total else 0.0
        return _result(
            self.check_id, COMPLETENESS, self.table, self.column, self.severity,
            ratio >= self.min_ratio,
            f">= {self.min_ratio:.0%} of {self.parent_table}",
            f"{ratio:.1%} ({covered}/{total})",
            failing_rows=total - covered,
            detail=self.where,
        )


@dataclass
class Freshness:
    """Assert the newest year in a table is recent enough.

    Statistical publication lags are long and uneven -- a census is decennial,
    the World Bank posts an annual series months after year end -- so the
    tolerance is set per source rather than globally.
    """

    check_id: str
    table: str
    column: str
    max_age_years: int
    reference_year: int
    where: str | None = None
    severity: str = WARNING

    def run(self, conn) -> CheckResult:
        clause = f"WHERE {self.where}" if self.where else ""
        latest = conn.execute(
            f"SELECT max({self.column}) FROM {self.table} {clause}"
        ).fetchone()[0]
        if latest is None:
            return _result(
                self.check_id, FRESHNESS, self.table, self.column, self.severity,
                False, f">= {self.reference_year - self.max_age_years}", "no rows",
                failing_rows=1, detail=self.where,
            )
        age = self.reference_year - int(latest)
        return _result(
            self.check_id, FRESHNESS, self.table, self.column, self.severity,
            age <= self.max_age_years,
            f"at most {self.max_age_years} years behind {self.reference_year}",
            f"latest = {latest} ({age} years behind)",
            failing_rows=0 if age <= self.max_age_years else 1,
            detail=self.where,
        )


@dataclass
class Conformity:
    """Assert every value of a column matches a regular expression."""

    check_id: str
    table: str
    column: str
    pattern: str
    severity: str = ERROR
    detail: str | None = None

    def run(self, conn) -> CheckResult:
        total, bad = conn.execute(
            f"SELECT count(*), count(*) FILTER "
            f"(WHERE NOT regexp_matches({self.column}, '{self.pattern}')) "
            f"FROM {self.table}"
        ).fetchone()
        return _result(
            self.check_id, CONFORMITY, self.table, self.column, self.severity,
            bad == 0, f"matches /{self.pattern}/", f"{bad} of {total} do not match",
            failing_rows=bad, detail=self.detail,
        )


@dataclass
class SqlCheck:
    """A check expressed directly in SQL.

    The query must return a single row of (failing_rows, observed_text). Used
    where the condition is genuinely specific to this warehouse and wrapping it
    in a generic primitive would obscure rather than clarify it.
    """

    check_id: str
    family: str
    table: str
    sql: str
    expected: str
    severity: str = ERROR
    column: str | None = None
    detail: str | None = None
    passes_when_zero: bool = True

    def run(self, conn) -> CheckResult:
        row = conn.execute(self.sql).fetchone()
        failing = int(row[0] or 0)
        observed = str(row[1]) if len(row) > 1 and row[1] is not None else str(failing)
        passed = (failing == 0) if self.passes_when_zero else (failing > 0)
        return _result(
            self.check_id, self.family, self.table, self.column, self.severity,
            passed, self.expected, observed, failing_rows=failing, detail=self.detail,
        )
