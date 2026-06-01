# app/analytics.py
"""
Pure SQL query helpers for all store intelligence metrics.
Extracted from main.py for testability without HTTP overhead.
All functions accept a SQLAlchemy session and return plain Python dicts/lists.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text


def get_unique_visitors(db: Session, store_id: str, date: str) -> int:
    # Use any zone presence (ZONE_ENTER/ZONE_DWELL) as visitor signal.
    # ENTRY crossing-line events require calibrated camera geometry;
    # using zone presence is more robust for batch-processed clips.
    row = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM events
        WHERE store_id = :sid
          AND is_staff = 0
          AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'BILLING_QUEUE_JOIN')
          AND date(timestamp) = :d
    """), {"sid": store_id, "d": date}).fetchone()
    return row[0] if row else 0


def get_converted_visitors(db: Session, store_id: str, date: str) -> int:
    """
    Visitors who were seen at the billing zone on the same date as a POS transaction.
    Time-window join: billing zone event within 8 hours of any transaction that day.
    """
    row = db.execute(text("""
        SELECT COUNT(DISTINCT e.visitor_id)
        FROM events e
        WHERE e.store_id = :sid
          AND e.is_staff = 0
          AND e.zone_id = 'ZONE_CASH_COUNTER'
          AND date(e.timestamp) = :d
          AND EXISTS (
              SELECT 1 FROM pos_transactions p
              WHERE p.store_id = e.store_id AND p.order_date = :d
          )
    """), {"sid": store_id, "d": date}).fetchone()
    return row[0] if row else 0


def get_avg_dwell_per_zone(db: Session, store_id: str, date: str) -> dict:
    rows = db.execute(text("""
        SELECT zone_id,
               AVG(dwell_ms) AS avg_dwell,
               COUNT(*)      AS visits
        FROM events
        WHERE store_id = :sid
          AND is_staff = 0
          AND event_type = 'ZONE_DWELL'
          AND date(timestamp) = :d
          AND zone_id IS NOT NULL
        GROUP BY zone_id
        ORDER BY avg_dwell DESC
    """), {"sid": store_id, "d": date}).fetchall()

    return {
        r[0]: {"avg_dwell_ms": round(r[1]), "visit_count": r[2]}
        for r in rows
    }


def get_queue_depth_now(db: Session, store_id: str) -> int:
    row = db.execute(text("""
        SELECT queue_depth FROM events
        WHERE store_id = :sid
          AND event_type = 'BILLING_QUEUE_JOIN'
          AND timestamp > datetime('now', '-10 minutes')
        ORDER BY timestamp DESC
        LIMIT 1
    """), {"sid": store_id}).fetchone()
    return row[0] if row and row[0] is not None else 0


def get_abandonment_rate(db: Session, store_id: str, date: str) -> float:
    total_row = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = :sid
          AND event_type = 'BILLING_QUEUE_JOIN'
          AND date(timestamp) = :d
    """), {"sid": store_id, "d": date}).fetchone()

    abandon_row = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = :sid
          AND event_type = 'BILLING_QUEUE_ABANDON'
          AND date(timestamp) = :d
    """), {"sid": store_id, "d": date}).fetchone()

    total = total_row[0] if total_row else 0
    abandoned = abandon_row[0] if abandon_row else 0
    return round(abandoned / max(total, 1), 4)


