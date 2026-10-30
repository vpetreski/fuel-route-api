import io
import re

import pytest
from django.core.management import call_command

from .conftest import OSRM_ROUTE, VALHALLA_ROUTE, make_stop, osrm_payload, straight_route

OSRM = re.compile(re.escape(OSRM_ROUTE) + ".*")
URL = "/api/v1/route/"
TRIP = {"start": "41.0,-100.0", "finish": "41.0,-90.0"}  # ~523 miles along the 41st parallel

pytestmark = pytest.mark.django_db


@pytest.fixture
def corridor_stops():
    make_stop(1, 41.0, -99.0, "3.50")
    make_stop(2, 41.0, -95.0, "3.10")
    make_stop(3, 41.02, -93.0, "3.30")
    make_stop(4, 41.5, -94.0, "2.00")  # 34 miles off the route
    make_stop(5, 41.0, -96.0, "1.00", country="CA")  # not a US stop
    make_stop(6, None, None, "1.00", precision="")  # never located


@pytest.fixture
def osrm(http):
    lat, lon = straight_route()
    http.get(OSRM, json=osrm_payload(lat, lon, duration_hours=8.0))
    return http


def test_plans_the_cheapest_stop(client, corridor_stops, osrm):
    response = client.get(URL, TRIP)
    assert response.status_code == 200
    body = response.json()

    [stop] = body["fuel_stops"]
    assert stop["opis_id"] == 2
    assert stop["price_per_gallon"] == 3.1
    assert stop["miles_from_start"] == pytest.approx(261.6, abs=1)
    assert stop["gallons_purchased"] == pytest.approx((body["route"]["distance_miles"] - 500) / 10, abs=0.05)

    summary = body["summary"]
    assert summary["number_of_stops"] == 1
    assert summary["total_fuel_cost"] == pytest.approx(stop["cost"], abs=0.01)
    assert summary["fuel_used_gallons"] == pytest.approx(body["route"]["distance_miles"] / 10, abs=0.1)
    assert body["vehicle"] == {
        "max_range_miles": 500.0,
        "mpg": 10.0,
        "tank_gallons": 50.0,
        "start_fuel_gallons": 50.0,
        "end_fuel_gallons": 0.0,
    }

    assert body["meta"]["external_api_calls"] == {"routing": 1, "geocoding": 0, "total": 1}
    assert body["meta"]["route_cached"] is False
    assert body["meta"]["stations_considered"] == 3
    assert "routing;dur=" in response["Server-Timing"]

    kinds = [f["properties"]["kind"] for f in body["map"]["geojson"]["features"]]
    assert kinds == ["route", "start", "finish", "fuel_stop"]
    assert body["map"]["url"].startswith("http://testserver/api/v1/route/map/?start=41.0")


def test_repeated_trip_is_served_from_cache(client, corridor_stops, osrm):
    client.get(URL, TRIP)
    body = client.get(URL, TRIP).json()
    assert body["meta"]["external_api_calls"]["total"] == 0
    assert body["meta"]["route_cached"] is True
    assert len(osrm.calls) == 1


def test_post_with_json_body_and_options(client, corridor_stops, osrm):
    payload = {**TRIP, "start_fuel_gallons": 30, "stop_penalty": 0}
    response = client.post(URL, payload, content_type="application/json")
    assert response.status_code == 200
    body = response.json()
    assert body["vehicle"]["start_fuel_gallons"] == 30.0
    assert body["summary"]["number_of_stops"] >= 1
    assert "start_fuel_gallons=30" in body["map"]["url"]


def test_short_trip_needs_no_stop(client, corridor_stops, http):
    lat, lon = straight_route(lon_to=-96.0)
    http.get(OSRM, json=osrm_payload(lat, lon))
    body = client.get(URL, {"start": "41.0,-100.0", "finish": "41.0,-96.0"}).json()
    assert body["fuel_stops"] == []
    assert body["summary"]["total_fuel_cost"] == 0
    assert body["summary"]["average_price_per_gallon"] is None


