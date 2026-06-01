# app/models.py
"""
SQLAlchemy ORM models and Pydantic request/response schemas.
"""

from datetime import datetime
from typing import Optional, List, Any

from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, Text, Index, func
)
from pydantic import BaseModel, field_validator

from app.database import Base


# ── SQLAlchemy ORM Models ──────────────────────────────────────────────────────

class EventORM(Base):
    __tablename__ = "events"

    event_id    = Column(String,  primary_key=True)
    store_id    = Column(String,  nullable=False, index=True)
    camera_id   = Column(String,  nullable=False)
    visitor_id  = Column(String,  nullable=False, index=True)
    event_type  = Column(String,  nullable=False, index=True)
    timestamp   = Column(DateTime, nullable=False)
    zone_id     = Column(String,  nullable=True,  index=True)
    dwell_ms    = Column(Integer, default=0)
    is_staff    = Column(Boolean, default=False)
    confidence  = Column(Float,   nullable=True)
    queue_depth = Column(Integer, nullable=True)
    sku_zone    = Column(String,  nullable=True)
    session_seq = Column(Integer, nullable=True)
    ingested_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_events_store_ts",  "store_id", "timestamp"),
        Index("idx_events_type_date", "event_type", "timestamp"),
    )


class POSTransactionORM(Base):
    __tablename__ = "pos_transactions"

    invoice_number  = Column(String, primary_key=True)
    store_id        = Column(String, nullable=False, index=True)
    order_date      = Column(String, nullable=False)
    order_time      = Column(String, nullable=False)
    customer_number = Column(String, nullable=True)
    salesperson_id  = Column(String, nullable=True)
    total_amount    = Column(Float,  nullable=True)
    gmv             = Column(Float,  nullable=True)
    brand_name      = Column(String, nullable=True)
    dep_name        = Column(String, nullable=True)
    product_name    = Column(String, nullable=True)
    qty             = Column(Integer, nullable=True)
    ingested_at     = Column(DateTime, server_default=func.now())


# ── Pydantic Schemas ───────────────────────────────────────────────────────────

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone:    Optional[str] = None
    session_seq: Optional[int] = 0
    group_size:  Optional[int] = 1

    model_config = {"extra": "allow"}


class EventSchema(BaseModel):
    event_id:   str
    store_id:   str
    camera_id:  str
    visitor_id: str
    event_type: str
    timestamp:  str
    zone_id:    Optional[str]   = None
    dwell_ms:   Optional[int]   = 0
    is_staff:   Optional[bool]  = False
    confidence: Optional[float] = 1.0
    metadata:   Optional[EventMetadata] = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        valid = {
            "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
            "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
        }
        if v not in valid:
            raise ValueError(f"Invalid event_type: {v}. Must be one of {valid}")
        return v


class IngestPayload(BaseModel):
    events: List[EventSchema]

    @field_validator("events")
    @classmethod
    def limit_batch(cls, v):
        if len(v) > 500:
            raise ValueError("Batch size exceeds 500 events")
        return v


class POSRow(BaseModel):
    invoice_number:  str
    store_id:        str = "ST1008"
    order_date:      str
    order_time:      str
    customer_number: Optional[str] = None
    salesperson_id:  Optional[str] = None
    total_amount:    Optional[float] = 0.0
    gmv:             Optional[float] = 0.0
    brand_name:      Optional[str] = None
    dep_name:        Optional[str] = None
    product_name:    Optional[str] = None
    qty:             Optional[int] = 1


class POSIngestPayload(BaseModel):
    transactions: List[POSRow]
