# pipeline/emit.py
from __future__ import annotations
"""
Canonical event schema builder for the Store Intelligence pipeline.
All events produced by the detection pipeline pass through build_event().
"""

import uuid
from datetime import datetime


# Valid event types
EVENT_TYPES = frozenset({
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
})

STORE_ID = "ST1008"


def build_event(
    *,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 1.0,
    queue_depth: int | None = None,
    sku_zone: str | None = None,
    session_seq: int = 0,
    group_size: int = 1,
) -> dict:
    """
    Build a canonical store intelligence event dict.

    Args:
        store_id:    Store identifier (ST1008)
        camera_id:   Camera that observed this event
        visitor_id:  Persistent visitor identifier (VIS_xxxxxx or re-used on re-entry)
        event_type:  One of EVENT_TYPES
        timestamp:   UTC datetime of the event
        zone_id:     Zone where event occurred (None for store-level events)
        dwell_ms:    Milliseconds spent in zone (for ZONE_DWELL events)
        is_staff:    True if person classified as Purplle staff
        confidence:  Detection confidence [0, 1]
        queue_depth: Number of people in billing queue at event time
        sku_zone:    SKU category zone (from store_layout.json)
        session_seq: Sequential event counter within this visitor's session
        group_size:  Number of people entering together (for group entry)

    Returns:
        dict conforming to the events table schema
    """
    assert event_type in EVENT_TYPES, f"Unknown event_type: {event_type}"

    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id":    zone_id,
        "dwell_ms":   int(dwell_ms),
        "is_staff":   bool(is_staff),
        "confidence": round(float(confidence), 4),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone":    sku_zone,
            "session_seq": session_seq,
            "group_size":  group_size,
        },
    }