@pytest.mark.parametrize(
    "params, field",
    [
        ({"start": "Chicago, IL"}, "finish"),
        ({**TRIP, "mpg": "-3"}, "mpg"),
        ({**TRIP, "start_fuel_gallons": "80"}, "start_fuel_gallons"),
        ({**TRIP, "start": "x" * 300}, "start"),
    ],
)
def test_invalid_parameters(client, params, field):
    response = client.get(URL, params)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert field in error["fields"]


def test_location_outside_the_usa(client):
    response = client.get(URL, {"start": "43.6532,-79.3832", "finish": "Chicago, IL"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "location_outside_usa"


def test_no_station_within_range(client, http):
    lat, lon = straight_route(lon_from=-104.0, lon_to=-86.0)
    http.get(OSRM, json=osrm_payload(lat, lon))
    response = client.get(URL, {"start": "41.0,-104.0", "finish": "41.0,-86.0"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "no_fuel_stop_in_range"
    assert error["gap_start_mile"] == 0


def test_no_route(client, http):
    http.get(OSRM, json={"code": "NoRoute"}, status=400)
    response = client.get(URL, TRIP)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_route"


def test_routing_down(client, http):
    http.get(OSRM, status=503, json={})
    http.post(VALHALLA_ROUTE, status=503, json={})
    response = client.get(URL, TRIP)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"


def test_map_page_reuses_the_cached_route(client, corridor_stops, osrm):
    client.get(URL, TRIP)
    response = client.get("/api/v1/route/map/", TRIP)
    assert response.status_code == 200
    html = response.content.decode()
    assert "maplibre-gl.js" in html and 'id="trip-data"' in html
    assert len(osrm.calls) == 1


def test_map_page_reports_errors(client):
    assert client.get("/api/v1/route/map/").status_code == 400
    response = client.get("/api/v1/route/map/", {"start": "43.6532,-79.3832", "finish": "Chicago, IL"})
    assert response.status_code == 422
    assert "outside the USA" in response.content.decode()


def test_health(client, corridor_stops):
    body = client.get("/api/v1/health/").json()
    assert body["status"] == "ok"
    assert body["stations"] == 4  # located US stops; the Canadian and the unlocated one are left out


def test_health_without_data(client):
    response = client.get("/api/v1/health/")
    assert response.status_code == 503
    assert "import_fuel_prices" in response.json()["hint"]


def test_index_links(client):
    body = client.get("/").json()
    assert set(body) == {"route", "map", "health"}


def test_real_stations_on_a_recorded_route(client, http, chicago_denver):
    """The committed station data on a real Chicago-Denver route (recorded OSRM response, no network)."""
    call_command("import_fuel_prices", stdout=io.StringIO())
    http.get(OSRM, json=chicago_denver)
    body = client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"}).json()

    assert body["route"]["distance_miles"] == pytest.approx(1002.7, abs=0.1)
    assert body["meta"]["external_api_calls"]["total"] == 1
    assert body["meta"]["stations_considered"] > 50
    stops = body["fuel_stops"]
    assert len(stops) == 2  # 1,003 miles on a 500-mile tank that starts full

    # Leg by leg, with the rounded numbers of the response (0.03 gal covers their rounding).
    fuel, mile, detour = 50.0, 0.0, 0.0
    for stop in stops:
        assert stop["off_route_miles"] <= 5
        driven = stop["miles_from_start"] - mile + detour + stop["off_route_miles"]
        assert stop["fuel_on_arrival_gallons"] == pytest.approx(fuel - driven / 10, abs=0.03)
        assert stop["fuel_on_arrival_gallons"] >= 0
        fuel = stop["fuel_on_arrival_gallons"] + stop["gallons_purchased"]
        assert fuel <= 50.01
        mile, detour = stop["miles_from_start"], stop["off_route_miles"]
    end = fuel - (body["route"]["distance_miles"] - mile + detour) / 10
    assert body["vehicle"]["end_fuel_gallons"] == pytest.approx(end, abs=0.03)
    assert body["summary"]["total_fuel_cost"] == pytest.approx(sum(s["cost"] for s in stops), abs=0.02)
    assert body["meta"]["timings_ms"]["planning"] < 250
