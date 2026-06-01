# app/database.py
"""
Database engine and session factory.
SQLite for local dev (zero dependencies), PostgreSQL for production.
Switch via DATABASE_URL environment variable.
WAL mode enabled on SQLite for concurrent read/write access.
"""

import os
from pathlib import Path
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# ── Engine setup ──────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{Path(__file__).parent.parent / 'store_intel.db'}"
)

# SQLite: use WAL mode + check_same_thread=False for concurrent FastAPI access
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    echo=False,
)

# Enable WAL mode for SQLite (allows concurrent reads while writing)
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_wal_mode(dbapi_conn, conn_record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")
        dbapi_conn.execute("PRAGMA cache_size=-64000")  # 64MB cache

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yield a DB session, close on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Create all tables, indexes, and views.
    Called on application startup. Idempotent.
    """
    from app.models import EventORM, POSTransactionORM  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # Create sessions view (SQLite-compatible)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE VIEW IF NOT EXISTS sessions AS
            SELECT
                visitor_id,
                store_id,
                date(min(timestamp)) AS session_date,
                min(timestamp) AS session_start,
                max(timestamp) AS session_end,
                count(*) AS event_count,
                max(CASE WHEN event_type='REENTRY' THEN 1 ELSE 0 END) AS had_reentry
            FROM events
            WHERE is_staff = 0
              AND event_type IN (
                'ENTRY','EXIT','ZONE_ENTER','ZONE_EXIT','ZONE_DWELL',
                'BILLING_QUEUE_JOIN','BILLING_QUEUE_ABANDON','REENTRY'
              )
            GROUP BY visitor_id, store_id, date(timestamp)
        """))
        conn.commit()
