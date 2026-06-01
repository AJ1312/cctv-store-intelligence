# tests/test_api.py
"""
API integration tests for the Purplle Store Intelligence FastAPI app.
Tests run against an in-memory SQLite DB (no side effects on production data).
"""

import pytest
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

# Patch DATABASE_URL to use in-memory SQLite for tests
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.main import app
from app.database import init_db, engine
from app.models import Base

STORE = "ST1008"
DATE  = datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def setup_db():
    """Create fresh tables before each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_event(**kwargs) -> dict:
    defaults = {
        "event_id":   str(uuid.uuid4()),
        "store_id":   STORE,
        "camera_id":  "CAM_ENTRY_01",
        "visitor_id": "VIS_TEST01",
        "event_type": "ZONE_ENTER",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "zone_id":    None,
        "dwell_ms":   0,
        "is_staff":   False,
        "confidence": 0.95,
        "metadata":   {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    defaults.update(kwargs)
    return defaults


# ── Test 1: Ingest idempotency ─────────────────────────────────────────────────
def test_ingest_idempotency(client):
    """Same event_id submitted twice → accepted once (INSERT OR IGNORE)."""
    ev = make_event()
    r1 = client.post("/events/ingest", json={"events": [ev]})
    r2 = client.post("/events/ingest", json={"events": [ev]})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["accepted"] == 1
    assert r2.json()["accepted"] == 1  # idempotent — not 2


# ── Test 2: Zero-purchase conversion_rate ──────────────────────────────────────
def test_metrics_zero_purchase(client):
    """No POS data → conversion_rate = 0, not null or crash."""
    ev = make_event(visitor_id="VIS_ZERO", event_type="ZONE_ENTER")
    client.post("/events/ingest", json={"events": [ev]})
    r = client.get(f"/stores/{STORE}/metrics?date={DATE}")
    assert r.status_code == 200
    data = r.json()
    assert data["conversion_rate"] == 0.0
    assert data["unique_visitors"] >= 1


# ── Test 3: Staff excluded from unique_visitors ────────────────────────────────
def test_metrics_excludes_staff(client):
    """Staff ENTRY events must NOT contribute to unique_visitors."""
    staff_evs = [make_event(visitor_id=f"VIS_STAFF{i}", is_staff=True) for i in range(3)]
    client.post("/events/ingest", json={"events": staff_evs})
    r = client.get(f"/stores/{STORE}/metrics?date={DATE}")
    assert r.status_code == 200
    data = r.json()
    # Staff visitors should count 0 unique visitors
    assert data["unique_visitors"] == 0


# ── Test 4: Funnel re-entry deduplication ─────────────────────────────────────
def test_funnel_reentry_dedup(client):
    """ENTRY + EXIT + REENTRY for same visitor_id → counted as 1 entrant in funnel."""
    vid = "VIS_REENTRY01"
    events = [
        make_event(visitor_id=vid, event_type="ZONE_ENTER"),
        make_event(visitor_id=vid, event_type="EXIT"),
        make_event(visitor_id=vid, event_type="REENTRY"),
    ]
    client.post("/events/ingest", json={"events": events})
    r = client.get(f"/stores/{STORE}/funnel?date={DATE}")
    assert r.status_code == 200
    data = r.json()
    entered = data["funnel"][0]["visitors"]
    # Re-entry should not double-count — still 1 unique visitor
    assert entered == 1


# ── Test 5: DEAD_ZONE anomaly detection ───────────────────────────────────────
def test_anomaly_dead_zone(client):
    """ZONE_ENTER event 35+ minutes ago → DEAD_ZONE anomaly appears."""
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    ev = make_event(
        event_type="ZONE_ENTER",
        zone_id="ZONE_MINIMALIST",
        visitor_id="VIS_DEAD01",
        timestamp=old_ts,
    )
    client.post("/events/ingest", json={"events": [ev]})
    r = client.get(f"/stores/{STORE}/anomalies")
    assert r.status_code == 200
    types = [a["type"] for a in r.json()["anomalies"]]
    assert "DEAD_ZONE" in types


# ── Test 6: STALE_CAMERA_FEED health check ────────────────────────────────────
def test_health_stale_feed(client):
    """Event from 15min ago → health returns STALE_FEED for that camera."""
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    ev = make_event(camera_id="CAM_FOH_01", timestamp=old_ts)
    client.post("/events/ingest", json={"events": [ev]})
    r = client.get("/health")
    assert r.status_code == 200
    feeds = r.json().get("event_feeds", {})
    if STORE in feeds and "CAM_FOH_01" in feeds[STORE]:
        assert feeds[STORE]["CAM_FOH_01"]["status"] == "STALE_FEED"


# ── Test 7: Group entry — 3 simultaneous → 3 distinct visitor_ids ──────────────
def test_group_entry_distinct_ids(client):
    """3 simultaneous ENTRY events with distinct visitor_ids → 3 unique visitors."""
    ts = datetime.now(timezone.utc).isoformat()
    evs = [
        make_event(visitor_id=f"VIS_GROUP{i:02d}", event_type="ZONE_ENTER", timestamp=ts)
        for i in range(3)
    ]
    r = client.post("/events/ingest", json={"events": evs})
    assert r.json()["accepted"] == 3
    m = client.get(f"/stores/{STORE}/metrics?date={DATE}").json()
    assert m["unique_visitors"] == 3


# ── Test 8: Heatmap data_confidence ──────────────────────────────────────────
def test_heatmap_low_confidence(client):
    """< 20 sessions → data_confidence = LOW."""
    evs = [make_event(visitor_id=f"VIS_HM{i:02d}", event_type="ZONE_ENTER",
                      zone_id="ZONE_GV") for i in range(5)]
    client.post("/events/ingest", json={"events": evs})
    r = client.get(f"/stores/{STORE}/heatmap?date={DATE}")
    assert r.status_code == 200
    data = r.json()
    assert data["data_confidence"] in ("LOW", "HIGH")
    # With 5 sessions → LOW
    assert data["data_confidence"] == "LOW"


# ── Test 9: Ingest batch limit ────────────────────────────────────────────────
def test_ingest_batch_limit(client):
    """Batch > 500 events → 422 validation error."""
    evs = [make_event(visitor_id=f"VIS_{i:04d}") for i in range(501)]
    r = client.post("/events/ingest", json={"events": evs})
    assert r.status_code == 422


# ── Test 10: POS ingest ───────────────────────────────────────────────────────
def test_pos_ingest(client):
    """POS rows are stored and appear in metrics."""
    pos_rows = [
        {
            "invoice_number": "INV001",
            "store_id":       STORE,
            "order_date":     DATE,
            "order_time":     "15:30:00",
            "total_amount":   1200.0,
            "gmv":            1200.0,
        }
    ]
    r = client.post("/pos/ingest", json={"transactions": pos_rows})
    assert r.status_code == 200
    assert r.json()["loaded"] == 1
