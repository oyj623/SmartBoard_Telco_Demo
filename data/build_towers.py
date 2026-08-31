#!/usr/bin/env python3
"""
Build `towers_my.csv` — real cell-site positions for Malaysia.

    python data/build_towers.py

Run once. The output is committed, so a clone needs no network and the demo is
not hostage to somebody else's uptime.

Source: the OpenCelliD `cell_towers` dataset hosted on the public ClickHouse
playground, filtered to MCC 502 (Malaysia) and to the country's bounding box.
Positions are deduplicated onto a ~250 m grid, because OpenCelliD holds one row
per *cell* and a single physical mast carries several — we want masts.

If the playground is unreachable, nothing is written and the seed falls back to
synthesizing positions around real city coordinates. See ATTRIBUTION.md.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent / "towers_my.csv"
ENDPOINT = "https://play.clickhouse.com/?user=play"

# One row per ~100 m grid square, so the several cells a single physical mast
# carries collapse onto one site. `radio` comes through because it is a real
# signal about what is deployed there — the seed uses it to weight which sites
# become 5G.
#
# Two details worth keeping if you adapt this:
#
#   - The output aliases must NOT be `lat`/`lon`. ClickHouse resolves the alias
#     back to the source column inside the aggregate and the query 500s.
#   - Sample with `cityHash64`, not `ORDER BY cells DESC`. Ordering by cell
#     count returns four thousand masts inside Kuala Lumpur; hashing takes a
#     deterministic uniform sample of the deduplicated list, which preserves the
#     real national density — dense on the Klang Valley and Penang, sparse
#     across Pahang and interior Sarawak, which is what the map should show.
QUERY = """
SELECT
    round(avg(lat), 5) AS y,
    round(avg(lon), 5) AS x,
    any(radio) AS r,
    count() AS cells
FROM cell_towers
WHERE mcc = 502
  AND lat BETWEEN 0.80 AND 7.40
  AND lon BETWEEN 99.50 AND 119.40
  AND radio IN ('GSM', 'UMTS', 'LTE', 'NR')
GROUP BY round(lat, 3) AS gy, round(lon, 3) AS gx
ORDER BY cityHash64(gy, gx)
LIMIT 4000
FORMAT CSVWithNames
"""


def build() -> int:
    print("querying the ClickHouse playground for OpenCelliD Malaysia …")
    try:
        response = httpx.post(ENDPOINT, content=QUERY.encode(), timeout=180)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — any failure means "fall back"
        print(f"  unreachable ({type(exc).__name__}: {exc})")
        print("  nothing written — the seed will synthesize positions instead.")
        return 1

    rows = list(csv.DictReader(response.text.splitlines()))
    if len(rows) < 500:
        print(f"  only {len(rows)} rows came back; that is too few to be the real dataset.")
        return 1

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["lat", "lng", "radio", "cells"])
        for row in rows:
            writer.writerow([row["y"], row["x"], row["r"].strip('"'), row["cells"]])

    print(f"wrote {OUT.name}: {len(rows):,} mast positions, {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(build())
