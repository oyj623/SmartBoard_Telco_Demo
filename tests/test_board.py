#!/usr/bin/env python3
"""
Nusatel board tests.

    python -m backend.seed.seed_all      # once, to build the fixture
    python tests/test_board.py

No framework, no fixtures, no network, no model. Every case here is either
something that broke while the manifest was being written, or something that
must never break in production. The refusal cases matter more than the happy
path: they are the security argument, and an argument you do not test is a wish.

The story-arc checks at the end are unusual for a test suite and earn their
place: the demo script says specific things out loud from a stage, and if the
seed ever stops producing them, the presenter finds out in front of a room
rather than here.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "nusatel.db"
if not DB.exists():
    print(f"fixture missing: {DB}\nrun `python -m backend.seed.seed_all` first")
    sys.exit(1)

from backend.board_security import FINANCIAL_METRICS, visible_metric_ids  # noqa: E402
from backend.routers.board import ENGINE, MANIFEST  # noqa: E402
from backend.smartboard import ManifestError, SecurityContext, TurnContext, run_turn  # noqa: E402
from backend.smartboard.brain import HeuristicBrain  # noqa: E402

EXEC = SecurityContext(user_id="1", tenant_id="1", roles=["exec"], attributes={"state_codes": []})
NORTH = SecurityContext(
    user_id="2", tenant_id="2", roles=["regional"],
    attributes={"state_codes": ["PLS", "KDH", "PNG", "PRK"]},
)
BORNEO = SecurityContext(
    user_id="3", tenant_id="3", roles=["regional"],
    attributes={"state_codes": ["SBH", "SWK", "LBN"]},
)
UNASSIGNED = SecurityContext(user_id="9", tenant_id="9", roles=["regional"], attributes={"state_codes": []})

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def query(ir, ctx=EXEC):
    return ENGINE.run_query(ir, ctx)


def refused(ir, ctx=EXEC):
    """Returns the refusal message, or None if the query was allowed."""
    try:
        query(ir, ctx)
        return None
    except (ManifestError, ValueError) as exc:
        return str(exc)


def rows(ir, ctx=EXEC):
    """
    The full result, the way the browser fetches it.

    Not `handle.preview` — that is deliberately three rows, because the preview
    is what the *model* receives and it must never be enough to quote figures
    from. Anything asserting on more than three rows has to go through the
    store, exactly as the browser goes through /api/board/result/{id}.
    """
    handle = query(ir, ctx)
    stored = ENGINE.store.get(handle.result_id)
    return stored.rows if stored else []


def scalar(ir, field, ctx=EXEC):
    found = rows(ir, ctx)
    return found[0][field] if found else None


# ---------------------------------------------------------------------------
print("\ncatalog")
# ---------------------------------------------------------------------------

check("manifest loads", MANIFEST.name == "nusatel")
check("currency is set", MANIFEST.currency == "RM", MANIFEST.currency)
check("three locales", set(MANIFEST.locales) == {"en", "zh", "ms"}, MANIFEST.locales)
check(
    "every metric resolves to a declared dataset",
    all(m.dataset in MANIFEST.datasets for m in MANIFEST.metrics.values()),
)
check(
    "every dimension names only declared datasets",
    all(set(d.columns) <= set(MANIFEST.datasets) | {"*"} for d in MANIFEST.dimensions.values()),
)
check("a geo dimension exists", any(d.is_geo for d in MANIFEST.dimensions.values()))
check("map_regions is enabled", "map_regions" in MANIFEST.viz_enabled)
check("no ontology command survived the generalization", "add_object_panel" not in MANIFEST.commands_enabled)
check(
    "every metric label carries all three locales",
    all(set(m.label) >= {"en", "zh", "ms"} for m in MANIFEST.metrics.values()),
    [m.id for m in MANIFEST.metrics.values() if set(m.label) < {"en", "zh", "ms"}],
)

# ---------------------------------------------------------------------------
print("\nevery metric compiles and runs")
# ---------------------------------------------------------------------------

broken = []
for mid in sorted(MANIFEST.metrics):
    try:
        query({"metrics": [mid], "limit": 1})
    except Exception as exc:  # noqa: BLE001 — we want the name, whatever failed
        broken.append(f"{mid}: {exc}")
check(f"all {len(MANIFEST.metrics)} metrics execute", not broken, "\n       ".join(broken))

broken = []
for did, dim in MANIFEST.dimensions.items():
    for ds in dim.columns:
        metric = next((m for m in MANIFEST.metrics.values() if m.dataset == ds), None)
        if not metric:
            continue
        try:
            query({"metrics": [metric.id], "dimensions": [did], "limit": 1})
        except Exception as exc:  # noqa: BLE001
            broken.append(f"{did} on {ds}: {exc}")
check("all dimension/dataset pairs execute", not broken, "\n       ".join(broken))

# ---------------------------------------------------------------------------
print("\nshapes")
# ---------------------------------------------------------------------------

h = query({"metrics": ["traffic_tb"], "dimensions": ["month"], "time_range": {"last_n": 12, "grain": "month"}})
check("relative window returns 12 months", h.row_count == 12, f"got {h.row_count}")
check("months are not null", all(r["month"] for r in h.preview))

h = query({"metrics": ["avg_download_mbps"], "dimensions": ["site_location"], "limit": 3000})
cols = {c["id"]: c for c in h.columns}
check(
    "geo dimension expands to key + lat + lng",
    "lat_field" in cols["site_location"] and "lng_field" in cols["site_location"],
)
check("every site has coordinates", all(r.get("site_location__lat") is not None for r in h.preview))
check("the whole estate fits in one map query", h.row_count > 2400, h.row_count)

h = query({"metrics": ["share_5g_pct"], "dimensions": ["state_code"], "limit": 20})
check("the choropleth key returns all sixteen states", h.row_count == 16, h.row_count)
check(
    "state codes are three letters",
    all(len(r["state_code"]) == 3 for r in h.preview),
    h.preview[:2],
)

by_tech = {
    r["technology"]: r["avg_download_mbps"]
    for r in rows({"metrics": ["avg_download_mbps"], "dimensions": ["technology"], "limit": 5})
}
check("5G is faster than 4G is faster than 3G",
      by_tech.get("5G", 0) > by_tech.get("4G", 0) > by_tech.get("3G", 0), by_tech)

# ---------------------------------------------------------------------------
print("\ncaching")
# ---------------------------------------------------------------------------

first = query({"metrics": ["churn_rate_pct"], "dimensions": ["plan"]})
second = query({"metrics": ["churn_rate_pct"], "dimensions": ["plan"]})
check("identical query is a cache hit", first.result_id == second.result_id)

# ---------------------------------------------------------------------------
print("\nrefusals")
# ---------------------------------------------------------------------------

check("unknown metric", refused({"metrics": ["profit_margin"]}))
check("injected metric name", refused({"metrics": ["DROP TABLE sites"]}))
check("unknown dimension", refused({"metrics": ["traffic_tb"], "dimensions": ["weather"]}))
check(
    "cross-dataset metrics",
    "span multiple datasets" in (refused({"metrics": ["revenue_myr", "avg_latency_ms"]}) or ""),
)
check(
    "dimension off-dataset",
    "not available" in (refused({"metrics": ["revenue_myr"], "dimensions": ["technology"]}) or ""),
)
check(
    "grain finer than storage",
    refused({"metrics": ["revenue_myr"], "dimensions": ["month"], "time_range": {"grain": "day"}}),
)
check("sort on a field not selected", refused({"metrics": ["traffic_tb"], "sort": [{"field": "cost_myr"}]}))
check("empty metric list", refused({"metrics": []}))

# ---------------------------------------------------------------------------
print("\nentitlements")
# ---------------------------------------------------------------------------

msg = refused({"metrics": ["revenue_myr"]}, NORTH)
check("a regional manager is refused revenue", msg and "executive access" in msg, msg or "allowed!")
check("an executive is allowed the same metric", refused({"metrics": ["revenue_myr"]}, EXEC) is None)
check("cost is gated too", refused({"metrics": ["cost_myr"]}, NORTH) is not None)
check(
    "ARPU is deliberately NOT gated — it is operational",
    refused({"metrics": ["arpu_myr"]}, NORTH) is None,
)
check(
    "the regional tool schema omits financial metrics",
    not (set(visible_metric_ids(MANIFEST, NORTH)) & FINANCIAL_METRICS),
    sorted(set(visible_metric_ids(MANIFEST, NORTH)) & FINANCIAL_METRICS),
)
check(
    "the executive tool schema includes them",
    FINANCIAL_METRICS <= set(visible_metric_ids(MANIFEST, EXEC)),
)

# ---------------------------------------------------------------------------
print("\ntenancy")
# ---------------------------------------------------------------------------

north_states = {
    r["state_code"]
    for r in rows({"metrics": ["site_count"], "dimensions": ["state_code"], "limit": 20}, NORTH)
}
check("north sees exactly its four states", north_states == {"PLS", "KDH", "PNG", "PRK"}, north_states)

borneo_rows = query({"metrics": ["site_count"], "dimensions": ["state_code"], "limit": 20}, BORNEO)
check("borneo sees exactly its three states", borneo_rows.row_count == 3, borneo_rows.row_count)

check(
    "an executive sees all sixteen",
    query({"metrics": ["site_count"], "dimensions": ["state_code"], "limit": 20}, EXEC).row_count == 16,
)
check(
    "an unassigned manager sees nothing",
    query({"metrics": ["site_count"], "dimensions": ["state_code"]}, UNASSIGNED).row_count == 0,
)
check(
    "a filter cannot widen tenancy",
    query(
        {
            "metrics": ["site_count"],
            "dimensions": ["state_code"],
            "filters": [{"dim": "state_code", "op": "in",
                         "value": ["PLS", "KDH", "PNG", "PRK", "SGR", "KUL", "JHR"]}],
        },
        NORTH,
    ).row_count == 4,
)
check(
    "tenancy applies to every dataset, not just the spine",
    all(
        query({"metrics": [metric], "dimensions": ["state_code"], "limit": 20}, NORTH).row_count <= 4
        for metric in ("traffic_tb", "subscribers_total", "alarm_count", "ticket_count", "site_count")
    ),
)
check(
    "a scoped total really is smaller than the national one",
    scalar({"metrics": ["site_count"]}, "site_count", NORTH)
    < scalar({"metrics": ["site_count"]}, "site_count", EXEC),
)

# ---------------------------------------------------------------------------
print("\ninjection")
# ---------------------------------------------------------------------------

hostile = "SGR-0001'; DROP TABLE sites;--"
h = query({"metrics": ["site_count"], "filters": [{"dim": "site", "op": "=", "value": hostile}]})
stored = ENGINE.store.get(h.result_id)
check("hostile value is bound, not spliced", hostile not in stored.sql, stored.sql)
check("it appears in the parameters instead", hostile in stored.params.values())
check("and it matches nothing", h.preview[0]["site_count"] == 0, h.preview)

with sqlite3.connect(DB) as conn:
    check("target table intact", conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] > 2400)

try:
    ENGINE.adapter.run("UPDATE sites SET status = 'offline'", {})
    check("read-only handle blocks writes", False, "the UPDATE succeeded")
except sqlite3.DatabaseError:
    check("read-only handle blocks writes", True)

# ---------------------------------------------------------------------------
print("\ncommand validation")
# ---------------------------------------------------------------------------

h = query({"metrics": ["traffic_tb"], "dimensions": ["month"]})
live = {h.result_id}

out = ENGINE.validate_commands(
    [{"action": "add_panel", "panel_id": "p_t", "result_id": h.result_id, "viz": "line",
      "encoding": {"x": "month", "y": ["traffic_tb"]}, "title": {"en": "Traffic", "zh": "流量", "ms": "Trafik"}}],
    EXEC, known_result_ids=live,
)
check("a valid trilingual command is accepted", len(out.accepted) == 1, out.rejected)

out = ENGINE.validate_commands(
    [
        {"action": "add_panel", "panel_id": "p_a", "result_id": h.result_id, "viz": "iframe",
         "encoding": {}, "title": {"en": "x"}},
        {"action": "add_panel", "panel_id": "p_b", "result_id": "r_made_up", "viz": "line",
         "encoding": {"x": "month"}, "title": {"en": "x"}},
        {"action": "add_panel", "panel_id": "p_c", "result_id": h.result_id, "viz": "line",
         "encoding": {"x": "not_a_column"}, "title": {"en": "x"}},
        {"action": "run_sql", "sql": "SELECT 1"},
        {"action": "add_object_panel", "panel_id": "p_d", "object_type": "site", "keys": ["SGR-0001"],
         "title": {"en": "x"}},
    ],
    EXEC, known_result_ids=live,
)
check("five bad commands rejected", len(out.rejected) == 5 and not out.accepted, out.rejected)
check("unregistered viz refused", any("iframe" in r["error"] for r in out.rejected))
check("fabricated result_id refused", any("r_made_up" in r["error"] for r in out.rejected))
check("bad encoding refused", any("not_a_column" in r["error"] for r in out.rejected))
check("unknown action refused", any("run_sql" in r["error"] for r in out.rejected))
check("the removed ontology command is gone for good",
      any("add_object_panel" in r["error"] for r in out.rejected))

# Control panels: the one kind that is drawn without a result. The exception is
# declared in the manifest under `viz.dataless`, and it has to stay narrow — the
# point of these three checks is that making it possible for a filter to have no
# result_id did not make it optional for a chart.
out = ENGINE.validate_commands(
    [{"action": "add_panel", "panel_id": "p_ctl", "viz": "filter_panel",
      "encoding": {"dims": ["state", "technology"]}, "title": {"en": "Filters"}}],
    EXEC, known_result_ids=live,
)
check("a control panel is accepted with no result_id", len(out.accepted) == 1, out.rejected)

out = ENGINE.validate_commands(
    [
        {"action": "add_panel", "panel_id": "p_e", "viz": "line",
         "encoding": {"x": "month"}, "title": {"en": "x"}},
        {"action": "add_panel", "panel_id": "p_f", "viz": "filter_panel",
         "encoding": {"dims": ["not_a_dimension"]}, "title": {"en": "x"}},
    ],
    EXEC, known_result_ids=live,
)
check("a data panel still needs a result_id", any("needs a result_id" in r["error"] for r in out.rejected))
check("a control panel's dims are checked against the catalog",
      any("not_a_dimension" in r["error"] for r in out.rejected))
check("both control-panel abuses rejected", len(out.rejected) == 2 and not out.accepted, out.rejected)

# ---------------------------------------------------------------------------
print("\nturn loop (heuristic brain, no network)")
# ---------------------------------------------------------------------------

events = list(run_turn(ENGINE, HeuristicBrain(MANIFEST), "Map download speed across the sites",
                       TurnContext(), EXEC))
kinds = [e["type"] for e in events]
check("emits result then command then done",
      kinds.index("result") < kinds.index("command") < kinds.index("done"), kinds)
check("no errors on the golden path", "error" not in kinds, [e for e in events if e["type"] == "error"])

commands = [e["command"] for e in events if e["type"] == "command"]
check("drew a panel", bool(commands) and commands[0]["action"] == "add_panel")
check("the panel references a live result", ENGINE.store.get(commands[0]["result_id"]) is not None)
check("no SQL reaches the event stream", not any("sql" in e for e in events))

# ---------------------------------------------------------------------------
print("\nthe story arcs the demo script depends on")
# ---------------------------------------------------------------------------

# Arc 1 — the Klang Valley congestion, and the chain it sets off.
klang = rows(
    {"metrics": ["congestion_pct"], "dimensions": ["month"],
     "filters": [{"dim": "state_code", "op": "in", "value": ["SGR", "KUL"]}],
     "time_range": {"last_n": 6, "grain": "month"}, "limit": 12},
)
elsewhere = rows(
    {"metrics": ["congestion_pct"], "dimensions": ["month"],
     "filters": [{"dim": "state_code", "op": "not_in", "value": ["SGR", "KUL"]}],
     "time_range": {"last_n": 6, "grain": "month"}, "limit": 12},
)
check("arc 1: Klang congestion ends far above where it started",
      klang[-1]["congestion_pct"] - klang[0]["congestion_pct"] > 15,
      [round(r["congestion_pct"], 1) for r in klang])
check("arc 1: the rest of the country stayed flat",
      abs(elsewhere[-1]["congestion_pct"] - elsewhere[0]["congestion_pct"]) < 3,
      [round(r["congestion_pct"], 1) for r in elsewhere])

sgr_churn = rows(
    {"metrics": ["churn_rate_pct"], "dimensions": ["month"],
     "filters": [{"dim": "state_code", "op": "=", "value": "SGR"},
                 {"dim": "plan", "op": "=", "value": "prepaid"}],
     "limit": 30},
)
check("arc 1: Selangor prepaid churn rises at the end",
      sgr_churn[-1]["churn_rate_pct"] - sgr_churn[0]["churn_rate_pct"] > 0.8,
      [round(r["churn_rate_pct"], 2) for r in sgr_churn[-4:]])

# Arc 1's middle link: the complaints that arrive between the two.
sgr_tickets = rows(
    {"metrics": ["ticket_count"], "dimensions": ["month"],
     "filters": [{"dim": "state_code", "op": "=", "value": "SGR"},
                 {"dim": "ticket_category", "op": "in", "value": ["coverage", "speed"]}],
     "time_range": {"last_n": 6, "grain": "month"}, "limit": 12},
)
check("arc 1: Selangor coverage and speed tickets spike",
      max(r["ticket_count"] for r in sgr_tickets[-2:])
      > 1.3 * min(r["ticket_count"] for r in sgr_tickets[:3]),
      [r["ticket_count"] for r in sgr_tickets])

# Arc 2 — the rollout gradient.
five_g = {
    r["state_code"]: r["share_5g_pct"]
    for r in rows({"metrics": ["share_5g_pct"], "dimensions": ["state_code"], "limit": 20})
}
check("arc 2: Kuala Lumpur leads the 5G rollout", five_g["KUL"] > 55, five_g.get("KUL"))
check("arc 2: the east coast lags", five_g["KTN"] < 15 and five_g["TRG"] < 15,
      {k: round(v, 1) for k, v in five_g.items() if k in ("KTN", "TRG")})

# Arc 3 — the Sabah storm week.
storm = scalar(
    {"metrics": ["alarm_count"],
     "filters": [{"dim": "state_code", "op": "=", "value": "SBH"},
                 {"dim": "date", "op": "between", "value": ["2026-07-06", "2026-07-12"]}]},
    "alarm_count",
)
quiet = scalar(
    {"metrics": ["alarm_count"],
     "filters": [{"dim": "state_code", "op": "=", "value": "SBH"},
                 {"dim": "date", "op": "between", "value": ["2026-06-06", "2026-06-12"]}]},
    "alarm_count",
)
check("arc 3: the Sabah storm week is a real spike", storm > quiet * 8, f"storm={storm} quiet={quiet}")

# Arc 4 — the fibre shift.
fibre_first = scalar(
    {"metrics": ["revenue_myr"],
     "filters": [{"dim": "service_line", "op": "=", "value": "fibre"},
                 {"dim": "month", "op": "=", "value": "2025-03"}]}, "revenue_myr")
fibre_last = scalar(
    {"metrics": ["revenue_myr"],
     "filters": [{"dim": "service_line", "op": "=", "value": "fibre"},
                 {"dim": "month", "op": "=", "value": "2026-08"}]}, "revenue_myr")
check("arc 4: fibre revenue grew by a third or more", fibre_last > fibre_first * 1.3,
      f"{fibre_first:,.0f} → {fibre_last:,.0f}")

prepaid_first = scalar(
    {"metrics": ["arpu_myr"],
     "filters": [{"dim": "plan", "op": "=", "value": "prepaid"},
                 {"dim": "month", "op": "=", "value": "2025-03"}]}, "arpu_myr")
prepaid_last = scalar(
    {"metrics": ["arpu_myr"],
     "filters": [{"dim": "plan", "op": "=", "value": "prepaid"},
                 {"dim": "month", "op": "=", "value": "2026-08"}]}, "arpu_myr")
check("arc 4: prepaid ARPU eroded", prepaid_last < prepaid_first,
      f"{prepaid_first:.2f} → {prepaid_last:.2f}")

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(1 if failed else 0)
