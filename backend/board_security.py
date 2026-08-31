"""
The two security hooks Nusatel gives SmartBoard.

SmartBoard ships neither of these deliberately: who a user is, and what that
entitles them to, is the one thing a framework must not guess. Both functions
below read only from `SecurityContext`, which `routers/board.py` builds from the
verified JWT and the assignment table. Nothing the model emits reaches either.

    scope_by_state    row-level: which states' rows exist at all
    RoleAwareGuard    column-level: which metrics may be named at all
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .smartboard.ir import Query
from .smartboard.manifest import ManifestError
from .smartboard.security import QueryGuard, SecurityContext

# Every dataset whose FROM/JOIN chain reaches `states st`, and can therefore be
# scoped by an `st.code IN (...)` predicate.
#
# Listing them explicitly, rather than defaulting to "scope everything", means a
# newly added dataset is unscoped until someone adds it here — which fails
# closed on visibility and open on data, the right way round for a code review
# to catch. All seven currently reach `states`; the day one does not, this set
# is where that fact has to be recorded.
_STATE_SCOPED = {
    "network",
    "sites",
    "subscribers",
    "revenue",
    "costs",
    "alarms",
    "tickets",
}

# Metrics a regional manager may not see. Named as metrics rather than datasets
# so that adding a financial metric to a non-financial dataset later still trips
# the check.
#
# ARPU is deliberately NOT here. It is an operational number a regional manager
# needs in order to reason about churn, and it says nothing about the group's
# money. Revenue and cost do.
FINANCIAL_METRICS = {
    "revenue_myr",
    "cost_myr",
}

EXEC_ROLE = "exec"


def scope_by_state(
    ctx: SecurityContext,
    dataset: str,
) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Row-level tenancy.

    Returns SQL predicates the compiler appends *after* every filter the model
    supplied, in a separate parameter namespace with a collision check. There is
    no combination of model-chosen filters that can widen this.

    Executives see every state. Regional managers see only the states listed
    against them in `region_assignments`. A manager with no assignment sees
    nothing at all, which is the correct failure direction.
    """
    if EXEC_ROLE in ctx.roles:
        return []
    if dataset not in _STATE_SCOPED:
        return []

    state_codes = list(ctx.attributes.get("state_codes") or [])
    if not state_codes:
        return [("1 = 0", {})]

    params = {f"state_scope_{i}": code for i, code in enumerate(state_codes)}
    placeholders = ", ".join(f":{k}" for k in params)
    return [(f"st.code IN ({placeholders})", params)]


class RoleAwareGuard(QueryGuard):
    """
    Column-level entitlement, enforced server-side.

    The tool schema handed to the model is already filtered per role, so a
    well-behaved model never asks for a metric it cannot have. This class is the
    reason that does not matter: schema filtering is a convenience for the model,
    and this is the control.
    """

    def check(self, q: Query, ctx: SecurityContext) -> None:
        super().check(q, ctx)

        if EXEC_ROLE not in ctx.roles:
            blocked = sorted(set(q.metrics) & FINANCIAL_METRICS)
            if blocked:
                raise ManifestError(
                    f"metric(s) {', '.join(blocked)} require executive access — "
                    "answer the operational part of the question instead"
                )


def visible_metric_ids(manifest, ctx: SecurityContext) -> List[str]:
    """Which metrics this caller may name. Used to trim the generated tool schema."""
    if EXEC_ROLE in ctx.roles:
        return sorted(manifest.metrics)
    return sorted(set(manifest.metrics) - FINANCIAL_METRICS)
