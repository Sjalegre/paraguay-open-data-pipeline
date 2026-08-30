"""Project paths and source-registry loading."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"
EXPORT_DIR = DATA_DIR / "exports"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"

SOURCES_PATH = CONFIG_DIR / "sources.yml"


@functools.cache
def load_sources() -> dict[str, Any]:
    """Read and cache the declarative source registry."""
    with SOURCES_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ensure_dirs() -> None:
    for path in (DATA_DIR, RAW_DIR, REPORT_DIR, EXPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)
