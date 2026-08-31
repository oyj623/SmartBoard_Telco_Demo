"""
Heuristic brain — no API key required.

This exists for three reasons. It lets the POC run before any key is wired up.
It gives you a deterministic brain for demos, where a live model call is a
liability. And it is a useful test harness: if the heuristic brain can drive
your dashboard end to end, your manifest and viz registry are sound, and any
remaining problem is a prompting problem.

It is genuinely dumb — keyword matching over the manifest's own labels — and it
is meant to be. Swap in OpenAICompatBrain and the rest of the system does not
change.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..manifest import Manifest
from .base import AssistantTurn, ToolCall

_TIME_WORDS = re.compile(r"\b(trend|over time|monthly|by month|history|timeline|daily|每月|趋势)\b", re.I)
_MAP_WORDS = re.compile(r"\b(map|geograph|where|location|site|sites|coverage|地图)\b", re.I)
_TABLE_WORDS = re.compile(r"\b(table|list|breakdown of all|raw|详细|列表)\b", re.I)
_TOP_WORDS = re.compile(r"\b(top|worst|best|highest|lowest|bottom)\s*(\d+)?\b", re.I)
_BY_PHRASE = re.compile(r"\bby\s+([a-z ]{3,20})", re.I)
_HIGHLIGHT = re.compile(r"\b(highlight|flag|show me which|point out|标出)\b", re.I)
_CLEAR = re.compile(r"\b(clear|reset|start over|clean|清空)\b", re.I)


class HeuristicBrain:
    name = "heuristic (no model)"
    available = True

    def __init__(self, manifest: Manifest):
        self.mf = manifest
        self._metric_index = self._index(
            {mid: [mid.replace("_", " "), *m.label.values(), *(m.description.split(",") if m.description else [])]
             for mid, m in manifest.metrics.items()}
        )
        self._dim_index = self._index(
            {did: [did.replace("_", " "), *d.label.values()] for did, d in manifest.dimensions.items()}
        )

    @staticmethod
    def _index(spec: Dict[str, List[str]]) -> List[tuple]:
        out = []
        for key, phrases in spec.items():
            for p in phrases:
                p = (p or "").strip().lower()
                if len(p) >= 3:
                    out.append((p, key))
        return sorted(out, key=lambda kv: -len(kv[0]))

    # -- protocol --------------------------------------------------------

    def complete(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], system: str) -> AssistantTurn:
        user_text = _last_user_text(messages)
        pending = _last_tool_result(messages)

        if pending and pending.get("result_id"):
            return self._draw(user_text, pending)
        if _CLEAR.search(user_text):
            return AssistantTurn(
                text="Cleared the board.",
                tool_calls=[
                    ToolCall(id="c1", name="apply_commands", arguments={"commands": [{"action": "clear_filters"}]})
                ],
            )
        return self._fetch(user_text)

    # -- passes ----------------------------------------------------------

    def _fetch(self, text: str) -> AssistantTurn:
        low = text.lower()
        metrics = self._match(low, self._metric_index, limit=3)
        if not metrics:
            metrics = [next(iter(self.mf.metrics))]

        dims = self._match(low, self._dim_index, limit=2, exclude=set(metrics))

        if _TIME_WORDS.search(low):
            tdim = self.mf.default_time_dim
            if tdim and tdim not in dims:
                dims = [tdim] + dims[:1]
        if _MAP_WORDS.search(low):
            geo = next((d.id for d in self.mf.dimensions.values() if d.type == "geo"), None)
            if geo and geo not in dims:
                dims = [geo]

        # Keep every metric on one dataset — the compiler would reject a mix.
        primary_ds = self.mf.metrics[metrics[0]].dataset
        metrics = [m for m in metrics if self.mf.metrics[m].dataset == primary_ds]
        dims = [d for d in dims if self.mf.dimensions[d].column_for(primary_ds)]

        top = _TOP_WORDS.search(low)
        args: Dict[str, Any] = {
            "metrics": metrics,
            "dimensions": dims,
            "label": _title_from(metrics, dims, self.mf),
            "limit": int(top.group(2)) if top and top.group(2) else 200,
        }
        if top:
            args["sort"] = [{"field": metrics[0], "dir": "asc" if _is_worst(low) else "desc"}]

        return AssistantTurn(tool_calls=[ToolCall(id="q1", name="query_metrics", arguments=args)])

    def _draw(self, text: str, result: Dict[str, Any]) -> AssistantTurn:
        low = text.lower()
        cols = result.get("columns", [])
        metrics = [c["id"] for c in cols if c.get("role") == "metric"]
        dims = [c["id"] for c in cols if c.get("role") == "dimension"]
        rows = result.get("row_count", 0)

        if not dims:
            viz, enc = "kpi", {"value": metrics[0]}
        elif _MAP_WORDS.search(low) and any(self.mf.dimensions[d].type == "geo" for d in dims if d in self.mf.dimensions):
            viz = "map_points"
            enc = {"geo": dims[0], "value": metrics[0], "size": metrics[0]}
        elif _TABLE_WORDS.search(low) or (len(metrics) > 2 and rows > 12):
            viz, enc = "table", {"x": dims[0], "y": metrics}
        elif any(self.mf.dimensions[d].type == "time" for d in dims if d in self.mf.dimensions):
            tdim = next(d for d in dims if self.mf.dimensions[d].type == "time")
            viz = "line"
            enc = {"x": tdim, "y": metrics}
            other = [d for d in dims if d != tdim]
            if other:
                enc["series"] = other[0]
        else:
            viz, enc = "bar", {"x": dims[0], "y": metrics}

        label = result.get("label") or "Result"
        panel_id = "p_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]

        commands: List[Dict[str, Any]] = [
            {
                "action": "add_panel",
                "panel_id": panel_id,
                "result_id": result["result_id"],
                "viz": viz,
                "encoding": enc,
                "title": {"en": label},
                "size": "lg" if viz in ("line", "map_points", "table") else "md",
                "slot": "prepend",
                "note": {"en": f"{rows} row(s) from the {result.get('dataset', 'catalog')} dataset."},
            }
        ]

        if _HIGHLIGHT.search(low) and dims and result.get("preview"):
            keys = [str(r.get(dims[0])) for r in result["preview"][:3] if r.get(dims[0]) is not None]
            if keys:
                commands.append({"action": "highlight", "panel_id": panel_id, "keys": keys})

        return AssistantTurn(
            text=f"Drew {label.lower()} as a {viz.replace('_', ' ')} chart.",
            tool_calls=[ToolCall(id="c1", name="apply_commands", arguments={"commands": commands})],
        )

    def _match(self, text: str, index: List[tuple], limit: int, exclude: Optional[set] = None) -> List[str]:
        found: List[str] = []
        exclude = exclude or set()
        for phrase, key in index:
            if key in found or key in exclude:
                continue
            if phrase in text:
                found.append(key)
            if len(found) >= limit:
                break
        return found


def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _last_tool_result(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    import json

    if not messages or messages[-1].get("role") != "tool":
        return None
    try:
        payload = json.loads(messages[-1]["content"])
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _is_worst(text: str) -> bool:
    return any(w in text for w in ("worst", "lowest", "bottom", "weakest"))


def _title_from(metrics: List[str], dims: List[str], mf: Manifest) -> str:
    mlabel = " & ".join(mf.metrics[m].label.get("en", m) for m in metrics)
    if not dims:
        return mlabel
    return f"{mlabel} by {' and '.join(mf.dimensions[d].label.get('en', d) for d in dims)}"
