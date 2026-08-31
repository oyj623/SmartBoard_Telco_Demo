#!/usr/bin/env python3
"""
Build the Nusatel database.

    python -m backend.seed.seed_all

Sixteen states, ~2,500 sites on real coordinates, eighteen months of daily
network KPIs, and the commercial numbers that hang off them. Takes about a
minute and lands around 150 MB.

Everything is deterministic: one seeded RNG, no clock reads, no network. Every
clone of this repository builds a byte-identical database, which is what lets
the tests assert on figures and the demo script quote them from the stage.

Four story arcs are planted in the noise. They are the reason the demo has
anything to find:

  1. klang_congestion — Selangor and KL degrade over the final sixty days:
     congestion up, drop rate up, download down. Coverage and speed tickets
     spike a fortnight later; churn follows a month after that. The point of the
     demo: the commercial number moved because of an operational cause, and the
     board can walk back down the chain.

  2. five_g_rollout — 5G sites come on air in waves. KL, Selangor and Penang
     first; the east coast and interior Borneo last.

  3. sabah_storm — a week of power and transmission alarms across Sabah in July
     2026, with availability dropping on the affected sites. A map story.

  4. fibre_shift — fibre net adds and revenue grow steadily while prepaid ARPU
     erodes. The slow structural change under the quarterly noise.
"""

from __future__ import annotations

import csv
import math
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.auth import hash_password  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "nusatel.db"
TOWERS_CSV = ROOT / "data" / "towers_my.csv"

RNG = random.Random(42)

# The window every table shares: eighteen months ending 31 August 2026.
END = date(2026, 8, 31)
DAYS = 548
START = END - timedelta(days=DAYS - 1)

TARGET_SITES = 2500

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# code, name, region group, population (millions, roughly 2024), centroid
STATES = [
    ("PLS", "Perlis",          "northern",      0.26,  6.4414, 100.1986),
    ("KDH", "Kedah",           "northern",      2.19,  6.1184, 100.3685),
    ("PNG", "Pulau Pinang",    "northern",      1.77,  5.4141, 100.3288),
    ("PRK", "Perak",           "northern",      2.51,  4.5921, 101.0901),
    ("SGR", "Selangor",        "central",       7.21,  3.0738, 101.5183),
    ("KUL", "Kuala Lumpur",    "central",       2.03,  3.1390, 101.6869),
    ("PJY", "Putrajaya",       "central",       0.12,  2.9264, 101.6964),
    ("NSN", "Negeri Sembilan", "southern",      1.20,  2.7258, 101.9424),
    ("MLK", "Melaka",          "southern",      1.01,  2.1896, 102.2501),
    ("JHR", "Johor",           "southern",      4.01,  1.9344, 103.3595),
    ("PHG", "Pahang",          "east_coast",    1.66,  3.8126, 103.3256),
    ("TRG", "Terengganu",      "east_coast",    1.26,  5.3117, 103.1324),
    ("KTN", "Kelantan",        "east_coast",    1.94,  6.1254, 102.2381),
    ("SBH", "Sabah",           "east_malaysia", 3.42,  5.9788, 116.0753),
    ("SWK", "Sarawak",         "east_malaysia", 2.45,  1.5533, 110.3592),
    ("LBN", "Labuan",          "east_malaysia", 0.10,  5.2831, 115.2308),
]

STATE_BY_CODE = {s[0]: s for s in STATES}

