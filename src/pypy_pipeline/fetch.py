"""HTTP access with recorded provenance.

Every byte that enters the warehouse arrives through `fetch`, which records
where it came from, when, how large it was, what content type the server
claimed, and the SHA-256 of the payload. Those records become `dim_source`, so
any number in the warehouse can be traced back to a specific retrieval of a
specific URL -- and the conformity checks can compare the format the catalogue
*declares* against the content type the server actually *served*.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from .config import RAW_DIR

USER_AGENT = (
    "paraguay-open-data-pipeline/1.0 "
    "(+https://github.com/sjalegre/paraguay-open-data-pipeline)"
)

_RETRY_STATUS = {429, 500, 502, 503, 504}
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class FetchError(RuntimeError):
    """Raised when a source cannot be retrieved after retries."""


@dataclass(frozen=True)
class Provenance:
    """What we know about one retrieval, independent of what it contained."""

    source_key: str
    url: str
    fetched_at: datetime
    http_status: int
    content_type: str
    content_length: int
    sha256: str
    declared_format: str | None = None
    cache_path: str | None = None
    from_cache: bool = False

    @property
    def served_format(self) -> str:
        """Coarse format implied by the served Content-Type header."""
        ct = self.content_type.split(";", 1)[0].strip().lower()
        return {
            "text/csv": "csv",
            "application/csv": "csv",
            "application/json": "json",
            "text/json": "json",
            "application/geo+json": "geojson",
            "text/html": "html",
            "application/pdf": "pdf",
            "application/vnd.ms-excel": "xls",
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet": "xlsx",
        }.get(ct, ct or "unknown")

    @property
    def format_matches_declared(self) -> bool | None:
        """Whether the catalogue's declared format matches what was served.

        `None` when there is nothing to compare -- either the catalogue declared
        no format, or the payload came from cache and carries no response
        headers. Returning `False` in the cached case would report every
        resource as a mismatch, which is a property of the cache and not of the
        data. The `excel` / `xlsx` / `xls` spread in the national catalogue is
        treated as a single family, since that inconsistency is a metadata
        defect rather than a content defect.
        """
        if not self.declared_format or self.from_cache or not self.content_type:
            return None
        family = {"excel": "xlsx", "xls": "xlsx", "geojson": "json"}
        declared = self.declared_format.strip().lower()
        declared = family.get(declared, declared)
        served = family.get(self.served_format, self.served_format)
        return declared == served


@dataclass
class Session:
    """A requests session that accumulates provenance for the whole run."""

    timeout: int = 90
    max_retries: int = 3
    backoff: float = 2.0
    # Re-read a previously downloaded payload instead of hitting the network.
    # The district GeoJSON alone is 46 MB for five attribute fields, so a
    # developer iterating on transformations should not have to re-pull it.
    # Off by default: a scheduled run must see the live source.
    reuse_cache: bool = False
    records: list[Provenance] = field(default_factory=list)
    _http: requests.Session = field(default_factory=requests.Session, repr=False)

    def __post_init__(self) -> None:
        self._http.headers.update({"User-Agent": USER_AGENT})

    def fetch(
        self,
        source_key: str,
        url: str,
        *,
        declared_format: str | None = None,
        cache: bool = True,
    ) -> bytes:
        """Retrieve `url`, record provenance, and return the raw bytes.

        Bytes are returned undecoded on purpose. Several sources are cp1252
        served without a charset parameter, so letting `requests` guess the
        encoding would corrupt exactly the characters this project is about.
        Decoding is the caller's decision, driven by `config/sources.yml`.
        """
        if self.reuse_cache:
            cached = self._cache_path(source_key, url)
            if cached.exists():
                payload = cached.read_bytes()
                self.records.append(
                    Provenance(
                        source_key=source_key,
                        url=url,
                        fetched_at=datetime.fromtimestamp(
                            cached.stat().st_mtime, tz=timezone.utc
                        ),
                        http_status=200,
                        content_type="",
                        content_length=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest(),
                        declared_format=declared_format,
                        cache_path=str(cached),
                        from_cache=True,
                    )
                )
                return payload

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._http.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.backoff ** attempt)
                continue

            if response.status_code in _RETRY_STATUS and attempt < self.max_retries:
                time.sleep(self.backoff ** attempt)
                continue

            if response.status_code != 200:
                raise FetchError(
                    f"{source_key}: HTTP {response.status_code} for {url}"
                )

            payload = response.content
            record = Provenance(
                source_key=source_key,
                url=url,
                fetched_at=datetime.now(timezone.utc),
                http_status=response.status_code,
                content_type=response.headers.get("Content-Type", ""),
                content_length=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                declared_format=declared_format,
                cache_path=self._cache(source_key, url, payload) if cache else None,
            )
            self.records.append(record)
            return payload

        raise FetchError(f"{source_key}: could not retrieve {url}") from last_error

    def get_json(self, source_key: str, url: str, params: dict | None = None):
        """Fetch and parse JSON, recording provenance for the request."""
        if params:
            prepared = requests.Request("GET", url, params=params).prepare()
            url = prepared.url
        import json

        return json.loads(self.fetch(source_key, url).decode("utf-8"))

    @staticmethod
    def _cache_path(source_key: str, url: str):
        # The URL, not the source key alone, decides the filename: one source
        # key legitimately pulls many files (17 census tables, for instance).
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        stem = _UNSAFE_FILENAME_RE.sub("_", source_key)[:60]
        return RAW_DIR / f"{stem}.{digest}"

    def _cache(self, source_key: str, url: str, payload: bytes) -> str:
        """Write the payload to `data/raw` so a run is inspectable after the fact."""
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(source_key, url)
        path.write_bytes(payload)
        return str(path)
