"""
Engine — the seam everything else plugs into.

Two public methods. `run_query` takes a raw dict from a tool call, validates it
into IR, guards it, compiles it, executes it and returns a handle.
`validate_commands` takes raw command dicts and returns typed commands, dropping
anything that references a result the caller never fetched or a viz kind the
deployment does not enable.

Neither method trusts its input. Both are the same code path whether the caller
is the brain, a REST client or a test.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from pydantic import TypeAdapter, ValidationError

from .commands import COMMAND_TYPES, DashboardCommand
from .compiler import Compiler
from .ir import Query, ResultHandle
from .manifest import Manifest, ManifestError
from .security import QueryGuard, SecurityContext, load_tenancy_hook, no_tenancy
from .store import ResultStore, StoredResult

log = logging.getLogger("smartboard")
_command_adapter = TypeAdapter(DashboardCommand)


@dataclass
class CommandOutcome:
    accepted: List[Dict[str, Any]]
    rejected: List[Dict[str, str]]

    def feedback(self) -> str:
        """What we tell the brain so it can correct itself on the next turn."""
        if not self.rejected:
            return f"Applied {len(self.accepted)} command(s) to the dashboard."
        lines = [f"Applied {len(self.accepted)} command(s)."]
        lines += [f"Rejected {r['action']}: {r['error']}" for r in self.rejected]
        return " ".join(lines)


class Engine:
    def __init__(
        self,
        manifest: Manifest,
        adapter,
        store: Optional[ResultStore] = None,
        guard: Optional[QueryGuard] = None,
        command_validators: Optional[Dict[str, Any]] = None,
    ):
        self.mf = manifest
        self.adapter = adapter
        self.store = store or ResultStore()
        self.guard = guard or QueryGuard(manifest)
        self.compiler = Compiler(manifest, dialect=getattr(adapter, "dialect", "sqlite"))
        self.tenancy = load_tenancy_hook(manifest.tenancy_hook) or no_tenancy

        # Deployment hook. Extra per-action checks the core cannot make because
        # it does not know about them — a custom command a deployment adds may
        # name things the engine has never heard of (an object type, a report
        # template, a saved view). Each validator takes the typed command and
        # raises to reject it. Without this hook a command naming a nonexistent
        # thing would be accepted here and fail in the browser, which puts the
        # error in front of the person instead of in front of the model.
        self.command_validators: Dict[str, Any] = dict(command_validators or {})

    # -- data ------------------------------------------------------------

    def run_query(self, raw: Dict[str, Any], ctx: SecurityContext) -> ResultHandle:
        try:
            q = Query.model_validate(raw)
        except ValidationError as exc:
            raise ManifestError(_friendly(exc)) from exc

        self.guard.check(q, ctx)
        q = self._resolve_relative_time(q, ctx)

        predicates = self.tenancy(ctx, self.mf.resolve_dataset(q.metrics, q.dimensions).id)
        compiled = self.compiler.compile(q, tenancy_predicates=predicates)

        fp = ResultStore.fingerprint(compiled.sql, compiled.params, ctx.scope_key())
        cached = self.store.get_by_fingerprint(fp)
        if cached:
            return _handle(cached, cached_hit=True)

        log.info("smartboard.query dataset=%s sql=%s", compiled.dataset, compiled.sql)
        rows, elapsed = self.adapter.run(compiled.sql, compiled.params)

        stored = StoredResult(
            result_id=ResultStore.new_id(),
            rows=rows,
            columns=compiled.columns,
            label=q.label,
            sql=compiled.sql,
            params=compiled.params,
            dataset=compiled.dataset,
            elapsed_ms=elapsed,
        )
        self.store.put(stored, fingerprint=fp)
        return _handle(stored)

    def _resolve_relative_time(self, q: Query, ctx: SecurityContext) -> Query:
        """Turn `last_n` into concrete bounds anchored on the newest row, not on today."""
        tr = q.time_range
        if not tr or not tr.last_n or tr.start or tr.end:
            return q

        tdim_id = tr.dim or self.mf.default_time_dim
        if not tdim_id:
            return q

        ds = self.mf.resolve_dataset(q.metrics, q.dimensions)
        col = self.mf.dimension(tdim_id).column_for(ds.id)
        if col is None:
            return q

        rows, _ = self.adapter.run(f"SELECT MAX({col}) AS mx {ds.sql_from()}", {})
        latest = rows[0]["mx"] if rows and rows[0].get("mx") else None
        if not latest:
            return q

        grain = tr.grain or ("month" if re.fullmatch(r"\d{4}-\d{2}", str(latest)) else "day")
        start = _step_back(str(latest), grain, tr.last_n - 1)
        if start:
            q = q.model_copy(deep=True)
            q.time_range.dim = tdim_id
            q.time_range.start = start
            q.time_range.end = str(latest)
            q.time_range.grain = q.time_range.grain or grain
        return q

    # -- view ------------------------------------------------------------

    def validate_commands(
        self,
        raw_commands: List[Dict[str, Any]],
        ctx: SecurityContext,
        known_result_ids: Optional[set] = None,
        allowed_actions: Optional[set] = None,
    ) -> CommandOutcome:
        # `allowed_actions` narrows the manifest's enabled set for one turn.
        # A deployment with a per-mode toggle uses it so a command belonging to
        # a switched-off module is refused, rather than merely absent from the
        # schema — a toggle has to be a real boundary, not a hint to the model.
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, str]] = []

        for raw in raw_commands:
            action = raw.get("action", "?")
            try:
                if action not in COMMAND_TYPES:
                    raise ManifestError(f"unknown action '{action}'")
                if action not in self.mf.commands_enabled:
                    raise ManifestError(f"action '{action}' is not enabled in this deployment")
                if allowed_actions is not None and action not in allowed_actions:
                    raise ManifestError(f"action '{action}' is not available in this mode")

                cleaned = {k: v for k, v in raw.items() if v is not None}
                cmd = _command_adapter.validate_python(cleaned)

                if action in ("add_panel", "update_panel"):
                    self._check_panel(cmd, known_result_ids)
                if action == "set_filter":
                    self.mf.dimension(cmd.filter.dim)

                validator = self.command_validators.get(action)
                if validator:
                    validator(cmd)

                accepted.append(cmd.model_dump(exclude_none=True))
            except (ValidationError, ManifestError, ValueError) as exc:
                msg = _friendly(exc) if isinstance(exc, ValidationError) else str(exc)
                log.warning("smartboard.command_rejected action=%s error=%s", action, msg)
                rejected.append({"action": action, "error": msg})

        return CommandOutcome(accepted=accepted, rejected=rejected)

    def _check_panel(self, cmd, known_result_ids: Optional[set]) -> None:
        viz = getattr(cmd, "viz", None)
        if viz and viz not in self.mf.viz_enabled:
            raise ManifestError(f"viz '{viz}' is not enabled (available: {', '.join(self.mf.viz_enabled)})")

        rid = getattr(cmd, "result_id", None)
        if rid:
            if known_result_ids is not None and rid not in known_result_ids:
                raise ManifestError(f"result_id '{rid}' was not produced in this turn")
            if self.store.get(rid) is None:
                raise ManifestError(f"result_id '{rid}' has expired — re-run query_metrics")

        enc = getattr(cmd, "encoding", None)
        if enc and rid:
            stored = self.store.get(rid)
            if stored:
                available = {c["id"] for c in stored.columns}
                for channel in ("x", "series", "color", "size", "value", "geo"):
                    ref = getattr(enc, channel, None)
                    if ref and ref not in available:
                        raise ManifestError(
                            f"encoding.{channel}='{ref}' is not a column of {rid} "
                            f"(has: {', '.join(sorted(available))})"
                        )
                for ref in enc.y or []:
                    if ref not in available:
                        raise ManifestError(f"encoding.y='{ref}' is not a column of {rid}")


# -- helpers -------------------------------------------------------------


def _handle(stored: StoredResult, cached_hit: bool = False) -> ResultHandle:
    return ResultHandle(
        result_id=stored.result_id,
        label=stored.label,
        columns=stored.columns,
        row_count=len(stored.rows),
        preview=stored.rows[:3],
        truncated=False,
        elapsed_ms=0 if cached_hit else stored.elapsed_ms,
    )


def _step_back(latest: str, grain: str, n: int) -> Optional[str]:
    if grain == "month" and re.fullmatch(r"\d{4}-\d{2}", latest):
        y, m = int(latest[:4]), int(latest[5:7])
        total = y * 12 + (m - 1) - n
        return f"{total // 12:04d}-{total % 12 + 1:02d}"
    if grain == "year" and re.fullmatch(r"\d{4}", latest):
        return f"{int(latest) - n:04d}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", latest[:10]):
        d = date.fromisoformat(latest[:10]) - timedelta(days=n * (7 if grain == "week" else 1))
        return d.isoformat()
    return None


def _friendly(exc: ValidationError) -> str:
    bits = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err["loc"] if p != "function-after")
        bits.append(f"{loc or 'input'}: {err['msg']}")
    return "; ".join(bits)
