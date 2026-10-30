import pytest
import requests

from planner.exceptions import LocationNotFound, LocationOutsideUSA, UpstreamUnavailable
from planner.locations import resolve

from .conftest import NOMINATIM_SEARCH


def test_coordinates():
    location, calls = resolve(" 41.8781 , -87.6298 ")
    assert (location.latitude, location.longitude, location.source, calls) == (41.8781, -87.6298, "coordinates", 0)


def test_coordinates_outside_the_usa():
    with pytest.raises(LocationOutsideUSA):
        resolve("43.6532,-79.3832")  # Toronto


def test_swapped_coordinates_get_a_hint():
    with pytest.raises(LocationOutsideUSA, match="swapped"):
        resolve("-87.6298,41.8781")


def test_invalid_coordinates():
    with pytest.raises(LocationNotFound):
        resolve("123.0,45.0")


def test_out_of_range_latitude_that_is_a_swapped_pair_gets_a_hint():
    with pytest.raises(LocationNotFound, match="swap the two numbers"):
        resolve("-122.4194,37.7749")  # San Francisco, as longitude,latitude


def test_place_and_zip_are_resolved_offline(http):
    place, calls = resolve("Big Cabin, OK")
    assert (place.label, place.source, calls) == ("Big Cabin, OK", "place", 0)
    zip_code, calls = resolve("78701")
    assert (zip_code.source, calls) == ("zip", 0)
    assert len(http.calls) == 0


def test_free_text_goes_to_the_geocoder_once(http):
    http.get(
        NOMINATIM_SEARCH,
        json=[
            {
                "display_name": "Empire State Building, New York",
                "lat": "40.7484",
                "lon": "-73.9857",
                "address": {"country_code": "us"},
            }
        ],
    )
    location, calls = resolve("Empire State Building")
    assert (location.source, calls) == ("geocoder", 1)
    assert location.latitude == pytest.approx(40.7484)
    _, calls = resolve("empire state building")
    assert calls == 0
    assert len(http.calls) == 1


def test_geocoder_result_outside_the_usa(http):
    http.get(
        NOMINATIM_SEARCH,
        json=[
            {
                "display_name": "Toronto, Ontario, Canada",
                "lat": "43.65",
                "lon": "-79.38",
                "address": {"country_code": "ca"},
            }
        ],
    )
    with pytest.raises(LocationOutsideUSA, match="Canada"):
        resolve("Toronto")


def test_unknown_place(http):
    http.get(NOMINATIM_SEARCH, json=[])
    with pytest.raises(LocationNotFound):
        resolve("Atlantis, XY")


def test_geocoder_down(http):
    http.get(NOMINATIM_SEARCH, body=requests.Timeout("slow"))
    with pytest.raises(UpstreamUnavailable):
        resolve("Somewhere unusual")
