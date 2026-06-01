# CCTV DETECTION SYSTEM
> **Real-time retail analytics system** — 5 CCTV cameras · YOLOv8n detection · FastAPI backend · Premium web dashboard.

This repository comes pre-packaged with:
1. **Pre-trained YOLOv8n model weights** (`yolov8n.pt`)
2. **Pre-populated SQLite database** (`store_intel.db`) containing **354 ingested camera events** and **101 POS transaction items** from **April 10, 2026**.
3. **Comprehensive Documentation & Walkthrough** detailing the system architecture, mathematical models, and performance logs.

Anyone can clone this repository and run the live REST API and interactive web dashboard immediately without needing to re-run the detection pipeline or install external database services.

---

## 🚀 Quick Start (Run in 3 Commands)

To clone, set up, and launch the system:

```bash
# 1. Clone and enter directory
git clone https://github.com/AJ1312/cctv-store-intelligence.git && cd store-intelligence

# 2. Start the application services (Runs API on 8000 and Dashboard on 3000)
chmod +x run.sh && ./run.sh

# 3. View the live analytics dashboard
# Open your browser to: http://localhost:3000
```

---

## 📖 System Design & WALKTHROUGH

We have written thorough logs and design docs explaining our choices:
- 🗺️ **[System Architecture & Design (Mermaid Diagram)](file:///Users/ajiteshsharma/Documents/CCTV%20Proj/store-intelligence/docs/DOCUMENTATION.md)**: Explains the multi-camera Re-ID algorithm, staff detection hue-saturation filters, frame-skipping optimizations, and database schemas.
- 📈 **[Execution Walkthrough](file:///Users/ajiteshsharma/Documents/CCTV%20Proj/store-intelligence/docs/WALKTHROUGH.md)**: Displays actual frame rates, API latencies, unit test logs, conversion funnel stats, and anomalies from the real April 10 run.
- 💡 **[Architecture Trade-Offs](file:///Users/ajiteshsharma/Documents/CCTV%20Proj/store-intelligence/docs/CHOICES.md)**: Covers why we chose YOLOv8n, SQLite (WAL mode), and a vanilla HTML/CSS/JS frontend framework.

---

## 📂 Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py         ← YOLO + ByteTrack main loop (frame-skip optimised)
│   ├── tracker.py        ← Re-ID · staff detection · zone dwell · group entry
│   ├── emit.py           ← Canonical event schema builder
│   ├── zones.py          ← Zone polygons + crossing detector
│   ├── loader.py         ← Batch ingest JSONL → API
│   └── run.py            ← Parallel multiprocessing runner (all 5 clips)
├── app/
│   ├── main.py           ← FastAPI endpoints
│   ├── models.py         ← SQLAlchemy ORM + Pydantic schemas
│   ├── database.py       ← SQLite/Postgres engine + WAL mode
│   ├── analytics.py      ← Pure SQL query helpers
│   └── logging_config.py ← structlog JSON middleware
├── dashboard/
│   ├── index.html        ← Single-page app with SVG store floor plan
│   ├── style.css         ← Dark glassmorphism design system
│   └── app.js            ← Vanilla JS polling, heatmap, charts
├── tests/
│   ├── test_api.py       ← 10 API integration tests
│   └── test_pipeline.py  ← Pipeline unit tests
├── docs/
│   ├── DOCUMENTATION.md  ← Architecture diagram, Re-ID & uniform detection
│   ├── WALKTHROUGH.md    ← Execution log & metrics summary
│   ├── DESIGN.md         ← Initial pipeline and zone assignment decisions
│   └── CHOICES.md        ← Technical trade-offs (models, databases, UI framework)
├── events/
│   └── events.jsonl      ← Raw JSONL events from April 10 run (354 events)
├── store_layout.json     ← All 23 zones + 5 cameras configurations
├── store_intel.db        ← Pre-populated SQLite DB (354 events + 101 POS transactions)
├── yolov8n.pt            ← YOLOv8 nano model weights (~6.2MB)
├── docker-compose.yml    ← High-scale deployment setup
├── requirements.txt      ← Python dependency checklist
├── run.sh                ← One-click setup & launch script
└── README.md             ← This file
```

---

## 🛠️ Ingestion & Testing Commands

### Run Unit & Integration Tests

To run the full test suite (25 tests covering zone-assignment, Re-ID similarity, staff classification, and API metrics):

```bash
source venv/bin/activate
pytest tests/ -v --tb=short
```

### Re-run Detection Pipeline (Optional)

If you have the raw CCTV clips and want to re-run the object detection and tracking pipeline:

```bash
source venv/bin/activate
python pipeline/run.py "path/to/CCTV Footage/" --pos-csv "path/to/POS_Transactions.csv"
```

---

## 📊 Live Verification

Once `./run.sh` is active, verify endpoints manually via:

```bash
# 1. Check Store Metrics
curl http://localhost:8000/stores/ST1008/metrics?date=2026-04-10

# 2. Check Store Funnel
curl http://localhost:8000/stores/ST1008/funnel?date=2026-04-10

# 3. Check Live Camera Lag / Health
curl http://localhost:8000/health
```

---

## 📷 Camera ↔ File Mapping

| Camera ID | File Name | Coverage Zone |
|-----------|-----------|---------------|
| CAM_ENTRY_01 | CAM 1.mp4 | Entry/Exit glass door |
| CAM_FOH_01 | CAM 2.mp4 | Central floor + Makeup Unit |
| CAM_NORTH_WALL_01 | CAM 3.mp4 | North wall skincare brands |
| CAM_SOUTH_WALL_01 | CAM 4.mp4 | South wall makeup brands |
| CAM_BILLING_01 | CAM 5.mp4 | Cash counter + billing queue |

---

## 📈 Ground Truth Store Metrics (10 April 2026)

- **Total Invoices**: 24 transactions
- **Total GMV**: ₹44,920
- **Average Basket Value**: ₹1,430
- **Peak Hours**: 19:00–20:00 (5 invoices)
- **Top Category**: Makeup (54% of transaction lines)
- **Active Floor Employees**: 5 salespersons
