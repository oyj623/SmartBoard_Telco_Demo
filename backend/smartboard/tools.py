"""
Tool generation.

The brain's capability surface is *derived*, never hand-written. Metric ids
become an enum drawn from the manifest, viz kinds become an enum drawn from the
manifest, and command types become an enum drawn from the manifest. That is what
stops the prompt and the catalog drifting apart as a project grows, and it means
a hallucinated metric name is rejected by the provider's own schema validation
before it ever reaches your code.

Output is plain JSON Schema, which both OpenAI-compatible providers (DeepSeek,
OpenAI, Together, vLLM) and Anthropic accept with only a wrapper difference.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .commands import COMMAND_TYPES
from .manifest import Manifest


def _i18n_schema(locales: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {loc: {"type": "string"} for loc in locales},
        "required": ["en"] if "en" in locales else [locales[0]],
        "additionalProperties": False,
    }


def query_tool(mf: Manifest) -> Dict[str, Any]:
    metric_ids = sorted(mf.metrics)
    dim_ids = sorted(mf.dimensions)
    time_dims = sorted(d.id for d in mf.dimensions.values() if d.type == "time")

    return {
        "name": "query_metrics",
        "description": (
            "Fetch aggregated data from the governed catalog. Returns a result handle "
            "(result_id, column shape, row count and a 3-row preview) — not the full rows. "
            "Pass the result_id to apply_commands to draw it. Call this once per distinct "
            "shape of data you need; do not try to fetch everything in one call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "enum": metric_ids},
                    "minItems": 1,
                    "maxItems": 8,
                    "description": "Measures to aggregate. All must belong to the same dataset.",
                },
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string", "enum": dim_ids},
                    "maxItems": 4,
                    "description": "Group-by fields. Leave empty for a single total (a KPI).",
                },
                "filters": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "dim": {"type": "string", "enum": dim_ids},
                            "op": {
                                "type": "string",
                                "enum": ["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "between", "contains"],
                            },
                            "value": {
                                "description": "Scalar for most ops; an array for in/not_in/between.",
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "boolean"},
                                    {"type": "array", "items": {"type": ["string", "number"]}},
                                ],
                            },
                        },
                        "required": ["dim", "op", "value"],
                        "additionalProperties": False,
                    },
                },
                "time_range": {
                    "type": "object",
                    "properties": {
                        "dim": {"type": "string", "enum": time_dims or dim_ids},
                        "start": {"type": "string", "description": "Inclusive lower bound."},
                        "end": {"type": "string", "description": "Inclusive upper bound."},
                        "last_n": {"type": "integer", "minimum": 1, "maximum": 730},
                        "grain": {"type": "string", "enum": ["day", "week", "month", "quarter", "year"]},
                    },
                    "additionalProperties": False,
                },
                "sort": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": metric_ids + dim_ids},
                            "dir": {"type": "string", "enum": ["asc", "desc"]},
                        },
                        "required": ["field"],
                        "additionalProperties": False,
                    },
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": mf.max_rows, "default": 500},
                "label": {"type": "string", "description": "Short label for this result, e.g. 'ARPU by month'."},
            },
            "required": ["metrics"],
            "additionalProperties": False,
        },
    }


def _encoding_schema(mf: Manifest) -> Dict[str, Any]:
    ids = sorted(set(mf.metrics) | set(mf.dimensions))
    return {
        "type": "object",
        "description": "Maps result columns onto visual channels. Every value must be a column present in the result.",
        "properties": {
            "x": {"type": "string", "enum": ids, "description": "Category or time axis."},
            "y": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(mf.metrics)},
                "description": "One or more measures on the value axis.",
            },
            "series": {"type": "string", "enum": ids, "description": "Dimension that splits into multiple lines/bars."},
            "color": {"type": "string", "enum": ids},
            "size": {"type": "string", "enum": sorted(mf.metrics)},
            "value": {"type": "string", "enum": sorted(mf.metrics), "description": "The measure for kpi/gauge/map fill."},
            "geo": {"type": "string", "enum": ids, "description": "Dimension carrying map features."},
        },
        "additionalProperties": False,
    }


def _style_schema() -> Dict[str, Any]:
    """
    The look of a panel, as a flat bag of optional overrides.

    Deliberately separate from `encoding`. Encoding is checked against the
    result's real columns and a mistake there means the chart cannot be drawn;
    style is checked against nothing but its own types and a mistake there means
    the chart is drawn slightly wrong. Keeping them apart means a bad colour
    never rejects a correct chart.
    """
    colour = {
        "type": "string",
        "description": "A hex colour ('#4aa8d8'), rgb(), a CSS colour name, or 'token:chart-2' "
                       "to use a theme token that follows the light/dark switch.",
    }
    return {
        "type": "object",
        "description": "Visual overrides. Every field is optional; omit the ones you do not care about.",
        "properties": {
            "palette": {"type": "array", "items": colour, "maxItems": 12,
                        "description": "Series colours, in series order."},
            "color": {**colour, "description": "Shorthand for a single-colour palette."},
            "y_min": {"type": "number", "description": "Lower bound of the value axis."},
            "y_max": {"type": "number", "description": "Upper bound of the value axis."},
            "x_min": {"type": "number"},
            "x_max": {"type": "number"},
            "y_label": {"type": "string", "maxLength": 60},
            "x_label": {"type": "string", "maxLength": 60},
            "legend": {"type": "boolean"},
            "grid": {"type": "boolean", "description": "Axis gridlines."},
            "labels": {"type": "boolean", "description": "Draw the value on each mark."},
            "smooth": {"type": "boolean", "description": "Line charts: curved or straight."},
            "stack": {"type": "boolean"},
            "horizontal": {"type": "boolean", "description": "Bar charts: lay the bars sideways."},
            "sort": {"type": "string", "enum": ["asc", "desc", "none"],
                     "description": "Reorder categories by value at draw time. Does not re-query."},
            "opacity": {"type": "number", "minimum": 0.05, "maximum": 1.0},
            "reference_line": {"type": "number",
                               "description": "A horizontal marker, e.g. the benchmark or SLA for this metric."},
            "reference_label": {"type": "string", "maxLength": 60},
        },
        "additionalProperties": False,
    }


def _layout_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "description": "Where the panel sits on a twelve-column grid.",
        "properties": {
            "col_span": {"type": "integer", "minimum": 1, "maximum": 12,
                         "description": "Width in grid columns out of twelve."},
            "row_span": {"type": "integer", "minimum": 1, "maximum": 3,
                         "description": "Height in row units. 1 is standard, 2 for a map or a tall table."},
            "section": {"type": "string", "description": "Id of the section this panel belongs to."},
        },
        "additionalProperties": False,
    }


def commands_tool(mf: Manifest) -> Dict[str, Any]:
    enabled = [c for c in mf.commands_enabled if c in COMMAND_TYPES]
    i18n = _i18n_schema(mf.locales)
    enc = _encoding_schema(mf)
    style = _style_schema()
    layout = _layout_schema()
    dim_ids = sorted(mf.dimensions)

    return {
        "name": "apply_commands",
        "description": (
            "Change the dashboard. Emit an ordered list of commands.\n"
            "\n"
            "Prefer reaching into panels that already exist over adding new ones: "
            "update_panel to restyle or re-encode, highlight to draw attention, set_filter to narrow, "
            "focus_map to move a camera. Use add_panel with an existing panel_id to replace in place.\n"
            "\n"
            "APPEARANCE. Colour, axis bounds, sort order, gridlines, a benchmark line and axis titles "
            "all live in `style` on add_panel and update_panel. To recolour or rescale an existing "
            "chart, send update_panel with only `panel_id` and `style` — do not re-query, and do not "
            "redraw the panel.\n"
            "\n"
            "DESIGN. You are laying out a dashboard, not stacking charts. A good board leads with the "
            "headline numbers, groups related panels under titled sections, and gives the most "
            "important panel the most room. Use `layout.col_span` for relative importance (12 is full "
            "width, 3 is a small tile) and set_layout to declare sections and order in one command. "
            "When asked to redesign or reorganise, emit one set_layout that describes the finished "
            "board rather than a run of small moves.\n"
            "\n"
            "If the request is ambiguous in a way that changes the answer, emit a single "
            "ask_clarification command instead of guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": enabled},
                            "panel_id": {"type": "string", "description": "Stable snake_case id, e.g. 'p_arpu_trend'."},
                            "result_id": {"type": "string", "description": "From a prior query_metrics call."},
                            "viz": {"type": "string", "enum": mf.viz_enabled},
                            "encoding": enc,
                            "title": i18n,
                            "subtitle": i18n,
                            "note": {**i18n, "description": "One-line reading of what the chart shows."},
                            "size": {"type": "string", "enum": ["sm", "md", "lg", "full"]},
                            "style": style,
                            "layout": layout,
                            "sections": {
                                "type": "array",
                                "maxItems": 8,
                                "description": "set_layout only: the titled bands of the board, in order.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "title": i18n,
                                        "subtitle": i18n,
                                        "collapsed": {"type": "boolean"},
                                    },
                                    "required": ["id", "title"],
                                    "additionalProperties": False,
                                },
                            },
                            "panels": {
                                "type": "array",
                                "maxItems": 40,
                                "description": "set_layout only: per-panel layout. Only the panels you name change.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "panel_id": {"type": "string"},
                                        "col_span": {"type": "integer", "minimum": 1, "maximum": 12},
                                        "row_span": {"type": "integer", "minimum": 1, "maximum": 3},
                                        "section": {"type": "string"},
                                    },
                                    "required": ["panel_id"],
                                    "additionalProperties": False,
                                },
                            },
                            "slot": {"type": "string", "enum": ["prepend", "append", "replace_all", "replace_panel"]},
                            "replaces": {"type": "string"},
                            "filter": {
                                "type": "object",
                                "properties": {
                                    "dim": {"type": "string", "enum": dim_ids},
                                    "op": {"type": "string", "enum": ["=", "in", "between", ">", "<", "contains"]},
                                    "value": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {"type": "number"},
                                            {"type": "array", "items": {"type": ["string", "number"]}},
                                        ]
                                    },
                                },
                                "required": ["dim", "op", "value"],
                                "additionalProperties": False,
                            },
                            "scope": {"type": "string", "description": "'global' or a panel_id."},
                            "dims": {"type": "array", "items": {"type": "string", "enum": dim_ids}},
                            "keys": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                            "reason": i18n,
                            "ttl_ms": {"type": "integer", "minimum": 1000, "maximum": 600000},
                            "feature_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                            "zoom": {"type": "number", "minimum": 1, "maximum": 18},
                            "order": {"type": "array", "items": {"type": "string"}},
                            "text": i18n,
                            "tone": {"type": "string", "enum": ["neutral", "positive", "warning", "critical"]},
                            "question": i18n,
                            "options": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["commands"],
            "additionalProperties": False,
        },
    }


def build_tools(mf: Manifest) -> List[Dict[str, Any]]:
    return [query_tool(mf), commands_tool(mf)]


def to_openai_format(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DeepSeek, OpenAI, Together, vLLM and friends."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def to_anthropic_format(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return tools  # already the right shape
