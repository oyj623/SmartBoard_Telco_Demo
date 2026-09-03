"""
The catalog — datasets, metrics and dimensions, wherever they came from.

This is the half of the old manifest that describes *the data model*. It is
authored by whoever owns that model, changes constantly, and may arrive from a
file, from warehouse introspection, or from a metadata service. The other half —
deployment configuration — lives in `smartboard.config` and stays in your repo.

Two field classes, and the distinction is the whole reason this split is safe:

    SEMANTIC    label, unit, format, direction, description, values, glossary
                Never reaches SQL text. May change freely from any source.

    STRUCTURAL  expr, from, joins, column(s), geo lat/lng
                *Is* SQL text. Trusted for exactly the reason your route
                handlers are trusted — because you shipped it. When it arrives
                from somewhere else, it must match a digest you committed.
                See `smartboard.catalog.lock`.

Get that distinction wrong and the sentence the whole framework rests on stops
being true, because whoever can edit a description in a metadata UI can also
rewrite a metric's SQL.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ManifestError(ValueError):
    """Kept under this name because it is part of the public API and every test names it."""


# Which fields on each kind carry SQL text. The lock covers exactly these.
STRUCTURAL_FIELDS = {
    "dataset": ("from_", "joins"),
    "metric": ("expr",),
    "dimension": ("columns", "geo"),
}


@dataclass
class Dataset:
    id: str
    from_: str
    joins: List[str] = field(default_factory=list)
    description: str = ""

    # -- coverage, for the data plane (Project B) -------------------------
    # Parsed and exposed now so that adding the coverage router later is purely
    # additive rather than a re-pull of every catalog in every deployment.
    # Nothing in the engine reads these yet.
    layer: Optional[str] = None                      # L1 | L2 | L3 | L4
    grain: List[str] = field(default_factory=list)   # finest slice stored here
    covers: Dict[str, Any] = field(default_factory=dict)        # {dimensions: [...], time_grain: day}
    escalates_to: Dict[str, Any] = field(default_factory=dict)  # {model, layer, note}

    def sql_from(self) -> str:
        return " ".join([f"FROM {self.from_}", *self.joins])


@dataclass
class Metric:
    id: str
    dataset: str
    expr: str
    label: Dict[str, str]
    unit: str = ""
    format: str = "number"       # number | currency | percent | duration | bytes
    grain: List[str] = field(default_factory=list)
    direction: str = "neutral"   # up_good | down_good | neutral — drives colour of deltas
    description: str = ""

    # Where this entry came from, and how much to trust its semantics. An
    # inferred metric renders, but never carries a benchmark line — a guess
    # should not quietly become a judgement. See catalog/introspect.py.
    source: str = "file"
    confidence: str = "high"     # high | low


@dataclass
class Dimension:
    id: str
    type: str                    # string | time | number | geo
    label: Dict[str, str]
    columns: Dict[str, str] = field(default_factory=dict)   # dataset id -> SQL expression
    values: Optional[List[str]] = None                      # small enums, given to the brain verbatim
    description: str = ""
    geo: Dict[str, str] = field(default_factory=dict)       # {"lat": "...", "lng": "..."}
    native_grain: Optional[str] = None                      # grain the column is already stored at

    source: str = "file"
    confidence: str = "high"

    def column_for(self, dataset: str) -> Optional[str]:
        return self.columns.get(dataset) or self.columns.get("*")

    @property
    def is_geo(self) -> bool:
        return self.type == "geo" and bool(self.geo)


class Catalog:
    """
    A resolved data model: datasets, metrics, dimensions and a glossary.

    Concrete rather than abstract, because every source produces one of these
    and merging them is a field-level operation on plain dicts. A deployment
    that needs something exotic can duck-type this surface — the engine only
    ever calls `metric`, `dimension`, `resolve_dataset`, and iterates.
    """

    def __init__(
        self,
        datasets: Optional[Dict[str, Dataset]] = None,
        metrics: Optional[Dict[str, Metric]] = None,
        dimensions: Optional[Dict[str, Dimension]] = None,
        glossary: Optional[Dict[str, str]] = None,
        trusted: bool = False,
    ):
        self.datasets: Dict[str, Dataset] = dict(datasets or {})
        self.metrics: Dict[str, Metric] = dict(metrics or {})
        self.dimensions: Dict[str, Dimension] = dict(dimensions or {})
        self.glossary: Dict[str, str] = dict(glossary or {})
        # `trusted` means the structural fields came from your own source tree
        # and need no lock. A single in-repo YAML is trusted; a metadata service
        # is not, however much you like it.
        self.trusted = trusted

    # -- lookups ---------------------------------------------------------

    def metric(self, mid: str) -> Metric:
        if mid not in self.metrics:
            raise ManifestError(f"unknown metric '{mid}'")
        return self.metrics[mid]

    def dimension(self, did: str) -> Dimension:
        if did not in self.dimensions:
            raise ManifestError(f"unknown dimension '{did}'")
        return self.dimensions[did]

    def dataset(self, dsid: str) -> Dataset:
        if dsid not in self.datasets:
            raise ManifestError(f"unknown dataset '{dsid}'")
        return self.datasets[dsid]

    def resolve_dataset(self, metric_ids: List[str], dim_ids: List[str]) -> Dataset:
        """
        Pick the single dataset that can serve every requested metric and dimension.

        SmartBoard deliberately refuses to invent cross-dataset joins. If a question
        spans two fact tables, that is a modelling decision you make in the
        catalog by declaring a third dataset — not something an LLM improvises.
        """
        wanted = {self.metric(m).dataset for m in metric_ids}
        if len(wanted) > 1:
            raise ManifestError(
                "metrics span multiple datasets "
                f"({', '.join(sorted(wanted))}); ask them as separate queries"
            )
        ds_id = wanted.pop()
        ds = self.datasets[ds_id]
        for d in dim_ids:
            if self.dimension(d).column_for(ds_id) is None:
                raise ManifestError(f"dimension '{d}' is not available on dataset '{ds_id}'")
        return ds

    # -- identity --------------------------------------------------------

    def fingerprint(self) -> str:
        """
        A digest of the whole catalog, semantic fields included.

        Used as a cache key: tool schemas and the system prompt are derived from
        the catalog, so a label change has to invalidate them or the model keeps
        being handed a stale enum.
        """
        blob = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        def _ds(d: Dataset) -> Dict[str, Any]:
            return {
                "from": d.from_, "joins": list(d.joins), "description": d.description,
                "layer": d.layer, "grain": list(d.grain),
                "covers": d.covers, "escalates_to": d.escalates_to,
            }

        def _m(m: Metric) -> Dict[str, Any]:
            return {
                "dataset": m.dataset, "expr": m.expr, "label": m.label, "unit": m.unit,
                "format": m.format, "grain": list(m.grain), "direction": m.direction,
                "description": m.description, "confidence": m.confidence,
            }

        def _dim(x: Dimension) -> Dict[str, Any]:
            return {
                "type": x.type, "label": x.label, "columns": x.columns, "values": x.values,
                "description": x.description, "geo": x.geo, "native_grain": x.native_grain,
                "confidence": x.confidence,
            }

        return {
            "datasets": {k: _ds(v) for k, v in sorted(self.datasets.items())},
            "metrics": {k: _m(v) for k, v in sorted(self.metrics.items())},
            "dimensions": {k: _dim(v) for k, v in sorted(self.dimensions.items())},
            "glossary": dict(sorted(self.glossary.items())),
        }

    # -- prompt surface --------------------------------------------------

    def for_brain(self, locale: str = "en") -> Dict[str, Any]:
        """A compact catalog description, injected into the system prompt."""
        return {
            "metrics": [
                {
                    "id": m.id,
                    "label": m.label.get(locale, m.label.get("en", m.id)),
                    "unit": m.unit,
                    "format": m.format,
                    "dataset": m.dataset,
                    "grain": m.grain,
                    "direction": m.direction,
                    "about": m.description,
                }
                for m in self.metrics.values()
            ],
            "dimensions": [
                {
                    "id": d.id,
                    "label": d.label.get(locale, d.label.get("en", d.id)),
                    "type": d.type,
                    "datasets": sorted(d.columns.keys()),
                    "values": d.values,
                    "about": d.description,
                }
                for d in self.dimensions.values()
            ],
            "glossary": self.glossary,
        }

    def validate(self) -> None:
        """Structural integrity, checked once at load rather than at query time."""
        for mid, m in self.metrics.items():
            if not SAFE_ID.match(mid):
                raise ManifestError(f"metric id '{mid}' must be snake_case")
            if m.dataset not in self.datasets:
                raise ManifestError(f"metric '{mid}' references unknown dataset '{m.dataset}'")
        for did in self.dimensions:
            if not SAFE_ID.match(did):
                raise ManifestError(f"dimension id '{did}' must be snake_case")
        for dsid in self.datasets:
            if not SAFE_ID.match(dsid):
                raise ManifestError(f"dataset id '{dsid}' must be snake_case")

    def __repr__(self) -> str:
        return (
            f"<Catalog {len(self.datasets)} datasets, {len(self.metrics)} metrics, "
            f"{len(self.dimensions)} dimensions, fp={self.fingerprint()}>"
        )


def labels(raw: Any, fallback: str) -> Dict[str, str]:
    if raw is None:
        return {"en": fallback}
    if isinstance(raw, str):
        return {"en": raw}
    return dict(raw)
