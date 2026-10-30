"""Great-circle helpers. Everything works on numpy arrays of degrees and returns miles."""

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def to_cartesian(lat, lon) -> np.ndarray:
    """Points on a sphere of Earth's radius, in miles. Straight-line (chord) distances between them
    are within 0.01% of the great-circle distance below ~100 miles, which is all the corridor search needs."""
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    cos_lat = np.cos(lat)
    return EARTH_RADIUS_MILES * np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def cumulative_miles(lat, lon) -> np.ndarray:
    """Distance from the first vertex to every vertex of a polyline."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    steps = haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])
    return np.concatenate(([0.0], np.cumsum(steps)))
