import time

import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from planner.optimizer import Candidate, NoFuelInRange, NotEnoughStartFuel, Vehicle, plan_fuel_stops

TRUCK = Vehicle(max_range_miles=500, mpg=10)


def stations(*specs):
    """(mile, price) or (mile, price, detour) tuples."""
    return [
        Candidate(mile=s[0], price=s[1], detour_miles=s[2] if len(s) > 2 else 0.0, ref=i) for i, s in enumerate(specs)
    ]


def test_no_stop_when_the_tank_covers_the_route():
    plan = plan_fuel_stops(480, stations((100, 3.0), (300, 2.5)), TRUCK)
    assert plan.stops == ()
    assert plan.total_cost == 0
    assert plan.fuel_used_gallons == pytest.approx(48.0)


def test_buys_just_enough_to_finish():
    plan = plan_fuel_stops(600, stations((300, 3.0)), TRUCK)
    [stop] = plan.stops
    assert stop.arrival_gallons == pytest.approx(20.0)
    assert stop.gallons == pytest.approx(10.0)
    assert plan.total_cost == pytest.approx(30.0)
    assert plan.end_gallons == pytest.approx(0.0)


def test_fills_up_at_the_cheap_station_before_an_expensive_stretch():
    plan = plan_fuel_stops(1000, stations((400, 2.0), (700, 5.0)), TRUCK)
    assert [s.candidate.mile for s in plan.stops] == [400, 700]
    cheap, expensive = plan.stops
    assert cheap.gallons == pytest.approx(40.0)  # 10 left on arrival, fill to 50
    assert expensive.gallons == pytest.approx(10.0)  # only what the last 200 miles still need
    assert plan.total_cost == pytest.approx(40 * 2.0 + 10 * 5.0)


def test_buys_only_enough_to_reach_a_cheaper_station():
    plan = plan_fuel_stops(1200, stations((450, 4.0), (600, 3.0), (900, 3.5)), TRUCK)
    first = plan.stops[0]
    assert first.candidate.mile == 450
    assert first.gallons == pytest.approx(10.0)  # 450 -> 600 needs 15 gal, 5 still in the tank
    assert plan.stops[1].candidate.mile == 600
    assert plan.stops[1].gallons == pytest.approx(50.0)  # arrives empty, fills: cheapest fuel on the route


def test_pays_for_the_detour():
    plan = plan_fuel_stops(600, stations((300, 3.0, 2.0)), TRUCK)
    [stop] = plan.stops
    assert stop.arrival_gallons == pytest.approx(19.8)  # 302 miles driven
    assert stop.gallons == pytest.approx(10.4)  # 2 miles back to the route + 300 to go
    assert plan.fuel_used_gallons == pytest.approx(60.4)


def test_detour_can_make_a_cheap_station_not_worth_it():
    nearby = stations((300, 3.00), (310, 2.99, 4.0))
    plan = plan_fuel_stops(600, nearby, TRUCK, stop_penalty=0)
    assert [s.candidate.ref for s in plan.stops] == [0]


def test_stop_penalty_trades_cents_for_fewer_stops():
    # Prices drop a cent every 50 miles: the pure optimum tops up at nearly every station.
    falling = stations(*[(450 + 50 * i, 3.50 - 0.01 * i) for i in range(10)])
    cheapest = plan_fuel_stops(1000, falling, TRUCK, stop_penalty=0)
    practical = plan_fuel_stops(1000, falling, TRUCK, stop_penalty=5)
    assert len(cheapest.stops) > 3
    assert len(practical.stops) == 1
    assert practical.total_cost - cheapest.total_cost < 5


def test_never_records_a_stop_without_a_purchase():
    candidates = [Candidate(mile=float(m), detour_miles=0.5, price=3.0, ref=i) for i, m in enumerate(range(5, 400, 7))]
    plan = plan_fuel_stops(400, candidates, TRUCK, stop_penalty=0)
    assert plan.stops == ()


