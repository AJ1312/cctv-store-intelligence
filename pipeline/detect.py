# pipeline/detect.py
"""
Main YOLO + ByteTrack detection loop.
Processes a single video clip and appends events to a JSONL output file.

Optimizations:
  - Frame skipping: process every FRAME_SKIP-th frame (default=3 → ~5fps effective)
  - YOLOv8n: lightest model, CPU-optimized
  - classes=[0] only: person class, skip all other detections
  - opencv-headless: no GUI overhead
"""

import cv2
import json
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ultralytics import YOLO
import supervision as sv

from pipeline.tracker import VisitorTracker

# ── Configuration ─────────────────────────────────────────────────────────────

# Process 1 in every N frames. At 15fps source: skip=3 → 5fps effective.
# 5 clips × 120s × 5fps = 3,000 frames total (vs 9,000 without skipping).
FRAME_SKIP: int = 3

# YOLO model — nano for speed, person class only
MODEL_NAME: str = "yolov8n.pt"

# Camera ID → video file name + clip start timestamp
CLIP_METADATA: dict[str, dict] = {
    "CAM_ENTRY_01":      {"file": "CAM 1.mp4", "start_ts": "2026-04-10T12:00:00+05:30"},
    "CAM_FOH_01":        {"file": "CAM 2.mp4", "start_ts": "2026-04-10T12:00:00+05:30"},
    "CAM_NORTH_WALL_01": {"file": "CAM 3.mp4", "start_ts": "2026-04-10T12:00:00+05:30"},
    "CAM_SOUTH_WALL_01": {"file": "CAM 4.mp4", "start_ts": "2026-04-10T12:00:00+05:30"},
    "CAM_BILLING_01":    {"file": "CAM 5.mp4", "start_ts": "2026-04-10T12:00:00+05:30"},
}

STORE_ID = "ST1008"


def process_clip(video_path: str, camera_id: str, output_path: str) -> int:
    """
    Run the detection pipeline on a single video clip.

    Args:
        video_path:  Absolute path to the MP4 clip
        camera_id:   Camera identifier (must be in CLIP_METADATA)
        output_path: Path to append events JSONL to

    Returns:
        Number of events written
    """
    print(f"[{camera_id}] Loading model...", flush=True)
    model = YOLO(MODEL_NAME)

    # ByteTrack tracker — handles occlusion and track re-association
    byte_tracker = sv.ByteTrack(
        track_activation_threshold=0.25,
        lost_track_buffer=30,
        minimum_matching_threshold=0.8,
        frame_rate=15,
    )

    tracker = VisitorTracker(camera_id)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[{camera_id}] ERROR: Cannot open {video_path}", flush=True)
        return 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Parse clip start timestamp (IST → UTC aware)
    raw_ts = CLIP_METADATA[camera_id]["start_ts"]
    clip_start = datetime.fromisoformat(raw_ts).astimezone(timezone.utc)

    print(
        f"[{camera_id}] {Path(video_path).name} | "
        f"{total_frames} frames @ {fps:.0f}fps | "
        f"{frame_w}×{frame_h} | "
        f"Processing every {FRAME_SKIP} frames (~{fps/FRAME_SKIP:.1f}fps effective)",
        flush=True,
    )

    event_count = 0
    frame_idx = 0
    last_timestamp = clip_start

    with open(output_path, "a", buffering=1) as out:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx += 1

            # ── Frame skipping ────────────────────────────────────────────────
            if frame_idx % FRAME_SKIP != 0:
                continue

            ts = clip_start + timedelta(seconds=frame_idx / fps)
            last_timestamp = ts

            # ── YOLO inference ────────────────────────────────────────────────
            results = model(
                frame,
                classes=[0],     # person only
                verbose=False,
                conf=0.35,       # minimum detection confidence
                iou=0.45,
            )[0]

            detections = sv.Detections.from_ultralytics(results)

            # ── ByteTrack update ──────────────────────────────────────────────
            if len(detections) > 0:
                detections = byte_tracker.update_with_detections(detections)
            
            active_ids: set[int] = set()
            if detections.tracker_id is not None:
                active_ids = set(detections.tracker_id.tolist())

            # ── Per-detection processing ──────────────────────────────────────
            if detections.tracker_id is not None:
                for i, track_id in enumerate(detections.tracker_id):
                    if track_id is None:
                        continue

                    bbox = detections.xyxy[i]  # [x1, y1, x2, y2]
                    conf = float(detections.confidence[i]) if detections.confidence is not None else 0.5

                    # Crop person region for appearance analysis
                    x1, y1, x2, y2 = (
                        max(0, int(bbox[0])), max(0, int(bbox[1])),
                        min(frame_w, int(bbox[2])), min(frame_h, int(bbox[3])),
                    )
                    crop = frame[y1:y2, x1:x2] if y2 > y1 and x2 > x1 else None

                    events = tracker.update(
                        track_id=int(track_id),
                        bbox=bbox,
                        frame=frame,
                        crop=crop if crop is not None and crop.size > 0 else frame[0:1, 0:1],
                        timestamp=ts,
                        confidence=conf,
                        frame_w=frame_w,
                        frame_h=frame_h,
                    )

                    for ev in events:
                        json.dump(ev, out)
                        out.write("\n")
                        event_count += 1

            # Flush disappeared tracks
            tracker.flush_lost_tracks(active_ids, ts, out)

            # Progress
            if frame_idx % (FRAME_SKIP * 50) == 0:
                pct = (frame_idx / max(total_frames, 1)) * 100
                print(f"[{camera_id}] {pct:.0f}% ({frame_idx}/{total_frames} frames, {event_count} events)", flush=True)

        # ── End of clip: flush all remaining tracks ───────────────────────────
        tracker.flush_all(last_timestamp, out)

    cap.release()
    print(f"[{camera_id}] DONE — {event_count} events written", flush=True)
    return event_count


if __name__ == "__main__":
    # CLI: python detect.py <video_path> <camera_id> <output_jsonl>
    if len(sys.argv) != 4:
        print("Usage: python detect.py <video_path> <camera_id> <output.jsonl>")
        sys.exit(1)

    video_path, camera_id, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    n = process_clip(video_path, camera_id, output_path)
    print(f"Total events: {n}")
