"""
Manifest — configuration and catalog, assembled.

Two things used to live in one file. They now have separate homes:

    smartboard/config.py     deployment configuration — yours, in your repo
    smartboard/catalog/      the data model — a file, introspection, or a service

`Manifest` is what the engine actually runs against: the two halves joined. It
keeps the exact shape it always had, deliberately, so that the compiler, the
guard, the tool generator and the session loop did not have to change at all.
That none of them needed touching is the measure of whether the seam was real.

Two entry points:

    load_manifest(path)   one file with everything — how both shipped demos work,
                          and how a small deployment should keep working forever
    load_board(path)      board.yaml plus catalog sources, with the SQL locked

The rule that keeps the whole design honest, unchanged from before: SQL fragments
(`expr`, `column`, `from`, `joins`) are trusted because you authored them.
`load_board` extends that to catalogs you did not author, by requiring their SQL
to match a digest you committed. See `smartboard/catalog/lock.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .catalog import Catalog, Dataset, Dimension, Metric, ManifestError, build_catalog, verify
from .catalog import sources_from_config
from .catalog.base import SAFE_ID, labels as _labels  # noqa: F401  (re-exported for compatibility)
from .catalog.file import catalog_from_dict
from .config import AppConfig, config_from_dict

log = logging.getLogger("smartboard.manifest")

__all__ = [
    "Dataset",
    "Dimension",
    "Manifest",
    "ManifestError",
    "Metric",
    "SAFE_ID",
    "load_board",
    "load_manifest",
]


@dataclass
class Manifest:
    """
    The assembled view the engine runs against.

    Still a flat dataclass with the same fields it always had, because
    `dataclasses.replace(manifest, metrics=...)` is how the API binding trims the
    catalog per role — and because a compatibility break here would reach every
    deployment for no benefit. The split happens at load time, which is where it
    matters, not in the object model.
    """

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
    currency: str = ""

    # Where this came from, for cache keys and audit. `catalog_fingerprint`
    # changes whenever any catalog field does — including a label — which is what
    # lets the API binding notice that the tool schemas it cached are stale.
    catalog_fingerprint: str = ""
    capabilities: Dict[str, Any] = field(default_factory=dict)
    data_plane: Dict[str, Any] = field(default_factory=dict)

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


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _assemble(config: AppConfig, catalog: Catalog) -> Manifest:
    return Manifest(
        name=config.name,
        title=config.title,
        source=config.source,
        datasets=catalog.datasets,
        metrics=catalog.metrics,
        dimensions=catalog.dimensions,
        viz_enabled=config.viz_enabled,
        commands_enabled=config.commands_enabled,
        default_time_dim=config.default_time_dim,
        max_rows=config.max_rows,
        statement_timeout_ms=config.statement_timeout_ms,
        tenancy_hook=config.tenancy_hook,
        locales=config.locales,
        glossary=catalog.glossary,
        suggestions=config.suggestions,
        currency=config.currency,
        catalog_fingerprint=catalog.fingerprint(),
        capabilities=config.capabilities,
        data_plane=config.data_plane,
    )


def load_manifest(path: str | Path) -> Manifest:
    """
    Load a single-file manifest — configuration and catalog together.

    This is how both shipped demos work and it stays supported without
    qualification. A deployment with one team and one YAML has nothing to gain
    from splitting it, and the catalog is trusted because it is in the repo.

    If the file carries a `catalog.sources` block, it is treated as a board.yaml
    and `load_board` takes over, so a deployment can migrate by adding a block
    rather than by changing its call site.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    if (raw.get("catalog") or {}).get("sources"):
        return load_board(path)

    for key in ("name", "datasets", "metrics", "dimensions"):
        if key not in raw:
            raise ManifestError(f"manifest is missing required key '{key}'")

    config = config_from_dict(raw)
    catalog = catalog_from_dict(raw, source=f"file:{path.name}", trusted=True)
    catalog.validate()
    return _assemble(config, catalog)


def load_board(
    path: str | Path,
    *,
    introspector: Any = None,
    adapter: Any = None,
) -> Manifest:
    """
    Load a board.yaml whose catalog comes from configured sources.

    `introspector` and `adapter` are supplied by the deployment rather than built
    from config, because both need live connections and credentials that belong
    to the application, not to a YAML file. Configuration selects and
    parameterises; it does not dial out on its own.

    The lock is verified before the manifest is returned. A catalog drawing on
    anything outside your repository must match the digests you committed, or
    this raises — because the alternative is executing SQL nobody reviewed.
    """
    path = Path(path)
    base_dir = path.parent
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    config = config_from_dict(raw)
    if not config.catalog_sources:
        raise ManifestError(
            f"{path.name} has no catalog.sources. Use load_manifest() for a single-file manifest."
        )

    sources = sources_from_config(
        config.catalog_sources, base_dir, adapter=adapter, introspector=introspector
    )
    catalog = build_catalog(sources)

    lock_path = config.catalog_lock
    if lock_path and not Path(lock_path).is_absolute():
        lock_path = str((base_dir / lock_path).resolve())

    diff = verify(catalog, lock_path, strict=config.strict_lock)
    if not diff.clean:
        log.warning("smartboard.catalog.unlocked %s", diff.describe())

    log.info(
        "smartboard.catalog.loaded sources=%d metrics=%d dimensions=%d trusted=%s fp=%s",
        len(sources), len(catalog.metrics), len(catalog.dimensions),
        catalog.trusted, catalog.fingerprint(),
    )
    return _assemble(config, catalog)