def get_funnel(db: Session, store_id: str, date: str) -> list:
    # Stage 1: any zone presence = entered the store
    entered = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = :sid AND is_staff = 0
          AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'BILLING_QUEUE_JOIN')
          AND date(timestamp) = :d
    """), {"sid": store_id, "d": date}).fetchone()[0]

    # Stage 2: visited at least one product zone
    browsed = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = :sid AND is_staff = 0 AND event_type = 'ZONE_ENTER'
          AND zone_id NOT IN ('ZONE_ENTRY_EXIT', 'ZONE_CASH_COUNTER', 'ZONE_FOH')
          AND date(timestamp) = :d
    """), {"sid": store_id, "d": date}).fetchone()[0]

    # Stage 3: reached billing counter
    reached_billing = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = :sid AND is_staff = 0
          AND zone_id = 'ZONE_CASH_COUNTER'
          AND date(timestamp) = :d
    """), {"sid": store_id, "d": date}).fetchone()[0]

    # Stage 4: purchased (billing zone visitor + POS transaction exists same day)
    purchased = db.execute(text("""
        SELECT COUNT(DISTINCT e.visitor_id)
        FROM events e
        WHERE e.store_id = :sid AND e.is_staff = 0
          AND e.zone_id = 'ZONE_CASH_COUNTER'
          AND date(e.timestamp) = :d
          AND EXISTS (
              SELECT 1 FROM pos_transactions p
              WHERE p.store_id = e.store_id AND p.order_date = :d
          )
    """), {"sid": store_id, "d": date}).fetchone()[0]

    def drop_pct(a, b):
        return round(1 - b / max(a, 1), 4) if a > 0 else 0.0

    return [
        {"stage": "Entry",           "visitors": entered,         "drop_off_pct": 0.0},
        {"stage": "Browsed Zone",    "visitors": browsed,         "drop_off_pct": drop_pct(entered, browsed)},
        {"stage": "Reached Billing", "visitors": reached_billing, "drop_off_pct": drop_pct(browsed, reached_billing)},
        {"stage": "Purchased",       "visitors": purchased,       "drop_off_pct": drop_pct(reached_billing, purchased)},
    ], round(purchased / max(entered, 1), 4)


def get_heatmap(db: Session, store_id: str, date: str) -> dict:
    rows = db.execute(text("""
        SELECT zone_id,
               COUNT(DISTINCT visitor_id) AS visit_count,
               COALESCE(AVG(dwell_ms), 0) AS avg_dwell_ms
        FROM events
        WHERE store_id = :sid AND is_staff = 0 AND date(timestamp) = :d
          AND zone_id IS NOT NULL
          AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
        GROUP BY zone_id
    """), {"sid": store_id, "d": date}).fetchall()

    total_sessions = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = :sid AND is_staff = 0 AND date(timestamp) = :d
          AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL', 'BILLING_QUEUE_JOIN')
    """), {"sid": store_id, "d": date}).fetchone()[0]

    if not rows:
        return {"zones": [], "data_confidence": "LOW", "total_sessions": 0}

    max_visits = max(r[1] for r in rows) or 1
    max_dwell  = max(r[2] for r in rows) or 1

    zones = sorted([
        {
            "zone_id":      r[0],
            "visit_count":  r[1],
            "avg_dwell_ms": round(r[2]),
            "visit_score":  round(r[1] / max_visits * 100),
            "dwell_score":  round(r[2] / max_dwell  * 100),
        }
        for r in rows
    ], key=lambda z: -z["visit_score"])

    return {
        "zones":            zones,
        "data_confidence":  "LOW" if total_sessions < 20 else "HIGH",
        "total_sessions":   total_sessions,
    }


