# pipeline/zones.py
"""
Zone polygon definitions for Brigade Bangalore (ST1008).
All coordinates are normalized (0.0–1.0) relative to each camera's frame.
Polygons are derived from the store floor plan blueprint measurements.
"""

from __future__ import annotations
from typing import Optional, Dict
from shapely.geometry import Point, Polygon

# ── Camera zone polygons ─────────────────────────────────────────────────────
# Each camera maps zone_id → Shapely Polygon in (x, y) normalized frame coords.

CAMERA_ZONE_POLYGONS: dict[str, dict[str, Polygon]] = {

    "CAM_ENTRY_01": {
        # Sliding glass door on west wall — narrow strip on far left
        "ZONE_ENTRY_EXIT": Polygon([(0.00, 0.25), (0.15, 0.25), (0.15, 0.75), (0.00, 0.75)]),
        # Backlit display panel visible beyond the door threshold
        "ZONE_BACKLIT":    Polygon([(0.15, 0.00), (0.55, 0.00), (0.55, 1.00), (0.15, 1.00)]),
    },

    "CAM_FOH_01": {
        # Main open floor — central aisle
        "ZONE_FOH":         Polygon([(0.10, 0.15), (0.90, 0.15), (0.90, 0.55), (0.10, 0.55)]),
        # Makeup island — two chairs, 900mm unit (centre-lower frame)
        "ZONE_MAKEUP_UNIT": Polygon([(0.30, 0.45), (0.70, 0.45), (0.70, 0.90), (0.30, 0.90)]),
        # Fragrance fixture — left-centre
        "ZONE_FRAGRANCE":   Polygon([(0.05, 0.50), (0.28, 0.50), (0.28, 0.95), (0.05, 0.95)]),
        # Nail unit — adjacent to fragrance
        "ZONE_NAIL":        Polygon([(0.28, 0.50), (0.42, 0.50), (0.42, 0.95), (0.28, 0.95)]),
    },

    "CAM_NORTH_WALL_01": {
        # 8 zones equally distributed left→right across north wall
        "ZONE_EB_KOREAN":   Polygon([(0.000, 0.0), (0.125, 0.0), (0.125, 1.0), (0.000, 1.0)]),
        "ZONE_TFS":         Polygon([(0.125, 0.0), (0.250, 0.0), (0.250, 1.0), (0.125, 1.0)]),
        "ZONE_GV":          Polygon([(0.250, 0.0), (0.375, 0.0), (0.375, 1.0), (0.250, 1.0)]),
        "ZONE_DERMDOC":     Polygon([(0.375, 0.0), (0.500, 0.0), (0.500, 1.0), (0.375, 1.0)]),
        "ZONE_MINIMALIST":  Polygon([(0.500, 0.0), (0.625, 0.0), (0.625, 1.0), (0.500, 1.0)]),
        "ZONE_AQUALOGICA":  Polygon([(0.625, 0.0), (0.750, 0.0), (0.750, 1.0), (0.625, 1.0)]),
        "ZONE_LAKME_SKIN":  Polygon([(0.750, 0.0), (0.875, 0.0), (0.875, 1.0), (0.750, 1.0)]),
        "ZONE_ACCESSORIES": Polygon([(0.875, 0.0), (1.000, 0.0), (1.000, 1.0), (0.875, 1.0)]),
    },

    "CAM_SOUTH_WALL_01": {
        # 7 zones across south wall (makeup brands)
        "ZONE_MAYBELLINE":  Polygon([(0.000, 0.0), (0.143, 0.0), (0.143, 1.0), (0.000, 1.0)]),
        "ZONE_FACES":       Polygon([(0.143, 0.0), (0.286, 0.0), (0.286, 1.0), (0.143, 1.0)]),
        "ZONE_LAKME":       Polygon([(0.286, 0.0), (0.429, 0.0), (0.429, 1.0), (0.286, 1.0)]),
        "ZONE_COLORBAR":    Polygon([(0.429, 0.0), (0.571, 0.0), (0.571, 1.0), (0.429, 1.0)]),
        "ZONE_SWISS_RENEE": Polygon([(0.571, 0.0), (0.714, 0.0), (0.714, 1.0), (0.571, 1.0)]),
        "ZONE_ALPS":        Polygon([(0.714, 0.0), (0.857, 0.0), (0.857, 1.0), (0.714, 1.0)]),
        "ZONE_STREAX":      Polygon([(0.857, 0.0), (1.000, 0.0), (1.000, 1.0), (0.857, 1.0)]),
    },

    "CAM_BILLING_01": {
        # Cash counter — mid-right area
        "ZONE_CASH_COUNTER": Polygon([(0.35, 0.15), (0.90, 0.15), (0.90, 0.70), (0.35, 0.70)]),
        # PMU service zone — rear right
        "ZONE_PMU":          Polygon([(0.60, 0.55), (1.00, 0.55), (1.00, 1.00), (0.60, 1.00)]),
        # 55" LED panel — top-right corner
        "ZONE_LED_PANEL":    Polygon([(0.82, 0.00), (1.00, 0.00), (1.00, 0.40), (0.82, 0.40)]),
    },
}

# Entry/exit virtual line for CAM_ENTRY_01 (normalized x coordinate)
ENTRY_LINE_X: float = 0.12


class CrossingDetector:
    """
    Detects virtual line crossings for the entry camera.
    A person moving LEFT→RIGHT (increasing x) past ENTRY_LINE_X = ENTRY.
    A person moving RIGHT→LEFT (decreasing x) past ENTRY_LINE_X = EXIT.
    """

    def __init__(self) -> None:
        self._prev_cx: dict[int, float] = {}  # track_id → previous normalized cx

    def check(self, track_id: int, cx_norm: float) -> Optional[str]:
        """Return 'ENTRY', 'EXIT', or None."""
        prev = self._prev_cx.get(track_id)
        self._prev_cx[track_id] = cx_norm

        if prev is None:
            return None
        if prev < ENTRY_LINE_X <= cx_norm:
            return "ENTRY"
        if prev >= ENTRY_LINE_X > cx_norm:
            return "EXIT"
        return None

    def remove(self, track_id: int) -> None:
        self._prev_cx.pop(track_id, None)


def get_zone(camera_id: str, bbox_norm: tuple) -> Optional[str]:
    """
    Return the zone_id for a detection bounding box centroid.
    bbox_norm = (x1, y1, x2, y2) in normalized [0,1] frame coordinates.
    Returns the first matching zone or None.
    """
    cx = (bbox_norm[0] + bbox_norm[2]) / 2.0
    cy = (bbox_norm[1] + bbox_norm[3]) / 2.0
    pt = Point(cx, cy)

    for zone_id, poly in CAMERA_ZONE_POLYGONS.get(camera_id, {}).items():
        if poly.contains(pt):
            return zone_id
    return None
