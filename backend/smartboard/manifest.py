"""
Manifest — the one file that makes SmartBoard project-specific.

Everything the brain is allowed to do is declared here: which datasets exist,
which metrics and dimensions can be named, which viz kinds may be rendered,
which commands may be issued. Swap this file and the same engine drives a
different product.

Rule that keeps the whole design honest: SQL fragments (`expr`, `column`,
`from`, `joins`) come only from this file, which is authored by you and shipped
with your code. They are trusted. Everything arriving from the brain or the
browser is an *identifier* that must resolve against this catalog, or a *value*
that becomes a bound parameter. Nothing in between.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ManifestError(ValueError):
    pass


@dataclass
class Dataset:
    id: str
    from_: str
    joins: List[str] = field(default_factory=list)
    description: str = ""

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

    def column_for(self, dataset: str) -> Optional[str]:
        return self.columns.get(dataset) or self.columns.get("*")

    @property
    def is_geo(self) -> bool:
        return self.type == "geo" and bool(self.geo)


@dataclass
class Manifest:
    name: str
    title: Dict[str, str]
    source: Dict[str, Any]
    datasets: Dict[str, Dataset]
    metrics: Dict[str, Metric]
    dimensions: Dict[str, Dimension]
    viz_enabled: List[str]
    commands_enabled: List[str]
    default_time_dim: Optional[str] = None
    max_rows: int = 5000
    statement_timeout_ms: int = 5000
    tenancy_hook: Optional[str] = None
    locales: List[str] = field(default_factory=lambda: ["en"])
    glossary: Dict[str, str] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    currency: str = ""   # prefix for `format: currency` metrics, e.g. "RM" or "$"

    # ---- lookups -------------------------------------------------------

    def metric(self, mid: str) -> Metric:
        if mid not in self.metrics:
            raise ManifestError(f"unknown metric '{mid}'")
        return self.metrics[mid]

    def dimension(self, did: str) -> Dimension:
        if did not in self.dimensions:
            raise ManifestError(f"unknown dimension '{did}'")
        return self.dimensions[did]

    def resolve_dataset(self, metric_ids: List[str], dim_ids: List[str]) -> Dataset:
        """
        Pick the single dataset that can serve every requested metric and dimension.

        SmartBoard deliberately refuses to invent cross-dataset joins. If a question
        spans two fact tables, that is a modelling decision you make in the
        manifest by declaring a third dataset — not something an LLM improvises.
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

    def catalog_for_brain(self, locale: str = "en") -> Dict[str, Any]:
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
            "viz": self.viz_enabled,
            "commands": self.commands_enabled,
            "glossary": self.glossary,
        }


def _labels(raw: Any, fallback: str) -> Dict[str, str]:
    if raw is None:
        return {"en": fallback}
    if isinstance(raw, str):
        return {"en": raw}
    return dict(raw)


def _all_command_types() -> List[str]:
    """Imported lazily and relatively: `commands` imports `ir`, not `manifest`,
    so there is no cycle, but a module-level import here would still be one."""
    from .commands import COMMAND_TYPES

    return list(COMMAND_TYPES)


def load_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    for key in ("name", "datasets", "metrics", "dimensions"):
        if key not in raw:
            raise ManifestError(f"manifest is missing required key '{key}'")

    datasets: Dict[str, Dataset] = {}
    for did, d in raw["datasets"].items():
        if not SAFE_ID.match(did):
            raise ManifestError(f"dataset id '{did}' must be snake_case")
        datasets[did] = Dataset(
            id=did, from_=d["from"], joins=list(d.get("joins", [])), description=d.get("description", "")
        )

    metrics: Dict[str, Metric] = {}
    for mid, m in raw["metrics"].items():
        if not SAFE_ID.match(mid):
            raise ManifestError(f"metric id '{mid}' must be snake_case")
        if m["dataset"] not in datasets:
            raise ManifestError(f"metric '{mid}' references unknown dataset '{m['dataset']}'")
        metrics[mid] = Metric(
            id=mid,
            dataset=m["dataset"],
            expr=m["expr"],
            label=_labels(m.get("label"), mid),
            unit=m.get("unit", ""),
            format=m.get("format", "number"),
            grain=list(m.get("grain", [])),
            direction=m.get("direction", "neutral"),
            description=m.get("description", ""),
        )

    dimensions: Dict[str, Dimension] = {}
    for did, d in raw["dimensions"].items():
        if not SAFE_ID.match(did):
            raise ManifestError(f"dimension id '{did}' must be snake_case")
        cols = d.get("columns")
        if cols is None and "column" in d:
            cols = {"*": d["column"]}
        if not cols:
            raise ManifestError(f"dimension '{did}' needs 'column' or 'columns'")
        dimensions[did] = Dimension(
            id=did,
            type=d.get("type", "string"),
            label=_labels(d.get("label"), did),
            columns=dict(cols),
            values=d.get("values"),
            description=d.get("description", ""),
            geo=dict(d.get("geo", {})),
            native_grain=d.get("native_grain"),
        )

    limits = raw.get("limits", {})
    source = dict(raw.get("source", {}))
    if "dsn_env" in source:
        source.setdefault("dsn", os.environ.get(source["dsn_env"], ""))

    mf = Manifest(
        name=raw["name"],
        title=_labels(raw.get("title"), raw["name"]),
        source=source,
        datasets=datasets,
        metrics=metrics,
        dimensions=dimensions,
        viz_enabled=list(raw.get("viz", {}).get("enabled", ["kpi", "line", "bar", "table"])),
        commands_enabled=list(raw.get("commands", {}).get("enabled", _all_command_types())),
        default_time_dim=raw.get("default_time_dim"),
        max_rows=int(limits.get("max_rows", 5000)),
        statement_timeout_ms=int(limits.get("statement_timeout_ms", 5000)),
        tenancy_hook=(raw.get("tenancy") or {}).get("hook"),
        locales=list(raw.get("locales", ["en"])),
        glossary=dict(raw.get("glossary", {})),
        suggestions=list(raw.get("suggestions", [])),
        currency=str(raw.get("currency", "")),
    )
    return mf
