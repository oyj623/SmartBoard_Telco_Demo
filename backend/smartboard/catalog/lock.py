"""
The structural lock.

SmartBoard's security argument is one sentence: *every fragment of SQL text
originates in a file you author and ship with your code, so it is trusted for
exactly the reason your route handlers are trusted.* The moment a catalog can
arrive over HTTP, that sentence is false — whoever can edit a description in a
metadata UI can also rewrite a metric's SQL expression.

This module restores it, by borrowing the mechanism every package manager already
taught people. Structural fields — the ones that are literally SQL text — are
hashed, and the hashes are committed to your repository. On load they must match.
Semantic fields are not hashed and float freely, which is the point: labels,
units and descriptions are what business users actually change, and none of them
reach the statement.

    smartboard catalog pull     refresh the lock, print the SQL diff to review
    smartboard catalog verify   compare and fail — for CI

A mismatch refuses the load. That is deliberate: the failure mode of carrying on
is executing SQL nobody reviewed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .base import STRUCTURAL_FIELDS, Catalog, ManifestError

LOCK_VERSION = 1


class LockMismatch(ManifestError):
    """Raised when the catalog's SQL no longer matches what was reviewed."""


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def structural_entries(catalog: Catalog) -> Dict[str, str]:
    """
    One digest per catalog entry, over its SQL-bearing fields only.

    Keyed `kind:id` so a diff names the thing that changed rather than pointing
    at a wall of hashes.
    """
    entries: Dict[str, str] = {}

    for did, ds in catalog.datasets.items():
        entries[f"dataset:{did}"] = _digest(
            {f: getattr(ds, f) for f in STRUCTURAL_FIELDS["dataset"]}
        )
    for mid, m in catalog.metrics.items():
        entries[f"metric:{mid}"] = _digest(
            {f: getattr(m, f) for f in STRUCTURAL_FIELDS["metric"]}
        )
    for did, dim in catalog.dimensions.items():
        entries[f"dimension:{did}"] = _digest(
            {f: getattr(dim, f) for f in STRUCTURAL_FIELDS["dimension"]}
        )

    return entries


def structural_text(catalog: Catalog) -> Dict[str, Any]:
    """
    The readable form of what the digests cover.

    Written alongside the hashes so that `git diff` on the lock shows the SQL
    that changed, not just that something did. A digest nobody can read is a
    digest nobody reviews.
    """
    out: Dict[str, Any] = {}
    for did, ds in catalog.datasets.items():
        out[f"dataset:{did}"] = {"from": ds.from_, "joins": list(ds.joins)}
    for mid, m in catalog.metrics.items():
        out[f"metric:{mid}"] = {"expr": m.expr}
    for did, dim in catalog.dimensions.items():
        out[f"dimension:{did}"] = {"columns": dict(dim.columns), "geo": dict(dim.geo)}
    return out


@dataclass
class LockDiff:
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def describe(self) -> str:
        if self.clean:
            return "catalog structure matches the lock"
        bits = []
        if self.changed:
            bits.append(f"{len(self.changed)} changed: {', '.join(sorted(self.changed)[:8])}")
        if self.added:
            bits.append(f"{len(self.added)} new: {', '.join(sorted(self.added)[:8])}")
        if self.removed:
            bits.append(f"{len(self.removed)} removed: {', '.join(sorted(self.removed)[:8])}")
        return "; ".join(bits)


def read_lock(path: str | Path) -> Dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if raw.get("version") != LOCK_VERSION:
        raise ManifestError(
            f"{p} was written by lock version {raw.get('version')}, this build speaks {LOCK_VERSION}"
        )
    return dict(raw.get("entries", {}))


def write_lock(path: str | Path, catalog: Catalog) -> Dict[str, str]:
    entries = structural_entries(catalog)
    payload = {
        "version": LOCK_VERSION,
        "comment": (
            "Digests of every SQL-bearing catalog field. Committed on purpose: a change here "
            "is a change to the SQL this deployment will execute, and should be reviewed as such. "
            "Regenerate with `smartboard catalog pull`."
        ),
        "entries": dict(sorted(entries.items())),
        "sql": dict(sorted(structural_text(catalog).items())),
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entries


def diff_lock(catalog: Catalog, locked: Dict[str, str]) -> LockDiff:
    current = structural_entries(catalog)
    diff = LockDiff()
    for key, digest in current.items():
        if key not in locked:
            diff.added.append(key)
        elif locked[key] != digest:
            diff.changed.append(key)
    for key in locked:
        if key not in current:
            diff.removed.append(key)
    return diff


def verify(catalog: Catalog, lock_path: str | Path | None, *, strict: bool = True) -> LockDiff:
    """
    Check a catalog against its lock.

    A trusted catalog — one assembled entirely from your own source tree — needs
    no lock and is passed through. Anything else must match, or the load fails.

    `strict=False` downgrades a mismatch to a warning. It exists because there is
    a real development loop where you are iterating on a metadata service and do
    not want to re-pull constantly. It is off by default and should stay off
    anywhere that matters, because with it on, an external service can silently
    change the SQL you execute.
    """
    import logging

    log = logging.getLogger("smartboard.catalog")

    if catalog.trusted:
        return LockDiff()

    if not lock_path:
        raise ManifestError(
            "this catalog draws on a source outside your repository, so it needs a lock. "
            "Set catalog.lock in board.yaml and run `smartboard catalog pull`."
        )

    locked = read_lock(lock_path)
    if not locked:
        raise ManifestError(
            f"no lock at {lock_path}. Run `smartboard catalog pull` and review the SQL it records."
        )

    diff = diff_lock(catalog, locked)
    if diff.clean:
        return diff

    message = (
        f"the catalog's SQL no longer matches {lock_path} — {diff.describe()}. "
        "Run `smartboard catalog pull` to review the change and re-lock."
    )
    if strict:
        raise LockMismatch(message)
    log.warning("smartboard.catalog.lock_mismatch %s", message)
    return diff
