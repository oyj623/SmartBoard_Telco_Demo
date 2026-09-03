"""
Merging several catalog sources into one.

Most enterprises are in a messy middle state: structure is discoverable from the
warehouse, meanings live in a metadata service or someone's spreadsheet, and the
board-specific bits (format, direction, coverage) live nowhere yet. Composition
is what makes that state workable instead of a blocker.

The rule is **field-level, later wins**. A later source that omits a field leaves
the earlier value alone, so an overrides file can supply one label without having
to restate an entire metric. That is the difference between an override file
someone maintains and one they abandon.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from typing import Any, Dict, List, Sequence

from .base import Catalog, Dataset, Dimension, Metric

# Fields whose "unset" value is a legitimate value, so they can only be
# overridden explicitly rather than by truthiness.
_ALWAYS_TAKE = {"id"}


def _merge_entry(base: Any, over: Any) -> Any:
    """
    Merge one dataclass over another, field by field.

    A field is taken from the override when it is not the field's default. That
    means a source can set `direction: neutral` deliberately and it will not be
    confused with having said nothing, as long as the default differs — and for
    the fields where it does not, the outcome is identical anyway.
    """
    if base is None:
        return over
    if over is None:
        return base

    merged = {}
    for f in dataclass_fields(base):
        bval = getattr(base, f.name)
        oval = getattr(over, f.name)

        if f.name in _ALWAYS_TAKE:
            merged[f.name] = oval
            continue

        # Dicts merge rather than replace, so a service can add a Chinese label
        # without dropping the English one that came from the file.
        if isinstance(bval, dict) and isinstance(oval, dict):
            merged[f.name] = {**bval, **oval}
            continue

        default = f.default
        if default is not None and hasattr(f, "default_factory") and f.default_factory is not None:  # type: ignore[misc]
            try:
                default = f.default_factory()  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                default = None

        merged[f.name] = oval if oval != default and oval not in (None, "", [], {}) else bval

    return type(base)(**merged)


def _merge_map(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge_entry(out.get(key), value)
    return out


def merge_catalogs(catalogs: Sequence[Catalog]) -> Catalog:
    """
    Fold several catalogs into one, in declaration order.

    The result is trusted only if *every* contributing source was trusted — one
    untrusted source anywhere means the structural fields have to be locked,
    because a merge cannot tell you which source supplied the `expr` that ends up
    in your SQL.
    """
    if not catalogs:
        return Catalog()
    if len(catalogs) == 1:
        return catalogs[0]

    datasets: Dict[str, Dataset] = {}
    metrics: Dict[str, Metric] = {}
    dimensions: Dict[str, Dimension] = {}
    glossary: Dict[str, str] = {}

    for cat in catalogs:
        datasets = _merge_map(datasets, cat.datasets)
        metrics = _merge_map(metrics, cat.metrics)
        dimensions = _merge_map(dimensions, cat.dimensions)
        glossary.update(cat.glossary)

    return Catalog(
        datasets=datasets,
        metrics=metrics,
        dimensions=dimensions,
        glossary=glossary,
        trusted=all(c.trusted for c in catalogs),
    )


def build_catalog(sources: List[Any]) -> Catalog:
    """Load every source in order and merge them."""
    loaded = [s.load() for s in sources]
    merged = merge_catalogs(loaded)
    merged.validate()
    return merged
