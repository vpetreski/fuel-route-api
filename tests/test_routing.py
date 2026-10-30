import re

import pytest
import requests

from planner.exceptions import NoRouteFound, UpstreamUnavailable
from planner.locations import Location
from planner.routing import get_route

from .conftest import OSRM_ROUTE, VALHALLA_ROUTE

CHICAGO = Location("Chicago, IL", "Chicago, IL", 41.837045, -87.684939, "place")
DENVER = Location("Denver, CO", "Denver, CO", 39.76185, -104.881105, "place")
OSRM = re.compile(re.escape(OSRM_ROUTE) + ".*")


def encode(points, precision=6):
    """Encoded polyline (the inverse of geo.polyline.decode), for building Valhalla responses."""
    factor, out, prev = 10**precision, [], (0, 0)
    for lat, lon in points:
        current = (round(lat * factor), round(lon * factor))
        for value in (current[0] - prev[0], current[1] - prev[1]):
            value = ~(value << 1) if value < 0 else value << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev = current
    return "".join(out)


def test_osrm_route_is_parsed(http, chicago_denver):
    http.get(OSRM, json=chicago_denver)
    result = get_route(CHICAGO, DENVER)
    route = result.route
    assert (result.calls, result.cached, route.provider) == (1, False, "osrm")
    assert route.distance_miles == pytest.approx(1002.7, abs=0.1)
    assert len(route.latitude) == 9248
    assert 50 < len(route.display) < 1500  # simplified for responses
    sent = http.calls[0].request
    assert "overview=full" in sent.url and "geometries=geojson" in sent.url
    assert sent.headers["User-Agent"].startswith("fuel-route-api/")


def test_route_is_cached(http, chicago_denver):
    http.get(OSRM, json=chicago_denver)
    get_route(CHICAGO, DENVER)
    again = get_route(CHICAGO, DENVER)
    assert (again.calls, again.cached) == (0, True)
    assert len(http.calls) == 1


def test_no_route_is_not_retried_elsewhere(http):
    http.get(OSRM, json={"code": "NoRoute", "message": "Impossible route between points"}, status=400)
    with pytest.raises(NoRouteFound):
        get_route(CHICAGO, DENVER)
    assert len(http.calls) == 1


def test_falls_back_to_valhalla_when_osrm_is_down(http):
    http.get(OSRM, body=requests.ConnectionError("down"))
    shape = encode([(41.837045, -87.684939), (40.8, -96.7), (39.76185, -104.881105)])
    http.post(VALHALLA_ROUTE, json={"trip": {"legs": [{"shape": shape}], "summary": {"length": 1004.8, "time": 54000}}})
    result = get_route(CHICAGO, DENVER)
    assert (result.route.provider, result.calls) == ("valhalla", 2)
    assert result.route.distance_miles == pytest.approx(1004.8)
    assert result.route.latitude[1] == pytest.approx(40.8)


def test_valhalla_no_route(http, settings):
    settings.ROUTING_PROVIDERS = ["valhalla"]
    http.post(VALHALLA_ROUTE, json={"error_code": 442, "error": "No path could be found for input"}, status=400)
    with pytest.raises(NoRouteFound):
        get_route(CHICAGO, DENVER)


def test_every_provider_down(http):
    http.get(OSRM, status=503, json={})
    http.post(VALHALLA_ROUTE, status=502, body="Bad gateway")
    with pytest.raises(UpstreamUnavailable):
        get_route(CHICAGO, DENVER)
