"""
Compiler — Query IR to a parameterized SQL statement.

The safety argument in one paragraph: every fragment of SQL text emitted here
originates in the manifest, which is authored by you and shipped with your code.
Every value originates outside and is emitted as a placeholder, never
interpolated. There is no code path that concatenates caller-supplied text into
the statement. An LLM that returns `region = 'x'; DROP TABLE users--` fails
identifier validation before compilation begins, and even if it passed, the
string would land as a bound parameter and match zero rows.

The compiler is dialect-aware only in small ways (placeholder style, time
truncation), which live in the adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .ir import Filter, Query, Sort
from .manifest import Manifest, ManifestError

_OP_SQL = {"=": "=", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}


@dataclass
class CompiledQuery:
    sql: str
    params: Dict[str, Any]
    columns: List[Dict[str, Any]]   # ordered description of the select list
    dataset: str

    def explain(self) -> str:
        """Human-readable form for the audit log and the result inspector."""
        out = self.sql
        for k, v in self.params.items():
            out = out.replace(f":{k}", repr(v))
        return out


class Compiler:
    def __init__(self, manifest: Manifest, dialect: str = "sqlite"):
        self.mf = manifest
        self.dialect = dialect

    # -- helpers ---------------------------------------------------------

    # Grain ordering, coarse to fine. Used to decide whether truncation is
    # needed at all, and to refuse impossible requests up front.
    _GRAIN_RANK = {"year": 0, "quarter": 1, "month": 2, "week": 3, "day": 4}
    _PREFIX_LEN = {"year": 4, "month": 7, "day": 10}

    def _time_trunc(self, expr: str, grain: str, native_grain: Optional[str]) -> str:
        """
        Truncate a time column to the requested grain.

        Columns already stored at a fixed grain (a 'YYYY-MM' text month, say)
        must not be passed through strftime — SQLite returns NULL for a string
        that is not a full date, which silently collapses a time series into one
        empty bucket. Declaring `native_grain` in the manifest avoids that, and
        lets a coarser roll-up be a cheap prefix instead of a date parse.
        """
        if native_grain:
            want, have = self._GRAIN_RANK[grain], self._GRAIN_RANK[native_grain]
            if want == have:
                return expr
            if want > have:
                raise ManifestError(
                    f"cannot break '{native_grain}' data down to '{grain}' — it is not stored that finely"
                )
            if grain in self._PREFIX_LEN:
                return f"substr({expr}, 1, {self._PREFIX_LEN[grain]})"

        if self.dialect == "sqlite":
            fmt = {"day": "%Y-%m-%d", "week": "%Y-%W", "month": "%Y-%m", "quarter": "%Y-%m", "year": "%Y"}[grain]
            return f"strftime('{fmt}', {expr})"
        return f"date_trunc('{grain}', {expr})"

    def _dim_sql(self, dim_id: str, dataset: str, grain: Optional[str] = None) -> str:
        dim = self.mf.dimension(dim_id)
        col = dim.column_for(dataset)
        if col is None:
            raise ManifestError(f"dimension '{dim_id}' is not available on dataset '{dataset}'")
        if dim.type == "time" and grain:
            return self._time_trunc(col, grain, dim.native_grain)
        return col

    def _filter_sql(self, f: Filter, dataset: str, bind: "_Binder") -> str:
        col = self._dim_sql(f.dim, dataset)
        if f.op in _OP_SQL:
            return f"{col} {_OP_SQL[f.op]} {bind(f.value)}"
        if f.op in ("in", "not_in"):
            if not f.value:
                return "1=0" if f.op == "in" else "1=1"
            placeholders = ", ".join(bind(v) for v in f.value)
            keyword = "IN" if f.op == "in" else "NOT IN"
            return f"{col} {keyword} ({placeholders})"
        if f.op == "between":
            lo, hi = f.value
            return f"{col} BETWEEN {bind(lo)} AND {bind(hi)}"
        if f.op == "contains":
            return f"{col} LIKE {bind(f'%{f.value}%')}"
        raise ManifestError(f"unsupported filter op '{f.op}'")

    # -- main ------------------------------------------------------------

    def compile(
        self,
        q: Query,
        tenancy_predicates: Optional[List[Tuple[str, Dict[str, Any]]]] = None,
    ) -> CompiledQuery:
        ds = self.mf.resolve_dataset(q.metrics, q.dimensions)
        bind = _Binder()

        grain = q.time_range.grain if q.time_range else None

        select_parts: List[str] = []
        columns: List[Dict[str, Any]] = []

        for d in q.dimensions:
            dim = self.mf.dimension(d)
            select_parts.append(f"{self._dim_sql(d, ds.id, grain)} AS {d}")
            col: Dict[str, Any] = {"id": d, "role": "dimension", "type": dim.type, "label": dim.label.get("en", d)}

            # A geo dimension is one identifier to the brain and three columns to
            # the map. Expanding it here means the model never has to remember to
            # ask for latitude and longitude alongside the place name.
            if dim.is_geo:
                select_parts.append(f"{dim.geo['lat']} AS {d}__lat")
                select_parts.append(f"{dim.geo['lng']} AS {d}__lng")
                col["lat_field"] = f"{d}__lat"
                col["lng_field"] = f"{d}__lng"

            columns.append(col)

        for m in q.metrics:
            metric = self.mf.metric(m)
            select_parts.append(f"({metric.expr}) AS {m}")
            columns.append(
                {
                    "id": m,
                    "role": "metric",
                    "type": "number",
                    "unit": metric.unit,
                    "format": metric.format,
                    "direction": metric.direction,
                    "label": metric.label.get("en", m),
                }
            )

        where: List[str] = []

        for f in q.filters:
            where.append(self._filter_sql(f, ds.id, bind))

        if q.time_range and (q.time_range.start or q.time_range.end):
            tdim = q.time_range.dim or self.mf.default_time_dim
            if not tdim:
                raise ManifestError("time_range given but no time dimension resolved")
            col = self._dim_sql(tdim, ds.id)
            if q.time_range.start:
                where.append(f"{col} >= {bind(q.time_range.start)}")
            if q.time_range.end:
                where.append(f"{col} <= {bind(q.time_range.end)}")

        # Tenancy is appended last and cannot be influenced by the caller.
        for pred_sql, pred_params in tenancy_predicates or []:
            where.append(pred_sql)
            bind.merge(pred_params)

        sql = f"SELECT {', '.join(select_parts)} {ds.sql_from()}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if q.dimensions:
            # Count emitted columns, not logical dimensions — geo expands to three.
            n_group = sum(3 if self.mf.dimension(d).is_geo else 1 for d in q.dimensions)
            sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(n_group))

        order = self._order_by(q, columns)
        if order:
            sql += " ORDER BY " + order

        limit = min(q.limit, self.mf.max_rows)
        sql += f" LIMIT {int(limit)}"

        return CompiledQuery(sql=sql, params=bind.params, columns=columns, dataset=ds.id)

    def _order_by(self, q: Query, columns: List[Dict[str, Any]]) -> str:
        known = {c["id"] for c in columns}
        parts: List[str] = []
        for s in q.sort:
            if s.field not in known:
                raise ManifestError(f"cannot sort by '{s.field}' — not in the select list")
            parts.append(f"{s.field} {'ASC' if s.dir == 'asc' else 'DESC'}")
        if not parts and q.dimensions:
            # Time series read left to right; categorical breakdowns read biggest first.
            first = self.mf.dimension(q.dimensions[0])
            if first.type == "time":
                parts.append(f"{q.dimensions[0]} ASC")
            else:
                parts.append(f"{q.metrics[0]} DESC")
        return ", ".join(parts)


class _Binder:
    """Allocates parameter names and returns the placeholder to splice in."""

    def __init__(self) -> None:
        self.params: Dict[str, Any] = {}
        self._n = 0

    def __call__(self, value: Any) -> str:
        self._n += 1
        key = f"p{self._n}"
        self.params[key] = value
        return f":{key}"

    def merge(self, extra: Dict[str, Any]) -> None:
        for k, v in extra.items():
            if k in self.params:
                raise ManifestError(f"parameter name collision on '{k}'")
            self.params[k] = v
