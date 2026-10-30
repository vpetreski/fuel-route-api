"""Driving routes from public OpenStreetMap routing engines, with caching.

One route request per trip: the response carries the full geometry, which is all the planner
needs. OSRM is tried first; Valhalla is only called if OSRM cannot be reached.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from geo.distance import METERS_PER_MILE
from geo.polyline import decode
from geo.simplify import simplify

from . import http
from .exceptions import NoRouteFound, UpstreamUnavailable

logger = logging.getLogger(__name__)

# Tolerance for the geometry returned to clients (~100 m): enough for any map, a fraction of the size.
DISPLAY_TOLERANCE_DEGREES = 0.001


@dataclass(frozen=True)
class Route:
    latitude: np.ndarray
    longitude: np.ndarray
    distance_miles: float
    duration_hours: float
    provider: str
    display: np.ndarray = field(repr=False)  # simplified [[lon, lat], ...] for responses and maps

    @classmethod
    def build(cls, latitude, longitude, distance_miles, duration_hours, provider) -> "Route":
        lat = np.asarray(latitude, dtype=float)
        lon = np.asarray(longitude, dtype=float)
        display = np.round(simplify(np.column_stack((lon, lat)), DISPLAY_TOLERANCE_DEGREES), 5)
        return cls(lat, lon, float(distance_miles), float(duration_hours), provider, display)


@dataclass(frozen=True)
class RouteResult:
    route: Route
    cached: bool
    calls: int  # requests sent to routing providers for this result


class ProviderError(Exception):
    """A provider could not be reached or failed; the next provider may be tried."""


class OSRM:
    name = "osrm"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def route(self, start, finish) -> Route:
        coordinates = f"{start.longitude:.6f},{start.latitude:.6f};{finish.longitude:.6f},{finish.latitude:.6f}"
        params = {"overview": "full", "geometries": "geojson", "alternatives": "false", "steps": "false"}
        response = _send("GET", f"{self.base_url}/route/v1/driving/{coordinates}", params=params)
        payload = _json(response)
        code = payload.get("code")
        if code in {"NoRoute", "NoSegment"}:
            raise NoRouteFound(
                f"No drivable route between {start.label} and {finish.label}.", provider=self.name, reason=code
            )
        if response.status_code != 200 or code != "Ok" or not payload.get("routes"):
            raise ProviderError(f"{self.name}: HTTP {response.status_code} {code or ''}".strip())
        best = payload["routes"][0]
        lon, lat = np.asarray(best["geometry"]["coordinates"], dtype=float).T
        return Route.build(lat, lon, best["distance"] / METERS_PER_MILE, best["duration"] / 3600, self.name)


class Valhalla:
    name = "valhalla"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def route(self, start, finish) -> Route:
        body = {
            "locations": [
                {"lat": start.latitude, "lon": start.longitude},
                {"lat": finish.latitude, "lon": finish.longitude},
            ],
            "costing": "auto",
            "units": "miles",
            "directions_type": "none",
        }
        response = _send("POST", f"{self.base_url}/route", json=body)
        payload = _json(response)
        if response.status_code == 400 and payload.get("error_code") in {170, 171, 442, 443}:
            raise NoRouteFound(
                f"No drivable route between {start.label} and {finish.label}.",
                provider=self.name,
                reason=payload.get("error", ""),
            )
        if response.status_code != 200 or "trip" not in payload:
            raise ProviderError(f"{self.name}: HTTP {response.status_code} {payload.get('error', '')}".strip())
        trip = payload["trip"]
        points = [point for leg in trip["legs"] for point in decode(leg["shape"], precision=6)]
        lat, lon = np.asarray(points, dtype=float).T
        return Route.build(lat, lon, trip["summary"]["length"], trip["summary"]["time"] / 3600, self.name)


PROVIDERS = {"osrm": lambda: OSRM(settings.OSRM_BASE_URL), "valhalla": lambda: Valhalla(settings.VALHALLA_BASE_URL)}


def get_route(start, finish) -> RouteResult:
    key = (
        "route:v1:"
        + hashlib.sha256(
            f"{start.latitude:.5f},{start.longitude:.5f}|{finish.latitude:.5f},{finish.longitude:.5f}".encode()
        ).hexdigest()
    )
    if (route := cache.get(key)) is not None:
        return RouteResult(route, cached=True, calls=0)

    calls = 0
    failures = []
    for name in settings.ROUTING_PROVIDERS:
        provider = PROVIDERS[name]()
        calls += 1
        try:
            route = provider.route(start, finish)
        except ProviderError as exc:
            logger.warning("routing provider failed: %s", exc)
            failures.append(str(exc))
            continue
        cache.set(key, route, settings.ROUTE_CACHE_SECONDS)
        return RouteResult(route, cached=False, calls=calls)
    raise UpstreamUnavailable("The routing service is unavailable, please retry shortly.", providers=failures)


def _send(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return http.request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise ProviderError(f"{urlsplit(url).netloc}: {exc.__class__.__name__}") from exc


def _json(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
