# tests/test_pipeline.py
"""
Unit tests for pipeline components (zone detection, staff uniform, Re-ID, crossing).
These tests run without CCTV clips — they use synthetic numpy arrays.
"""

import pytest
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.zones import get_zone, CrossingDetector, ENTRY_LINE_X
from pipeline.tracker import is_staff_uniform, ReIDManager


# ── Staff Uniform Detection ────────────────────────────────────────────────────

def make_crop(h: int, w: int, bgr: tuple) -> np.ndarray:
    """Create a solid-color BGR crop."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = bgr
    return img


def test_is_staff_uniform_magenta():
    """A bright magenta crop (Purplle brand color) → is_staff=True."""
    import cv2
    # HSV hue=160 (magenta), sat=200, val=200 → bright pink
    hsv = np.zeros((80, 60, 3), dtype=np.uint8)
    hsv[:40, :] = [160, 200, 200]  # upper body region
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    is_staff, conf = is_staff_uniform(bgr)
    assert is_staff, "Magenta crop should be detected as staff"
    assert conf > 0.5


def test_is_staff_uniform_blue():
    """A blue crop should NOT be detected as staff."""
    # BGR: blue
    crop = make_crop(80, 60, (200, 50, 50))
    is_staff, conf = is_staff_uniform(crop)
    assert not is_staff, "Blue crop should not be staff"


def test_is_staff_uniform_empty():
    """Empty crop → returns (False, 0.0) without crashing."""
    crop = np.array([], dtype=np.uint8).reshape(0, 0, 3)
    is_staff, conf = is_staff_uniform(crop)
    assert not is_staff
    assert conf == 0.0


# ── Zone Assignment ────────────────────────────────────────────────────────────

def test_zone_assignment_minimalist():
    """Centroid at (0.56, 0.5) on CAM_NORTH_WALL_01 → ZONE_MINIMALIST."""
    zone = get_zone("CAM_NORTH_WALL_01", (0.52, 0.3, 0.60, 0.7))
    assert zone == "ZONE_MINIMALIST", f"Expected ZONE_MINIMALIST, got {zone}"


def test_zone_assignment_cash_counter():
    """Centroid at (0.65, 0.45) on CAM_BILLING_01 → ZONE_CASH_COUNTER."""
    zone = get_zone("CAM_BILLING_01", (0.55, 0.30, 0.75, 0.60))
    assert zone == "ZONE_CASH_COUNTER", f"Expected ZONE_CASH_COUNTER, got {zone}"


def test_zone_assignment_none():
    """Centroid outside all polygons → None."""
    zone = get_zone("CAM_FOH_01", (0.0, 0.0, 0.02, 0.02))
    assert zone is None


def test_zone_assignment_south_wall():
    """Centroid at left edge of CAM_SOUTH_WALL_01 → ZONE_MAYBELLINE."""
    zone = get_zone("CAM_SOUTH_WALL_01", (0.0, 0.2, 0.10, 0.8))
    assert zone == "ZONE_MAYBELLINE"


def test_zone_assignment_unknown_camera():
    """Unknown camera ID → None (no crash)."""
    zone = get_zone("CAM_UNKNOWN_99", (0.3, 0.3, 0.6, 0.6))
    assert zone is None


# ── Crossing Detector ─────────────────────────────────────────────────────────

def test_crossing_detector_entry():
    """Track moving LEFT→RIGHT past ENTRY_LINE_X → ENTRY."""
    det = CrossingDetector()
    det.check(1, ENTRY_LINE_X - 0.02)  # initialize position
    result = det.check(1, ENTRY_LINE_X + 0.02)  # cross line right
    assert result == "ENTRY"


def test_crossing_detector_exit():
    """Track moving RIGHT→LEFT past ENTRY_LINE_X → EXIT."""
    det = CrossingDetector()
    det.check(2, ENTRY_LINE_X + 0.05)  # inside store
    result = det.check(2, ENTRY_LINE_X - 0.05)  # cross line left
    assert result == "EXIT"


def test_crossing_detector_no_cross():
    """Track staying on same side → None."""
    det = CrossingDetector()
    det.check(3, 0.50)
    result = det.check(3, 0.55)
    assert result is None


def test_crossing_detector_first_frame_none():
    """First observation → always None (no previous to compare against)."""
    det = CrossingDetector()
    result = det.check(4, ENTRY_LINE_X + 0.01)
    assert result is None


# ── Re-ID Manager ─────────────────────────────────────────────────────────────

def test_reid_same_track():
    """Same track_id → always same visitor_id."""
    from datetime import datetime, timezone
    mgr = ReIDManager()
    now = datetime.now(timezone.utc)
    crop = make_crop(80, 60, (100, 50, 150))
    vid1, _ = mgr.match_or_create(1, crop, now)
    vid2, _ = mgr.match_or_create(1, crop, now)
    assert vid1 == vid2


def test_reid_new_track_new_id():
    """New track_id with different appearance → new visitor_id."""
    from datetime import datetime, timezone
    mgr = ReIDManager()
    now = datetime.now(timezone.utc)
    crop1 = make_crop(80, 60, (255, 0, 0))   # blue
    crop2 = make_crop(80, 60, (0, 255, 0))   # green
    vid1, _ = mgr.match_or_create(1, crop1, now)
    vid2, _ = mgr.match_or_create(2, crop2, now)
    assert vid1 != vid2


def test_reid_reentry():
    """Same appearance exits and re-enters → same visitor_id, is_reentry=True."""
    from datetime import datetime, timezone
    mgr = ReIDManager(threshold=0.5)  # lower threshold for test
    now = datetime.now(timezone.utc)
    crop = make_crop(80, 60, (80, 120, 200))  # distinctive color
    vid1, _ = mgr.match_or_create(1, crop, now)
    mgr.record_exit(1, crop, now)
    vid2, is_reentry = mgr.match_or_create(99, crop, now)
    assert vid1 == vid2
    assert is_reentry
