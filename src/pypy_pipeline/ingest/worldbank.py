"""Ingest national annual indicators from the World Bank API.

This is the clean half of the integration problem, and it is in the project as a
control: it is a documented JSON API with stable codes, so anything that goes
wrong downstream is attributable to the national sources rather than to the
pipeline. It is not, however, free of quality issues -- several series Paraguay
is nominally covered by are sparse or entirely null, and the pipeline reports
that rather than dropping them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..fetch import Session

# The API paginates at 50 by default; Paraguay's longest annual series is well
# under this, so one page per indicator is enough.
_PAGE_SIZE = 500


@dataclass(frozen=True)
class NationalObservation:
    indicator_code: str
    year: int
    value: float
    source_key: str


@dataclass(frozen=True)
class IndicatorMeta:
    code: str
    name: str
    unit: str
    expect_complete_from: int | None


@dataclass
class WorldBankOutcome:
    observations: list[NationalObservation]
    indicators: list[IndicatorMeta]
    empty_indicators: list[str]


def ingest(session: Session, cfg: dict, date_range: str = "1990:2026") -> WorldBankOutcome:
    """Pull every configured indicator for the configured country."""
    base = cfg["base_url"].rstrip("/")
    country = cfg["country"]

    observations: list[NationalObservation] = []
    indicators: list[IndicatorMeta] = []
    empty: list[str] = []

    for entry in cfg["indicators"]:
        code = entry["code"]
        indicators.append(
            IndicatorMeta(
                code=code,
                name=entry["name"],
                unit=entry.get("unit", ""),
                expect_complete_from=entry.get("expect_complete_from"),
            )
        )
        payload = session.get_json(
            f"worldbank:{code}",
            f"{base}/country/{country}/indicator/{code}",
            params={"format": "json", "per_page": _PAGE_SIZE, "date": date_range},
        )
        # The API answers with [metadata, rows]; an unknown indicator answers
        # with a single-element list carrying a message instead.
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else None
        if not rows:
            empty.append(code)
            continue

        kept = 0
        for row in rows:
            value = row.get("value")
            if value is None:
                # Null years are left out of the fact table rather than stored
                # as zero. The gap is what the completeness check measures.
                continue
            observations.append(
                NationalObservation(
                    indicator_code=code,
                    year=int(row["date"]),
                    value=float(value),
                    source_key=f"worldbank:{code}",
                )
            )
            kept += 1
        if kept == 0:
            # A series that exists but is null for every year Paraguay is
            # covered by. `GC.DOD.TOTL.GD.ZS` is one; reporting it is more
            # useful than quietly returning nothing.
            empty.append(code)

    return WorldBankOutcome(observations, indicators, empty)
