"""
Query IR — the only shape in which the brain may ask for data.

Nothing here contains SQL, table names or column names. Metric and dimension
identifiers are validated against the manifest at request time, and every
literal value ends up as a bound parameter. That combination is what makes
injection structurally impossible rather than merely unlikely.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

FilterOp = Literal["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "between", "contains"]
TimeGrain = Literal["day", "week", "month", "quarter", "year"]
SortDir = Literal["asc", "desc"]

# Ops that take a list rather than a scalar.
LIST_OPS = {"in", "not_in", "between"}


class Filter(BaseModel):
    dim: str = Field(description="Dimension id declared in the manifest.")
    op: FilterOp = "="
    value: Any = Field(description="Scalar for most ops; a list for in/not_in/between.")

    @field_validator("value")
    @classmethod
    def _shape(cls, v, info):
        op = info.data.get("op", "=")
        if op in LIST_OPS:
            if not isinstance(v, (list, tuple)):
                raise ValueError(f"op '{op}' requires a list value")
            if op == "between" and len(v) != 2:
                raise ValueError("op 'between' requires exactly two values")
            if len(v) > 200:
                raise ValueError("filter list too long (max 200)")
        elif isinstance(v, (list, tuple, dict)):
            raise ValueError(f"op '{op}' requires a scalar value")
        return v


class TimeRange(BaseModel):
    dim: Optional[str] = Field(
        default=None, description="Time dimension to bound. Defaults to the manifest's default_time_dim."
    )
    start: Optional[str] = Field(default=None, description="Inclusive lower bound, ISO-ish string.")
    end: Optional[str] = Field(default=None, description="Inclusive upper bound, ISO-ish string.")
    last_n: Optional[int] = Field(
        default=None, ge=1, le=730, description="Relative window: the last N grains ending at the latest data point."
    )
    grain: Optional[TimeGrain] = None


class Sort(BaseModel):
    field: str = Field(description="A metric id or dimension id present in the select list.")
    dir: SortDir = "desc"


class Query(BaseModel):
    """A request for data, expressed purely in catalog vocabulary."""

    metrics: List[str] = Field(min_length=1, max_length=8)
    dimensions: List[str] = Field(default_factory=list, max_length=4)
    filters: List[Filter] = Field(default_factory=list, max_length=12)
    time_range: Optional[TimeRange] = None
    sort: List[Sort] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=500, ge=1, le=10_000)
    label: Optional[str] = Field(
        default=None, description="Short human label for this result, shown in the result inspector."
    )


class ResultHandle(BaseModel):
    """
    What comes back to the brain after a query.

    Deliberately *not* the rows. The brain gets a handle, a shape description and
    a three-row preview; the browser fetches the full result directly. Token cost
    stays flat no matter how large the result is, and the model cannot hallucinate
    values it never saw.
    """

    result_id: str
    label: Optional[str] = None
    columns: List[dict]
    row_count: int
    preview: List[dict]
    truncated: bool = False
    elapsed_ms: int = 0
