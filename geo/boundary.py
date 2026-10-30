"""Point-in-polygon test against the US state outlines."""

import gzip
import json
from pathlib import Path

import numpy as np

MILES_PER_DEGREE = 69.0


class Boundary:
    """A set of lon/lat rings evaluated with the even-odd rule (islands and holes both work)."""

    def __init__(self, rings: list[np.ndarray]):
        self._rings = [np.asarray(r, dtype=float) for r in rings]
        self._boxes = np.array([[r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max()] for r in self._rings])

    @classmethod
    def from_file(cls, path: Path) -> "Boundary":
        path = Path(path)
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            return cls(json.load(fh)["rings"])

    def contains(self, latitude: float, longitude: float, tolerance_miles: float = 0.0) -> bool:
        """True inside the outline, or within `tolerance_miles` of it (absorbs coastline simplification)."""
        if self._inside(latitude, longitude):
            return True
        return tolerance_miles > 0 and self._distance_miles(latitude, longitude, tolerance_miles) <= tolerance_miles

    def _candidates(self, x: float, y: float, pad_x: float = 0.0, pad_y: float = 0.0) -> np.ndarray:
        b = self._boxes
        within_x = (b[:, 0] - pad_x <= x) & (x <= b[:, 2] + pad_x)
        return np.flatnonzero(within_x & (b[:, 1] - pad_y <= y) & (y <= b[:, 3] + pad_y))

    def _inside(self, y: float, x: float) -> bool:
        inside = False
        for index in self._candidates(x, y):
            ring = self._rings[index]
            x1, y1 = ring[:, 0], ring[:, 1]
            x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
            straddles = (y1 > y) != (y2 > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if np.count_nonzero(straddles & (x < crossing_x)) % 2:
                inside = not inside
        return inside

    def _distance_miles(self, y: float, x: float, limit: float) -> float:
        """Distance to the nearest ring edge, on a local flat projection (fine for a few miles)."""
        scale_x = MILES_PER_DEGREE * np.cos(np.radians(y))
        pad_y = limit / MILES_PER_DEGREE
        pad_x = limit / max(scale_x, 1e-6)
        best = np.inf
        for index in self._candidates(x, y, pad_x, pad_y):
            ring = self._rings[index]
            ax, ay = (ring[:, 0] - x) * scale_x, (ring[:, 1] - y) * MILES_PER_DEGREE
            bx, by = np.roll(ax, -1), np.roll(ay, -1)
            dx, dy = bx - ax, by - ay
            with np.errstate(divide="ignore", invalid="ignore"):
                t = np.clip(-(ax * dx + ay * dy) / (dx * dx + dy * dy), 0.0, 1.0)
            t = np.nan_to_num(t)
            best = min(best, float(np.hypot(ax + t * dx, ay + t * dy).min()))
        return best
