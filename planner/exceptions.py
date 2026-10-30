"""Errors the API reports, and the DRF handler that gives every error the same JSON shape:

{"error": {"code": "location_not_found", "message": "...", ...details}}
"""

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class PlanningError(Exception):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "planning_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class LocationNotFound(PlanningError):
    code = "location_not_found"


class LocationOutsideUSA(PlanningError):
    code = "location_outside_usa"


class NoRouteFound(PlanningError):
    code = "no_route"


class NoFuelStopInRange(PlanningError):
    code = "no_fuel_stop_in_range"


class StartFuelTooLow(PlanningError):
    code = "start_fuel_too_low"


class UpstreamUnavailable(PlanningError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "upstream_unavailable"


def api_exception_handler(exc, context):
    if isinstance(exc, PlanningError):
        return Response({"error": {"code": exc.code, "message": str(exc), **exc.details}}, status=exc.status_code)

    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, exceptions.ValidationError):
        response.data = {
            "error": {"code": "invalid_request", "message": "Invalid parameters.", "fields": response.data}
        }
    else:
        detail = response.data.get("detail", "") if isinstance(response.data, dict) else response.data
        code = getattr(exc, "default_code", "error")
        response.data = {"error": {"code": code, "message": str(detail)}}
    return response
