# app/main.py
"""
FastAPI application — Purplle Store Intelligence API.
All endpoints serve real-time analytics derived from the events database
and POS transaction data.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db, init_db
from app.models import (
    IngestPayload, EventORM, POSIngestPayload, POSTransactionORM
)
from app.analytics import (
    get_unique_visitors, get_converted_visitors, get_avg_dwell_per_zone,
    get_queue_depth_now, get_abandonment_rate, get_funnel, get_heatmap,
    get_anomalies, get_hourly_traffic, get_zone_performance,
)
from app.logging_config import logging_middleware, log

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Purplle Store Intelligence API",
    description="Real-time retail analytics for Brigade Bangalore (ST1008)",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.middleware("http")(logging_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    init_db()
    log.info("startup", message="Database initialised", status="ok")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health(db: Session = Depends(get_db)):
    """System health: per-store, per-camera feed lag."""
    stores = db.execute(text(
        "SELECT store_id, camera_id, MAX(timestamp) FROM events GROUP BY store_id, camera_id"
    )).fetchall()

    feeds: dict = {}
    now = datetime.now(timezone.utc)
    for store_id, camera_id, last_ts in stores:
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
                lag_min = (now - last_dt).total_seconds() / 60
            except Exception:
                lag_min = 9999
            feeds.setdefault(store_id, {})[camera_id] = {
                "last_event":   last_ts,
                "lag_minutes":  round(lag_min, 1),
                "status":       "STALE_FEED" if lag_min > 10 else "OK",
            }

    return {
        "status":       "healthy",
        "timestamp":    now.isoformat(),
        "event_feeds":  feeds,
    }


# ── Events Ingest ──────────────────────────────────────────────────────────────

@app.post("/events/ingest", tags=["ingest"])
async def ingest_events(payload: IngestPayload, db: Session = Depends(get_db)):
    """
    Batch ingest up to 500 events. Idempotent by event_id (INSERT OR IGNORE).
    Returns partial success on malformed rows.
    """
    accepted, rejected = [], []

    for ev in payload.events:
        try:
            ts = datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00"))
            meta = ev.metadata or {}
            if hasattr(meta, "model_dump"):
                meta = meta.model_dump()

            db.execute(text("""
                INSERT OR IGNORE INTO events
                  (event_id, store_id, camera_id, visitor_id, event_type,
                   timestamp, zone_id, dwell_ms, is_staff, confidence,
                   queue_depth, sku_zone, session_seq)
                VALUES
                  (:eid, :sid, :cid, :vid, :etype,
                   :ts, :zid, :dms, :staff, :conf,
                   :qdepth, :skuz, :seq)
            """), {
                "eid":    ev.event_id,
                "sid":    ev.store_id,
                "cid":    ev.camera_id,
                "vid":    ev.visitor_id,
                "etype":  ev.event_type,
                "ts":     ts,
                "zid":    ev.zone_id,
                "dms":    ev.dwell_ms or 0,
                "staff":  1 if ev.is_staff else 0,
                "conf":   ev.confidence,
                "qdepth": meta.get("queue_depth"),
                "skuz":   meta.get("sku_zone"),
                "seq":    meta.get("session_seq", 0),
            })
            accepted.append(ev.event_id)
        except Exception as e:
            rejected.append({"event_id": ev.event_id, "error": str(e)})

    db.commit()
    return {
        "accepted":        len(accepted),
        "rejected":        len(rejected),
        "rejected_events": rejected,
    }


# ── POS Ingest ─────────────────────────────────────────────────────────────────

@app.post("/pos/ingest", tags=["ingest"])
async def ingest_pos(payload: POSIngestPayload, db: Session = Depends(get_db)):
    """Load POS transaction rows. Idempotent by invoice_number."""
    loaded = 0
    for tx in payload.transactions:
        db.execute(text("""
            INSERT OR IGNORE INTO pos_transactions
              (invoice_number, store_id, order_date, order_time,
               customer_number, salesperson_id, total_amount, gmv,
               brand_name, dep_name, product_name, qty)
            VALUES
              (:inv, :sid, :odate, :otime, :cnum, :spid,
               :total, :gmv, :brand, :dep, :prod, :qty)
        """), {
            "inv":   tx.invoice_number,
            "sid":   tx.store_id,
            "odate": tx.order_date,
            "otime": tx.order_time,
            "cnum":  tx.customer_number,
            "spid":  tx.salesperson_id,
            "total": tx.total_amount,
            "gmv":   tx.gmv,
            "brand": tx.brand_name,
            "dep":   tx.dep_name,
            "prod":  tx.product_name,
            "qty":   tx.qty,
        })
        loaded += 1

    db.commit()
    return {"loaded": loaded, "message": f"POS data ingested: {loaded} rows"}


# ── Metrics ────────────────────────────────────────────────────────────────────

@app.get("/stores/{store_id}/metrics", tags=["analytics"])
async def get_metrics(
    store_id: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
    db: Session = Depends(get_db),
):
    """
    Core KPIs: unique visitors, conversion rate, avg dwell per zone,
    queue depth, abandonment rate.
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    unique_visitors  = get_unique_visitors(db, store_id, target_date)
    converted        = get_converted_visitors(db, store_id, target_date)
    conversion_rate  = round(converted / max(unique_visitors, 1), 4)
    avg_dwell        = get_avg_dwell_per_zone(db, store_id, target_date)
    queue_depth      = get_queue_depth_now(db, store_id)
    abandonment_rate = get_abandonment_rate(db, store_id, target_date)
    hourly           = get_hourly_traffic(db, store_id, target_date)

    return {
        "store_id":           store_id,
        "date":               target_date,
        "unique_visitors":    unique_visitors,
        "converted_visitors": converted,
        "conversion_rate":    conversion_rate,
        "avg_dwell_per_zone": avg_dwell,
        "queue_depth_now":    queue_depth,
        "abandonment_rate":   abandonment_rate,
        "hourly_traffic":     hourly,
        "data_freshness":     "real_time",
    }


