"""Where to buy fuel along a fixed route, and how much.

The route runs from mile 0 to mile L. Each candidate station sits at some mile along it, a short
detour off it, and sells at a known price. The vehicle carries at most `max_range / mpg` gallons
and may buy any amount at any station.

The plan comes from an exact dynamic program over (station, fuel on board), with fuel measured in
steps of `resolution_miles` of range (0.25 mile = 0.025 gal at 10 mpg):

    cost[f] = cheapest way to stand at the current point of the route with f steps of fuel

Driving to the next station shifts the array by the distance. Stopping at a station means paying
for the detour fuel, a fixed `stop_penalty`, and `price * amount` for the fuel bought. Because the
price is linear, the best way to leave with t steps is a running minimum over the arrival levels
a <= t, so one station is O(K) array work and the whole plan O(n * K) for n stations and K fuel
levels. Plans for coast-to-coast routes (hundreds of candidates) take about ten milliseconds.

With no detours and no penalty this reproduces the classic optimal greedy for the gas station
problem (buy just enough to reach the next cheaper station in range, otherwise fill up); the DP
form lets the same code account for detours and avoid stops that save only cents.

The grid only chooses the stops. Its distances are rounded so that a plan that works on the grid
also works with the exact distances (see `_choose_stops`), and the amounts are then worked out
exactly for the chosen stops with the greedy, which is optimal once the stops are fixed. So the
reported plan never runs dry or overfills, whatever the distances.

Arriving with at least `end_gallons` is planned as driving on `end_gallons * mpg` miles past the
finish, a stretch with no stations.
"""

import itertools
import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Vehicle:
    max_range_miles: float = 500.0
    mpg: float = 10.0

    @property
    def tank_gallons(self) -> float:
        return self.max_range_miles / self.mpg


@dataclass(frozen=True)
class Candidate:
    mile: float  # position along the route
    detour_miles: float  # one way, from the route to the station
    price: float  # USD per gallon
    ref: Any = None  # caller's handle, returned untouched


@dataclass(frozen=True)
class Stop:
    candidate: Candidate
    arrival_gallons: float
    gallons: float
    cost: float


@dataclass(frozen=True)
class FuelPlan:
    stops: tuple[Stop, ...]
    vehicle: Vehicle
    route_miles: float
    start_gallons: float
    end_gallons: float

    @property
    def total_cost(self) -> float:
        return sum(stop.cost for stop in self.stops)

    @property
    def gallons_bought(self) -> float:
        return sum(stop.gallons for stop in self.stops)

    @property
    def fuel_used_gallons(self) -> float:
        return self.start_gallons + self.gallons_bought - self.end_gallons


class NoFuelInRange(Exception):
    """No sequence of stations lets the vehicle cover the route."""

    def __init__(self, from_mile: float, to_mile: float, max_range_miles: float, message: str = ""):
        self.from_mile = from_mile
        self.to_mile = to_mile
        super().__init__(
            message
            or f"No reachable fuel station between mile {from_mile:,.0f} and mile {to_mile:,.0f} of the route "
            f"(vehicle range is {max_range_miles:,.0f} miles)."
        )


class NotEnoughStartFuel(NoFuelInRange):
    """The fuel on board at the start reaches neither a station nor the finish."""

    def __init__(self, start_gallons: float, reach_miles: float, first_station_mile: float, max_range_miles: float):
        self.reach_miles = reach_miles
        self.first_station_mile = first_station_mile
        super().__init__(
            0.0,
            first_station_mile,
            max_range_miles,
            f"The starting fuel ({start_gallons:.1f} gal, {reach_miles:,.1f} miles) does not reach the first "
            f"station on the route (mile {first_station_mile:,.1f}).",
        )


