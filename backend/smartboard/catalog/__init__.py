"""
Catalog providers.

    from smartboard.catalog import build_catalog, sources_from_config

Three tiers, one interface. A deployment composes whichever it has:

    file         hand-authored YAML — always available, and the override layer
    introspect   the warehouse's own system catalogs plus naming conventions
    service      a metadata service, through an adapter

Structural fields — the ones that are SQL text — are locked unless every source
was your own source tree. See `lock.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    STRUCTURAL_FIELDS,
    Catalog,
    Dataset,
    Dimension,
    ManifestError,
    Metric,
    labels,
)
from .file import FileSource, catalog_from_dict
from .introspect import (
    ColumnInfo,
    Conventions,
    HiveIntrospector,
    IntrospectSource,
    Introspector,
    PartitionInfo,
    TableInfo,
    TableStats,
)
from .lock import LockDiff, LockMismatch, diff_lock, read_lock, verify, write_lock
from .merged import build_catalog, merge_catalogs
from .service import JSONFixtureAdapter, ServiceAdapter, ServiceSource

__all__ = [
    "STRUCTURAL_FIELDS",
    "Catalog",
    "ColumnInfo",
    "Conventions",
    "Dataset",
    "Dimension",
    "FileSource",
    "HiveIntrospector",
    "IntrospectSource",
    "Introspector",
    "JSONFixtureAdapter",
    "LockDiff",
    "LockMismatch",
    "ManifestError",
    "Metric",
    "PartitionInfo",
    "ServiceAdapter",
    "ServiceSource",
    "TableInfo",
    "TableStats",
    "build_catalog",
    "catalog_from_dict",
    "diff_lock",
    "labels",
    "merge_catalogs",
    "read_lock",
    "sources_from_config",
    "verify",
    "write_lock",
]


def sources_from_config(
    specs: List[Dict[str, Any]],
    base_dir: Path,
    *,
    adapter: Optional[ServiceAdapter] = None,
    introspector: Optional[Introspector] = None,
) -> List[Any]:
    """
    Turn the `catalog.sources` block of board.yaml into source objects.

    Live connections are not built here. An introspection source needs a
    warehouse connection and a service source may need credentials, and both are
    things the deployment owns — so they are passed in. Config selects and
    parameterises; it does not dial out on its own.
    """
    out: List[Any] = []

    for spec in specs:
        kind = spec.get("kind")

        if kind == "file":
            path = Path(spec["path"])
            if not path.is_absolute():
                path = (base_dir / path).resolve()
            out.append(FileSource(path, trusted=bool(spec.get("trusted", True))))

        elif kind == "introspect":
            if introspector is None:
                raise ManifestError(
                    "catalog source 'introspect' needs an Introspector. Pass one to load_board(): "
                    "load_board(path, introspector=HiveIntrospector(adapter))"
                )
            conventions = None
            if spec.get("conventions"):
                conv_path = Path(spec["conventions"])
                if not conv_path.is_absolute():
                    conv_path = (base_dir / conv_path).resolve()
                conventions = Conventions.load(conv_path)
            schemas = spec.get("schemas") or ([spec["schema"]] if spec.get("schema") else [])
            if not schemas:
                raise ManifestError("catalog source 'introspect' needs 'schema' or 'schemas'")
            out.append(IntrospectSource(introspector, schemas, conventions))

        elif kind == "service":
            chosen = adapter
            if chosen is None and spec.get("path"):
                path = Path(spec["path"])
                if not path.is_absolute():
                    path = (base_dir / path).resolve()
                chosen = JSONFixtureAdapter(path)
            if chosen is None:
                raise ManifestError(
                    "catalog source 'service' needs an adapter. Pass one to load_board(), or give "
                    "the source a 'path' to read an exported metadata document."
                )
            out.append(ServiceSource(chosen))

        else:
            raise ManifestError(
                f"unknown catalog source kind '{kind}' (expected file, introspect or service)"
            )

    return out