# Districts, so the `district` dimension has something real in it.
DISTRICTS = {
    "PLS": ["Kangar", "Arau", "Padang Besar"],
    "KDH": ["Alor Setar", "Sungai Petani", "Kulim", "Langkawi", "Jitra"],
    "PNG": ["George Town", "Bayan Lepas", "Butterworth", "Bukit Mertajam", "Balik Pulau"],
    "PRK": ["Ipoh", "Taiping", "Teluk Intan", "Sitiawan", "Kuala Kangsar"],
    "SGR": ["Petaling", "Klang", "Hulu Langat", "Gombak", "Sepang", "Kuala Selangor"],
    "KUL": ["Bukit Bintang", "Cheras", "Kepong", "Setapak", "Bangsar"],
    "PJY": ["Presint 1", "Presint 9", "Presint 15"],
    "NSN": ["Seremban", "Port Dickson", "Nilai", "Rembau"],
    "MLK": ["Melaka Tengah", "Alor Gajah", "Jasin"],
    "JHR": ["Johor Bahru", "Batu Pahat", "Kluang", "Muar", "Kulai", "Pontian"],
    "PHG": ["Kuantan", "Temerloh", "Bentong", "Pekan", "Cameron Highlands"],
    "TRG": ["Kuala Terengganu", "Kemaman", "Dungun", "Besut"],
    "KTN": ["Kota Bharu", "Pasir Mas", "Tanah Merah", "Gua Musang"],
    "SBH": ["Kota Kinabalu", "Sandakan", "Tawau", "Lahad Datu", "Keningau"],
    "SWK": ["Kuching", "Miri", "Sibu", "Bintulu", "Sri Aman"],
    "LBN": ["Victoria", "Rancha-Rancha"],
}

VENDOR_BY_REGION = {
    "northern": ["Nokia", "Nokia", "Ericsson", "Huawei"],
    "central": ["Ericsson", "Ericsson", "Huawei", "Nokia"],
    "southern": ["Ericsson", "Huawei", "Huawei", "ZTE"],
    "east_coast": ["Huawei", "Huawei", "ZTE", "Nokia"],
    "east_malaysia": ["Huawei", "ZTE", "ZTE", "Ericsson"],
}

# 5G rollout waves — which states get built out first, and how deep.
FIVE_G_PRIORITY = {
    "KUL": 0.62, "PJY": 0.58, "SGR": 0.47, "PNG": 0.44, "MLK": 0.31,
    "JHR": 0.30, "NSN": 0.24, "PRK": 0.21, "SWK": 0.18, "SBH": 0.16,
    "KDH": 0.15, "LBN": 0.14, "PHG": 0.11, "TRG": 0.09, "KTN": 0.08, "PLS": 0.07,
}

PLANS = ["prepaid", "postpaid", "fibre"]
PLAN_MIX = {"prepaid": 0.55, "postpaid": 0.33, "fibre": 0.12}
BASE_ARPU = {"prepaid": 31.0, "postpaid": 82.0, "fibre": 128.0}
BASE_CHURN = {"prepaid": 2.9, "postpaid": 1.1, "fibre": 0.8}

