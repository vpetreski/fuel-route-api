"""Calls the public routing service. Not part of the default run: `pytest -m live`."""

import io

import pytest
from django.core.management import call_command

pytestmark = [pytest.mark.live, pytest.mark.django_db]


def test_coast_to_coast_with_one_routing_call(client):
    call_command("import_fuel_prices", stdout=io.StringIO())
    response = client.get("/api/v1/route/", {"start": "New York, NY", "finish": "Los Angeles, CA"})
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["route"]["distance_miles"] > 2500
    assert body["summary"]["number_of_stops"] >= 5
    assert body["meta"]["external_api_calls"]["total"] == 1
