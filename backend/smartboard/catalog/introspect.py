"""
Catalog from warehouse introspection.

For the common case of a warehouse with no metadata service: read the system
catalogs, apply the naming conventions your team already follows (usually
unwritten), and produce a draft catalog a human edits down.

The output is a *draft*, and the code says so. Introspection can tell you a
column is a DECIMAL named `revenue_amt`; it cannot tell you whether up is good,
what the benchmark is, or what a business person calls it. Those are inferred
from conventions, marked `confidence: low`, and never allowed to carry a
reference line — a guess must not quietly become a judgement on a chart.

Hive and Spark specifics worth knowing:

  - Table statistics are frequently stale or absent. `TableStats` carries
    `collected_at` and every field is optional; callers must cope with None
    rather than trusting a number.
  - Partition columns are the single most valuable thing here. They determine
    what a time dimension can be, and later (Project B) whether a query can be
    bounded at all.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

import yaml

from .base import Catalog, Dataset, Dimension, Metric, labels

log = logging.getLogger("smartboard.catalog")


# ---------------------------------------------------------------------------
# What a warehouse must be able to tell us
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    name: str
    type: str
    comment: str = ""
    is_partition: bool = False


@dataclass
class TableInfo:
    name: str                      # bare table name
    schema: str
    comment: str = ""
    owner: str = ""
    properties: Dict[str, str] = field(default_factory=dict)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class PartitionInfo:
    keys: List[str] = field(default_factory=list)
    count: Optional[int] = None
    latest: Optional[str] = None


@dataclass
class TableStats:
    rows: Optional[int] = None
    bytes: Optional[int] = None
    collected_at: Optional[datetime] = None

    @property
    def usable(self) -> bool:
        """Hive will happily hand you statistics collected two years ago."""
        return self.rows is not None or self.bytes is not None


class Introspector(Protocol):
    """One method per fact we need. A second engine is a second class, nothing more."""

    def tables(self, schema: str) -> Sequence[TableInfo]: ...
    def columns(self, table: TableInfo) -> Sequence[ColumnInfo]: ...
    def partitions(self, table: TableInfo) -> PartitionInfo: ...
    def stats(self, table: TableInfo) -> TableStats: ...


# ---------------------------------------------------------------------------
# Conventions
# ---------------------------------------------------------------------------

DEFAULT_CONVENTIONS: Dict[str, Any] = {
    "time_columns": ["dt", "ds", "stat_date", "event_date", "date", "day"],
    "measure_suffix": {
        "_amt": {"agg": "SUM", "format": "currency", "direction": "up_good"},
        "_cnt": {"agg": "SUM", "format": "number", "direction": "neutral"},
        "_num": {"agg": "SUM", "format": "number", "direction": "neutral"},
        "_rate": {"agg": "AVG", "format": "percent", "direction": "neutral"},
        "_pct": {"agg": "AVG", "format": "percent", "direction": "neutral"},
        "_dur": {"agg": "AVG", "format": "duration", "direction": "down_good"},
    },
    "dimension_suffix": ["_id", "_code", "_type", "_name", "_flag", "_status", "_group"],
    "ignore": ["etl_*", "_tmp_*", "dw_load_*", "*_bak"],
    "layer_from_schema": {"ods": "L1", "dwd": "L2", "dws": "L3", "ads": "L4"},
    "numeric_types": ["int", "bigint", "smallint", "tinyint", "double", "float", "decimal", "numeric", "long"],
}


@dataclass
class Conventions:
    """How to read a warehouse that cannot explain itself."""

    time_columns: List[str]
    measure_suffix: Dict[str, Dict[str, str]]
    dimension_suffix: List[str]
    ignore: List[str]
    layer_from_schema: Dict[str, str]
    numeric_types: List[str]

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "Conventions":
        raw = dict(DEFAULT_CONVENTIONS)
        if path:
            p = Path(path)
            if p.exists():
                raw.update(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        return cls(
            time_columns=list(raw["time_columns"]),
            measure_suffix=dict(raw["measure_suffix"]),
            dimension_suffix=list(raw["dimension_suffix"]),
            ignore=list(raw["ignore"]),
            layer_from_schema=dict(raw["layer_from_schema"]),
            numeric_types=list(raw["numeric_types"]),
        )

    def ignored(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, pat) for pat in self.ignore)

    def measure_rule(self, column: str) -> Optional[Dict[str, str]]:
        for suffix, rule in self.measure_suffix.items():
            if column.endswith(suffix):
                return rule
        return None

    def looks_dimensional(self, column: str) -> bool:
        return any(column.endswith(s) for s in self.dimension_suffix)

    def is_numeric(self, sql_type: str) -> bool:
        base = sql_type.lower().split("(")[0].strip()
        return base in self.numeric_types

    def layer_of(self, schema: str) -> Optional[str]:
        return self.layer_from_schema.get(schema.lower())


# ---------------------------------------------------------------------------
# Draft catalog
# ---------------------------------------------------------------------------

class IntrospectSource:
    """
    Build a draft catalog from a warehouse's own system catalogs.

    Never trusted: the structural fields are generated, not authored, so they go
    through the lock like any other external source. That is not paranoia about
    your warehouse — it is that a generated `expr` should be read by a person
    once before it is executed a million times.
    """

    kind = "introspect"

    def __init__(
        self,
        introspector: Introspector,
        schemas: Sequence[str],
        conventions: Optional[Conventions] = None,
        alias_prefix: str = "t",
    ):
        self.introspector = introspector
        self.schemas = list(schemas)
        self.conventions = conventions or Conventions.load()
        self.alias_prefix = alias_prefix
        self.trusted = False

    @property
    def name(self) -> str:
        return f"introspect:{','.join(self.schemas)}"

    def load(self) -> Catalog:
        conv = self.conventions
        datasets: Dict[str, Dataset] = {}
        metrics: Dict[str, Metric] = {}
        dimensions: Dict[str, Dimension] = {}

        for schema in self.schemas:
            for table in self.introspector.tables(schema):
                if conv.ignored(table.name):
                    continue

                ds_id = _snake(table.name)
                alias = self.alias_prefix
                cols = list(self.introspector.columns(table))
                part = self.introspector.partitions(table)

                datasets[ds_id] = Dataset(
                    id=ds_id,
                    from_=f"{table.fqn} {alias}",
                    joins=[],
                    description=table.comment or f"introspected from {table.fqn}",
                    layer=conv.layer_of(schema),
                    grain=list(part.keys),
                    covers={"dimensions": [], "time_grain": None},
                )

                for col in cols:
                    if conv.ignored(col.name):
                        continue
                    qualified = f"{alias}.{col.name}"

                    # Time first: a partition key that looks like a date is the
                    # most useful thing in the whole warehouse, because it is
                    # what makes a query boundable.
                    if col.name.lower() in conv.time_columns or (col.is_partition and _date_ish(col)):
                        dim_id = _snake(col.name)
                        _add_dimension(
                            dimensions, dim_id, ds_id, qualified,
                            type_="time",
                            native_grain=_grain_of(col),
                            comment=col.comment,
                            source=self.name,
                        )
                        datasets[ds_id].covers["time_grain"] = _grain_of(col)
                        datasets[ds_id].covers["dimensions"].append(dim_id)
                        continue

                    rule = conv.measure_rule(col.name)
                    if rule and conv.is_numeric(col.type):
                        mid = _snake(col.name)
                        metrics[mid] = Metric(
                            id=mid,
                            dataset=ds_id,
                            expr=f"{rule['agg']}({qualified})",
                            label=labels(None, _humanise(col.name)),
                            unit="",
                            format=rule.get("format", "number"),
                            direction=rule.get("direction", "neutral"),
                            description=col.comment or "",
                            source=self.name,
                            confidence="low",
                        )
                        continue

                    if conv.looks_dimensional(col.name) or not conv.is_numeric(col.type):
                        dim_id = _snake(col.name)
                        _add_dimension(
                            dimensions, dim_id, ds_id, qualified,
                            type_="string",
                            comment=col.comment,
                            source=self.name,
                        )
                        datasets[ds_id].covers["dimensions"].append(dim_id)

        low = sum(1 for m in metrics.values() if m.confidence == "low")
        log.info(
            "smartboard.catalog.introspected datasets=%d metrics=%d (%d inferred) dimensions=%d",
            len(datasets), len(metrics), low, len(dimensions),
        )
        return Catalog(datasets, metrics, dimensions, glossary={}, trusted=False)


def _add_dimension(
    dimensions: Dict[str, Dimension],
    dim_id: str,
    dataset_id: str,
    column: str,
    *,
    type_: str,
    source: str,
    native_grain: Optional[str] = None,
    comment: str = "",
) -> None:
    """
    Register a column as a dimension on this dataset.

    A dimension that appears in several tables gets one entry with several
    column bindings — which is exactly the shape the compiler wants, and the
    reason the same id can be asked for against different datasets.
    """
    existing = dimensions.get(dim_id)
    if existing:
        existing.columns[dataset_id] = column
        return
    dimensions[dim_id] = Dimension(
        id=dim_id,
        type=type_,
        label=labels(None, _humanise(dim_id)),
        columns={dataset_id: column},
        description=comment or "",
        native_grain=native_grain,
        source=source,
        confidence="low",
    )


def _snake(name: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    return out if out and out[0].isalpha() else f"c_{out}"


def _humanise(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _date_ish(col: ColumnInfo) -> bool:
    t = col.type.lower()
    return "date" in t or "timestamp" in t or col.name.lower() in ("dt", "ds")


def _grain_of(col: ColumnInfo) -> str:
    name = col.name.lower()
    if "month" in name:
        return "month"
    if "hour" in name or "timestamp" in col.type.lower():
        return "day"
    return "day"


# ---------------------------------------------------------------------------
# Hive / Spark
# ---------------------------------------------------------------------------

class HiveIntrospector:
    """
    Introspect Hive or Spark SQL through any adapter with `run(sql, params)`.

    Deliberately built on the adapter protocol SmartBoard already has, so it
    works against Spark Thrift, Hive Server 2, or anything else that speaks the
    dialect — including, in tests, a fake.
    """

    def __init__(self, adapter):
        self.adapter = adapter

    def _rows(self, sql: str) -> List[Dict[str, Any]]:
        rows, _ = self.adapter.run(sql, {})
        return rows

    def tables(self, schema: str) -> List[TableInfo]:
        out: List[TableInfo] = []
        for row in self._rows(f"SHOW TABLES IN {schema}"):
            name = row.get("tableName") or row.get("table_name") or row.get("tab_name")
            if not name:
                continue
            out.append(TableInfo(name=name, schema=schema))
        return out

    def columns(self, table: TableInfo) -> List[ColumnInfo]:
        """
        `DESCRIBE` returns columns, then a blank line, then the partition block —
        and the partition columns are repeated in it. Tracking that transition is
        the only way to know which columns are partitions, which is the fact we
        most want.
        """
        out: List[ColumnInfo] = []
        seen: Dict[str, ColumnInfo] = {}
        in_partitions = False

        for row in self._rows(f"DESCRIBE {table.fqn}"):
            name = (row.get("col_name") or "").strip()
            dtype = (row.get("data_type") or "").strip()
            comment = (row.get("comment") or "").strip()

            if not name or name.startswith("#"):
                if "partition" in name.lower():
                    in_partitions = True
                continue

            if name in seen:
                if in_partitions:
                    seen[name].is_partition = True
                continue

            col = ColumnInfo(name=name, type=dtype, comment=comment, is_partition=in_partitions)
            seen[name] = col
            out.append(col)

        return out

    def partitions(self, table: TableInfo) -> PartitionInfo:
        keys = [c.name for c in self.columns(table) if c.is_partition]
        try:
            rows = self._rows(f"SHOW PARTITIONS {table.fqn}")
        except Exception as exc:  # noqa: BLE001 — unpartitioned tables raise, and that is fine
            log.debug("no partitions for %s: %s", table.fqn, exc)
            return PartitionInfo(keys=keys)

        values = [str(list(r.values())[0]) for r in rows if r]
        return PartitionInfo(keys=keys, count=len(values), latest=max(values) if values else None)

    def stats(self, table: TableInfo) -> TableStats:
        """
        Hive statistics are optional and often stale, so a failure here is not an
        error — it is the normal case, and callers must handle None.
        """
        try:
            rows = self._rows(f"DESCRIBE FORMATTED {table.fqn}")
        except Exception as exc:  # noqa: BLE001
            log.debug("no stats for %s: %s", table.fqn, exc)
            return TableStats()

        stats = TableStats()
        for row in rows:
            key = (row.get("col_name") or "").strip().lower()
            value = (row.get("data_type") or "").strip()
            if "numrows" in key.replace("_", ""):
                stats.rows = _as_int(value)
            elif "totalsize" in key.replace("_", ""):
                stats.bytes = _as_int(value)
        return stats


def _as_int(value: str) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