SCHEMA = """
PRAGMA journal_mode = MEMORY;
PRAGMA synchronous = OFF;

DROP TABLE IF EXISTS network_kpis;
DROP TABLE IF EXISTS alarms;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS subscriber_stats;
DROP TABLE IF EXISTS revenue_records;
DROP TABLE IF EXISTS cost_records;
DROP TABLE IF EXISTS sites;
DROP TABLE IF EXISTS states;
DROP TABLE IF EXISTS region_assignments;
DROP TABLE IF EXISTS users;

CREATE TABLE states (
    id           INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    region_group TEXT NOT NULL,
    population   REAL NOT NULL,
    lat          REAL NOT NULL,
    lng          REAL NOT NULL
);

CREATE TABLE sites (
    id          INTEGER PRIMARY KEY,
    site_code   TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    state_id    INTEGER NOT NULL REFERENCES states(id),
    district    TEXT NOT NULL,
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    technology  TEXT NOT NULL,
    vendor      TEXT NOT NULL,
    on_air_date TEXT NOT NULL,
    status      TEXT NOT NULL
);

CREATE TABLE network_kpis (
    site_id          INTEGER NOT NULL REFERENCES sites(id),
    date             TEXT NOT NULL,
    availability_pct REAL NOT NULL,
    download_mbps    REAL NOT NULL,
    upload_mbps      REAL NOT NULL,
    latency_ms       REAL NOT NULL,
    drop_rate_pct    REAL NOT NULL,
    congestion_pct   REAL NOT NULL,
    traffic_gb       REAL NOT NULL
);

CREATE TABLE subscriber_stats (
    state_id       INTEGER NOT NULL REFERENCES states(id),
    month          TEXT NOT NULL,
    plan           TEXT NOT NULL,
    subscribers    INTEGER NOT NULL,
    net_adds       INTEGER NOT NULL,
    churn_rate_pct REAL NOT NULL,
    arpu_myr       REAL NOT NULL
);

CREATE TABLE revenue_records (
    state_id     INTEGER NOT NULL REFERENCES states(id),
    month        TEXT NOT NULL,
    service_line TEXT NOT NULL,
    revenue_myr  REAL NOT NULL
);

CREATE TABLE cost_records (
    state_id INTEGER NOT NULL REFERENCES states(id),
    month    TEXT NOT NULL,
    category TEXT NOT NULL,
    cost_myr REAL NOT NULL
);

CREATE TABLE alarms (
    id           INTEGER PRIMARY KEY,
    site_id      INTEGER NOT NULL REFERENCES sites(id),
    opened_at    TEXT NOT NULL,
    severity     TEXT NOT NULL,
    alarm_type   TEXT NOT NULL,
    duration_min REAL NOT NULL,
    status       TEXT NOT NULL
);

CREATE TABLE tickets (
    id               INTEGER PRIMARY KEY,
    state_id         INTEGER NOT NULL REFERENCES states(id),
    opened_date      TEXT NOT NULL,
    category         TEXT NOT NULL,
    channel          TEXT NOT NULL,
    resolution_hours REAL NOT NULL,
    status           TEXT NOT NULL,
    csat             INTEGER
);

CREATE TABLE users (
    id              INTEGER PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role            TEXT NOT NULL,
    name            TEXT
);

CREATE TABLE region_assignments (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    state_code TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX ix_kpi_site_date ON network_kpis(site_id, date);
CREATE INDEX ix_kpi_date      ON network_kpis(date);
CREATE INDEX ix_sites_state   ON sites(state_id);
CREATE INDEX ix_alarms_site   ON alarms(site_id);
CREATE INDEX ix_alarms_opened ON alarms(opened_at);
CREATE INDEX ix_tickets_state ON tickets(state_id);
CREATE INDEX ix_tickets_date  ON tickets(opened_date);
CREATE INDEX ix_subs_state    ON subscriber_stats(state_id, month);
CREATE INDEX ix_rev_state     ON revenue_records(state_id, month);
CREATE INDEX ix_cost_state    ON cost_records(state_id, month);
CREATE INDEX ix_region_user   ON region_assignments(user_id);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def months_between(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


MONTHS = months_between(START, END)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def nearest_state(lat: float, lng: float) -> str:
    """
    Assign a tower position to a state.

    Nearest centroid, with one correction that matters: Borneo and the peninsula
    are 600 km apart across the South China Sea, and a naive nearest-centroid
    puts Labuan's masts in Sarawak and some Sarawak masts in Johor. Splitting the
    candidate list by longitude first makes the assignment behave.
    """
    borneo = lng > 109.0
    candidates = [
        s for s in STATES
        if (s[0] in {"SBH", "SWK", "LBN"}) == borneo
    ]
    return min(candidates, key=lambda s: (s[4] - lat) ** 2 + (s[5] - lng) ** 2)[0]


def load_tower_positions() -> list[tuple[float, float, str]]:
    """
    Real mast positions from OpenCelliD, or synthesized ones if the file is
    absent. See data/ATTRIBUTION.md.
    """
    if TOWERS_CSV.exists():
        with TOWERS_CSV.open(encoding="utf-8") as fh:
            rows = [
                (float(r["lat"]), float(r["lng"]), r["radio"])
                for r in csv.DictReader(fh)
            ]
        if len(rows) >= 500:
            print(f"  using {len(rows):,} real mast positions from towers_my.csv")
            return rows

    print("  towers_my.csv missing — synthesizing positions around state centroids")
    synth = []
    total_weight = sum(s[3] for s in STATES)
    for code, _, _, pop, lat, lng in STATES:
        count = max(12, round(TARGET_SITES * 1.3 * pop / total_weight))
        spread = 0.55 if code in {"SBH", "SWK", "PHG"} else 0.22
        for _ in range(count):
            synth.append(
                (
                    lat + RNG.gauss(0, spread),
                    lng + RNG.gauss(0, spread),
                    RNG.choices(["LTE", "UMTS", "NR"], weights=[70, 22, 8])[0],
                )
            )
    return synth


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def seed_states(conn: sqlite3.Connection) -> dict[str, int]:
    conn.executemany(
        "INSERT INTO states (id, code, name, region_group, population, lat, lng) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(i + 1, *s) for i, s in enumerate(STATES)],
    )
    return {s[0]: i + 1 for i, s in enumerate(STATES)}


def seed_sites(conn: sqlite3.Connection, state_ids: dict[str, int]) -> list[dict]:
    """
    Place sites on real coordinates, then decide what each one is.

    Technology is not sprinkled at random: 5G is assigned by state priority and
    its `on_air_date` lands inside a rollout wave, so "5G share by state" and
    "5G sites over time" both tell the same story — which is arc 2.
    """
    positions = load_tower_positions()
    RNG.shuffle(positions)

    by_state: dict[str, list] = {}
    for lat, lng, radio in positions:
        by_state.setdefault(nearest_state(lat, lng), []).append((lat, lng, radio))

    # Sites per state follow population, floored so every state is on the map.
    total_pop = sum(s[3] for s in STATES)
    quota = {
        s[0]: max(15, round(TARGET_SITES * s[3] / total_pop))
        for s in STATES
    }

    sites: list[dict] = []
    site_id = 0

    for code, name, region, _pop, _lat, _lng in STATES:
        available = by_state.get(code, [])
        RNG.shuffle(available)
        want = quota[code]

        # If OpenCelliD is thin in a state, top up around its centroid rather
        # than leaving a hole in the map.
        chosen = available[:want]
        while len(chosen) < want:
            s = STATE_BY_CODE[code]
            spread = 0.5 if code in {"SBH", "SWK", "PHG"} else 0.18
            chosen.append((s[4] + RNG.gauss(0, spread), s[5] + RNG.gauss(0, spread), "LTE"))

        five_g_share = FIVE_G_PRIORITY[code]
        districts = DISTRICTS[code]
        vendors = VENDOR_BY_REGION[region]

        for seq, (lat, lng, radio) in enumerate(chosen, start=1):
            site_id += 1

            # A mast OpenCelliD already sees as NR is a good candidate for 5G.
            roll = RNG.random() * (0.75 if radio == "NR" else 1.0)
            if roll < five_g_share:
                technology = "5G"
            elif roll < five_g_share + 0.70:
                technology = "4G"
            else:
                technology = "3G"

            if technology == "5G":
                # Rollout waves: priority states start earlier in the window.
                earliest = 30 + int((1 - five_g_share) * 300)
                on_air = START + timedelta(days=RNG.randint(earliest, DAYS - 20))
            elif technology == "4G":
                on_air = START - timedelta(days=RNG.randint(200, 2600))
            else:
                on_air = START - timedelta(days=RNG.randint(2600, 5200))

            status = RNG.choices(["on_air", "maintenance", "offline"], weights=[97, 2, 1])[0]

            sites.append(
                {
                    "id": site_id,
                    "site_code": f"{code}-{seq:04d}",
                    "name": f"{RNG.choice(districts)} {seq:03d}",
                    "state_id": state_ids[code],
                    "state_code": code,
                    "district": RNG.choice(districts),
                    "lat": round(lat, 5),
                    "lng": round(lng, 5),
                    "technology": technology,
                    "vendor": RNG.choice(vendors),
                    "on_air_date": on_air.isoformat(),
                    "status": status,
                    # Site "size" drives traffic — a lognormal spread, so a few
                    # city masts carry far more than a rural one.
                    "scale": math.exp(RNG.gauss(0, 0.55)),
                }
            )

    conn.executemany(
        "INSERT INTO sites (id, site_code, name, state_id, district, lat, lng, "
        "technology, vendor, on_air_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (s["id"], s["site_code"], s["name"], s["state_id"], s["district"], s["lat"],
             s["lng"], s["technology"], s["vendor"], s["on_air_date"], s["status"])
            for s in sites
        ],
    )
    return sites


# Arc 1 and arc 3 need to be recognised from inside the daily loop.
ARC1_STATES = {"SGR", "KUL"}
ARC1_RAMP_DAYS = 60
ARC3_STATE = "SBH"
ARC3_START = date(2026, 7, 6)
ARC3_END = date(2026, 7, 12)

BASE_SPEED = {"3G": (6.0, 2.0), "4G": (38.0, 8.0), "5G": (210.0, 45.0)}
BASE_LATENCY = {"3G": (90.0, 15.0), "4G": (38.0, 6.0), "5G": (16.0, 3.0)}


def seed_network_kpis(conn: sqlite3.Connection, sites: list[dict]) -> int:
    """
    One row per site per day. This is the big table — about 1.35 million rows —
    and it is where arcs 1 and 3 actually live.
    """
    written = 0
    batch: list[tuple] = []

    for site in sites:
        tech = site["technology"]
        speed_mu, speed_sd = BASE_SPEED[tech]
        lat_mu, lat_sd = BASE_LATENCY[tech]
        scale = site["scale"]
        in_arc1 = site["state_code"] in ARC1_STATES
        in_arc3 = site["state_code"] == ARC3_STATE
        # A per-site personality, so two sites in one state are not identical.
        site_bias = RNG.gauss(0, 0.06)

        on_air = date.fromisoformat(site["on_air_date"])

        for offset in range(DAYS):
            day = START + timedelta(days=offset)
            if day < on_air:
                continue      # a site does not report before it exists

            weekday = day.weekday()
            # Traffic and congestion peak midweek; speeds sag when busy.
            busy = 1.0 + (0.14 if weekday < 5 else -0.18)
            trend = 1.0 + 0.25 * (offset / DAYS)          # +25%/yr data growth

            availability = clamp(RNG.gauss(99.4, 0.4), 90.0, 100.0)
            congestion = clamp(RNG.gauss(52.0, 12.0) * busy, 5.0, 100.0)
            download = max(0.5, RNG.gauss(speed_mu, speed_sd) * (1 + site_bias) / busy)
            latency = max(3.0, RNG.gauss(lat_mu, lat_sd) * busy)
            drop_rate = clamp(RNG.gauss(0.55, 0.20), 0.05, 30.0)
            traffic = max(0.4, RNG.gauss(42.0, 9.0) * scale * trend * busy)

            # --- arc 1: the Klang Valley squeeze --------------------------
            if in_arc1:
                remaining = DAYS - 1 - offset
                if remaining < ARC1_RAMP_DAYS:
                    ramp = (ARC1_RAMP_DAYS - remaining) / ARC1_RAMP_DAYS
                    congestion = clamp(congestion + 28.0 * ramp, 5.0, 100.0)
                    drop_rate = clamp(drop_rate + 0.9 * ramp, 0.05, 30.0)
                    download *= 1.0 - 0.25 * ramp
                    latency *= 1.0 + 0.35 * ramp

            # --- arc 3: the Sabah storm week ------------------------------
            if in_arc3 and ARC3_START <= day <= ARC3_END and RNG.random() < 0.6:
                availability = clamp(availability - RNG.uniform(3.0, 25.0), 40.0, 100.0)
                drop_rate = clamp(drop_rate + RNG.uniform(0.5, 3.0), 0.05, 30.0)

            batch.append(
                (
                    site["id"], day.isoformat(),
                    round(availability, 3), round(download, 2), round(download / 8.2, 2),
                    round(latency, 1), round(drop_rate, 3), round(congestion, 2),
                    round(traffic, 2),
                )
            )

        if len(batch) >= 200_000:
            conn.executemany(
                "INSERT INTO network_kpis (site_id, date, availability_pct, download_mbps, "
                "upload_mbps, latency_ms, drop_rate_pct, congestion_pct, traffic_gb) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            written += len(batch)
            batch.clear()
            print(f"    {written:,} KPI rows …")

    if batch:
        conn.executemany(
            "INSERT INTO network_kpis (site_id, date, availability_pct, download_mbps, "
            "upload_mbps, latency_ms, drop_rate_pct, congestion_pct, traffic_gb) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
        written += len(batch)

    return written


def seed_subscribers(conn: sqlite3.Connection, state_ids: dict[str, int]) -> int:
    """
    Monthly base, net adds, churn and ARPU by state and plan.

    Arc 4 (the fibre shift) is the plan-level trend; arc 1's consequence — a
    churn spike in Selangor in the last two months — is layered on top.
    """
    rows = []
    total_pop = sum(s[3] for s in STATES)
    national_subs = 11_800_000

    for code, _name, _region, pop, _lat, _lng in STATES:
        state_total = national_subs * (pop / total_pop)

        for plan in PLANS:
            base = state_total * PLAN_MIX[plan]
            subs = base

            for i, month in enumerate(MONTHS):
                # Arc 4: fibre grows, prepaid slowly bleeds.
                growth = {"fibre": 0.012, "postpaid": 0.003, "prepaid": -0.003}[plan]
                net = subs * (growth + RNG.gauss(0, 0.0015))

                churn = max(0.15, RNG.gauss(BASE_CHURN[plan], BASE_CHURN[plan] * 0.10))
                arpu = BASE_ARPU[plan] * (1 + RNG.gauss(0, 0.02))
                if plan == "prepaid":
                    arpu *= 1.0 - 0.02 * (i / 12.0)      # arc 4: prepaid ARPU erodes

                # Arc 1: the Klang Valley congestion shows up as churn, late.
                if code == "SGR":
                    if month == MONTHS[-2]:
                        churn += {"prepaid": 0.8, "postpaid": 0.3, "fibre": 0.2}[plan]
                        net -= subs * 0.004
                    elif month == MONTHS[-1]:
                        churn += {"prepaid": 1.4, "postpaid": 0.5, "fibre": 0.3}[plan]
                        net -= subs * 0.007

                subs = max(1000.0, subs + net)
                rows.append(
                    (state_ids[code], month, plan, int(subs), int(net), round(churn, 3), round(arpu, 2))
                )

    conn.executemany(
        "INSERT INTO subscriber_stats (state_id, month, plan, subscribers, net_adds, "
        "churn_rate_pct, arpu_myr) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def seed_financials(conn: sqlite3.Connection, state_ids: dict[str, int], sites: list[dict]) -> tuple[int, int]:
    """Revenue by service line and cost by category. Exec-only, both of them."""
    sites_per_state: dict[str, int] = {}
    for s in sites:
        sites_per_state[s["state_code"]] = sites_per_state.get(s["state_code"], 0) + 1

    total_pop = sum(s[3] for s in STATES)
    revenue_rows, cost_rows = [], []

    for code, _name, _region, pop, _lat, _lng in STATES:
        share = pop / total_pop
        n_sites = sites_per_state.get(code, 20)

        for i, month in enumerate(MONTHS):
            # ~RM 400M a month nationally, split across service lines.
            mobile = 400_000_000 * share * 0.62 * (1 + 0.004 * i) * (1 + RNG.gauss(0, 0.02))
            fibre = 400_000_000 * share * 0.24 * (1 + 0.022 * i) * (1 + RNG.gauss(0, 0.03))
            enterprise = 400_000_000 * share * 0.14 * (1 + 0.006 * i) * (1 + RNG.gauss(0, 0.04))

            if code == "SGR" and month in MONTHS[-2:]:
                mobile *= 0.97      # arc 1 reaches the money, a little

            for line, value in (("mobile", mobile), ("fibre", fibre), ("enterprise", enterprise)):
                revenue_rows.append((state_ids[code], month, line, round(value, 2)))

            inflation = 1 + 0.003 * i
            costs = {
                "site_rental": n_sites * 3_400 * inflation,
                "power": n_sites * 2_100 * inflation * (1 + RNG.gauss(0, 0.05)),
                "maintenance": n_sites * 1_250 * inflation * (1 + RNG.gauss(0, 0.08)),
                "backhaul": n_sites * 1_800 * inflation,
                "marketing": 400_000_000 * share * 0.045 * (1 + RNG.gauss(0, 0.12)),
            }
            for category, value in costs.items():
                cost_rows.append((state_ids[code], month, category, round(value, 2)))

    conn.executemany(
        "INSERT INTO revenue_records (state_id, month, service_line, revenue_myr) VALUES (?, ?, ?, ?)",
        revenue_rows,
    )
    conn.executemany(
        "INSERT INTO cost_records (state_id, month, category, cost_myr) VALUES (?, ?, ?, ?)",
        cost_rows,
    )
    return len(revenue_rows), len(cost_rows)


ALARM_TYPES = ["power", "transmission", "hardware", "congestion", "environment"]


def seed_alarms(conn: sqlite3.Connection, sites: list[dict]) -> int:
    """Fault alarms. Arc 3 is a dense burst of them across Sabah in one week."""
    rows = []

    for site in sites:
        # Roughly one alarm per site per two months, more on older 3G kit.
        rate = 9 if site["technology"] == "3G" else 6
        for _ in range(RNG.randint(0, rate)):
            day = START + timedelta(days=RNG.randint(0, DAYS - 1))
            severity = RNG.choices(["critical", "major", "minor"], weights=[8, 32, 60])[0]
            alarm_type = RNG.choice(ALARM_TYPES)
            duration = math.exp(RNG.gauss(4.4, 0.8)) * (2.4 if severity == "critical" else 1.0)
            age = (END - day).days
            status = "open" if age < 14 and RNG.random() < 0.35 else "resolved"
            rows.append(
                (
                    site["id"],
                    f"{day.isoformat()} {RNG.randint(0, 23):02d}:{RNG.randint(0, 59):02d}:00",
                    severity, alarm_type, round(duration, 1), status,
                )
            )

    # --- arc 3: the Sabah storm week ---------------------------------------
    sabah = [s for s in sites if s["state_code"] == ARC3_STATE]
    span = (ARC3_END - ARC3_START).days
    for _ in range(220):
        site = RNG.choice(sabah)
        day = ARC3_START + timedelta(days=RNG.randint(0, span))
        rows.append(
            (
                site["id"],
                f"{day.isoformat()} {RNG.randint(0, 23):02d}:{RNG.randint(0, 59):02d}:00",
                RNG.choices(["critical", "major", "minor"], weights=[45, 40, 15])[0],
                RNG.choices(["power", "transmission", "environment"], weights=[50, 35, 15])[0],
                round(math.exp(RNG.gauss(5.4, 0.7)), 1),
                "resolved",
            )
        )

    conn.executemany(
        "INSERT INTO alarms (site_id, opened_at, severity, alarm_type, duration_min, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


TICKET_CATEGORIES = ["billing", "coverage", "speed", "installation", "service"]
TICKET_WEIGHTS = [30, 22, 18, 15, 15]


def seed_tickets(conn: sqlite3.Connection, state_ids: dict[str, int]) -> int:
    """
    Care contacts. Arc 1's middle link: Selangor coverage and speed tickets more
    than double in the final two months, between the congestion and the churn.
    """
    rows = []
    total_pop = sum(s[3] for s in STATES)

    for code, _name, _region, pop, _lat, _lng in STATES:
        share = pop / total_pop

        for month in MONTHS:
            base = int(1500 * share * 16 * RNG.uniform(0.9, 1.1))
            arc1_month = code == "SGR" and month in MONTHS[-2:]

            for _ in range(base):
                category = RNG.choices(TICKET_CATEGORIES, weights=TICKET_WEIGHTS)[0]
                if arc1_month and category in ("coverage", "speed") and RNG.random() < 0.55:
                    pass                       # keep it — inflates these two
                elif arc1_month and RNG.random() < 0.30:
                    category = RNG.choice(["coverage", "speed"])

                year, mon = int(month[:4]), int(month[5:7])
                last_day = (date(year + (mon == 12), (mon % 12) + 1, 1) - timedelta(days=1)).day
                opened = date(year, mon, RNG.randint(1, last_day))
                if opened > END:
                    continue

                resolution = math.exp(RNG.gauss(2.9, 0.75))
                if arc1_month:
                    resolution *= 1.35
                status = "open" if (END - opened).days < 5 and RNG.random() < 0.3 else "resolved"
                # Satisfaction falls as resolution drags — the honest relationship.
                csat = int(clamp(round(RNG.gauss(5.2 - math.log1p(resolution) * 0.55, 0.7)), 1, 5))

                rows.append(
                    (state_ids[code], opened.isoformat(), category,
                     RNG.choices(["app", "call_center", "store"], weights=[45, 38, 17])[0],
                     round(resolution, 2), status, csat)
                )

    conn.executemany(
        "INSERT INTO tickets (state_id, opened_date, category, channel, resolution_hours, status, csat) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


ACCOUNTS = [
    ("hq", "hq", "exec", "Aisyah Rahman", []),
    ("north", "north", "regional", "Tan Wei Ming", ["PLS", "KDH", "PNG", "PRK"]),
    ("borneo", "borneo", "regional", "Jelani Abdullah", ["SBH", "SWK", "LBN"]),
]


def seed_users(conn: sqlite3.Connection) -> None:
    for i, (username, password, role, name, codes) in enumerate(ACCOUNTS, start=1):
        conn.execute(
            "INSERT INTO users (id, username, hashed_password, role, name) VALUES (?, ?, ?, ?, ?)",
            (i, username, hash_password(password), role, name),
        )
        conn.executemany(
            "INSERT INTO region_assignments (user_id, state_code) VALUES (?, ?)",
            [(i, code) for code in codes],
        )


# ---------------------------------------------------------------------------

def main() -> None:
    if DB.exists():
        DB.unlink()

    print(f"Building {DB.name} — 18 months to {END.isoformat()}\n")
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    print("  states")
    state_ids = seed_states(conn)

    print("  sites")
    sites = seed_sites(conn, state_ids)
    print(f"    {len(sites):,} sites")

    print("  network KPIs (this is the slow one)")
    kpi_rows = seed_network_kpis(conn, sites)

    print("  subscribers")
    sub_rows = seed_subscribers(conn, state_ids)

    print("  revenue and costs")
    rev_rows, cost_rows = seed_financials(conn, state_ids, sites)

    print("  alarms")
    alarm_rows = seed_alarms(conn, sites)

    print("  tickets")
    ticket_rows = seed_tickets(conn, state_ids)

    print("  users")
    seed_users(conn)

    print("  indexes")
    conn.executescript(INDEXES)
    conn.commit()
    conn.execute("ANALYZE")
    conn.commit()
    conn.close()

    size_mb = DB.stat().st_size / 1024 / 1024
    print(
        f"\nDone — {size_mb:.0f} MB\n"
        f"  {len(sites):,} sites · {kpi_rows:,} KPI rows · {sub_rows:,} subscriber rows\n"
        f"  {rev_rows:,} revenue · {cost_rows:,} cost · {alarm_rows:,} alarms · {ticket_rows:,} tickets\n"
        f"  sign in as hq / hq, north / north, or borneo / borneo"
    )


if __name__ == "__main__":
    main()
