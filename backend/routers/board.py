"""
The AI Board API — Nusatel's binding to SmartBoard.

There is no telco logic in this file, and there is not much of any other kind
either. Everything domain-specific lives in `backend/board_manifest.yaml`;
everything entitlement-specific lives in `backend/board_security.py`. The five
endpoints come from `create_board_router`. What is left is three things a
framework must not guess: where the database is, who is asking, and how the
assistant should talk about this particular business.

Note what is *not* here: an endpoint that accepts SQL. There isn't one, and
there is no code path anywhere in this service that would execute it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..board_security import RoleAwareGuard, scope_by_state, visible_metric_ids  # noqa: F401
from ..database import RegionAssignment, User, get_db
from ..smartboard import Engine, SecurityContext, load_manifest
from ..smartboard.adapters.sqlite import SQLiteAdapter
from ..smartboard.brain import brain_from_env
from ..smartboard.fastapi_binding import create_board_router

log = logging.getLogger("nusatel.board")

BACKEND_DIR = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BACKEND_DIR / "board_manifest.yaml"


def _build_engine() -> Engine:
    mf = load_manifest(MANIFEST_PATH)

    path = mf.source.get("path", "nusatel.db")
    if not os.path.isabs(path):
        path = str((MANIFEST_PATH.parent / path).resolve())

    adapter = SQLiteAdapter(
        path,
        timeout_ms=mf.statement_timeout_ms,
        read_only=mf.source.get("mode", "readonly") != "rw",
    )
    return Engine(mf, adapter, guard=RoleAwareGuard(mf))


ENGINE = _build_engine()
BRAIN = brain_from_env(ENGINE.mf, logger=log)
MANIFEST = ENGINE.mf


# The only prompt text in the project. Everything above it — the catalog, the
# tool schemas, the board snapshot — is generated from the manifest, which is
# why the prompt and the catalog cannot drift apart as metrics are added.
NUSATEL_VOICE = """

## Domain notes
You are the analyst for Nusatel, a Malaysian mobile and fibre operator running
about 2,500 sites across all sixteen states and federal territories.

Read numbers against these benchmarks and say when something is off, and by how
much: site availability 99.0%, 4G download 35 Mbps and 5G 200 Mbps, latency 38 ms
on 4G and 16 ms on 5G, drop rate under 1%, congestion under 70%. Monthly churn
runs near 2.9% prepaid, 1.1% postpaid and 0.8% fibre; ARPU near RM 31, RM 82 and
RM 128 respectively.

Sites are coded like SGR-0142 — the state code, then a sequence number. If
someone names a site without its prefix, filter with `contains` rather than
guessing a state.

MAPS. There are two, and they answer different questions. Use `map_regions` with
the `state_code` dimension when the answer is about *states* — coverage, churn,
revenue by geography. Use `map_points` with `site_location` when the answer is
about *individual masts* — which sites are congested, where the alarms are. Say
state names in prose even when you grouped by `state_code`.

Prefer `highlight` over drawing a fourth chart when the board already shows the
answer. Network problems usually show up as a chain — congestion first, then drop
rate, then tickets, then churn a month later — so when someone asks why a
commercial number moved, look for the operational cause behind it rather than
restating the commercial number a different way.

If a question needs a figure you cannot reach, say so plainly and offer the
closest thing in the catalog. Do not approximate with a metric that means
something else.
"""


def board_context(
    locale: str = "en",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SecurityContext:
    """
    Build the security context from the verified JWT and the assignment table.

    This is the hinge the whole entitlement story turns on: it reads the database
    and the token, never the request body, and never anything the model produced.
    If a value in here could be influenced by chat content, the tenancy predicate
    would be decoration.
    """
    state_codes = [
        a.state_code
        for a in db.query(RegionAssignment).filter(RegionAssignment.user_id == user.id).all()
    ]
    return SecurityContext(
        user_id=str(user.id),
        tenant_id=str(user.id),
        roles=[user.role],
        locale=locale,
        attributes={"state_codes": state_codes, "name": user.name},
    )


router = create_board_router(
    ENGINE,
    BRAIN,
    board_context,
    extra_system=NUSATEL_VOICE,
    visible_metrics=visible_metric_ids,
)
