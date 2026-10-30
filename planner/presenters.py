"""JSON shape of a trip plan (used by the API and embedded in the map page)."""

from .service import TripPlan

ATTRIBUTION = "Routing and map data © OpenStreetMap contributors."


def trip_payload(plan: TripPlan, map_url: str) -> dict:
    fuel = plan.fuel_plan
    stops = [_stop(plan, number, stop) for number, stop in enumerate(fuel.stops, start=1)]
    bought = fuel.gallons_bought
    return {
        "start": _location(plan.start),
        "finish": _location(plan.finish),
        "route": {
            "distance_miles": round(plan.route.distance_miles, 1),
            "duration_hours": round(plan.route.duration_hours, 2),
            "provider": plan.route.provider,
        },
        "summary": {
            "total_fuel_cost": round(fuel.total_cost, 2),
            "currency": "USD",
            "number_of_stops": len(stops),
            "gallons_purchased": round(bought, 2),
            "average_price_per_gallon": round(fuel.total_cost / bought, 3) if bought else None,
            "fuel_used_gallons": round(fuel.fuel_used_gallons, 2),
        },
        "fuel_stops": stops,
        "vehicle": {
            "max_range_miles": fuel.vehicle.max_range_miles,
            "mpg": fuel.vehicle.mpg,
            "tank_gallons": round(fuel.vehicle.tank_gallons, 2),
            "start_fuel_gallons": round(fuel.start_gallons, 2),
            "end_fuel_gallons": round(fuel.end_gallons, 2),
        },
        "options": {
            "stop_penalty": plan.options.stop_penalty,
            "end_fuel_gallons": plan.options.end_fuel_gallons,
            "corridor_miles": plan.options.corridor_miles,
        },
        "map": {"url": map_url, "geojson": _feature_collection(plan, stops)},
        "meta": {
            "external_api_calls": {
                "routing": plan.routing_calls,
                "geocoding": plan.geocoding_calls,
                "total": plan.routing_calls + plan.geocoding_calls,
            },
            "route_cached": plan.route_cached,
            "stations_considered": plan.stations_considered,
            "timings_ms": plan.timings_ms,
            "attribution": ATTRIBUTION,
        },
    }


def _location(location) -> dict:
    return {
        "query": location.query,
        "label": location.label,
        "latitude": round(location.latitude, 6),
        "longitude": round(location.longitude, 6),
        "resolved_by": location.source,
    }


def _stop(plan: TripPlan, number: int, stop) -> dict:
    i = stop.candidate.ref
    details = plan.stations.details[i]
    return {
        "number": number,
        "opis_id": details["opis_id"],
        "name": details["name"],
        "address": details["address"],
        "city": details["city"],
        "state": details["state"],
        "latitude": round(float(plan.stations.latitude[i]), 6),
        "longitude": round(float(plan.stations.longitude[i]), 6),
        "location_precision": details["precision"],
        "miles_from_start": round(stop.candidate.mile, 1),
        "off_route_miles": round(stop.candidate.detour_miles, 1),
        "price_per_gallon": round(stop.candidate.price, 3),
        "fuel_on_arrival_gallons": round(stop.arrival_gallons, 2),
        "gallons_purchased": round(stop.gallons, 2),
        "cost": round(stop.cost, 2),
    }


def _point(longitude, latitude, properties) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": properties,
    }


def _feature_collection(plan: TripPlan, stops: list[dict]) -> dict:
    """GeoJSON with simplestyle properties, so it renders as-is in geojson.io or any GIS tool."""
    route = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": plan.route.display.tolist()},
        "properties": {
            "kind": "route",
            "distance_miles": round(plan.route.distance_miles, 1),
            "stroke": "#2563eb",
            "stroke-width": 4,
        },
    }
    features = [
        route,
        _point(
            plan.start.longitude,
            plan.start.latitude,
            {"kind": "start", "label": plan.start.label, "marker-color": "#16a34a", "marker-symbol": "s"},
        ),
        _point(
            plan.finish.longitude,
            plan.finish.latitude,
            {"kind": "finish", "label": plan.finish.label, "marker-color": "#dc2626", "marker-symbol": "f"},
        ),
    ]
    for stop in stops:
        properties = {
            "kind": "fuel_stop",
            "number": stop["number"],
            "name": stop["name"],
            "price_per_gallon": stop["price_per_gallon"],
            "gallons_purchased": stop["gallons_purchased"],
            "cost": stop["cost"],
            "marker-color": "#f59e0b",
            "marker-symbol": str(stop["number"]),
        }
        features.append(_point(stop["longitude"], stop["latitude"], properties))
    return {"type": "FeatureCollection", "features": features}
