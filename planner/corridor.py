"""Which stations lie along a route, and where.

The route is resampled so no segment is longer than half a mile, then put in a KD-tree (points on a
sphere of Earth's radius, in miles). One query gives, for every station near the route's bounding box,
its distance to the route and the nearest route point, whose distance from the start is the
station's mile marker. This is a few milliseconds for a coast-to-coast route.
"""

import numpy as np
from scipy.spatial import cKDTree

from geo.distance import EARTH_RADIUS_MILES, cumulative_miles, to_cartesian

MAX_STEP_MILES = 0.5
MILES_PER_DEGREE_LAT = 69.0


def resample(lat: np.ndarray, lon: np.ndarray, miles: np.ndarray, max_step: float = MAX_STEP_MILES):
    """Insert points along long segments so consecutive points are at most `max_step` apart."""
    pieces = np.maximum(1, np.ceil(np.diff(miles) / max_step).astype(int))
    segment = np.repeat(np.arange(len(pieces)), pieces)
    fraction = (np.arange(pieces.sum()) - np.repeat(np.cumsum(pieces) - pieces, pieces)) / np.repeat(pieces, pieces)

    def interpolate(values):
        return np.append(values[segment] + (values[segment + 1] - values[segment]) * fraction, values[-1])

    return interpolate(lat), interpolate(lon), interpolate(miles)


def stations_along(
    route_lat: np.ndarray,
    route_lon: np.ndarray,
    route_miles: float,
    station_lat: np.ndarray,
    station_lon: np.ndarray,
    corridor_miles: float,
):
    """Indices of stations within `corridor_miles` of the route, their mile markers and offsets.

    Mile markers are scaled so the route's length equals the routing engine's distance.
    """
    empty = np.array([], dtype=int), np.array([]), np.array([])
    if len(route_lat) < 2 or len(station_lat) == 0:
        return empty
    miles = cumulative_miles(route_lat, route_lon)
    if miles[-1] > 0:
        miles *= route_miles / miles[-1]
    lat, lon, miles = resample(np.asarray(route_lat, float), np.asarray(route_lon, float), miles)

    pad_lat = corridor_miles / MILES_PER_DEGREE_LAT
    pad_lon = corridor_miles / (MILES_PER_DEGREE_LAT * max(np.cos(np.radians(np.abs(lat).max())), 0.01))
    nearby = np.flatnonzero(
        (station_lat >= lat.min() - pad_lat)
        & (station_lat <= lat.max() + pad_lat)
        & (station_lon >= lon.min() - pad_lon)
        & (station_lon <= lon.max() + pad_lon)
    )
    if not len(nearby):
        return empty

    chord_limit = 2 * EARTH_RADIUS_MILES * np.sin(corridor_miles / (2 * EARTH_RADIUS_MILES))
    tree = cKDTree(to_cartesian(lat, lon))
    chord, nearest = tree.query(
        to_cartesian(station_lat[nearby], station_lon[nearby]), distance_upper_bound=chord_limit
    )
    inside = np.isfinite(chord)
    offsets = 2 * EARTH_RADIUS_MILES * np.arcsin(chord[inside] / (2 * EARTH_RADIUS_MILES))
    markers = miles[nearest[inside]]
    order = np.argsort(markers, kind="stable")
    return nearby[inside][order], markers[order], offsets[order]
