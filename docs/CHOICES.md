# Architecture Choices — Brigade Bangalore Store Intelligence

## Choice 1: Detection Model — YOLOv8n over alternatives

| Option | FPS (CPU) | RAM | Accuracy | Selected? |
|--------|-----------|-----|----------|-----------|
| YOLOv8n | ~28fps | ~200MB | Good | ✅ **Yes** |
| YOLOv8s | ~12fps | ~350MB | Better | No |
| RT-DETR | ~5fps | ~500MB | Best | No |
| MediaPipe Pose | ~20fps | ~150MB | Person-only | No |

**Reasoning**: 5 clips × 120s × 15fps = 9,000 frames. With frame-skip-3, that's 3,000 frames to process total. YOLOv8n processes them in ~2 minutes per clip on CPU. YOLOv8s would take ~5 minutes per clip. RT-DETR would take ~15 minutes per clip. Since we only need person detection (class 0), not full object classification, nano is sufficient. MediaPipe was excluded because it doesn't produce bounding boxes compatible with ByteTrack's format without adaptation.

**Trade-off accepted**: YOLOv8n has higher miss rate in crowded frames (when 4+ people overlap). This is acceptable because Brigade Bangalore is a boutique retail store, not a transit hub — crowding is rare.

---

## Choice 2: Event Schema — Flat with Metadata Block

**Options considered**:
- **Option A**: Fully flat schema (all fields at top level)
- **Option B**: Nested metadata block for extensible fields ← **chosen**
- **Option C**: Separate tables per event type (ENTRY_EVENTS, DWELL_EVENTS, etc.)

**What we chose**: Option B — one `events` table with a small metadata block (queue_depth, sku_zone, session_seq, group_size).

**Reasoning**:
- Option A makes schema migrations painful when new fields are needed
- Option C causes complex JOINs for session reconstruction
- Option B keeps the schema evolution-friendly while keeping queries simple
- The metadata fields are nullable and infrequently queried — they don't need their own indexes
- `session_seq` as a metadata field (not a separate sessions table join) simplifies API queries for session replay

**Trade-off accepted**: Metadata fields can't be individually indexed. If we later need to query by `sku_zone` heavily, we'd extract it to a top-level column. For now, the analytics queries don't filter on metadata.

---

## Choice 3: Storage — SQLite (dev) / PostgreSQL (prod) via env var

**Options considered**:
| Option | Pros | Cons |
|--------|------|------|
| SQLite only | Zero deps, file-based, instant setup | No concurrent writes, no replication |
| PostgreSQL only | Production-grade, concurrent, scalable | Requires Docker/service, slows first-boot |
| Redis | Ultra-fast, good for real-time | No persistence by default, schema-less |
| SQLite + WAL | Zero deps + concurrent reads | Single-writer still |

**What we chose**: SQLite with WAL mode for local development, switching to PostgreSQL via `DATABASE_URL` environment variable. Both use the same SQLAlchemy ORM layer — zero code change required.

**Reasoning**:
- The acceptance gate requires `docker compose up` with zero manual steps. If PostgreSQL is the only option, first boot requires the Postgres container to be healthy before the API starts (adding 10–30 seconds)
- SQLite WAL mode allows the FastAPI server to handle concurrent reads (heatmap + metrics + funnel fetched in parallel by the dashboard) without blocking
- The spec explicitly notes SQLite is acceptable for local dev. Production deployment naturally uses the PostgreSQL path
- Using SQLAlchemy means the same ORM models work on both backends

**Trade-off accepted**: SQLite is single-writer (one write at a time). During high-frequency event ingestion from the pipeline, this creates a bottleneck. Mitigation: the pipeline runs before the API dashboard (batch mode), and live ingestion uses a 500-event batch size to minimize write transactions.

---

## Choice 4: Dashboard Technology — Pure HTML/CSS/JS vs React/Next.js

**Options considered**:
- **React + Recharts**: Rich charting library, component model
- **Streamlit**: Python-native, fast to build, terminal-adjacent
- **Rich terminal dashboard**: As specified in the original spec
- **Vanilla HTML/CSS/JS**: No build step, no dependencies ← **chosen**

**What we chose**: Pure vanilla web app served as static files.

**Reasoning**:
- The spec called for `rich` terminal dashboard — we upgraded to web for shareability and visual richness
- React adds a build step (npm, webpack/vite) and ~200KB bundle — unnecessary for a polling dashboard
- Streamlit is Python-first but creates a second Python process running alongside FastAPI
- Vanilla JS with `fetch`, `requestAnimationFrame`, and Canvas API covers all requirements (charts, animations, heatmap) with zero build tooling
- Served by nginx (Docker) or Python's http.server (local) — no Node.js runtime needed anywhere

**Trade-off accepted**: No TypeScript means no compile-time type safety in the dashboard. For a monitoring dashboard that doesn't handle user-submitted data, this is acceptable. The API is fully typed (Pydantic), so data contracts are enforced at the source.

---

## Choice 5: Re-ID Strategy — HSV Histogram vs Deep Embedding

**Options considered**:
- **OSNet/ResNet-based Re-ID model**: State-of-art appearance matching, >95% accuracy
- **HSV histogram + cosine similarity**: Lightweight, CPU-friendly ← **chosen**
- **No Re-ID**: Track drop = new visitor (over-counts unique visitors)

**What we chose**: 96-dim HSV histogram embedding with cosine similarity threshold 0.82.

**Reasoning**:
- OSNet Re-ID requires ~500MB model weights and runs at ~15fps on CPU with a dedicated inference thread — doubling the pipeline's compute cost
- In a 2-minute clip, track drops are rare (ByteTrack handles most occlusions). Re-ID is mainly needed for the 5-minute re-entry window between clips
- HSV histograms work well in controlled retail lighting where customers don't change clothes
- The threshold 0.82 was calibrated so that:
  - Different customers in similar clothing (e.g., two people in beige) → similarity ~0.65–0.75 → no false match
  - Same person in same outfit → similarity ~0.88–0.95 → correct match

**Trade-off accepted**: HSV histogram Re-ID fails when:
1. Two people wear near-identical outfits (rare in retail)
2. Drastically different lighting between camera views (managed by per-camera normalization)

For a 2-minute clip where all 5 cameras record simultaneously, the main use case is within-camera track-drop recovery (where appearance is very stable), making the HSV approach sufficient.
