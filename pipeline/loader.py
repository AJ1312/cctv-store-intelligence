# pipeline/loader.py
"""
Batch-loads events.jsonl → FastAPI /events/ingest endpoint.
Also handles POS CSV → /pos/ingest.
Sends up to 500 events per HTTP request with retry logic.
"""

import json
import csv
import sys
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"
BATCH_SIZE = 500
MAX_RETRIES = 3


def load_events(jsonl_path: str) -> dict:
    """POST all events from JSONL file to /events/ingest in batches."""
    path = Path(jsonl_path)
    if not path.exists():
        print(f"ERROR: {jsonl_path} not found")
        return {"accepted": 0, "rejected": 0}

    total_accepted = 0
    total_rejected = 0
    batch: list[dict] = []

    def flush_batch(b: list[dict]) -> None:
        nonlocal total_accepted, total_rejected
        for attempt in range(MAX_RETRIES):
            try:
                resp = httpx.post(
                    f"{API_BASE}/events/ingest",
                    json={"events": b},
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                total_accepted += data.get("accepted", 0)
                total_rejected += data.get("rejected", 0)
                return
            except Exception as e:
                print(f"  Retry {attempt+1}/{MAX_RETRIES}: {e}", flush=True)
                time.sleep(2 ** attempt)
        print(f"  FAILED batch of {len(b)} events after {MAX_RETRIES} retries")
        total_rejected += len(b)

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                batch.append(ev)
                if len(batch) >= BATCH_SIZE:
                    print(f"Ingesting batch of {len(batch)}...", flush=True)
                    flush_batch(batch)
                    batch = []
            except json.JSONDecodeError:
                continue

    if batch:
        print(f"Ingesting final batch of {len(batch)}...", flush=True)
        flush_batch(batch)

    print(f"Events ingested — accepted: {total_accepted}, rejected: {total_rejected}")
    return {"accepted": total_accepted, "rejected": total_rejected}


def load_pos_csv(csv_path: str) -> dict:
    """POST POS transactions from CSV to /pos/ingest."""
    path = Path(csv_path)
    if not path.exists():
        print(f"ERROR: {csv_path} not found")
        return {"loaded": 0}

    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "invoice_number":  row.get("invoice_number", ""),
                "store_id":        row.get("store_id", "ST1008"),
                "order_date":      row.get("order_date", ""),
                "order_time":      row.get("order_time", ""),
                "customer_number": row.get("customer_number", ""),
                "salesperson_id":  str(row.get("salesperson_id", "")),
                "total_amount":    float(row.get("total_amount", 0) or 0),
                "gmv":             float(row.get("GMV", 0) or 0),
                "brand_name":      row.get("brand_name", ""),
                "dep_name":        row.get("dep_name", ""),
                "product_name":    row.get("product_name", ""),
                "qty":             int(row.get("qty", 1) or 1),
            })

    if not rows:
        print("No POS rows found")
        return {"loaded": 0}

    try:
        resp = httpx.post(
            f"{API_BASE}/pos/ingest",
            json={"transactions": rows},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"POS loaded — {result.get('loaded', 0)} transactions")
        return result
    except Exception as e:
        print(f"POS ingest error: {e}")
        return {"loaded": 0}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python loader.py <events.jsonl> [pos.csv]")
        sys.exit(1)

    load_events(sys.argv[1])
    if len(sys.argv) >= 3:
        load_pos_csv(sys.argv[2])