def test_partial_start_fuel():
    plan = plan_fuel_stops(300, stations((40, 3.0), (200, 2.0)), TRUCK, start_gallons=5)
    assert plan.stops[0].candidate.mile == 40
    assert plan.stops[0].arrival_gallons == pytest.approx(1.0)
    assert plan.stops[0].gallons == pytest.approx(15.0)  # just enough to reach the cheaper station
    assert plan.stops[1].candidate.mile == 200


def test_gap_longer_than_the_range_is_reported():
    with pytest.raises(NoFuelInRange) as error:
        plan_fuel_stops(1400, stations((450, 3.0), (1000, 3.0)), TRUCK)
    assert (error.value.from_mile, error.value.to_mile) == (450, 1000)


def test_unreachable_destination_is_reported():
    with pytest.raises(NoFuelInRange) as error:
        plan_fuel_stops(1100, stations((450, 3.0)), TRUCK)
    assert error.value.to_mile == 1100


def test_station_beyond_the_start_fuel_is_unreachable():
    with pytest.raises(NotEnoughStartFuel) as error:
        plan_fuel_stops(300, stations((100, 3.0)), TRUCK, start_gallons=5)
    assert (error.value.reach_miles, error.value.first_station_mile) == (50, 100)
    assert "does not reach the first station" in str(error.value)


def test_arrives_with_the_requested_fuel():
    plan = plan_fuel_stops(600, stations((300, 3.0)), TRUCK, end_gallons=20)
    [stop] = plan.stops
    assert stop.arrival_gallons == pytest.approx(20.0)
    assert stop.gallons == pytest.approx(30.0)  # 30 for the last 300 miles, 20 to keep
    assert plan.end_gallons == pytest.approx(20.0)


def test_same_fuel_at_both_ends_buys_all_the_fuel_burned():
    plan = plan_fuel_stops(900, stations((200, 3.0), (450, 2.8), (700, 3.2)), TRUCK, start_gallons=25, end_gallons=25)
    assert plan.gallons_bought == pytest.approx(90.0)
    assert plan.end_gallons == pytest.approx(25.0)


def test_requested_end_fuel_needs_a_station_near_the_finish():
    with pytest.raises(NoFuelInRange) as error:
        plan_fuel_stops(600, stations((100, 3.0)), TRUCK, end_gallons=50)
    assert "needs a station within 0 miles of the finish" in str(error.value)


def lp_optimum(route_miles, candidates, vehicle, start):
    """Continuous optimum of the fixed-route problem without detours (an LP)."""
    n, m, tank = len(candidates), vehicle.mpg, vehicle.tank_gallons
    s = np.array([c.mile for c in candidates])
    rows, bounds = [], []
    for i in range(n):
        arrive = np.zeros(n)
        arrive[:i] = -1
        rows.append(arrive)
        bounds.append(start - s[i] / m)  # fuel on arrival >= 0
        leave = np.zeros(n)
        leave[: i + 1] = 1
        rows.append(leave)
        bounds.append(tank - start + s[i] / m)  # fuel after buying <= tank
    rows.append(-np.ones(n))
    bounds.append(start - route_miles / m)  # enough to finish
    result = linprog([c.price for c in candidates], A_ub=rows, b_ub=bounds, bounds=(0, None), method="highs")
    return result.fun if result.status == 0 else None


