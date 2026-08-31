"""
Brain protocol.

A provider does exactly one thing: take messages plus tool schemas, return an
assistant turn. It does not know about SQL, dashboards or SmartBoard. The
conversation loop in `smartboard.session` is provider-agnostic, which is why
switching from DeepSeek to Claude to a local vLLM is a config change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from ..manifest import Manifest


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    usage: Dict[str, int] = field(default_factory=dict)


class BrainClient(Protocol):
    name: str

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: str,
    ) -> AssistantTurn: ...


SYSTEM_TEMPLATE = """You are the analytical half of {product}, a dashboard that a person drives by conversation. \
The dashboard is on their left; you are on their right. They can see it. Do not describe charts in words when \
you can just draw them.

You have exactly two abilities:
  1. query_metrics — fetch aggregated data from the governed catalog below.
  2. apply_commands — change what is on the dashboard.

You cannot write SQL and you cannot write UI code. Name metrics and dimensions by their catalog id; anything else \
is rejected. This is a feature, not a limitation: it means you can never break their data or their screen.

## Catalog
{catalog}

## How to work
- Fetch first, then draw. A `result_id` from query_metrics is what add_panel needs.
- One query per distinct shape of data. Metrics from different datasets cannot be combined in one query — \
ask for them separately and draw them as separate panels.
- Prefer changing the board over rebuilding it. If a relevant panel already exists, `highlight`, `set_filter` or \
`update_panel` rather than adding a fourth chart that says nearly the same thing.
- Reuse a panel_id to replace that panel in place. Use fresh ids for genuinely new panels.
- Choose the viz that fits the shape: a single number is `kpi`; time on the x-axis is `line`; comparing \
categories is `bar`; two measures against each other is `scatter`; geography is `map_points`; anything with more \
than about twelve rows and several columns is `table`.
- Every panel gets a `note` — one sentence saying what the chart actually shows. That is where your analysis \
goes, not in a long chat message.
- If the request is ambiguous in a way that changes the answer, emit a single `ask_clarification` command. A \
wrong chart costs more trust than a question does.

## Current dashboard
{board}

## Voice
Reply in {locale}. Two or three sentences at most — the charts carry the detail. Say what you found, not what \
you did. Never invent a number you did not receive from query_metrics; if you need a figure to make a point, \
query for it.
"""


def build_system_prompt(
    mf: Manifest,
    board_state: Optional[Dict[str, Any]] = None,
    locale: str = "en",
) -> str:
    catalog = mf.catalog_for_brain(locale)
    lines: List[str] = ["Metrics (id — label [unit], dataset):"]
    for m in catalog["metrics"]:
        about = f" · {m['about']}" if m["about"] else ""
        lines.append(f"  {m['id']} — {m['label']} [{m['unit'] or '—'}], dataset={m['dataset']}{about}")

    lines.append("Dimensions (id — label, type, datasets):")
    for d in catalog["dimensions"]:
        vals = f" · values: {', '.join(d['values'][:12])}" if d.get("values") else ""
        lines.append(f"  {d['id']} — {d['label']}, {d['type']}, on={'/'.join(d['datasets'])}{vals}")

    lines.append(f"Viz kinds available: {', '.join(catalog['viz'])}")
    if catalog["glossary"]:
        lines.append("Glossary: " + "; ".join(f"{k} = {v}" for k, v in catalog["glossary"].items()))

    return SYSTEM_TEMPLATE.format(
        product=mf.title.get(locale, mf.title.get("en", mf.name)),
        catalog="\n".join(lines),
        board=_describe_board(board_state),
        locale={"en": "English", "zh": "Chinese", "ms": "Malay"}.get(locale, "English"),
    )


def _describe_board(board: Optional[Dict[str, Any]]) -> str:
    """
    A compact snapshot, kept under a few hundred tokens.

    Without this, "make that a bar chart instead" is unanswerable. With it, the
    brain can address panels by id and knows which filters are already applied.
    """
    if not board or not board.get("panels"):
        return "The dashboard is empty. The person is starting from nothing."

    out = []
    for p in board["panels"][:12]:
        enc = p.get("encoding") or {}
        bits = [f"{p.get('panel_id')}: {p.get('viz')}"]
        if enc.get("y"):
            bits.append("y=" + ",".join(enc["y"]))
        if enc.get("value"):
            bits.append("value=" + enc["value"])
        if enc.get("x"):
            bits.append("x=" + enc["x"])
        title = (p.get("title") or {}).get("en")
        if title:
            bits.append(f'"{title}"')
        out.append("  " + " ".join(bits))

    filters = board.get("global_filters") or []
    ftxt = (
        "  none"
        if not filters
        else "\n".join(f"  {f['dim']} {f['op']} {json.dumps(f['value'], ensure_ascii=False)}" for f in filters)
    )
    return f"Panels:\n" + "\n".join(out) + f"\nGlobal filters:\n{ftxt}"
