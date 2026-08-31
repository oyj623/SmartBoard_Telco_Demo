#!/usr/bin/env python3
"""
Build `my_states.geojson` from the geoBoundaries open release.

    python data/build_geo.py

Run once. The output is committed, so a clone of this repo needs no network to
draw the map — and the demo is not hostage to somebody else's uptime.

What this does to the upstream file:

  - keeps only the properties the board needs, renamed to what the manifest and
    the map adapter join on (`state_code`, `name`, `region_group`)
  - normalises two names to the forms used in Malaysia today ("Malacca" →
    "Melaka", "Penang" → "Pulau Pinang")
  - rounds coordinates to five decimal places, which is about a metre — far
    finer than a national choropleth can show, and it halves the file
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

SOURCE = (
    "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/"
    "releaseData/gbOpen/MYS/ADM1/geoBoundaries-MYS-ADM1_simplified.geojson"
)
OUT = Path(__file__).resolve().parent / "my_states.geojson"

# upstream shapeName -> (code, display name, region group)
STATES = {
    "Perlis":          ("PLS", "Perlis",          "northern"),
    "Kedah":           ("KDH", "Kedah",           "northern"),
    "Penang":          ("PNG", "Pulau Pinang",    "northern"),
    "Perak":           ("PRK", "Perak",           "northern"),
    "Selangor":        ("SGR", "Selangor",        "central"),
    "Kuala Lumpur":    ("KUL", "Kuala Lumpur",    "central"),
    "Putrajaya":       ("PJY", "Putrajaya",       "central"),
    "Negeri Sembilan": ("NSN", "Negeri Sembilan", "southern"),
    "Malacca":         ("MLK", "Melaka",          "southern"),
    "Johor":           ("JHR", "Johor",           "southern"),
    "Pahang":          ("PHG", "Pahang",          "east_coast"),
    "Terengganu":      ("TRG", "Terengganu",      "east_coast"),
    "Kelantan":        ("KTN", "Kelantan",        "east_coast"),
    "Sabah":           ("SBH", "Sabah",           "east_malaysia"),
    "Sarawak":         ("SWK", "Sarawak",         "east_malaysia"),
    "Labuan":          ("LBN", "Labuan",          "east_malaysia"),
}


def round_coords(node, places: int = 5):
    """Walk the nested coordinate arrays, rounding every number."""
    if isinstance(node, (int, float)):
        return round(node, places)
    return [round_coords(child, places) for child in node]


def build() -> None:
    print(f"fetching {SOURCE.rsplit('/', 1)[-1]} …")
    response = httpx.get(SOURCE, follow_redirects=True, timeout=180)
    response.raise_for_status()
    upstream = response.json()

    features = []
    for feature in upstream["features"]:
        name = feature["properties"].get("shapeName")
        if name not in STATES:
            raise SystemExit(f"unexpected state in the upstream file: {name!r}")
        code, display, group = STATES[name]
        features.append(
            {
                "type": "Feature",
                "properties": {"state_code": code, "name": display, "region_group": group},
                "geometry": {
                    "type": feature["geometry"]["type"],
                    "coordinates": round_coords(feature["geometry"]["coordinates"]),
                },
            }
        )

    missing = set(STATES.values()) - {(f["properties"]["state_code"],
                                       f["properties"]["name"],
                                       f["properties"]["region_group"]) for f in features}
    if missing:
        raise SystemExit(f"missing states: {sorted(missing)}")

    features.sort(key=lambda f: f["properties"]["state_code"])
    out = {
        "type": "FeatureCollection",
        "name": "Malaysia states and federal territories",
        "source": "geoBoundaries gbOpen MYS ADM1 (CC BY 4.0) — see ATTRIBUTION.md",
        "features": features,
    }

    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(features)} features, {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    build()
