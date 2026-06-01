# pipeline/tracker.py
from __future__ import annotations
"""
VisitorTracker — per-camera stateful tracker.
Wraps ByteTrack IDs into persistent visitor identities with:
  - Re-ID across track drops (HSV histogram similarity)
  - Staff detection (Purplle magenta/pink uniform)
  - Zone dwell timing
  - Group entry detection
  - Billing queue depth estimation
"""

import uuid
import json
from collections import deque
from datetime import datetime, timezone, timedelta

import cv2
import numpy as np

from pipeline.zones import CrossingDetector, get_zone
from pipeline.emit import build_event, STORE_ID


# ── Staff uniform detection ───────────────────────────────────────────────────

def is_staff_uniform(crop: np.ndarray) -> tuple[bool, float]:
    """
    Detect Purplle staff uniform using HSV color segmentation.
    Staff wear magenta/pink branded aprons (Purplle brand color).

    HSV range tuned for Purplle magenta: Hue ~140–175°, high saturation.
    Evaluates only the upper-body region (top 50% of crop) to avoid
    false positives from pink products or shopping bags in lower frame.

    Returns:
        (is_staff: bool, confidence: float 0–1)
    """
    if crop is None or crop.size == 0:
        return False, 0.0

    try:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    except cv2.error:
        return False, 0.0

    h = hsv.shape[0]
    upper = hsv[: h // 2, :, :]  # top 50% only

    # Purplle brand magenta range (tightened saturation to reduce shelf false positives)
    lower = np.array([140, 80, 60], dtype=np.uint8)
    upper_b = np.array([175, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(upper, lower, upper_b)

    total_pixels = upper.shape[0] * upper.shape[1]
    if total_pixels == 0:
        return False, 0.0

    ratio = mask.sum() / (255.0 * total_pixels + 1e-6)
    is_staff = ratio > 0.25
    conf = min(ratio / 0.25, 1.0)
    return is_staff, conf


# ── Re-ID Manager ─────────────────────────────────────────────────────────────

class ReIDManager:
    """
    Appearance-based re-identification across track drops and camera cuts.
    Uses a 96-dimensional HSV histogram embedding (32 bins per channel).
    Matches new tracks against recently exited visitors within a time window.
    """

    def __init__(self, window_seconds: int = 300, threshold: float = 0.82) -> None:
        self.window_seconds = window_seconds
        self.threshold = threshold
        # visitor_id → (embedding, exit_timestamp)
        self._exited: dict[str, tuple[np.ndarray, datetime]] = {}
        # track_id (int) → visitor_id
        self._active: dict[int, str] = {}

    def _embedding(self, crop: np.ndarray) -> np.ndarray:
        """32-bin HSV histogram (H + S + V = 96 dims), L2-normalised."""
        if crop is None or crop.size == 0:
            return np.zeros(96, dtype=np.float32)
        try:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        except cv2.error:
            return np.zeros(96, dtype=np.float32)

        h = cv2.calcHist([hsv], [0], None, [32], [0, 180]).flatten()
        s = cv2.calcHist([hsv], [1], None, [32], [0, 256]).flatten()
        v = cv2.calcHist([hsv], [2], None, [32], [0, 256]).flatten()
        feat = np.concatenate([h, s, v]).astype(np.float32)
        norm = np.linalg.norm(feat)
        return feat / (norm + 1e-6)

    def _prune(self, now: datetime) -> None:
        """Remove exits older than window_seconds."""
        expired = [
            vid for vid, (_, ts) in self._exited.items()
            if (now - ts).total_seconds() > self.window_seconds
        ]
        for vid in expired:
            del self._exited[vid]

    def match_or_create(
        self, track_id: int, crop: np.ndarray, now: datetime
    ) -> tuple[str, bool]:
        """
        Returns (visitor_id, is_reentry).
        If track_id already active → return existing mapping.
        Otherwise try to match against exited visitors.
        If no match → create new visitor_id.
        """
        if track_id in self._active:
            return self._active[track_id], False

        self._prune(now)
        emb = self._embedding(crop)
        best_vid, best_sim = None, 0.0

        for vid, (prev_emb, _) in self._exited.items():
            sim = float(np.dot(emb, prev_emb))
            if sim > best_sim:
                best_sim = sim
                best_vid = vid

        if best_vid and best_sim >= self.threshold:
            del self._exited[best_vid]
            self._active[track_id] = best_vid
            return best_vid, True
        else:
            new_id = "VIS_" + uuid.uuid4().hex[:6].upper()
            self._active[track_id] = new_id
            return new_id, False

    def record_exit(self, track_id: int, crop: np.ndarray, now: datetime) -> str | None:
        """Store embedding on exit for potential future re-match."""
        vid = self._active.pop(track_id, None)
        if vid:
            emb = self._embedding(crop)
            self._exited[vid] = (emb, now)
        return vid

    def get_visitor_id(self, track_id: int) -> str | None:
        return self._active.get(track_id)


# ── Visitor Tracker ───────────────────────────────────────────────────────────

class VisitorTracker:
    """
    Per-camera stateful visitor tracker.
    Converts raw ByteTrack detections into semantic store intelligence events.
    """

    # Dwell threshold: emit ZONE_DWELL event after visitor stays ≥ this long
    DWELL_THRESHOLD_MS: int = 3_000  # 3 seconds
    # Group entry window: entries within this window are flagged as group
    GROUP_WINDOW_SEC: float = 1.0
    # Billing zone IDs — used for queue depth estimation
    BILLING_ZONES = frozenset({"ZONE_CASH_COUNTER", "ZONE_LED_PANEL"})

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self._reid = ReIDManager()
        self._crossing = CrossingDetector()

        # Per track state
        self._track_visitor: dict[int, str] = {}          # track_id → visitor_id
        self._track_is_staff: dict[int, bool] = {}        # track_id → is_staff
        self._track_conf: dict[int, float] = {}           # track_id → staff_conf
        self._zone_enter_ts: dict[int, datetime] = {}     # track_id → zone entry time
        self._current_zone: dict[int, str | None] = {}   # track_id → current zone_id
        self._session_seq: dict[int, int] = {}            # track_id → event counter
        self._last_seen: dict[int, datetime] = {}         # track_id → last frame timestamp

        # Visitor-level session counters (persistent across track reassignments)
        self._visitor_seq: dict[str, int] = {}

        # Group entry detection
        self._recent_entries: deque[datetime] = deque()

        # Billing queue
        self._billing_visitors: set[str] = set()

    def _next_seq(self, visitor_id: str) -> int:
        seq = self._visitor_seq.get(visitor_id, 0) + 1
        self._visitor_seq[visitor_id] = seq
        return seq

    def _group_check(self, ts: datetime) -> int:
        """Returns current group size (incl. this entry) within GROUP_WINDOW_SEC."""
        cutoff = ts - timedelta(seconds=self.GROUP_WINDOW_SEC)
        while self._recent_entries and self._recent_entries[0] < cutoff:
            self._recent_entries.popleft()
        self._recent_entries.append(ts)
        return len(self._recent_entries)

    def update(
        self,
        *,
        track_id: int,
        bbox: np.ndarray,
        frame: np.ndarray,
        crop: np.ndarray,
        timestamp: datetime,
        confidence: float,
        frame_w: int,
        frame_h: int,
    ) -> list[dict]:
        """
        Process a single detection. Returns list of events (may be empty).
        Called once per detection per processed frame.
        """
        events: list[dict] = []
        self._last_seen[track_id] = timestamp

        # ── 1. Visitor identity ──────────────────────────────────────────────
        visitor_id, is_reentry = self._reid.match_or_create(track_id, crop, timestamp)
        self._track_visitor[track_id] = visitor_id

        # ── 2. Staff detection ───────────────────────────────────────────────
        if track_id not in self._track_is_staff:
            is_staff, staff_conf = is_staff_uniform(crop)
            self._track_is_staff[track_id] = is_staff
            self._track_conf[track_id] = staff_conf

        is_staff = self._track_is_staff[track_id]

        # ── 3. Re-entry event ────────────────────────────────────────────────
        if is_reentry:
            events.append(build_event(
                store_id=STORE_ID,
                camera_id=self.camera_id,
                visitor_id=visitor_id,
                event_type="REENTRY",
                timestamp=timestamp,
                is_staff=is_staff,
                confidence=confidence,
                session_seq=self._next_seq(visitor_id),
            ))

        # ── 4. Entry/exit crossing (only on entry camera) ────────────────────
        if self.camera_id == "CAM_ENTRY_01":
            cx_norm = ((bbox[0] + bbox[2]) / 2.0) / frame_w
            crossing = self._crossing.check(track_id, cx_norm)
            if crossing in ("ENTRY", "EXIT"):
                group_size = self._group_check(timestamp) if crossing == "ENTRY" else 1
                events.append(build_event(
                    store_id=STORE_ID,
                    camera_id=self.camera_id,
                    visitor_id=visitor_id,
                    event_type=crossing,
                    timestamp=timestamp,
                    is_staff=is_staff,
                    confidence=confidence,
                    session_seq=self._next_seq(visitor_id),
                    group_size=group_size,
                ))

        # ── 5. Zone assignment ───────────────────────────────────────────────
        bbox_norm = (
            bbox[0] / frame_w, bbox[1] / frame_h,
            bbox[2] / frame_w, bbox[3] / frame_h,
        )
        zone_id = get_zone(self.camera_id, bbox_norm)
        prev_zone = self._current_zone.get(track_id)

        if zone_id != prev_zone:
            now_ms = int(timestamp.timestamp() * 1000)

            # Zone exit + dwell
            if prev_zone is not None:
                enter_ts = self._zone_enter_ts.get(track_id)
                if enter_ts:
                    dwell_ms = max(0, int((timestamp - enter_ts).total_seconds() * 1000))
                    if dwell_ms >= self.DWELL_THRESHOLD_MS:
                        events.append(build_event(
                            store_id=STORE_ID,
                            camera_id=self.camera_id,
                            visitor_id=visitor_id,
                            event_type="ZONE_DWELL",
                            timestamp=timestamp,
                            zone_id=prev_zone,
                            dwell_ms=dwell_ms,
                            is_staff=is_staff,
                            confidence=confidence,
                            session_seq=self._next_seq(visitor_id),
                        ))
                events.append(build_event(
                    store_id=STORE_ID,
                    camera_id=self.camera_id,
                    visitor_id=visitor_id,
                    event_type="ZONE_EXIT",
                    timestamp=timestamp,
                    zone_id=prev_zone,
                    is_staff=is_staff,
                    confidence=confidence,
                    session_seq=self._next_seq(visitor_id),
                ))
                # Billing queue tracking
                if prev_zone in self.BILLING_ZONES:
                    self._billing_visitors.discard(visitor_id)

            # Zone enter
            if zone_id is not None:
                self._zone_enter_ts[track_id] = timestamp
                self._current_zone[track_id] = zone_id

                # Billing queue depth
                queue_depth = None
                if zone_id in self.BILLING_ZONES:
                    self._billing_visitors.add(visitor_id)
                    queue_depth = len(self._billing_visitors)
                    events.append(build_event(
                        store_id=STORE_ID,
                        camera_id=self.camera_id,
                        visitor_id=visitor_id,
                        event_type="BILLING_QUEUE_JOIN",
                        timestamp=timestamp,
                        zone_id=zone_id,
                        is_staff=is_staff,
                        confidence=confidence,
                        queue_depth=queue_depth,
                        session_seq=self._next_seq(visitor_id),
                    ))
                else:
                    events.append(build_event(
                        store_id=STORE_ID,
                        camera_id=self.camera_id,
                        visitor_id=visitor_id,
                        event_type="ZONE_ENTER",
                        timestamp=timestamp,
                        zone_id=zone_id,
                        is_staff=is_staff,
                        confidence=confidence,
                        session_seq=self._next_seq(visitor_id),
                    ))
            else:
                self._current_zone[track_id] = None
                self._zone_enter_ts.pop(track_id, None)

        return events

    def flush_lost_tracks(
        self,
        active_track_ids: set[int],
        timestamp: datetime,
        out_file,
    ) -> None:
        """
        Called each frame. Emit ZONE_EXIT for tracks that have disappeared.
        """
        lost = set(self._last_seen.keys()) - active_track_ids
        for track_id in lost:
            last_ts = self._last_seen[track_id]
            # Only flush if truly gone (not just missed a frame)
            if (timestamp - last_ts).total_seconds() > 2.0:
                self._flush_track(track_id, timestamp, out_file)

    def _flush_track(self, track_id: int, timestamp: datetime, out_file) -> None:
        """Emit final events for a track that has permanently disappeared."""
        visitor_id = self._track_visitor.get(track_id)
        if not visitor_id:
            return
        is_staff = self._track_is_staff.get(track_id, False)
        zone_id = self._current_zone.get(track_id)

        if zone_id:
            enter_ts = self._zone_enter_ts.get(track_id)
            if enter_ts:
                dwell_ms = max(0, int((timestamp - enter_ts).total_seconds() * 1000))
                if dwell_ms >= self.DWELL_THRESHOLD_MS:
                    ev = build_event(
                        store_id=STORE_ID,
                        camera_id=self.camera_id,
                        visitor_id=visitor_id,
                        event_type="ZONE_DWELL",
                        timestamp=timestamp,
                        zone_id=zone_id,
                        dwell_ms=dwell_ms,
                        is_staff=is_staff,
                        confidence=0.8,
                        session_seq=self._next_seq(visitor_id),
                    )
                    json.dump(ev, out_file)
                    out_file.write("\n")
            ev = build_event(
                store_id=STORE_ID,
                camera_id=self.camera_id,
                visitor_id=visitor_id,
                event_type="ZONE_EXIT",
                timestamp=timestamp,
                zone_id=zone_id,
                is_staff=is_staff,
                confidence=0.8,
                session_seq=self._next_seq(visitor_id),
            )
            json.dump(ev, out_file)
            out_file.write("\n")

        # Billing queue cleanup
        if zone_id in self.BILLING_ZONES:
            self._billing_visitors.discard(visitor_id)

        # Clean up state
        for d in (self._track_visitor, self._track_is_staff, self._track_conf,
                  self._zone_enter_ts, self._current_zone, self._session_seq,
                  self._last_seen):
            d.pop(track_id, None)
        self._crossing.remove(track_id)
        self._reid.record_exit(track_id, np.array([]), timestamp)

    def flush_all(self, timestamp: datetime, out_file) -> None:
        """Flush all active tracks at end of clip."""
        for track_id in list(self._track_visitor.keys()):
            self._flush_track(track_id, timestamp, out_file)
