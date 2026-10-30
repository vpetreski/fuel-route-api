from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from stations.index import get_station_index

from .exceptions import PlanningError
from .presenters import trip_payload
from .serializers import TripRequestSerializer
from .service import plan_trip

EXAMPLE = {"start": "New York, NY", "finish": "Los Angeles, CA"}


class RouteView(APIView):
    """Cheapest fuel stops between two US locations, with the route and its total fuel cost.

    GET  /api/v1/route/?start=Chicago, IL&finish=Denver, CO
    POST /api/v1/route/  {"start": "Chicago, IL", "finish": "Denver, CO"}

    Optional: max_range_miles (500), mpg (10), start_fuel_gallons (full tank), end_fuel_gallons (0),
    stop_penalty (5).
    """

    def get(self, request):
        return self.plan(request, request.query_params)

    def post(self, request):
        return self.plan(request, request.data)

    def plan(self, request, data):
        serializer = TripRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        trip = plan_trip(validated["start"], validated["finish"], serializer.to_options())

        sent = {key: data[key] for key in serializer.fields if key in data}
        map_url = request.build_absolute_uri(f"{reverse('planner:route-map')}?{urlencode(sent)}")
        response = Response(trip_payload(trip, map_url))
        response["Server-Timing"] = ", ".join(f"{name};dur={ms}" for name, ms in trip.timings_ms.items())
        return response


def route_map(request):
    """The same plan on an interactive map (re-uses the cached route: no extra routing call)."""
    if not AnonRateThrottle().allow_request(request, None):
        return render(request, "planner/map.html", {"error": "Too many requests, please retry shortly."}, status=429)
    serializer = TripRequestSerializer(data=request.GET)
    if not serializer.is_valid():
        message = "Pass start and finish, e.g. ?start=Chicago, IL&finish=Denver, CO"
        return render(request, "planner/map.html", {"error": message}, status=400)
    data = serializer.validated_data
    try:
        trip = plan_trip(data["start"], data["finish"], serializer.to_options())
    except PlanningError as exc:
        return render(request, "planner/map.html", {"error": str(exc)}, status=exc.status_code)
    payload = trip_payload(trip, request.build_absolute_uri())
    return render(request, "planner/map.html", {"trip": payload})


class HealthView(APIView):
    def get(self, request):
        stations = len(get_station_index())
        body = {
            "status": "ok" if stations else "no_stations",
            "stations": stations,
            "routing_providers": settings.ROUTING_PROVIDERS,
        }
        if not stations:
            body["hint"] = "Run `python manage.py import_fuel_prices`."
        return Response(body, status=200 if stations else 503)


class IndexView(APIView):
    def get(self, request):
        example = urlencode(EXAMPLE)
        return Response(
            {
                "route": request.build_absolute_uri(f"{reverse('planner:route')}?{example}"),
                "map": request.build_absolute_uri(f"{reverse('planner:route-map')}?{example}"),
                "health": request.build_absolute_uri(reverse("planner:health")),
            }
        )
