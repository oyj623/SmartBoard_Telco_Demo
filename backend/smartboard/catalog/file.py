"""
Catalog from a YAML file.

The original shape, minus the configuration keys — so an existing manifest can be
split by moving `datasets`, `metrics`, `dimensions` and `glossary` into their own
file and leaving everything else behind.

A file source is `trusted` by default: its structural fields are in your source
tree, so they already have the property the lock exists to restore. Set
`trusted: false` on a file you generate into rather than author, and it will be
locked like any other external source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from .base import Catalog, Dataset, Dimension, ManifestError, Metric, labels


def catalog_from_dict(raw: Dict[str, Any], *, source: str = "file", trusted: bool = True) -> Catalog:
    """
    Parse the catalog half of a manifest.

    Shared by the file source and by `load_manifest`, so a single-file manifest
    and a split one cannot drift into parsing differently.
    """
    datasets: Dict[str, Dataset] = {}
    for did, d in (raw.get("datasets") or {}).items():
        if "from" not in d:
            raise ManifestError(f"dataset '{did}' is missing 'from'")
        datasets[did] = Dataset(
            id=did,
            from_=d["from"],
            joins=list(d.get("joins", [])),
            description=d.get("description", ""),
            layer=d.get("layer"),
            grain=list(d.get("grain", [])),
            covers=dict(d.get("covers", {})),
            escalates_to=dict(d.get("escalates_to", {})),
        )

    metrics: Dict[str, Metric] = {}
    for mid, m in (raw.get("metrics") or {}).items():
        if "expr" not in m or "dataset" not in m:
            raise ManifestError(f"metric '{mid}' needs both 'dataset' and 'expr'")
        metrics[mid] = Metric(
            id=mid,
            dataset=m["dataset"],
            expr=m["expr"],
            label=labels(m.get("label"), mid),
            unit=m.get("unit", ""),
            format=m.get("format", "number"),
            grain=list(m.get("grain", [])),
            direction=m.get("direction", "neutral"),
            description=m.get("description", ""),
            source=source,
            confidence=m.get("confidence", "high"),
        )

    dimensions: Dict[str, Dimension] = {}
    for did, d in (raw.get("dimensions") or {}).items():
        cols = d.get("columns")
        if cols is None and "column" in d:
            cols = {"*": d["column"]}
        if not cols:
            raise ManifestError(f"dimension '{did}' needs 'column' or 'columns'")
        dimensions[did] = Dimension(
            id=did,
            type=d.get("type", "string"),
            label=labels(d.get("label"), did),
            columns=dict(cols),
            values=d.get("values"),
            description=d.get("description", ""),
            geo=dict(d.get("geo", {})),
            native_grain=d.get("native_grain"),
            source=source,
            confidence=d.get("confidence", "high"),
        )

    return Catalog(
        datasets=datasets,
        metrics=metrics,
        dimensions=dimensions,
        glossary=dict(raw.get("glossary", {})),
        trusted=trusted,
    )


class FileSource:
    """A catalog source backed by a YAML file on disk."""

    kind = "file"

    def __init__(self, path: str | Path, trusted: bool = True):
        self.path = Path(path)
        self.trusted = trusted

    @property
    def name(self) -> str:
        return f"file:{self.path.name}"

    def load(self) -> Catalog:
        if not self.path.exists():
            raise ManifestError(f"catalog file not found: {self.path}")
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return catalog_from_dict(raw, source=self.name, trusted=self.trusted)
