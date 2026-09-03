"""
Deployment configuration — the half of the old manifest that is not metadata.

This is written once by an engineer, lives in your repository, and changes when
the application changes. It says where the database is, what the limits are,
which chart kinds and commands this deployment enables, and who to ask about
entitlements. None of it describes the data model; that is the catalog's job.

The split matters because the two halves have different authors, different
change rates, and — most importantly — different trust. Configuration is trusted
because it is in your source tree. Catalog metadata may not be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AppConfig:
    name: str
    title: Dict[str, str]
    source: Dict[str, Any] = field(default_factory=dict)
    viz_enabled: List[str] = field(default_factory=list)
    commands_enabled: List[str] = field(default_factory=list)
    default_time_dim: Optional[str] = None
    max_rows: int = 5000
    statement_timeout_ms: int = 5000
    tenancy_hook: Optional[str] = None
    locales: List[str] = field(default_factory=lambda: ["en"])
    suggestions: List[str] = field(default_factory=list)
    currency: str = ""

    # Where the catalog comes from, and how its SQL is pinned.
    catalog_sources: List[Dict[str, Any]] = field(default_factory=list)
    catalog_lock: Optional[str] = None
    strict_lock: bool = True

    # Capability packs (Project C). Parsed now so that a deployment can declare
    # them before the packs exist, and so enabling one later is config only.
    capabilities: Dict[str, Any] = field(default_factory=dict)

    # Data plane budgets (Project B). Same reasoning.
    data_plane: Dict[str, Any] = field(default_factory=dict)


def _labels(raw: Any, fallback: str) -> Dict[str, str]:
    if raw is None:
        return {"en": fallback}
    if isinstance(raw, str):
        return {"en": raw}
    return dict(raw)


def _all_command_types() -> List[str]:
    """Imported lazily: `commands` imports `ir`, not this module, so there is no
    cycle — but a module-level import here would still be one."""
    from .commands import COMMAND_TYPES

    return list(COMMAND_TYPES)


def config_from_dict(raw: Dict[str, Any]) -> AppConfig:
    """Parse the configuration half of a board.yaml or a legacy manifest."""
    import os

    if "name" not in raw:
        raise ValueError("config is missing required key 'name'")

    limits = raw.get("limits", {})
    source = dict(raw.get("source", {}))
    if "dsn_env" in source:
        source.setdefault("dsn", os.environ.get(source["dsn_env"], ""))

    catalog_block = raw.get("catalog") or {}

    return AppConfig(
        name=raw["name"],
        title=_labels(raw.get("title"), raw["name"]),
        source=source,
        viz_enabled=list(raw.get("viz", {}).get("enabled", ["kpi", "line", "bar", "table"])),
        commands_enabled=list(raw.get("commands", {}).get("enabled", _all_command_types())),
        default_time_dim=raw.get("default_time_dim"),
        max_rows=int(limits.get("max_rows", 5000)),
        statement_timeout_ms=int(limits.get("statement_timeout_ms", 5000)),
        tenancy_hook=(raw.get("tenancy") or {}).get("hook"),
        locales=list(raw.get("locales", ["en"])),
        suggestions=list(raw.get("suggestions", [])),
        currency=str(raw.get("currency", "")),
        catalog_sources=list(catalog_block.get("sources", [])),
        catalog_lock=catalog_block.get("lock"),
        strict_lock=bool(catalog_block.get("strict_lock", True)),
        capabilities=dict(raw.get("capabilities", {})),
        data_plane=dict(raw.get("data_plane", {})),
    )