# ── Funnel ─────────────────────────────────────────────────────────────────────

@app.get("/stores/{store_id}/funnel", tags=["analytics"])
async def get_funnel_endpoint(
    store_id: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """4-stage conversion funnel: Entry → Browse → Billing → Purchase."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    funnel_stages, overall = get_funnel(db, store_id, target_date)
    return {
        "store_id":            store_id,
        "date":                target_date,
        "funnel":              funnel_stages,
        "overall_conversion":  overall,
    }


# ── Heatmap ────────────────────────────────────────────────────────────────────

@app.get("/stores/{store_id}/heatmap", tags=["analytics"])
async def get_heatmap_endpoint(
    store_id: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Zone visit frequency + avg dwell, normalised 0–100."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    data = get_heatmap(db, store_id, target_date)
    return {"store_id": store_id, "date": target_date, **data}


# ── Anomalies ──────────────────────────────────────────────────────────────────

@app.get("/stores/{store_id}/anomalies", tags=["analytics"])
async def get_anomalies_endpoint(
    store_id: str,
    db: Session = Depends(get_db),
):
    """Active anomalies: queue spike, conversion drop, dead zone, stale feed."""
    anomalies = get_anomalies(db, store_id)
    return {
        "store_id":      store_id,
        "anomaly_count": len(anomalies),
        "anomalies":     anomalies,
        "checked_at":    datetime.now(timezone.utc).isoformat(),
    }


# ── Zone Performance ────────────────────────────────────────────────────────────

@app.get("/stores/{store_id}/zones", tags=["analytics"])
async def get_zones_endpoint(
    store_id: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Zone-level visitor counts, avg dwell, and linked sales."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    zones = get_zone_performance(db, store_id, target_date)
    return {"store_id": store_id, "date": target_date, "zones": zones}


# ── Live SSE stream ────────────────────────────────────────────────────────────

@app.get("/stores/{store_id}/live", tags=["realtime"])
async def live_stream(store_id: str, db: Session = Depends(get_db)):
    """
    Server-Sent Events stream. Pushes a metrics snapshot every 3 seconds.
    Dashboard connects to this for zero-polling live updates.
    """
    import asyncio

    async def event_generator():
        while True:
            try:
                target_date = datetime.now().strftime("%Y-%m-%d")
                unique_visitors = get_unique_visitors(db, store_id, target_date)
                queue_depth     = get_queue_depth_now(db, store_id)
                anomalies       = get_anomalies(db, store_id)

                data = json.dumps({
                    "unique_visitors":  unique_visitors,
                    "queue_depth_now":  queue_depth,
                    "anomaly_count":    len(anomalies),
                    "critical_alerts":  [a for a in anomalies if a["severity"] == "CRITICAL"],
                    "timestamp":        datetime.now(timezone.utc).isoformat(),
                })
                yield f"data: {data}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(3)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
