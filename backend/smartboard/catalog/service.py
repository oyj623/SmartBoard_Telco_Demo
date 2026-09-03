"""
Catalog from a metadata service.

No service exists to test against yet, so what ships here is the *seam* and one
adapter that reads a JSON fixture. That is deliberate: proving the interface
holds is worth doing now, while inventing an untested HTTP client for a service
whose shape nobody has seen is not.

Adding a real adapter later means one class implementing `ServiceAdapter` and a
line of config. Nothing in the engine changes, because everything below this
already speaks `Catalog`.

A service catalog is never trusted. Its structural fields go through the lock —
see `smartboard.catalog.lock` for why that is the difference between a metadata
service being useful and being a remote code execution channel.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Protocol

from .base import Catalog, ManifestError
from .file import catalog_from_dict


class ServiceAdapter(Protocol):
    """
    Fetch a catalog document from wherever the metadata lives.

    The contract is deliberately thin: return the same dict shape a catalog YAML
    would have. Mapping a vendor's model — OpenMetadata, DataHub, Atlan — onto
    that shape is the adapter's whole job, and keeping it in one method means a
    vendor change is one file.
    """

    name: str

    def fetch(self) -> Dict[str, Any]: ...


class JSONFixtureAdapter:
    """
    Reads a catalog document from a JSON file.

    The reference implementation, and what the tests run against. It is also
    genuinely useful: a scheduled job can export from whatever internal system
    holds your metadata into this shape, and the board consumes it with no
    bespoke client at all.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.name = f"json:{self.path.name}"

    def fetch(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise ManifestError(f"metadata document not found: {self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))


class ServiceSource:
    """A catalog source backed by a metadata service adapter."""

    kind = "service"

    def __init__(self, adapter: ServiceAdapter):
        self.adapter = adapter
        self.trusted = False

    @property
    def name(self) -> str:
        return f"service:{self.adapter.name}"

    def load(self) -> Catalog:
        return catalog_from_dict(self.adapter.fetch(), source=self.name, trusted=False)
