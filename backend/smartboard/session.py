"""
The turn loop.

Provider-agnostic by construction: it talks to a BrainClient, an Engine, and
nothing else. It yields events as they happen rather than returning at the end,
because a first panel appearing in about a second feels like a conversation and
a six-second wait for the whole board does not.

Event types on the wire:
    status    — what the system is doing, for the chat's activity line
    text      — assistant prose
    result    — a query completed (id, label, row count, dataset, timing)
    command   — one validated dashboard command; apply it immediately
    error     — something went wrong, with a message worth showing
    done      — turn complete, includes usage
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from .brain.base import AssistantTurn, BrainClient, build_system_prompt
from .engine import Engine
from .manifest import ManifestError
from .security import SecurityContext
from .tools import build_tools, to_openai_format

log = logging.getLogger("smartboard.session")

MAX_TOOL_ROUNDS = 6


@dataclass
class TurnContext:
    """Everything one turn needs. History is owned by the caller, not by SmartBoard."""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    board_state: Optional[Dict[str, Any]] = None
    locale: str = "en"


def run_turn(
    engine: Engine,
    brain: BrainClient,
    user_message: str,
    turn: TurnContext,
    ctx: SecurityContext,
    tools: Optional[List[Dict[str, Any]]] = None,
    system: Optional[str] = None,
    tool_handlers: Optional[Dict[str, Any]] = None,
    allowed_actions: Optional[set] = None,
) -> Iterator[Dict[str, Any]]:
    # Callers may pass a pre-built tool schema and system prompt so entitlements
    # can be reflected in what the model is even offered. Both remain
    # conveniences — the server-side guard is what enforces access.
    system = system or build_system_prompt(engine.mf, turn.board_state, turn.locale)
    tools = tools if tools is not None else to_openai_format(build_tools(engine.mf))

    # Extra tools beyond query_metrics and apply_commands, dispatched by name.
    # A deployment adds domain tools here (see `prepare_turn` in the FastAPI
    # binding). Each handler takes the raw arguments dict and returns a
    # JSON-serialisable payload; the loop below turns an exception into a tool
    # result rather than an error, so the model self-corrects instead of the
    # person seeing a stack trace.
    handlers: Dict[str, Any] = dict(tool_handlers or {})

    messages: List[Dict[str, Any]] = [*turn.messages, {"role": "user", "content": user_message}]
    result_ids: set = set()
    usage: Dict[str, int] = {}

    # Did anything the person can actually see come out of this turn? A turn
    # that refuses every round — an entitlement the caller does not have, a
    # metric that is not in the catalog — would otherwise end in silence, and
    # silence in a chat column reads as a broken app rather than as an answer.
    # See the closing block below.
    spoke = False
    changed = False
    last_refusal: Optional[str] = None

    for round_no in range(MAX_TOOL_ROUNDS):
        yield {"type": "status", "stage": "thinking", "round": round_no + 1}

        try:
            reply: AssistantTurn = brain.complete(messages, tools, system)
        except Exception as exc:  # provider errors are the common failure in production
            log.exception("brain call failed")
            yield {"type": "error", "message": _readable(exc)}
            return

        for k, v in (reply.usage or {}).items():
            if isinstance(v, int):
                usage[k] = usage.get(k, 0) + v

        if reply.text:
            spoke = True
            yield {"type": "text", "text": reply.text}

        if not reply.tool_calls:
            messages.append({"role": "assistant", "content": reply.text})
            break

        messages.append(
            {
                "role": "assistant",
                "content": reply.text or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for tc in reply.tool_calls
                ],
            }
        )

        for tc in reply.tool_calls:
            payload: Dict[str, Any]

            if tc.name == "query_metrics":
                yield {"type": "status", "stage": "querying", "label": tc.arguments.get("label")}
                try:
                    handle = engine.run_query(tc.arguments, ctx)
                    result_ids.add(handle.result_id)
                    stored = engine.store.get(handle.result_id)
                    # The compiled SQL is deliberately NOT on this event.
                    # It is logged server-side under `smartboard.query` for audit,
                    # where an auditor can find it; putting it in the chat
                    # transcript only asked the person reading the answer to
                    # scroll past a SELECT statement to reach it.
                    yield {
                        "type": "result",
                        "result_id": handle.result_id,
                        "label": handle.label,
                        "row_count": handle.row_count,
                        "columns": handle.columns,
                        "dataset": stored.dataset if stored else "",
                        "elapsed_ms": handle.elapsed_ms,
                    }
                    payload = handle.model_dump()
                    payload["dataset"] = stored.dataset if stored else ""
                except (ManifestError, ValueError) as exc:
                    # Hand the error back to the brain rather than to the user.
                    # It nearly always self-corrects on the next round.
                    last_refusal = str(exc)
                    payload = {"error": str(exc), "hint": "Use only catalog ids listed in the system prompt."}
                    yield {"type": "status", "stage": "retrying", "detail": str(exc)}

            elif tc.name == "apply_commands":
                raw = tc.arguments.get("commands") or []
                outcome = engine.validate_commands(
                    raw, ctx, known_result_ids=result_ids, allowed_actions=allowed_actions
                )
                for cmd in outcome.accepted:
                    changed = True
                    yield {"type": "command", "command": cmd}
                payload = {
                    "applied": len(outcome.accepted),
                    "rejected": outcome.rejected,
                    "message": outcome.feedback(),
                }
                if outcome.rejected:
                    last_refusal = outcome.rejected[0]["error"]
                    yield {"type": "status", "stage": "retrying", "detail": outcome.rejected[0]["error"]}

            elif tc.name in handlers:
                yield {"type": "status", "stage": "reasoning", "label": tc.name}
                try:
                    payload = handlers[tc.name](tc.arguments, ctx)
                    if isinstance(payload, dict) and payload.get("__event__"):
                        # A handler may ask for something to reach the browser
                        # directly — an action proposal needs a confirm button,
                        # and that is not a dashboard command.
                        yield payload["__event__"]
                        payload = payload.get("result", {})
                except Exception as exc:  # noqa: BLE001 — hand it back to the model
                    log.warning("tool %s failed: %s", tc.name, exc)
                    payload = {"error": str(exc)[:400]}
                    yield {"type": "status", "stage": "retrying", "detail": str(exc)[:200]}

            else:
                payload = {"error": f"unknown tool '{tc.name}'"}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                }
            )

        # Nothing left to correct and the board has been touched: stop early.
        if all(tc.name == "apply_commands" for tc in reply.tool_calls) and reply.text:
            break

    # A turn that said nothing and drew nothing has to account for itself. This
    # happens when every round was refused — an entitlement the caller does not
    # have, or a metric that is simply not in the catalog — and the brain kept
    # trying the same thing. Ending in silence leaves the person looking at
    # their own message wondering whether the app is broken, which is the worst
    # of the available outcomes: the refusal was correct and we hid it.
    if not spoke and not changed:
        if last_refusal:
            message = (
                f"I could not answer that: {last_refusal}. "
                "Try asking for something in the catalog, or ask someone with wider access."
            )
        else:
            message = (
                "I could not turn that into a question I am able to ask. "
                "Try naming a specific metric or a breakdown you want to see."
            )
        log.info("smartboard.empty_turn refusal=%s", last_refusal)
        yield {"type": "text", "text": message}

    yield {"type": "done", "usage": usage, "messages": _trim(messages)}


def _trim(messages: List[Dict[str, Any]], keep: int = 12) -> List[Dict[str, Any]]:
    """
    Return history for the next turn, dropping tool traffic.

    Tool results are the bulkiest thing in the transcript and the least useful to
    keep — the board state summary already tells the brain what came of them.
    """
    kept = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    return [{"role": m["role"], "content": m["content"]} for m in kept[-keep:]]


def _readable(exc: Exception) -> str:
    msg = str(exc)
    if "401" in msg or "Unauthorized" in msg:
        return "The model provider rejected the API key. Check the key in your .env."
    if "429" in msg:
        return "The model provider is rate limiting. Wait a moment and try again."
    if "Insufficient Balance" in msg or "402" in msg:
        return "The model account has no credit left."
    return msg[:300]