def milp_optimum(route_miles, candidates, vehicle, start, penalty, end=0.0):
    """Exact optimum with detours and a per-stop penalty (a MILP: gallons x_i and stop flags y_i)."""
    n, m, tank = len(candidates), vehicle.mpg, vehicle.tank_gallons
    s = np.array([c.mile for c in candidates])
    d = np.array([c.detour_miles for c in candidates])
    rows, low, high = [], [], []

    def add(row, lo, hi):
        rows.append(row)
        low.append(lo)
        high.append(hi)

    for i in range(n):
        reach = np.zeros(2 * n)
        reach[:i] = 1
        reach[n : n + i] = -2 * d[:i] / m
        reach[n + i] = -d[i] / m
        add(reach, s[i] / m - start, np.inf)  # can drive to station i if stopping there
        full = reach.copy()
        full[i] = 1
        add(full, -np.inf, tank - start + s[i] / m)  # tank capacity at the pump
        back = np.zeros(2 * n)
        back[: i + 1] = 1
        back[n : n + i + 1] = -2 * d[: i + 1] / m
        add(back, s[i] / m - start, np.inf)  # back on the route with fuel >= 0
        link = np.zeros(2 * n)
        link[i], link[n + i] = 1, -tank
        add(link, -np.inf, 0)  # buy only where stopping
    finish = np.concatenate((np.ones(n), -2 * d / m))
    add(finish, route_miles / m - start + end, np.inf)  # arrive with at least `end`

    cost = np.concatenate(([c.price for c in candidates], np.full(n, penalty)))
    result = milp(
        cost,
        constraints=LinearConstraint(np.array(rows), low, high),
        integrality=np.concatenate((np.zeros(n), np.ones(n))),
        bounds=Bounds(np.zeros(2 * n), np.concatenate((np.full(n, np.inf), np.ones(n)))),
    )
    return result.fun if result.status == 0 else None


def random_instance(rng, detours=False):
    route = float(rng.integers(300, 2500))
    miles = np.sort(rng.integers(0, int(route), int(rng.integers(3, 18)))).astype(float)
    candidates = [
        Candidate(
            mile=float(mile),
            detour_miles=float(rng.choice([0.0, 0.5, 1.0, 2.5])) if detours else 0.0,
            price=float(np.round(rng.uniform(2.8, 4.2), 3)),
            ref=i,
        )
        for i, mile in enumerate(miles)
    ]
    return route, candidates, float(rng.choice([50.0, 30.0, 12.5]))


def test_matches_the_linear_program_optimum():
    rng = np.random.default_rng(2024)
    compared = 0
    for _ in range(150):
        route, candidates, start = random_instance(rng)
        expected = lp_optimum(route, candidates, TRUCK, start)
        if expected is None:
            with pytest.raises(NoFuelInRange):
                plan_fuel_stops(route, candidates, TRUCK, start_gallons=start)
            continue
        plan = plan_fuel_stops(route, candidates, TRUCK, start_gallons=start, stop_penalty=0)
        assert plan.total_cost == pytest.approx(expected, abs=1e-6)
        compared += 1
    assert compared > 50


def test_matches_the_exact_optimum_with_detours_and_stop_penalty():
    rng = np.random.default_rng(7)
    compared = 0
    for _ in range(80):
        route, candidates, start = random_instance(rng, detours=True)
        penalty = float(rng.choice([0.0, 2.0, 5.0, 15.0]))
        expected = milp_optimum(route, candidates, TRUCK, start, penalty)
        if expected is None:
            with pytest.raises(NoFuelInRange):
                plan_fuel_stops(route, candidates, TRUCK, start_gallons=start, stop_penalty=penalty)
            continue
        plan = plan_fuel_stops(route, candidates, TRUCK, start_gallons=start, stop_penalty=penalty)
        # abs=1e-4 (a hundredth of a cent) covers the MILP solver's own feasibility tolerance
        assert plan.total_cost + penalty * len(plan.stops) == pytest.approx(expected, abs=1e-4)
        compared += 1
    assert compared > 25


def test_matches_the_exact_optimum_when_arriving_with_fuel():
    rng = np.random.default_rng(19)
    compared = 0
    for _ in range(60):
        route, candidates, start = random_instance(rng, detours=True)
        penalty = float(rng.choice([0.0, 5.0]))
        end = float(rng.choice([5.0, 20.0, 35.0]))
        expected = milp_optimum(route, candidates, TRUCK, start, penalty, end=end)
        if expected is None:
            with pytest.raises(NoFuelInRange):
                plan_fuel_stops(route, candidates, TRUCK, start_gallons=start, end_gallons=end, stop_penalty=penalty)
            continue
        plan = plan_fuel_stops(route, candidates, TRUCK, start_gallons=start, end_gallons=end, stop_penalty=penalty)
        assert plan.end_gallons >= end - 1e-9
        assert plan.total_cost + penalty * len(plan.stops) == pytest.approx(expected, abs=1e-4)
        compared += 1
    assert compared > 15


