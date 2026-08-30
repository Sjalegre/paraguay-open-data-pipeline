"""Client for the datos.gov.py catalogue.

The national portal runs DKAN, which exposes a CKAN-v3-compatible action API at
`/api/3/action/*`. Not all CKAN actions are implemented -- `package_search` and
`organization_list` return HTML rather than JSON -- so this client sticks to the
three actions that were verified to work: `package_list`,
`current_package_list_with_resources`, and `package_show`.

Resolving download URLs through the API rather than hard-coding them means an
upstream URL change surfaces as a named resolution failure instead of a 404 in
the middle of a run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..fetch import Session


class ResourceNotFound(LookupError):
    """Raised when a configured resource pattern matches nothing."""


@dataclass(frozen=True)
class Resource:
    """One downloadable resource as the catalogue describes it."""

    dataset: str
    name: str
    url: str
    declared_format: str


class Catalog:
    def __init__(self, session: Session, api_base: str) -> None:
        self._session = session
        self._api_base = api_base.rstrip("/")

    def package_show(self, dataset: str) -> dict:
        """Fetch one dataset's metadata, including its resources."""
        payload = self._session.get_json(
            f"catalog:{dataset}",
            f"{self._api_base}/package_show",
            params={"id": dataset},
        )
        result = payload.get("result")
        # DKAN wraps the result in a single-element list where CKAN returns a
        # bare object, and returns an empty list for datasets that are listed
        # in package_list but no longer resolve.
        while isinstance(result, list):
            if not result:
                raise ResourceNotFound(
                    f"dataset {dataset!r} is listed in the catalogue but "
                    f"package_show returns no record"
                )
            result = result[0]
        return result

    def resources(
        self, dataset: str, pattern: str, fmt: str | None = None
    ) -> list[Resource]:
        """Return every resource of `dataset` whose name or URL matches `pattern`.

        Both are tried because the catalogue is inconsistent about which one
        carries the identifying information: the census tables are named after
        their published table number ("Cuadro 1.2. Departamento Concepcion...")
        while the geospatial resources are named generically ("Distritos
        GEOJSON") and only the filename distinguishes them.

        `fmt` is usually necessary, not optional in practice: most tables are
        published twice under a byte-identical resource name, once as CSV and
        once as JSON, so the name alone does not identify a resource.
        """
        record = self.package_show(dataset)
        regex = re.compile(pattern)
        wanted = fmt.strip().lower() if fmt else None
        found = []
        for item in record.get("resources") or []:
            name = (item.get("name") or "").strip()
            url = (item.get("url") or "").strip()
            declared = (item.get("format") or "").strip().lower()
            if not (regex.search(name) or (url and regex.search(url))):
                continue
            if wanted and declared != wanted:
                continue
            if not url:
                # 8 resources in the catalogue carry no URL at all. Skipping
                # them here would hide that; the caller reports it.
                continue
            found.append(
                Resource(
                    dataset=dataset,
                    name=name,
                    url=url,
                    declared_format=declared,
                )
            )
        if not found:
            raise ResourceNotFound(
                f"no resource in {dataset!r} matched {pattern!r}"
                + (f" with format {fmt!r}" if fmt else "")
            )
        return found

    def one_resource(
        self, dataset: str, pattern: str, fmt: str | None = None
    ) -> Resource:
        """Return the single resource matching `pattern`, or fail loudly."""
        found = self.resources(dataset, pattern, fmt)
        if len(found) > 1:
            names = ", ".join(r.name for r in found[:4])
            raise ResourceNotFound(
                f"{pattern!r} matched {len(found)} resources in {dataset!r}: {names}"
            )
        return found[0]

    def audit_catalogue(self) -> dict:
        """Walk the whole catalogue and summarise its metadata health.

        This is the measurement behind the project's headline claim, so it is
        computed from the live catalogue on every run rather than quoted from a
        one-off analysis.
        """
        packages: list[dict] = []
        offset = 0
        while True:
            payload = self._session.get_json(
                "catalog:all",
                f"{self._api_base}/current_package_list_with_resources",
                params={"limit": 100, "offset": offset},
            )
            batch = payload.get("result")
            while isinstance(batch, list) and len(batch) == 1 and isinstance(batch[0], list):
                batch = batch[0]
            if not batch:
                break
            packages.extend(batch)
            offset += 100
            if offset > 5000:  # guard against a pagination bug upstream
                break

        formats: dict[str, int] = {}
        publishers: dict[str, int] = {}
        resources = 0
        empty_url = 0
        no_resources = 0
        for pkg in packages:
            items = pkg.get("resources") or []
            if not items:
                no_resources += 1
            publisher = (pkg.get("author") or "").strip()
            publishers[publisher or "(none declared)"] = (
                publishers.get(publisher or "(none declared)", 0) + 1
            )
            for item in items:
                resources += 1
                fmt = (item.get("format") or "").strip().lower() or "(none)"
                formats[fmt] = formats.get(fmt, 0) + 1
                if not (item.get("url") or "").strip():
                    empty_url += 1

        return {
            "packages": len(packages),
            "resources": resources,
            "packages_without_resources": no_resources,
            "resources_without_url": empty_url,
            "packages_without_publisher": publishers.get("(none declared)", 0),
            "distinct_publishers": len(publishers),
            "formats": dict(sorted(formats.items(), key=lambda kv: -kv[1])),
            "publishers": dict(sorted(publishers.items(), key=lambda kv: -kv[1])),
        }
