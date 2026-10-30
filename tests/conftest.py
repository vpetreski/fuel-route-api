import gzip
import json
from pathlib import Path

import numpy as np
import pytest
import responses
from django.core.cache import cache

from geo.distance import METERS_PER_MILE, cumulative_miles
from stations.index import reset_station_index
from stations.models import TruckStop

FIXTURES = Path(__file__).parent / "fixtures"
OSRM_ROUTE = "https://router.project-osrm.org/route/v1/driving/"
VALHALLA_ROUTE = "https://valhalla1.openstreetmap.de/route"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"


@pytest.fixture(autouse=True)
def isolated(settings):
    """Every test gets an empty in-memory cache, no request pacing and the default providers."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.OUTBOUND_MIN_INTERVAL_SECONDS = 0
    settings.ROUTING_PROVIDERS = ["osrm", "valhalla"]
    settings.OSRM_BASE_URL = "https://router.project-osrm.org"
    settings.VALHALLA_BASE_URL = "https://valhalla1.openstreetmap.de"
    settings.NOMINATIM_BASE_URL = "https://nominatim.openstreetmap.org"
    settings.FUEL_STOP_PENALTY_USD = 5.0
    cache.clear()
    reset_station_index()
    yield
    reset_station_index()


@pytest.fixture(autouse=True)
def http(request):
    """Block the network: a test sees only the responses it registers (live tests excepted)."""
    if request.node.get_closest_marker("live"):
        yield None
        return
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def osrm_payload(lat, lon, distance_miles=None, duration_hours=10.0) -> dict:
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    miles = distance_miles if distance_miles is not None else float(cumulative_miles(lat, lon)[-1])
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": miles * METERS_PER_MILE,
                "duration": duration_hours * 3600,
                "geometry": {"type": "LineString", "coordinates": np.column_stack((lon, lat)).tolist()},
            }
        ],
    }


def straight_route(lat=41.0, lon_from=-100.0, lon_to=-90.0, points=200):
    """A route along a parallel (~523 miles from -100 to -90 at 41N)."""
    lon = np.linspace(lon_from, lon_to, points)
    return np.full_like(lon, lat), lon


@pytest.fixture
def chicago_denver() -> dict:
    with gzip.open(FIXTURES / "osrm_chicago_denver.json.gz", "rt") as fh:
        return json.load(fh)


def make_stop(opis_id, lat, lon, price, *, name=None, state="NE", country="US", precision="exit") -> TruckStop:
    return TruckStop.objects.create(
        opis_id=opis_id,
        name=name or f"STOP {opis_id}",
        address="I-80, EXIT 1",
        city="Town",
        state=state,
        country=country,
        retail_price=price,
        latitude=lat,
        longitude=lon,
        location_precision=precision,
    )
