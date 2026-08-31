"""
Security boundary.

Two ideas, both deliberately boring.

`SecurityContext` carries who is asking. It is populated by *your* auth
middleware from a session or JWT, never from anything the model produced. The
tenancy hook turns that context into SQL predicates the compiler appends after
every caller-supplied filter, so no query can escape its scope regardless of
what the model asks for.

`guard_query` enforces the caps that the IR's own field limits do not: how many
metrics may be combined, how wide a fan-out is permitted, and whether a
dimension the deployment marks as restricted may be grouped on at all.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .ir import Query
from .manifest import Manifest, ManifestError

TenancyHook = Callable[["SecurityContext", str], List[Tuple[str, Dict[str, Any]]]]


@dataclass
class SecurityContext:
    user_id: str = "anonymous"
    tenant_id: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    locale: str = "en"
    attributes: Dict[str, Any] = field(default_factory=dict)

    def scope_key(self) -> str:
        return f"{self.tenant_id or '-'}::{','.join(sorted(self.roles))}"


def load_tenancy_hook(dotted: Optional[str]) -> Optional[TenancyHook]:
    """Resolve 'package.module:function' from the manifest."""
    if not dotted:
        return None
    module_name, _, func_name = dotted.partition(":")
    module = importlib.import_module(module_name)
    hook = getattr(module, func_name)
    if not callable(hook):
        raise ManifestError(f"tenancy hook '{dotted}' is not callable")
    return hook


class QueryGuard:
    def __init__(
        self,
        manifest: Manifest,
        max_metrics: int = 8,
        max_dimensions: int = 4,
        restricted_dims: Optional[List[str]] = None,
    ):
        self.mf = manifest
        self.max_metrics = max_metrics
        self.max_dimensions = max_dimensions
        self.restricted_dims = set(restricted_dims or [])

    def check(self, q: Query, ctx: SecurityContext) -> None:
        if len(q.metrics) > self.max_metrics:
            raise ManifestError(f"too many metrics in one query (max {self.max_metrics})")
        if len(q.dimensions) > self.max_dimensions:
            raise ManifestError(f"too many dimensions in one query (max {self.max_dimensions})")

        for m in q.metrics:
            self.mf.metric(m)
        for d in q.dimensions:
            self.mf.dimension(d)
        for f in q.filters:
            self.mf.dimension(f.dim)

        blocked = self.restricted_dims - set(ctx.roles)
        for d in q.dimensions:
            if d in blocked:
                raise ManifestError(f"dimension '{d}' requires elevated access")

        if q.time_range and q.time_range.dim:
            dim = self.mf.dimension(q.time_range.dim)
            if dim.type != "time":
                raise ManifestError(f"time_range.dim '{q.time_range.dim}' is not a time dimension")


def no_tenancy(ctx: SecurityContext, dataset: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Default hook for single-tenant demos. Replace it in any real deployment."""
    return []
