"""
Dashboard commands — the fibre bundle.

Every change to the board, whether it originates from the brain or from a user
clicking something, crosses as one of these. One vocabulary, one reducer, one
source of truth. That symmetry is what lets the brain see and undo what the user
did, and the user undo what the brain did.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ir import Filter

Slot = Literal["prepend", "append", "replace_all", "replace_panel"]
PanelSize = Literal["sm", "md", "lg", "full"]

# Colours reach a canvas, not the DOM, so they cannot execute anything. They are
# still validated: a value that is not a colour is a bug worth catching at the
# boundary rather than a mystery blank chart three layers down. Design-token
# names are allowed and preferred, because they follow the light/dark switch.
_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*(,\s*[\d.]+\s*)?\)"
    r"|token:[a-z][a-z0-9-]{0,31}"
    r"|[a-zA-Z]{3,20})$"
)


class I18nText(BaseModel):
    """
    Deployments are usually multilingual; make that cheap rather than an afterthought.

    `en` is required as the universal fallback. Any other locale key the manifest
    declares (`zh`, `ms`, `th`, …) is accepted verbatim — the tool schema handed to
    the model is generated from `manifest.locales`, so the model only ever sees the
    locales the deployment actually ships.
    """

    model_config = ConfigDict(extra="allow")

    en: str
    zh: Optional[str] = None
    ms: Optional[str] = None


class Encoding(BaseModel):
    """How result columns map onto visual channels. All values are catalog ids."""

    x: Optional[str] = None
    y: Optional[List[str]] = None
    series: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    value: Optional[str] = None
    geo: Optional[str] = Field(default=None, description="Dimension carrying lat/lng or a feature key.")


class PanelStyle(BaseModel):
    """
    How a panel looks, as opposed to what it shows.

    Kept separate from `encoding` on purpose. Encoding answers "which column goes
    on which axis" and is validated against the result's actual columns; style
    answers "what colour, what range, what title" and is validated against
    nothing but its own types. Mixing them would mean a bad colour could reject
    an otherwise correct chart.

    Every field is optional and every renderer works with none of them set.
    Style is an override, never a requirement.
    """

    palette: Optional[List[str]] = Field(
        default=None,
        max_length=12,
        description=(
            "Series colours, in order. Hex ('#4aa8d8'), rgb(), a CSS colour name, or "
            "'token:chart-2' to use a design token that follows the light/dark theme."
        ),
    )
    color: Optional[str] = Field(default=None, description="Shorthand for a single-colour palette.")
    y_min: Optional[float] = Field(default=None, description="Lower bound of the value axis.")
    y_max: Optional[float] = Field(default=None, description="Upper bound of the value axis.")
    x_min: Optional[float] = Field(default=None, description="Lower bound of a numeric x axis. Ignored on category axes.")
    x_max: Optional[float] = Field(default=None, description="Upper bound of a numeric x axis.")
    y_label: Optional[str] = Field(default=None, max_length=60, description="Value-axis title. Omit to use the metric label.")
    x_label: Optional[str] = Field(default=None, max_length=60, description="Category-axis title.")
    legend: Optional[bool] = Field(default=None, description="Force the legend on or off.")
    grid: Optional[bool] = Field(default=None, description="Show or hide axis gridlines.")
    labels: Optional[bool] = Field(default=None, description="Draw the value on each mark.")
    smooth: Optional[bool] = Field(default=None, description="Line charts: curved or straight.")
    stack: Optional[bool] = Field(default=None, description="Stack series instead of grouping them.")
    horizontal: Optional[bool] = Field(default=None, description="Bar charts: lay the bars sideways.")
    sort: Optional[Literal["asc", "desc", "none"]] = Field(
        default=None, description="Reorder categories by value at draw time. Does not re-query."
    )
    opacity: Optional[float] = Field(default=None, ge=0.05, le=1.0)
    reference_line: Optional[float] = Field(
        default=None, description="Draw a horizontal marker line, e.g. a benchmark or SLA."
    )
    reference_label: Optional[str] = Field(default=None, max_length=60)

    @field_validator("palette")
    @classmethod
    def _colours(cls, v):
        if v:
            for c in v:
                if not _COLOUR.match(str(c)):
                    raise ValueError(f"'{c}' is not a colour; use #hex, rgb(), a colour name, or token:name")
        return v

    @field_validator("color")
    @classmethod
    def _colour(cls, v):
        if v and not _COLOUR.match(str(v)):
            raise ValueError(f"'{v}' is not a colour; use #hex, rgb(), a colour name, or token:name")
        return v


class PanelLayout(BaseModel):
    """
    Where a panel sits on a twelve-column grid.

    Spans rather than coordinates. Models reason poorly about absolute space, but
    they reason perfectly well about "this one is wide and that one is narrow",
    which is the part that actually carries meaning in a dashboard.
    """

    col_span: Optional[int] = Field(default=None, ge=1, le=12, description="Grid columns out of twelve.")
    row_span: Optional[int] = Field(default=None, ge=1, le=3, description="Height in row units. 1 is standard.")
    section: Optional[str] = Field(
        default=None, description="Id of the section this panel belongs to. Sections are declared with set_layout."
    )


class Section(BaseModel):
    """A titled band of the board. This is what makes a board a design rather than a pile."""

    id: str = Field(description="Stable snake_case id, e.g. 'sec_production'.")
    title: "I18nText"
    subtitle: Optional["I18nText"] = None
    collapsed: bool = False


class AddPanel(BaseModel):
    action: Literal["add_panel"] = "add_panel"
    panel_id: str = Field(description="Stable id you choose, e.g. 'p_arpu_trend'. Reuse it to replace the panel.")
    result_id: str = Field(description="A result_id returned by query_metrics.")
    viz: str = Field(description="A viz kind enabled in the manifest.")
    encoding: Encoding
    title: I18nText
    subtitle: Optional[I18nText] = None
    size: PanelSize = "md"
    slot: Slot = "prepend"
    replaces: Optional[str] = Field(default=None, description="Panel id to swap out when slot='replace_panel'.")
    note: Optional[I18nText] = Field(default=None, description="One-line reading of the chart, shown under it.")
    style: Optional[PanelStyle] = None
    layout: Optional[PanelLayout] = None


class UpdatePanel(BaseModel):
    """
    Change an existing panel without redrawing the board.

    This is the command for restyling: colour, axis bounds, sort order, titles, a
    benchmark line. Prefer it over add_panel whenever the underlying data is
    unchanged — it keeps the panel's position and costs no query.
    """

    action: Literal["update_panel"] = "update_panel"
    panel_id: str
    viz: Optional[str] = None
    encoding: Optional[Encoding] = None
    title: Optional[I18nText] = None
    subtitle: Optional[I18nText] = None
    note: Optional[I18nText] = None
    result_id: Optional[str] = None
    size: Optional[PanelSize] = None
    style: Optional[PanelStyle] = Field(
        default=None, description="Merged over the panel's existing style; fields you omit are left alone."
    )
    layout: Optional[PanelLayout] = None


class RemovePanel(BaseModel):
    action: Literal["remove_panel"] = "remove_panel"
    panel_id: str


class SetFilter(BaseModel):
    """Scope 'global' fans out to every panel that declares the dimension."""

    action: Literal["set_filter"] = "set_filter"
    filter: Filter
    scope: str = "global"


class ClearFilters(BaseModel):
    action: Literal["clear_filters"] = "clear_filters"
    dims: Optional[List[str]] = Field(default=None, description="Omit to clear everything.")


class Highlight(BaseModel):
    """Reach into an existing panel rather than drawing a new one."""

    action: Literal["highlight"] = "highlight"
    panel_id: Optional[str] = Field(default=None, description="Omit to highlight across all panels.")
    keys: List[str] = Field(max_length=50, description="Category or series values to emphasise.")
    reason: Optional[I18nText] = None
    ttl_ms: int = Field(default=25_000, ge=1000, le=600_000)


class FocusMap(BaseModel):
    action: Literal["focus_map"] = "focus_map"
    panel_id: Optional[str] = None
    feature_ids: List[str] = Field(max_length=200)
    zoom: Optional[float] = Field(default=None, ge=1, le=18)


class SetLayout(BaseModel):
    """
    Arrange the whole board in one command.

    Layout is a design decision, and design decisions land all at once or not at
    all — restyling six panels with six commands produces five intermediate
    boards the person has to sit through. This lets the assistant state what the
    finished board looks like.
    """

    action: Literal["set_layout"] = "set_layout"
    order: List[str] = Field(default_factory=list, description="Panel ids in the order they should appear.")
    sections: Optional[List[Section]] = Field(
        default=None,
        max_length=8,
        description="Titled bands, rendered in this order. Panels say which one they belong to.",
    )
    panels: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        max_length=40,
        description=(
            "Per-panel layout overrides: [{panel_id, col_span, row_span, section}]. "
            "Only the panels you name are changed."
        ),
    )


class Narrate(BaseModel):
    """A line of commentary pinned to the board, not the chat log."""

    action: Literal["narrate"] = "narrate"
    text: I18nText
    tone: Literal["neutral", "positive", "warning", "critical"] = "neutral"


class AskClarification(BaseModel):
    """
    Preferred over guessing. A wrong chart erodes trust faster than a question.
    """

    action: Literal["ask_clarification"] = "ask_clarification"
    question: I18nText
    options: Optional[List[str]] = Field(default=None, max_length=5)


DashboardCommand = Annotated[
    Union[
        AddPanel,
        UpdatePanel,
        RemovePanel,
        SetFilter,
        ClearFilters,
        Highlight,
        FocusMap,
        SetLayout,
        Narrate,
        AskClarification,
    ],
    Field(discriminator="action"),
]

COMMAND_TYPES: Dict[str, type] = {
    "add_panel": AddPanel,
    "update_panel": UpdatePanel,
    "remove_panel": RemovePanel,
    "set_filter": SetFilter,
    "clear_filters": ClearFilters,
    "highlight": Highlight,
    "focus_map": FocusMap,
    "set_layout": SetLayout,
    "narrate": Narrate,
    "ask_clarification": AskClarification,
}
