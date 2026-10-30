import numpy as np
import pytest

from geo.distance import cumulative_miles, haversine_miles
from planner.corridor import resample, stations_along

from .conftest import straight_route


def test_resample_keeps_segments_short():
    lat = np.array([41.0, 41.0, 42.0])
    lon = np.array([-100.0, -99.0, -99.0])
    miles = cumulative_miles(lat, lon)
    rlat, rlon, rmiles = resample(lat, lon, miles, max_step=0.5)
    assert np.diff(rmiles).max() <= 0.5 + 1e-9
    assert (rlat[0], rlon[0], rlat[-1], rlon[-1]) == (41.0, -100.0, 42.0, -99.0)
    assert rmiles[-1] == pytest.approx(miles[-1])


def test_finds_stations_near_the_route_with_mile_markers():
    lat, lon = straight_route()
    length = float(cumulative_miles(lat, lon)[-1])
    one_mile_north = 1 / 69.0
    stations_lat = np.array([41.0, 41.0 + 3 * one_mile_north, 41.0 + 8 * one_mile_north, 41.0, 35.0])
    stations_lon = np.array([-95.0, -92.0, -97.0, -100.0, -95.0])

    found, markers, offsets = stations_along(lat, lon, length, stations_lat, stations_lon, corridor_miles=5)

    assert list(found) == [3, 0, 1]  # sorted by position; 8 miles off and 400 miles south are excluded
    assert markers[0] == pytest.approx(0, abs=0.3)
    assert markers[1] == pytest.approx(haversine_miles(41, -100, 41, -95), rel=0.01)
    assert offsets[2] == pytest.approx(3, abs=0.05)


def test_mile_markers_follow_the_routing_engine_distance():
    lat, lon = straight_route()
    found, markers, _ = stations_along(lat, lon, 600.0, np.array([41.0]), np.array([-90.0]), 5)
    assert markers[-1] == pytest.approx(600.0, abs=0.5)


def test_no_stations_or_degenerate_route():
    lat, lon = straight_route()
    assert len(stations_along(lat, lon, 500, np.array([]), np.array([]), 5)[0]) == 0
    assert len(stations_along(lat[:1], lon[:1], 0, np.array([41.0]), np.array([-95.0]), 5)[0]) == 0
