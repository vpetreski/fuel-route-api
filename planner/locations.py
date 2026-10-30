"""Turn a start or finish string into a point inside the USA.

Accepted, in order of preference:
    "41.8781,-87.6298"      latitude,longitude          offline, checked against the US outline
    "60601"                 ZIP code                    offline (Census ZCTA centroids)
    "Chicago, IL"           town and state (or "Chicago, Illinois", "Chicago IL")   offline (Census places)
    anything else           geocoded with Nominatim (cached); must resolve to the US

So the common inputs cost no external request and the trip needs only the routing call.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache

import requests
from django.conf import settings
from django.core.cache import cache

from geo.boundary import Boundary
from geo.gazetteer import Gazetteer

from . import http
from .exceptions import LocationNotFound, LocationOutsideUSA, UpstreamUnavailable

COORDINATES = re.compile(r"^([-+]?\d{1,3}(?:\.\d+)?)\s*,\s*([-+]?\d{1,3}(?:\.\d+)?)$")
# The outline is simplified to ~60 m; a little slack keeps piers and beaches inside.
COAST_TOLERANCE_MILES = 0.25


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    latitude: float
    longitude: float
    source: str  # coordinates | zip | place | geocoder


@lru_cache(maxsize=1)
def gazetteer() -> Gazetteer:
    return Gazetteer(settings.DATA_DIR / "us_places.csv.gz", settings.DATA_DIR / "us_zips.csv.gz")


@lru_cache(maxsize=1)
def usa() -> Boundary:
    return Boundary.from_file(settings.DATA_DIR / "us_boundary.json.gz")


def resolve(query: str) -> tuple[Location, int]:
    """Return the location and the number of external requests it took (0 or 1)."""
    text = " ".join(query.split())
    if match := COORDINATES.match(text):
        lat, lon = float(match[1]), float(match[2])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            hint = ""
            if -90 <= lon <= 90 and -180 <= lat <= 180 and usa().contains(lon, lat, COAST_TOLERANCE_MILES):
                hint = " It looks like longitude,latitude: swap the two numbers."
            raise LocationNotFound(f"'{query}' is not a valid latitude,longitude pair.{hint}", query=query)
        if not usa().contains(lat, lon, COAST_TOLERANCE_MILES):
            hint = ""
            if usa().contains(lon, lat, COAST_TOLERANCE_MILES):
                hint = " Coordinates are read as latitude,longitude; these look swapped."
            raise LocationOutsideUSA(f"'{query}' is outside the USA.{hint}", query=query)
        return Location(query, f"{lat:.5f},{lon:.5f}", lat, lon, "coordinates"), 0

    if place := gazetteer().lookup(text):
        if place.state:
            return Location(query, place.label, place.latitude, place.longitude, "place"), 0
        return Location(query, f"ZIP {place.name}", place.latitude, place.longitude, "zip"), 0

    return geocode(text, query)


def geocode(text: str, query: str) -> tuple[Location, int]:
    key = "geocode:v1:" + hashlib.sha256(text.lower().encode()).hexdigest()
    hit = cache.get(key)
    calls = 0
    if hit is None:
        calls = 1
        try:
            response = http.request(
                "GET",
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={"q": text, "format": "jsonv2", "limit": 1, "addressdetails": 1},
            )
            response.raise_for_status()
            results = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise UpstreamUnavailable("The geocoding service is unavailable, please retry shortly.") from exc
        if results:
            best = results[0]
            country = best.get("address", {}).get("country_code", "")
            hit = (best["display_name"], float(best["lat"]), float(best["lon"]), country)
        else:
            hit = ()
        cache.set(key, hit, settings.GEOCODE_CACHE_SECONDS)
    if not hit:
        raise LocationNotFound(
            f"Could not find '{query}'. Try 'City, ST', a ZIP code or 'latitude,longitude'.", query=query
        )
    label, lat, lon, country = hit
    if country != "us":
        raise LocationOutsideUSA(f"'{query}' resolves to {label}, which is outside the USA.", query=query)
    return Location(query, label, lat, lon, "geocoder"), calls
