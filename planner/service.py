"""Plan a trip: resolve both ends, fetch one route, find the stations along it, choose the stops."""

import time
from dataclasses import dataclass

from stations.index import StationIndex, get_station_index

from .corridor import stations_along
from .exceptions import NoFuelStopInRange, StartFuelTooLow
from .locations import Location, resolve
from .optimizer import Candidate, FuelPlan, NoFuelInRange, NotEnoughStartFuel, Vehicle, plan_fuel_stops
from .routing import Route, get_route


@dataclass(frozen=True)
class TripOptions:
    max_range_miles: float
    mpg: float
    start_fuel_gallons: float | None  # None: full tank
    stop_penalty: float
    corridor_miles: float
    end_fuel_gallons: float = 0.0  # arrive with at least this much


@dataclass(frozen=True)
class TripPlan:
    start: Location
    finish: Location
    route: Route
    fuel_plan: FuelPlan
    stations: StationIndex
    stations_considered: int
    route_cached: bool
    routing_calls: int
    geocoding_calls: int
    timings_ms: dict[str, float]
    options: TripOptions


def plan_trip(start: str, finish: str, options: TripOptions) -> TripPlan:
    clock = time.perf_counter()
    timings = {}

    origin, origin_calls = resolve(start)
    destination, destination_calls = resolve(finish)
    timings["geocoding"] = _elapsed_ms(clock)

    clock = time.perf_counter()
    routed = get_route(origin, destination)
    route = routed.route
    timings["routing"] = _elapsed_ms(clock)

    clock = time.perf_counter()
    stations = get_station_index()
    found, markers, offsets = stations_along(
        route.latitude,
        route.longitude,
        route.distance_miles,
        stations.latitude,
        stations.longitude,
        options.corridor_miles,
    )
    candidates = [
        Candidate(mile=float(mile), detour_miles=float(offset), price=float(stations.price[i]), ref=int(i))
        for i, mile, offset in zip(found, markers, offsets, strict=True)
    ]
    vehicle = Vehicle(max_range_miles=options.max_range_miles, mpg=options.mpg)
    try:
        fuel_plan = plan_fuel_stops(
            route.distance_miles,
            candidates,
            vehicle,
            start_gallons=options.start_fuel_gallons,
            end_gallons=options.end_fuel_gallons,
            stop_penalty=options.stop_penalty,
        )
    except NotEnoughStartFuel as exc:
        raise StartFuelTooLow(
            str(exc), reach_miles=round(exc.reach_miles, 1), first_station_mile=round(exc.first_station_mile, 1)
        ) from exc
    except NoFuelInRange as exc:
        raise NoFuelStopInRange(
            str(exc), gap_start_mile=round(exc.from_mile, 1), gap_end_mile=round(exc.to_mile, 1)
        ) from exc
    timings["planning"] = _elapsed_ms(clock)

    return TripPlan(
        start=origin,
        finish=destination,
        route=route,
        fuel_plan=fuel_plan,
        stations=stations,
        stations_considered=len(candidates),
        route_cached=routed.cached,
        routing_calls=routed.calls,
        geocoding_calls=origin_calls + destination_calls,
        timings_ms=timings,
        options=options,
    )


def _elapsed_ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)
