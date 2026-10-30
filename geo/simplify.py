"""Polyline simplification (Ramer-Douglas-Peucker)."""

import numpy as np


def simplify(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Drop vertices that lie within `tolerance` (same units as the points) of the simplified line.

    Iterative, with each split's distance computation vectorised; keeps the first and last point.
    """
    points = np.asarray(points, dtype=float)
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        start, end = points[first], points[last]
        inner = points[first + 1 : last]
        direction = end - start
        length = np.hypot(*direction)
        if length == 0:
            distances = np.hypot(*(inner - start).T)
        else:
            distances = np.abs(direction[0] * (inner[:, 1] - start[1]) - direction[1] * (inner[:, 0] - start[0]))
            distances /= length
        farthest = int(np.argmax(distances))
        if distances[farthest] > tolerance:
            split = first + 1 + farthest
            keep[split] = True
            stack.append((first, split))
            stack.append((split, last))
    return points[keep]
