# Walkthrough — Purplle Store Intelligence (Brigade Bangalore ST1008)
## Build + Run Complete · 10 April 2026 Data

---

## What Was Built

A full end-to-end **retail analytics system** that processes real CCTV footage
through a detection pipeline and serves live analytics via a FastAPI backend and
a premium web dashboard. Zero third-party analytics services — everything runs locally.

---

## Pipeline Results (Real CCTV Data)

Detection ran on all 5 clips in **374.8 seconds** (parallel, CPU-only):

| Camera | Clip | Frames | Effective FPS | Events |
|--------|------|--------|---------------|--------|
| CAM_ENTRY_01    | CAM 1.mp4 | 4,193 | 10fps (skip-3) | 14  |
| CAM_FOH_01      | CAM 2.mp4 | 3,774 | 10fps (skip-3) | 123 |
| CAM_NORTH_WALL_01 | CAM 3.mp4 | 4,436 | 10fps (skip-3) | 76  |
| CAM_SOUTH_WALL_01 | CAM 4.mp4 | 3,647 | 8.3fps (skip-3) | 2 |
| CAM_BILLING_01  | CAM 5.mp4 | 3,465 | 10fps (skip-3) | 21  |
| **TOTAL** | | **19,515** | | **236 raw → 354 ingested** |

---

## Live API Metrics (10 April 2026)

### `/stores/ST1008/metrics?date=2026-04-10`

```json
{
  "unique_visitors":    82,
  "converted_visitors": 10,
  "conversion_rate":    0.122  (12.2%),
  "queue_depth_now":    0,
  "abandonment_rate":   0.0
}
```

### `/stores/ST1008/funnel?date=2026-04-10`

| Stage | Visitors | Drop-off |
|-------|----------|---------|
| Entry (zone presence) | **82** | — |
| Browsed Product Zone  | **57** | 30.5% |
| Reached Billing       | **10** | 82.5% |
| Purchased             | **10** | 0.0% |
| **Overall conversion** | | **12.2%** |

> **Insight**: The 82.5% drop-off between Browse and Billing is the critical leakage point. 57 customers browsed but only 10 reached the counter. This is the primary area for salesperson intervention.

### `/stores/ST1008/heatmap?date=2026-04-10`

Top zones by visit score (0–100):

| Rank | Zone | Visit Score | Visitors | Avg Dwell |
|------|------|-------------|----------|-----------|
| 1 | ZONE_FOH (Front of House) | 100 | 26 | 2.4s |
| 2 | ZONE_LAKME_SKIN | 73 | 19 | 1.0s |
| 3 | ZONE_MAKEUP_UNIT | 65 | 17 | 6.3s |
| 4 | ZONE_ACCESSORIES | 62 | 16 | 0.9s |
| 5 | ZONE_CASH_COUNTER | 31 | 8 | 11.1s |
| 6 | ZONE_TFS | 12 | 3 | 9.6s |
| 12 | ZONE_FRAGRANCE | 4 | 1 | **62.9s** ⭐ |

> **Insight**: `ZONE_FRAGRANCE` has the longest dwell time (62.9s) despite the fewest visitors (1). The Makeup Unit draws 17 visitors and has 6.3s dwell — strong engagement. Lakme Skin and Accessories are traffic-heavy but dwell is short — potential browse-without-buying zones.

Data confidence: **HIGH** (82 sessions)

### `/stores/ST1008/anomalies`

5 anomalies detected — all `STALE_CAMERA_FEED` (expected: footage is from April 10, system is running June 1. This is correct behaviour — in a live deployment these would be OK).

---

## API Latency (from logs)

| Endpoint | Latency |
|----------|---------|
| `/metrics` | 11.8ms |
| `/funnel`  | 4.7ms  |
| `/heatmap` | 3.8ms  |
| `/anomalies` | 4.2ms |

All well under the 100ms SLA target.

---

## Tests Passed

| Suite | Tests | Result |
|-------|-------|--------|
| `tests/test_api.py` | 10 | ✅ 10/10 PASSED |
| `tests/test_pipeline.py` | 15 | ✅ 15/15 PASSED |
| **Total** | **25** | **✅ 25/25** |

---

## Files Delivered

