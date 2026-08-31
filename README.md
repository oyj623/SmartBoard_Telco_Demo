# Nusatel

**A national telco network console you drive by talking to it.**

A full-stack demo for a fictional Malaysian mobile and fibre operator — sixteen
states, 2,514 cell sites on real coordinates, 1.1 million daily network readings,
eighteen months of subscribers, revenue, alarms and customer tickets. Trilingual
(English / 中文 / Bahasa Malaysia), FastAPI + SQLite behind React + Vite.

The whole surface is one **AI Board**: a dashboard the assistant rebuilds while
you talk to it. Ask for a chart and it appears; click a state and ask about it;
ask it to redesign the board and it lays out sections. The assistant cannot write
SQL and cannot write UI code — it names metrics from a catalog you author and
emits typed commands from a closed vocabulary.

It is built on [**SmartBoard**](https://github.com/oyj623/SmartBoard), which is
the generic half: engine, browser runtime and board UI. Everything in this
repository that is *about telco* is one YAML file, one security module, two
pages and a seed script.

---

## Run it

You need **Python 3.10+** and **Node.js 18+**.

```bash
pip install -r requirements.txt
```

Copy the environment file and add a model key:

```bash
copy .env.example .env
```

Put a DeepSeek or OpenAI key in `.env`. Without one the app still runs and the
board still works, driven by a keyword-matching fallback brain — enough to see
the whole pipeline, not enough to reason well.

Then, from the project root:

```bash
python start.py
```

That fetches the map geometry if missing, seeds the database if missing (about a
minute, lands near 165 MB), installs and builds the frontend, and serves
everything from one process on <http://localhost:8000>.

| Sign in | Role | Sees |
|---|---|---|
| `hq` / `hq` | Executive | All 16 states, including revenue and cost |
| `north` / `north` | Regional | Perlis, Kedah, Pulau Pinang, Perak |
| `borneo` / `borneo` | Regional | Sabah, Sarawak, Labuan |

Regional managers see fewer states and no financial metrics — enforced
server-side, not in the interface. Try asking one of them for revenue.

### Developing the frontend

`start.py` builds a production bundle. For hot reload, run the API and Vite
separately:

```bash
python -m uvicorn backend.main:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

Vite serves on <http://localhost:5173> and proxies `/api` to port 8000.

### Tests

```bash
python tests/test_board.py
```

67 checks over the catalog, the compiler, entitlements, tenancy, injection
resistance, command validation and the turn loop — plus the four story arcs the
demo script depends on, so a reseed that quietly flattens one of them fails here
rather than on stage. No network and no model; the fallback brain drives the
conversation tests.

---

## What is in the data

One geographic spine and six facts hanging off it.

| Table | Rows | What it is |
|---|---|---|
| `states` | 16 | The spine. Real boundaries, real centroids. |
| `sites` | 2,514 | Cell sites on **real OpenCelliD coordinates**, with technology, vendor and on-air date |
| `network_kpis` | 1,085,949 | Per site, per day: availability, throughput, latency, drop rate, congestion, traffic |
| `subscriber_stats` | 864 | Per state, per month, per plan: base, net adds, churn, ARPU |
| `revenue_records` | 864 | Per state, per month, per service line — **executive only** |
| `cost_records` | 1,440 | Per state, per month, per category — **executive only** |
| `alarms` | 8,063 | Faults against sites, with severity, type and time to repair |
| `tickets` | 431,149 | Customer contacts, with category, channel, resolution time and CSAT |

The map geometry and the site coordinates are **real open data** —
geoBoundaries (CC BY) and OpenCelliD (CC BY-SA). Everything else is synthetic and
deterministic: one seeded RNG, so every clone builds an identical database.
See [`data/ATTRIBUTION.md`](data/ATTRIBUTION.md).

**Nusatel is not a real company.** Do not cite any figure here as a fact about
Malaysian telecommunications.

### Four things are hidden in the noise

The demo has something to find because the seed puts it there. Click **Demo** in
the chat header for the presenter script that walks these.

1. **The Klang Valley squeeze.** Selangor and Kuala Lumpur sit at the national
   congestion average until July, then climb to 75% while nowhere else moves.
   Drop rate follows. Coverage and speed complaints jump a fortnight later.
   Prepaid churn in Selangor goes from 3.1% to 4.2% a month after that. *A
   commercial number moved for an operational reason, and the board can walk
   back down the chain.* This is the arc the demo exists for.
2. **The 5G rollout.** 64% of Kuala Lumpur's sites carry 5G; 5% of Perlis's do.
   The gradient is real in both the site table and the on-air dates.
3. **The Sabah storm week.** 6–12 July 2026: power and transmission alarms
   across Sabah run at roughly thirty a day against a baseline of two, with
   availability dropping on the affected masts.
4. **The fibre shift.** Fibre revenue grows 39% across the window while mobile
   grows 6%, and prepaid ARPU quietly erodes.

---

## Layout

```
start.py                      seed → build → serve, one command
backend/
  smartboard/                 vendored framework — the generic engine
  board_manifest.yaml         THE catalog: 7 datasets, 27 metrics, 19 dimensions
  board_security.py           tenancy hook + role-aware guard
  routers/board.py            the AI Board API — ~30 lines of binding, no telco logic
  routers/auth.py             login and identity
  routers/geo.py              state polygons for the choropleth
  seed/seed_all.py            the whole database, deterministic
  main.py auth.py database.py
data/
  my_states.geojson           real state boundaries (geoBoundaries, CC BY)
  towers_my.csv               real mast positions (OpenCelliD, CC BY-SA)
  build_geo.py build_towers.py  rebuild either from source
  ATTRIBUTION.md
frontend/src/
  smartboard/                 vendored browser runtime, adapters and board UI
  pages/Board.jsx             the board — panels and map config as data
  pages/Login.jsx
  demoScript.js               the presenter's storyline and FAQ, as buttons
  locales/                    en · zh · bm
tests/test_board.py           67 checks
```

### How little of this is telco

The board, the chat column, the panel grid, the ten commands, the twelve chart
kinds, the SQL compiler and every security mechanism come from SmartBoard and
know nothing about networks. What makes this a telco product:

- **`backend/board_manifest.yaml`** — the metrics and dimensions. Adding a number
  the assistant can reason about is six lines of YAML; the tool schema, the
  system prompt and the number formatting all follow from it.
- **`backend/board_security.py`** — who sees which states, and who may see money.
- **`NUSATEL_VOICE`** in `routers/board.py` — the only prompt text in the
  project: the benchmarks, and when to use which map.
- **`frontend/src/pages/Board.jsx`** — which panels the board opens with.

Point the same engine at a different manifest and it drives a different product.

---

## Two maps, and why there are two

Geography answers two different questions, so there are two renderers and the
assistant picks between them:

- **`map_regions`** — a choropleth over the real state polygons, joined on the
  `state_code` dimension. For questions about *states*: coverage, churn,
  revenue by geography.
- **`map_points`** — one mark per mast, positioned from the `site_location`
  dimension, which the compiler expands into latitude and longitude columns
  automatically. For questions about *individual sites*: which are congested,
  where the alarms are.

The model names a kind and a metric. Every decision about how either map is
drawn — the basemap, the colour ramp, the legend, what a click does — is made in
code you own and review. It has no say in any of it, and a kind that is not
registered simply does not render.

---

## Security

The short version: the model is an untrusted client that happens to be good at
natural language. It cannot emit SQL, and there is no endpoint in this service
that would execute it if it did. Values it supplies become bound parameters.
Tenancy predicates are appended server-side after every filter it asked for.
Financial metrics are refused by a guard, and also trimmed out of the tool schema
it is handed — the trim is a courtesy to the model, the guard is the control.

The full threat model, both entitlement layers, and an honest list of what this
does *not* defend against are in
[SmartBoard's SECURITY.md](https://github.com/oyj623/SmartBoard/blob/main/docs/SECURITY.md).

Worth knowing here: `tickets` and `alarms` carry no free text, and no table
exposes personal data. A production deployment that added a free-text complaint
column would be exposing a prompt-injection surface — the blast radius is capped
at a wrong chart, but it is worth thinking about before you do it.

---

## License

MIT for the code. The bundled open data keeps its own licenses — see
[`data/ATTRIBUTION.md`](data/ATTRIBUTION.md).