def off_grid_instance(rng):
    """Like `random_instance`, but nothing falls on the planner's 0.25-mile grid."""
    route = float(rng.uniform(300, 2500))
    candidates = [
        Candidate(
            mile=float(mile),
            detour_miles=float(rng.choice([0.0, rng.uniform(0, 3)])),
            price=float(np.round(rng.uniform(2.8, 4.2), 3)),
            ref=i,
        )
        for i, mile in enumerate(np.sort(rng.uniform(0, route, int(rng.integers(3, 18)))))
    ]
    return route, candidates, float(rng.choice([50.0, 30.0, 12.5, rng.uniform(5, 50)]))


def replay(plan, route_miles):
    """Drive the plan with the exact distances: every number in it has to add up."""
    fuel, mile, detour = plan.start_gallons, 0.0, 0.0
    for stop in plan.stops:
        fuel -= (stop.candidate.mile - mile + detour + stop.candidate.detour_miles) / TRUCK.mpg
        assert stop.arrival_gallons == pytest.approx(fuel, abs=1e-9)
        assert fuel >= -1e-9, "runs dry"
        assert stop.gallons > 0, "a stop without a purchase"
        fuel += stop.gallons
        assert fuel <= TRUCK.tank_gallons + 1e-9, "overfills"
        mile, detour = stop.candidate.mile, stop.candidate.detour_miles
    fuel -= (route_miles - mile + detour) / TRUCK.mpg
    assert plan.end_gallons == pytest.approx(fuel, abs=1e-9)
    assert fuel >= -1e-9, "runs dry before the finish"
    assert plan.total_cost == pytest.approx(sum(s.gallons * s.candidate.price for s in plan.stops))
    assert plan.fuel_used_gallons == pytest.approx(plan.start_gallons + plan.gallons_bought - plan.end_gallons)


def test_amounts_are_exact_off_the_grid():
    rng = np.random.default_rng(11)
    candidates = [
        Candidate(float(mile), float(rng.uniform(0, 3)), float(rng.uniform(2.8, 4.2)), i)
        for i, mile in enumerate(np.sort(rng.uniform(0, 2400, 300)))
    ]
    for penalty in (0, 5):
        replay(plan_fuel_stops(2400.0, candidates, TRUCK, stop_penalty=penalty), 2400.0)


def test_off_the_grid_plans_are_within_cents_of_the_exact_optimum():
    rng = np.random.default_rng(3)
    compared = 0
    for _ in range(150):
        route, candidates, start = off_grid_instance(rng)
        penalty = float(rng.choice([0.0, 2.0, 5.0, 15.0]))
        expected = milp_optimum(route, candidates, TRUCK, start, penalty)
        try:
            plan = plan_fuel_stops(route, candidates, TRUCK, start_gallons=start, stop_penalty=penalty)
        except NoFuelInRange:
            # The grid rounds every leg up, by under a mile, so a plan that only works with a
            # fraction of a mile to spare is refused. Stretching every leg by a mile must break it.
            shorter = Vehicle(max_range_miles=TRUCK.max_range_miles - 1, mpg=TRUCK.mpg)
            assert milp_optimum(route, candidates, shorter, start - 0.1, penalty) is None
            continue
        assert expected is not None
        replay(plan, route)
        gap = plan.total_cost + penalty * len(plan.stops) - expected
        assert -1e-4 <= gap <= 0.10
        compared += 1
    assert compared > 50


def test_coast_to_coast_plan_is_fast():
    rng = np.random.default_rng(5)
    candidates = [
        Candidate(float(mile), float(rng.uniform(0, 4)), float(rng.uniform(2.8, 4.2)), i)
        for i, mile in enumerate(np.sort(rng.uniform(0, 3000, 1000)))
    ]
    started = time.perf_counter()
    plan_fuel_stops(3000, candidates, TRUCK, stop_penalty=5)
    assert time.perf_counter() - started < 0.5