def get_anomalies(db: Session, store_id: str) -> list:
    anomalies = []
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # 1. BILLING_QUEUE_SPIKE
    row = db.execute(text("""
        SELECT MAX(queue_depth) FROM events
        WHERE store_id = :sid AND event_type = 'BILLING_QUEUE_JOIN'
          AND timestamp > datetime('now', '-10 minutes')
    """), {"sid": store_id}).fetchone()
    if row and row[0] and row[0] > 4:
        anomalies.append({
            "type":             "BILLING_QUEUE_SPIKE",
            "severity":         "CRITICAL" if row[0] > 7 else "WARN",
            "value":            row[0],
            "threshold":        4,
            "zone_id":          "ZONE_CASH_COUNTER",
            "suggested_action": "Open second cash counter. Queue depth exceeds SLA.",
            "detected_at":      now.isoformat(),
        })

    # 2. CONVERSION_DROP
    today_txns = db.execute(text("""
        SELECT COUNT(DISTINCT invoice_number) FROM pos_transactions
        WHERE store_id = :sid AND order_date = :d
    """), {"sid": store_id, "d": today}).fetchone()[0]

    today_visitors = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id) FROM events
        WHERE store_id = :sid AND is_staff = 0 AND event_type = 'ENTRY'
          AND date(timestamp) = :d
    """), {"sid": store_id, "d": today}).fetchone()[0]

    if today_visitors > 5:
        rate = today_txns / max(today_visitors, 1)
        if rate < 0.20:
            anomalies.append({
                "type":             "CONVERSION_DROP",
                "severity":         "WARN",
                "value":            round(rate, 3),
                "threshold":        0.20,
                "zone_id":          None,
                "suggested_action": "Check salesperson floor coverage. Conversion below 20%.",
                "detected_at":      now.isoformat(),
            })

    # 3. DEAD_ZONE (no activity for 30+ min)
    dead_rows = db.execute(text("""
        SELECT zone_id, MAX(timestamp) AS last_visit
        FROM events
        WHERE store_id = :sid AND event_type = 'ZONE_ENTER'
          AND zone_id NOT IN ('ZONE_ENTRY_EXIT', 'ZONE_CASH_COUNTER')
          AND timestamp > datetime('now', '-120 minutes')
        GROUP BY zone_id
        HAVING (julianday('now') - julianday(last_visit)) * 24 * 60 > 30
    """), {"sid": store_id}).fetchall()

    for row in dead_rows:
        anomalies.append({
            "type":             "DEAD_ZONE",
            "severity":         "INFO",
            "value":            None,
            "threshold":        None,
            "zone_id":          row[0],
            "last_activity":    row[1],
            "suggested_action": f"No customers in {row[0]} for 30+ min. Consider staff engagement.",
            "detected_at":      now.isoformat(),
        })

    # 4. STALE_CAMERA_FEED
    stale_rows = db.execute(text("""
        SELECT camera_id, MAX(timestamp) AS last_event
        FROM events
        WHERE store_id = :sid
        GROUP BY camera_id
        HAVING (julianday('now') - julianday(last_event)) * 24 * 60 > 10
    """), {"sid": store_id}).fetchall()

    for row in stale_rows:
        anomalies.append({
            "type":             "STALE_CAMERA_FEED",
            "severity":         "CRITICAL",
            "value":            None,
            "threshold":        None,
            "zone_id":          None,
            "camera_id":        row[0],
            "last_event":       row[1],
            "suggested_action": f"Camera {row[0]} feed stale. Check network/hardware.",
            "detected_at":      now.isoformat(),
        })

    return anomalies


def get_hourly_traffic(db: Session, store_id: str, date: str) -> list:
    """Hourly visitor count for traffic chart."""
    rows = db.execute(text("""
        SELECT strftime('%H', timestamp) AS hour,
               COUNT(DISTINCT visitor_id) AS visitors
        FROM events
        WHERE store_id = :sid AND is_staff = 0 AND event_type = 'ENTRY'
          AND date(timestamp) = :d
        GROUP BY hour
        ORDER BY hour
    """), {"sid": store_id, "d": date}).fetchall()
    return [{"hour": int(r[0]), "visitors": r[1]} for r in rows]


def get_zone_performance(db: Session, store_id: str, date: str) -> list:
    """Zone visit counts linked to POS sales by SKU zone."""
    rows = db.execute(text("""
        SELECT e.zone_id,
               COUNT(DISTINCT e.visitor_id) AS visitors,
               COALESCE(AVG(e.dwell_ms), 0) AS avg_dwell,
               COUNT(DISTINCT p.invoice_number) AS sales
        FROM events e
        LEFT JOIN pos_transactions p
          ON p.store_id = e.store_id
         AND p.order_date = :d
        WHERE e.store_id = :sid AND e.is_staff = 0
          AND date(e.timestamp) = :d
          AND e.zone_id IS NOT NULL
          AND e.event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
        GROUP BY e.zone_id
        ORDER BY visitors DESC
    """), {"sid": store_id, "d": date}).fetchall()
    return [
        {"zone_id": r[0], "visitors": r[1], "avg_dwell_ms": round(r[2]), "sales": r[3]}
        for r in rows
    ]
