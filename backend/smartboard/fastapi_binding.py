"""
FastAPI binding — the five endpoints, from one factory.

Everything a deployment's board router used to hand-write lives here now:

    router = create_board_router(engine, brain, context_dependency=board_context)

    GET  /manifest        labels, units, viz kinds — what the browser needs to render
    GET  /health          which brain is live, catalog size, caller's role
    POST /chat            SSE stream of one conversational turn
    GET  /result/{id}     full rows, fetched by the browser, never by the model
    POST /query           direct IR, no model in the loop — same guarded path

Note what is *not* here: an endpoint that accepts SQL. There isn't one, and there
is no code path anywhere in SmartBoard that would execute it.

The factory owns no identity. `context_dependency` is a FastAPI dependency you
write that turns your session/JWT into a `SecurityContext` — from verified
credentials, never from the request body, and never from anything the model
produced. If a value in that context could be influenced by chat content, the
tenancy predicate would be decoration.

Extension point: `prepare_turn(ctx, extra, defaults) -> overrides` runs before
each /chat turn. `defaults` carries {tools, system, handlers, allowed_actions,
brain}; return a dict with any of those keys to override them for this turn.
That is how a deployment adds domain tools (extra handlers), appends prompt
sections, swaps brains per mode, or narrows the command set — without forking
this file. The demo request's free-form `extra` dict rides in untouched.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any, Callable, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .brain import OpenAICompatBrain
from .brain.base import BrainClient, build_system_prompt
from .engine import Engine
from .manifest import Manifest, ManifestError
from .security import SecurityContext
from .session import TurnContext, run_turn
from .tools import build_tools, to_openai_format

log = logging.getLogger("smartboard.binding")

VisibleMetrics = Callable[[Manifest, SecurityContext], Iterable[str]]
PrepareTurn = Callable[[SecurityContext, Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


class ChatRequest(BaseModel):
    message: str
    history: List[Dict[str, Any]] = []
    board_state: Optional[Dict[str, Any]] = None
    locale: str = "en"
    # Free-form per-turn flags a deployment's UI sends (a mode toggle, say).
    # SmartBoard does not interpret them; they are handed to `prepare_turn`.
    extra: Dict[str, Any] = {}


class QueryRequest(BaseModel):
    query: Dict[str, Any]


def create_board_router(
    engine: Engine,
    brain: BrainClient,
    context_dependency: Callable[..., SecurityContext],
    *,
    extra_system: str = "",
    visible_metrics: Optional[VisibleMetrics] = None,
    allowed_actions: Optional[set] = None,
    prepare_turn: Optional[PrepareTurn] = None,
) -> APIRouter:
    """
    Build the board API around an engine and a brain.

    `visible_metrics(manifest, ctx)` trims the catalog per caller — the trimmed
    view drives both the /manifest response and the tool schema handed to the
    model, so a caller's model is never even offered a metric it may not have.
    That is a convenience; the engine's guard is the control. The trim is cached
    per `ctx.scope_key()`, so visibility must be a function of tenant + roles.
    """
    router = APIRouter()
    manifest = engine.mf

    # scope_key -> (manifest view, openai-format tool schema)
    _views: Dict[str, tuple] = {}

    def _scope_view(ctx: SecurityContext):
        key = ctx.scope_key()
        if key not in _views:
            if visible_metrics is None:
                view = manifest
            else:
                allowed = set(visible_metrics(manifest, ctx))
                view = replace(manifest, metrics={k: v for k, v in manifest.metrics.items() if k in allowed})
            if len(_views) > 256:  # a rotating cast of tenants should not grow this forever
                _views.clear()
            _views[key] = (view, to_openai_format(build_tools(view)))
        return _views[key]

    # -- endpoints ---------------------------------------------------------

    @router.get("/health")
    def health(ctx: SecurityContext = Depends(context_dependency)):
        view, _ = _scope_view(ctx)
        return {
            "ok": True,
            "manifest": manifest.name,
            "brain": getattr(brain, "name", brain.__class__.__name__),
            "live_model": isinstance(brain, OpenAICompatBrain),
            "metrics": len(view.metrics),
            "dimensions": len(manifest.dimensions),
            "role": ctx.roles[0] if ctx.roles else "anonymous",
        }

    @router.get("/manifest")
    def manifest_endpoint(ctx: SecurityContext = Depends(context_dependency)):
        """
        The catalog the browser needs to render labels, units and number formats,
        trimmed to what this caller may see.
        """
        view, _ = _scope_view(ctx)
        return {
            "name": manifest.name,
            "title": manifest.title,
            "locales": manifest.locales,
            "currency": manifest.currency,
            "viz": manifest.viz_enabled,
            "commands": manifest.commands_enabled,
            "suggestions": manifest.suggestions,
            "glossary": manifest.glossary,
            "metrics": {
                k: {
                    "label": v.label,
                    "unit": v.unit,
                    "format": v.format,
                    "direction": v.direction,
                    "dataset": v.dataset,
                    "description": v.description,
                }
                for k, v in view.metrics.items()
            },
            "dimensions": {
                k: {"label": v.label, "type": v.type, "values": v.values, "datasets": sorted(v.columns)}
                for k, v in manifest.dimensions.items()
            },
        }

    @router.get("/result/{result_id}")
    def result(result_id: str, ctx: SecurityContext = Depends(context_dependency)):
        """
        Full rows for a drawn panel. The model never sees this response — it only
        ever held a result_id, a column description and a three-row preview.
        """
        stored = engine.store.get(result_id)
        if not stored:
            raise HTTPException(404, "result expired or unknown")
        return {
            "result_id": stored.result_id,
            "label": stored.label,
            "columns": stored.columns,
            "rows": stored.rows,
            "sql": stored.sql,
            "params": stored.params,
            "dataset": stored.dataset,
            "elapsed_ms": stored.elapsed_ms,
        }

    @router.post("/query")
    def query(req: QueryRequest, ctx: SecurityContext = Depends(context_dependency)):
        """
        Direct IR access with no model in the loop. A default board built through
        this endpoint travels the exact same validate-compile-scope-execute path
        a model-issued query takes. One reducer, two callers.
        """
        try:
            handle = engine.run_query(req.query, ctx)
        except (ManifestError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        stored = engine.store.get(handle.result_id)
        return {
            **handle.model_dump(),
            "rows": stored.rows if stored else [],
            "sql": stored.sql if stored else "",
        }

    @router.post("/chat")
    def chat(req: ChatRequest, ctx: SecurityContext = Depends(context_dependency)):
        """One conversational turn, streamed as it happens."""
        view, tools = _scope_view(ctx)
        turn = TurnContext(messages=req.history, board_state=req.board_state, locale=req.locale)

        defaults: Dict[str, Any] = {
            "tools": tools,
            "system": build_system_prompt(view, req.board_state, req.locale) + extra_system,
            "handlers": None,
            "allowed_actions": allowed_actions,
            "brain": brain,
        }
        if prepare_turn is not None:
            defaults.update(prepare_turn(ctx, req.extra or {}, defaults) or {})

        def stream():
            try:
                for event in run_turn(
                    engine,
                    defaults["brain"],
                    req.message,
                    turn,
                    ctx,
                    tools=defaults["tools"],
                    system=defaults["system"],
                    tool_handlers=defaults["handlers"],
                    allowed_actions=defaults["allowed_actions"],
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            except Exception as exc:  # never leave the browser on an open stream
                log.exception("board turn failed")
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:300]})}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    return router