def plan_fuel_stops(
    route_miles: float,
    candidates: list[Candidate],
    vehicle: Vehicle,
    start_gallons: float | None = None,
    end_gallons: float = 0.0,
    stop_penalty: float = 0.0,
    resolution_miles: float = 0.25,
) -> FuelPlan:
    """Cheapest plan (fuel cost plus `stop_penalty` per stop) that arrives with at least `end_gallons`.

    `start_gallons` defaults to a full tank.
    """
    tank = vehicle.tank_gallons
    start_gallons = tank if start_gallons is None else min(max(start_gallons, 0.0), tank)
    end_gallons = min(max(end_gallons, 0.0), tank)
    extra_miles = end_gallons * vehicle.mpg
    stations = sorted((c for c in candidates if 0 <= c.mile <= route_miles), key=lambda c: c.mile)

    reach = start_gallons * vehicle.mpg
    nearest = min(stations, key=lambda c: c.mile + c.detour_miles, default=None)
    if nearest and reach < route_miles + extra_miles and reach < nearest.mile + nearest.detour_miles:
        raise NotEnoughStartFuel(start_gallons, reach, nearest.mile, vehicle.max_range_miles)
    last = max((c.mile - c.detour_miles for c in stations), default=-math.inf)
    if extra_miles and reach < route_miles + extra_miles and last + vehicle.max_range_miles < route_miles + extra_miles:
        raise NoFuelInRange(
            max(last, 0.0),
            route_miles,
            vehicle.max_range_miles,
            f"Arriving with {end_gallons:g} gal needs a station within "
            f"{vehicle.max_range_miles - extra_miles:,.0f} miles of the finish.",
        )

    chosen = _choose_stops(route_miles, extra_miles, stations, vehicle, start_gallons, stop_penalty, resolution_miles)
    return _buy(route_miles, chosen, vehicle, start_gallons, end_gallons)


def _choose_stops(
    route_miles: float,
    extra_miles: float,
    stations: list[Candidate],
    vehicle: Vehicle,
    start_gallons: float,
    stop_penalty: float,
    step: float,
) -> list[Candidate]:
    """The stops of the cheapest plan on the grid, in route order.

    Positions are rounded to the nearest step and the rounding error goes into the detours, which
    round up; the tank and the starting fuel round down. So no leg is shorter on the grid than it is
    in exact miles, and a plan that works on the grid works exactly. On grid-aligned input nothing
    is rounded and the grid is exact.
    """
    levels_count = int(math.floor(vehicle.max_range_miles / step + 1e-9))
    levels = np.arange(levels_count + 1)
    step_gallons = step / vehicle.mpg
    start = min(levels_count, int(math.floor(start_gallons / step_gallons + 1e-9)))

    finish = int(math.ceil((route_miles + extra_miles) / step - 1e-9))
    exact = [c.mile / step for c in stations]
    positions = [min(int(round(x)), finish) for x in exact]
    detours_in = [
        int(math.ceil(c.detour_miles / step + max(0.0, x - p) - 1e-9))
        for c, x, p in zip(stations, exact, positions, strict=True)
    ]
    detours_out = [
        int(math.ceil(c.detour_miles / step + max(0.0, p - x) - 1e-9))
        for c, x, p in zip(stations, exact, positions, strict=True)
    ]

    cost = np.full(levels_count + 1, np.inf)
    cost[start] = 0.0
    stopped = np.zeros((len(stations), levels_count + 1), dtype=bool)
    arrival = np.zeros((len(stations), levels_count + 1), dtype=np.int32)

    position = 0
    for i, station in enumerate(stations):
        cost = _drive(cost, positions[i] - position)
        if not np.isfinite(cost).any():
            raise NoFuelInRange(stations[i - 1].mile if i else 0.0, station.mile, vehicle.max_range_miles)
        position = positions[i]
        come, back = detours_in[i], detours_out[i]
        if max(come, back) > levels_count:
            continue
        unit_price = station.price * step_gallons

        # Arrive with a = f - come steps, leave with t >= a; g[a] = cost(a + come) - a * unit_price.
        g = np.full(levels_count + 1, np.inf)
        g[: levels_count + 1 - come] = cost[come:] - levels[: levels_count + 1 - come] * unit_price
        best = np.minimum.accumulate(g)
        best_arrival = np.maximum.accumulate(np.where(g == best, levels, 0))  # an argmin of each prefix
        # A stop buys at least one step: leaving with t means arriving with at most t - 1.
        best = np.concatenate(([np.inf], best[:-1]))
        best_arrival = np.concatenate(([0], best_arrival[:-1]))
        leave = best + levels * unit_price + stop_penalty

        # Back on the route with t - back steps.
        via_stop = np.full(levels_count + 1, np.inf)
        via_stop[: levels_count + 1 - back] = leave[back:]
        take = via_stop < cost
        stopped[i] = take
        arrival[i, : levels_count + 1 - back] = best_arrival[back:]
        cost = np.where(take, via_stop, cost)

    cost = _drive(cost, finish - position)
    if not np.isfinite(cost).any():
        raise NoFuelInRange(stations[-1].mile if stations else 0.0, route_miles, vehicle.max_range_miles)
    # Among equally cheap plans, keep the one that arrives with the most fuel.
    end = int(np.flatnonzero(cost <= cost.min() + 1e-9).max())

    # Walk the decisions back from the destination.
    chosen = []
    level = end + finish - position
    for i in range(len(stations) - 1, -1, -1):
        if stopped[i, level]:
            chosen.append(stations[i])
            level = int(arrival[i, level]) + detours_in[i]
        level += positions[i] - (positions[i - 1] if i else 0)
    if level != start:
        raise RuntimeError(f"backtracking ended at fuel level {level}, not at the start ({start})")
    return chosen[::-1]


