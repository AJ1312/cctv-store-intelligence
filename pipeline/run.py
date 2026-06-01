# pipeline/run.py
from __future__ import annotations
"""
Parallel pipeline runner — processes all 5 CCTV clips simultaneously.
Uses multiprocessing.Pool so all clips run in parallel on available CPU cores.
After detection finishes, auto-ingests events and POS data into the API.
"""

import sys
import os
import time
from pathlib import Path
from multiprocessing import Pool, cpu_count

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.detect import process_clip, CLIP_METADATA

STORE_ID = "ST1008"


def _worker(args: tuple) -> tuple[str, int]:
    """Multiprocessing worker function for one camera clip."""
    camera_id, video_path, output_path = args
    try:
        n = process_clip(video_path, camera_id, output_path)
        return camera_id, n
    except Exception as e:
        print(f"[{camera_id}] FATAL ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return camera_id, 0


def run_pipeline(clips_dir: str) -> str:
    """
    Run all 5 camera clips in parallel.
    Returns path to events.jsonl.
    """
    clips_path = Path(clips_dir)
    output_dir = ROOT / "events"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / "events.jsonl")

    # Clear previous output
    if Path(output_path).exists():
        print(f"Clearing previous {output_path}")
        Path(output_path).unlink()

    # Build task list
    tasks = []
    for camera_id, meta in CLIP_METADATA.items():
        video_file = clips_path / meta["file"]
        if not video_file.exists():
            print(f"WARNING: {video_file} not found — skipping {camera_id}")
            continue
        tasks.append((camera_id, str(video_file), output_path))

    if not tasks:
        print("ERROR: No video files found. Check clips directory.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Purplle Store Intelligence Pipeline")
    print(f"  Store: {STORE_ID} | Brigade Bangalore")
    print(f"  Clips directory: {clips_path}")
    print(f"  Processing {len(tasks)} cameras in parallel")
    print(f"  CPUs available: {cpu_count()}")
    print(f"{'='*60}\n")

    t0 = time.time()

    # Run all clips in parallel (limited to len(tasks) workers)
    workers = min(len(tasks), cpu_count())
    with Pool(processes=workers) as pool:
        results = pool.map(_worker, tasks)

    elapsed = time.time() - t0
    total_events = sum(n for _, n in results)

    print(f"\n{'='*60}")
    print(f"  Pipeline Complete in {elapsed:.1f}s")
    print(f"  Total events: {total_events}")
    for camera_id, n in sorted(results):
        print(f"  {camera_id}: {n} events")
    print(f"  Output: {output_path}")
    print(f"{'='*60}\n")

    return output_path


def run_ingest(output_path: str, pos_csv: str | None = None) -> None:
    """Ingest events.jsonl and optionally POS CSV into the API."""
    from pipeline.loader import load_events, load_pos_csv

    print("\nIngesting events into API...")
    result = load_events(output_path)

    if pos_csv and Path(pos_csv).exists():
        print("\nIngesting POS data...")
        load_pos_csv(pos_csv)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Purplle Store Intelligence Pipeline")
    parser.add_argument("clips_dir", help="Directory containing CAM 1.mp4 – CAM 5.mp4")
    parser.add_argument("--pos-csv", default=None, help="Path to POS transactions CSV")
    parser.add_argument("--no-ingest", action="store_true", help="Skip API ingest step")
    args = parser.parse_args()

    output_path = run_pipeline(args.clips_dir)

    if not args.no_ingest:
        pos_csv = args.pos_csv or str(ROOT.parent / "Brigade_Bangalore_10_April_26 (1)bc6219c.csv")
        run_ingest(output_path, pos_csv)