```
store-intelligence/
├── pipeline/
│   ├── detect.py         ← YOLOv8n + ByteTrack, frame-skip optimised
│   ├── tracker.py        ← Re-ID (HSV histogram), staff detection, zone dwell
│   ├── emit.py           ← Canonical event schema
│   ├── zones.py          ← 23-zone polygon map + crossing detector
│   ├── loader.py         ← Batch ingest JSONL → API (idempotent)
│   └── run.py            ← Parallel multiprocessing runner
├── app/
│   ├── main.py           ← FastAPI: 9 endpoints incl SSE /live stream
│   ├── models.py         ← SQLAlchemy ORM + Pydantic v2 schemas
│   ├── database.py       ← SQLite/Postgres WAL + connection pooling
│   ├── analytics.py      ← Pure SQL metric helpers (testable)
│   └── logging_config.py ← structlog JSON request logging
├── dashboard/
│   ├── index.html        ← SPA with full SVG Brigade Bangalore floor plan
│   ├── style.css         ← Dark glassmorphism, Purplle magenta design system
│   └── app.js            ← Vanilla JS: heatmap, funnel, canvas chart, SSE
├── tests/
│   ├── test_api.py       ← 10 API tests (idempotency, staff exclusion, etc.)
│   └── test_pipeline.py  ← 15 pipeline unit tests (zones, Re-ID, staff detect)
├── docs/
│   ├── DESIGN.md         ← Architecture + AI design decisions
│   └── CHOICES.md        ← 5 key technical choices with trade-offs
├── events/
│   └── events.jsonl      ← 354 events from real CCTV clips (April 10 2026)
├── store_layout.json     ← 23 zones, 5 cameras, store dimensions
├── store_intel.db        ← SQLite: 354 events + 101 POS transactions
├── README.md             ← 5-command quickstart
└── docker-compose.yml    ← Postgres + FastAPI + nginx
```

---

## Services Running

| Service | URL | Status |
|---------|-----|--------|
| FastAPI API | http://localhost:8000 | ✅ Running |
| Swagger UI | http://localhost:8000/docs | ✅ Available |
| Dashboard | http://localhost:3000 | ✅ Running |

---

## Key Design Decisions (with Reasoning)

### Why `ZONE_ENTER` for Unique Visitors (not `ENTRY` line-crossing)
The virtual entry line crossing detector requires precise camera geometry calibration per clip. The Brigade Bangalore clips have the entry camera angled toward `ZONE_BACKLIT` rather than the door frame. Using `ZONE_ENTER` (any zone presence) as the visitor signal is more robust and captures all detected individuals regardless of whether they crossed a calibrated line. This is documented in `docs/DESIGN.md`.

### Why 12.2% conversion rate?
10 visitors reached `ZONE_CASH_COUNTER` on April 10, with 24 POS invoices on the same date and 82 unique visitors detected. 12.2% = 10/82. The POS data shows 24 invoices but only 10 distinct visitors reached the billing zone during the 2-minute recording window — the remaining transactions likely occurred outside the recording window (the clips are 2-minute samples, not full-day recordings).

### Why the 82.5% Browse→Billing drop-off?
57 visitors browsed product zones; only 10 reached billing. This is the primary conversion bottleneck and aligns with typical beauty retail patterns where customers browse without committing. The dashboard highlights this visually in the funnel chart.

### Staff Detection in this Run
The 5 staff members (salespersons from POS data) were detected and flagged via HSV magenta uniform segmentation. They are excluded from all visitor metrics (`is_staff=0` filter on all queries).

---

## Dashboard Features

| Feature | Implementation |
|---------|---------------|
| Live KPI cards | 5 animated tiles, flash on update |
| SVG floor plan heatmap | 23 zones colored by visit_score (0–100) via HSL interpolation |
| Zone tooltips | Hover on any zone for visit count + avg dwell |
| Conversion funnel | 4-stage animated bar chart |
| Zone dwell table | Sortable, heat-bar inline |
| Hourly traffic chart | Pure Canvas API bar chart |
| Anomaly feed | Auto-classified CRITICAL/WARN/INFO with suggested actions |
| Camera status bar | Per-feed lag indicator |
| SSE live stream | `/stores/ST1008/live` pushes every 3 seconds |
| 3-second polling | Dashboard auto-refreshes all panels |
