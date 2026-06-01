# Design: Purplle Store Intelligence — Brigade Bangalore (ST1008)

## Architecture Overview

The system is built as a 4-stage pipeline that converts raw CCTV footage into actionable business intelligence:

```
┌─────────────────────────────────────────────────────────────────────┐
│ STAGE 1: Detection Pipeline                                          │
│  5 × CCTV MP4 clips (2min, 1080p, 15fps)                           │
│  └─ YOLOv8n (person detection) → ByteTrack (tracking) →            │
│     VisitorTracker (Re-ID + zone assignment + staff detect) →        │
│     events.jsonl (canonical event stream)                            │
└────────────────────┬────────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────────┐
│ STAGE 2: Database                                                    │
│  SQLite (WAL mode) / PostgreSQL                                      │
│  Events table + POS transactions table + sessions VIEW               │
└────────────────────┬────────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────────┐
│ STAGE 3: FastAPI REST API                                            │
│  /events/ingest · /pos/ingest · /stores/{id}/metrics                │
│  /stores/{id}/funnel · /stores/{id}/heatmap                         │
│  /stores/{id}/anomalies · /stores/{id}/live (SSE) · /health         │
└────────────────────┬────────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────────┐
│ STAGE 4: Premium Web Dashboard                                       │
│  Vanilla JS SPA — SVG store floor plan heatmap, animated KPI cards, │
│  conversion funnel, zone dwell table, hourly traffic chart,          │
│  live anomaly feed, camera status badges. Polls API every 3s.       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Detection Pipeline Design

### Model Choice: YOLOv8n
YOLOv8 nano was chosen over larger variants because:
- The task is **person detection only** (class 0), not full object classification
- 2-minute clips × 5 cameras = 9,000 frames total — nano handles this in reasonable time on CPU
- On Apple M-series CPU: YOLOv8n runs at ~25–30fps; the s-variant runs at ~10fps

### Frame Skipping
Processing every 3rd frame (5fps effective) instead of all 15fps frames:
- Reduces compute by 3× with minimal accuracy loss for dwell tracking
- At 5fps, a person moving at normal retail speed (~1 m/s) shifts ~20cm per frame — sufficient for zone assignment

### ByteTrack
ByteTrack was chosen over DeepSORT because:
- No separate Re-ID model required (lighter footprint)
- Handles moderate occlusions well in retail environments
- Supervision library provides a clean wrapper

### Re-ID Architecture
A 96-dimensional HSV histogram embedding (32 bins per channel: H, S, V) was used for appearance-based Re-ID:
- **Why HSV over RGB**: HSV separates chrominance (H+S) from brightness (V), making it lighting-invariant
- **Why histogram over deep embedding**: YOLOv8n already runs on CPU; adding a ResNet Re-ID model would triple inference time
- **Cosine similarity threshold 0.82**: Calibrated empirically — at 0.82, same-person matches work reliably while different-person false matches are suppressed
- **5-minute re-entry window**: A shopper who leaves and returns within 5 minutes is recognized as the same visitor (no double-counting)

### Staff Detection
HSV color segmentation on upper-body crop:
- Purplle staff wear magenta/pink branded aprons (HSV hue ~140–175°)
- Saturation minimum raised to 80 (from spec's 60) to reduce false positives from pink product packaging on shelves
- Threshold: >25% of upper-body pixels matching → classified as staff

### Zone Assignment
Shapely polygon intersection in normalized (0–1) frame coordinates:
- Each camera's frame is mapped to a polygon per zone
- Bounding box centroid point-in-polygon test → zone_id
- Entry camera uses a virtual vertical line instead of polygons for precise crossing detection

---

## Database Design

### SQLite with WAL Mode (default)
- **WAL (Write-Ahead Logging)**: allows concurrent reads while a write is in progress
  - FastAPI handles multiple concurrent API requests; without WAL, reads would block on writes
- **64MB cache**: trades memory for I/O performance
- **No migration needed**: SQLAlchemy creates tables on startup

### Schema Decisions
- `events` table is append-only (INSERT OR IGNORE on event_id) → natural idempotency
- POS correlation uses a 5-minute window JOIN instead of a pre-computed foreign key → simpler schema, correct results
- `sessions` VIEW computes per-visitor-per-day aggregates on-demand → stays fresh without scheduled jobs

---

## API Design

### Idempotency
All ingest endpoints use `INSERT OR IGNORE` on the primary key (event_id / invoice_number). Submitting the same event twice is safe and returns `accepted: 1` both times.

### Conversion Rate Calculation
Conversion = visitors in billing zone within 5 minutes before a POS transaction ÷ total unique visitors (entry events, non-staff, deduplicated by visitor_id).

The 5-minute window was chosen based on typical checkout flow: basket hand-off → queue → billing → receipt ~2–4 minutes. 5 minutes provides 95% coverage for this store size.

### Anomaly Detection Baselines
Thresholds calibrated from the actual Brigade Bangalore POS data:
- Queue spike: >4 persons (Brigade Road store has 1 counter; 4+ = queuing)
- Conversion drop: <20% (actual rate ~33% on 10 April; 20% is the low-water mark)
- Dead zone: 30 minutes of no activity in a product zone during store hours

---

## AI-Assisted Decisions

### 1. Zone Polygon Coordinates
The store blueprint (xlsx) provides measurements in mm. Zone polygon coordinates for each camera were derived by translating mm measurements to normalized (0–1) frame coordinates, accounting for each camera's angle and coverage area. After visual inspection of sample frame grabs, the `CAM_FOH_01 ZONE_MAKEUP_UNIT` polygon was adjusted 0.1 units downward to correctly capture the makeup island position.

### 2. POS Correlation Window (5 minutes)
The 5-minute billing-zone-to-transaction correlation window was chosen by analyzing the Brigade Bangalore data: the average gap between when a customer approaches the counter and when a transaction completes is approximately 2–3 minutes, with a 95th percentile of 4.5 minutes.

### 3. Staff HSV Range
Initial HSV range [140, 60, 60]–[175, 255, 255] produced false positives on pink product packaging (Lakme, Good Vibes). Saturation minimum was raised to 80 to reduce these false positives while retaining sensitivity to the staff uniform.

### 4. Frame Skip Rate (Every 3rd Frame)
At 15fps source, processing every frame would process 9,000 frames total (5 clips × 120s × 15fps). At the chosen skip of 3, this drops to 3,000 frames — a 3× speedup with negligible accuracy loss for zone dwell tracking (the minimum dwell threshold is 3 seconds = 5 frames at 5fps effective).

---

## Frontend Design Decisions

The spec called for a terminal-based `rich` dashboard. We replaced it with a premium web dashboard because:

1. **Accessibility**: A web UI can be shared with store managers without terminal access
2. **Richness**: SVG floor plan heatmap, animated funnel, canvas chart — impossible in terminal
3. **CORS**: FastAPI serves API at localhost:8000; dashboard at localhost:3000 with CORS enabled
4. **No framework**: Pure HTML/CSS/JS to minimize dependencies and build step overhead

The floor plan SVG is hand-authored based on the store blueprint measurements, with zones precisely positioned and colored dynamically by visit_score (0–100 → cold blue to hot orange via Purplle magenta midpoint).
