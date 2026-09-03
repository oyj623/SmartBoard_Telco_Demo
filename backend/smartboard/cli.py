"""
Command line: catalog management.

    python -m smartboard.cli catalog pull    board.yaml   refresh the lock, show the SQL diff
    python -m smartboard.cli catalog verify  board.yaml   compare and fail — for CI
    python -m smartboard.cli catalog show    board.yaml   the resolved catalog, with sources
    python -m smartboard.cli catalog draft   board.yaml   introspection only, to seed an overrides file

The lock is the mechanism that lets catalog metadata live outside your repository
without letting anyone outside your repository change the SQL you execute. It is
only useful if refreshing it is one command and reviewing it is a diff, which is
what this file is for.

`pull` and `draft` need a live warehouse when the config names an introspect
source. Deployments wire that up themselves — see `--introspect-dsn`, or import
`load_board` and pass your own Introspector.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from .catalog import (
    Conventions,
    HiveIntrospector,
    build_catalog,
    diff_lock,
    read_lock,
    sources_from_config,
    write_lock,
)
from .config import config_from_dict


def _load_config(path: Path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return config_from_dict(raw), raw


def _introspector_from(dsn: Optional[str]) -> Any:
    """
    Build an Introspector from a DSN, for the CLI's convenience only.

    Only SQLite is wired up here, because it is what the framework ships an
    adapter for and what the tests can exercise. A Hive deployment passes its own
    connection: `HiveIntrospector(your_adapter)`.
    """
    if not dsn:
        return None
    if dsn.startswith("sqlite://"):
        from .adapters.sqlite import SQLiteAdapter

        return HiveIntrospector(SQLiteAdapter(dsn.replace("sqlite://", ""), read_only=True))
    raise SystemExit(
        f"the CLI does not know how to connect to {dsn!r}. Import load_board and pass an "
        "Introspector built from your own adapter."
    )


def _build(path: Path, dsn: Optional[str]):
    config, _ = _load_config(path)
    if not config.catalog_sources:
        raise SystemExit(
            f"{path.name} has no catalog.sources — it is a single-file manifest, and its SQL is "
            "already in your repository. There is nothing to lock."
        )
    sources = sources_from_config(
        config.catalog_sources, path.parent, introspector=_introspector_from(dsn)
    )
    return config, build_catalog(sources)


def _lock_path(config, base: Path) -> Path:
    if not config.catalog_lock:
        raise SystemExit(
            "no catalog.lock in config. Add one — a catalog assembled from outside your "
            "repository needs its SQL pinned to something a person reviewed."
        )
    p = Path(config.catalog_lock)
    return p if p.is_absolute() else (base / p).resolve()


def cmd_pull(args) -> int:
    path = Path(args.board)
    config, catalog = _build(path, args.introspect_dsn)
    lock = _lock_path(config, path.parent)

    before = read_lock(lock)
    diff = diff_lock(catalog, before)

    if diff.clean and before:
        print(f"lock is already current — {len(before)} entries, nothing to review")
        return 0

    print(f"catalog: {len(catalog.metrics)} metrics, {len(catalog.dimensions)} dimensions, "
          f"{len(catalog.datasets)} datasets\n")

    if before:
        print("SQL changes needing review:")
        for key in sorted(diff.changed):
            print(f"  ~ {key}")
        for key in sorted(diff.added):
            print(f"  + {key}")
        for key in sorted(diff.removed):
            print(f"  - {key}")
        print()
    else:
        print(f"writing a first lock over {len(catalog.metrics) + len(catalog.dimensions)} entries\n")

    low = [m.id for m in catalog.metrics.values() if m.confidence == "low"]
    if low:
        print(f"{len(low)} inferred metric(s) — confirm these before trusting them:")
        for mid in sorted(low)[:20]:
            print(f"  ? {mid}  {catalog.metrics[mid].expr}")
        if len(low) > 20:
            print(f"  … and {len(low) - 20} more")
        print()

    write_lock(lock, catalog)
    print(f"wrote {lock}")
    print("Review the diff before committing — it is the SQL this deployment will execute.")
    return 0


def cmd_verify(args) -> int:
    path = Path(args.board)
    config, catalog = _build(path, args.introspect_dsn)
    lock = _lock_path(config, path.parent)

    locked = read_lock(lock)
    if not locked:
        print(f"no lock at {lock} — run: catalog pull", file=sys.stderr)
        return 1

    diff = diff_lock(catalog, locked)
    if diff.clean:
        print(f"ok — catalog structure matches {lock.name} ({len(locked)} entries)")
        return 0

    print(f"MISMATCH against {lock.name}: {diff.describe()}", file=sys.stderr)
    print("The SQL differs from what was reviewed. Run: catalog pull", file=sys.stderr)
    return 1


def cmd_show(args) -> int:
    path = Path(args.board)
    _, catalog = _build(path, args.introspect_dsn)

    if args.metric:
        m = catalog.metric(args.metric)
        print(json.dumps({
            "id": m.id, "dataset": m.dataset, "expr": m.expr, "label": m.label,
            "unit": m.unit, "format": m.format, "direction": m.direction,
            "description": m.description, "source": m.source, "confidence": m.confidence,
        }, indent=2, ensure_ascii=False))
        return 0

    print(f"fingerprint {catalog.fingerprint()}   trusted={catalog.trusted}\n")
    print(f"datasets ({len(catalog.datasets)})")
    for did, ds in sorted(catalog.datasets.items()):
        layer = f" [{ds.layer}]" if ds.layer else ""
        print(f"  {did}{layer}  {ds.from_}")
    print(f"\nmetrics ({len(catalog.metrics)})")
    for mid, m in sorted(catalog.metrics.items()):
        flag = " ?" if m.confidence == "low" else "  "
        print(f" {flag} {mid:<28} {m.dataset:<14} {m.expr[:52]}   <- {m.source}")
    print(f"\ndimensions ({len(catalog.dimensions)})")
    for did, d in sorted(catalog.dimensions.items()):
        flag = " ?" if d.confidence == "low" else "  "
        print(f" {flag} {did:<28} {d.type:<8} on {','.join(sorted(d.columns))[:44]}")
    return 0


def cmd_draft(args) -> int:
    """Introspection only, as YAML, to paste into an overrides file and edit down."""
    introspector = _introspector_from(args.introspect_dsn)
    if introspector is None:
        raise SystemExit("draft needs --introspect-dsn")

    from .catalog import IntrospectSource

    conventions = Conventions.load(args.conventions) if args.conventions else None
    catalog = IntrospectSource(introspector, args.schema, conventions).load()

    doc = catalog.to_dict()
    doc.pop("glossary", None)
    print("# Draft catalog from introspection. Every inferred field is a guess:")
    print("# confirm label, direction and format before anyone reads a chart built on them.")
    print(yaml.safe_dump(doc, sort_keys=True, allow_unicode=True, default_flow_style=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="smartboard", description="SmartBoard catalog management")
    sub = parser.add_subparsers(dest="group", required=True)

    cat = sub.add_parser("catalog", help="inspect and lock the metric catalog")
    cats = cat.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("board", help="path to board.yaml")
        p.add_argument("--introspect-dsn", default=None,
                       help="connection for introspect sources, e.g. sqlite://./warehouse.db")
        return p

    common(cats.add_parser("pull", help="refresh the lock and print the SQL diff")).set_defaults(fn=cmd_pull)
    common(cats.add_parser("verify", help="compare against the lock; non-zero on mismatch")).set_defaults(fn=cmd_verify)
    show = common(cats.add_parser("show", help="print the resolved catalog"))
    show.add_argument("--metric", default=None, help="show one metric in full")
    show.set_defaults(fn=cmd_show)

    draft = cats.add_parser("draft", help="introspection-only draft catalog as YAML")
    draft.add_argument("--introspect-dsn", required=True)
    draft.add_argument("--schema", nargs="+", required=True)
    draft.add_argument("--conventions", default=None)
    draft.set_defaults(fn=cmd_draft)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