def _buy(
    route_miles: float, stations: list[Candidate], vehicle: Vehicle, start_gallons: float, end_gallons: float
) -> FuelPlan:
    """Exact amounts for a fixed list of stops, with the greedy that is optimal for one.

    At each stop: if a cheaper stop (or the finish) is in range, buy just enough to get there;
    otherwise fill up. A stop left with nothing to buy is dropped, and the amounts are worked out
    again without its detour.
    """
    tank = vehicle.tank_gallons
    while True:
        points = [(0.0, 0.0), *((s.mile, s.detour_miles) for s in stations), (route_miles, 0.0)]
        # Fuel from each point (the start, then each station) to the next (each station, then the finish).
        legs = [(b[0] - a[0] + a[1] + b[1]) / vehicle.mpg for a, b in itertools.pairwise(points)]
        legs[-1] += end_gallons  # what must be left at the finish counts as fuel the last leg needs
        fuel = _gallons(start_gallons - legs[0])
        stops = []
        for j, station in enumerate(stations, start=1):
            target, need = tank, 0.0
            for k in range(j + 1, len(points)):
                need += legs[k - 1]
                if need > tank + 1e-9:
                    break
                if k == len(points) - 1 or stations[k - 1].price < station.price:
                    target = need
                    break
            bought = max(0.0, min(target, tank) - fuel)
            stops.append(Stop(candidate=station, arrival_gallons=fuel, gallons=bought, cost=bought * station.price))
            fuel = _gallons(fuel + bought - legs[j])

        kept = [stop.candidate for stop in stops if stop.gallons > 1e-9]
        if len(kept) == len(stations):
            return FuelPlan(
                stops=tuple(stops),
                vehicle=vehicle,
                route_miles=route_miles,
                start_gallons=start_gallons,
                end_gallons=fuel + end_gallons,
            )
        stations = kept


def _gallons(fuel: float) -> float:
    """Fuel on board after a leg; the stops are chosen so it never runs out (bar float noise)."""
    if fuel < -1e-6:
        raise RuntimeError(f"the plan runs dry ({fuel} gal)")
    return max(fuel, 0.0)


def _drive(cost: np.ndarray, steps: int) -> np.ndarray:
    """Fuel levels after driving `steps`: level f now costs what level f + steps did before."""
    if steps <= 0:
        return cost
    moved = np.full_like(cost, np.inf)
    if steps < len(cost):
        moved[: len(cost) - steps] = cost[steps:]
    return moved
