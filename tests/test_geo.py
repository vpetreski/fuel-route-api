from pathlib import Path

import numpy as np
import pytest

from geo.boundary import Boundary
from geo.distance import haversine_miles
from geo.gazetteer import Gazetteer, name_key
from geo.polyline import decode
from geo.simplify import simplify

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def gazetteer():
    return Gazetteer(DATA / "us_places.csv.gz", DATA / "us_zips.csv.gz")


@pytest.fixture(scope="module")
def usa():
    return Boundary.from_file(DATA / "us_boundary.json.gz")


@pytest.mark.parametrize(
    "query, label",
    [
        ("Chicago, IL", "Chicago, IL"),
        ("chicago,  illinois", "Chicago, IL"),
        ("Tomah WI", "Tomah, WI"),
        ("Kansas City Missouri", "Kansas City, MO"),
        ("Saint Louis, MO", "St. Louis, MO"),
        ("Ft. Worth, TX", "Fort Worth, TX"),
        ("Mc Calla, AL", "McCalla, AL"),
        ("Denver, CO 80202", "Denver, CO"),
        ("Houston, TX, USA", "Houston, TX"),
        ("Nashville, TN", "Nashville, TN"),
        ("Carson City, NV", "Carson City, NV"),
        ("Honolulu, HI", "Honolulu, HI"),
        ("Shrewsbury, MA", "Shrewsbury, MA"),
    ],
)
def test_place_lookup(gazetteer, query, label):
    assert gazetteer.lookup(query).label == label


@pytest.mark.parametrize("query", ["Springfield", "Toronto, ON", "Nowhere, TX", "", "  ,  "])
def test_place_lookup_misses(gazetteer, query):
    assert gazetteer.lookup(query) is None


def test_zip_lookup(gazetteer):
    place = gazetteer.lookup("10001")
    assert place.state == "" and place.name == "10001"
    assert haversine_miles(place.latitude, place.longitude, 40.7506, -73.9973) < 2
    assert gazetteer.lookup("10001-1234").name == "10001"


def test_city_points_are_on_land(gazetteer, usa):
    # The Census internal point of San Francisco is on the Farallon Islands; the build re-anchors it.
    for query in ["San Francisco, CA", "Corpus Christi, TX", "Portland, ME", "Santa Monica, CA"]:
        place = gazetteer.lookup(query)
        assert usa.contains(place.latitude, place.longitude, tolerance_miles=0.25), query


def test_name_key():
    assert name_key("St. Louis") == name_key("SAINT  LOUIS") == "saintlouis"
    assert name_key("Cañon City") == name_key("Canon City")
    assert name_key("De Forest") == name_key("DeForest")


@pytest.mark.parametrize(
    "lat, lon, expected",
    [
        (41.8781, -87.6298, True),  # Chicago
        (37.7793, -122.4193, True),  # San Francisco
        (24.5551, -81.7800, True),  # Key West
        (21.3069, -157.8583, True),  # Honolulu
        (61.2181, -149.9003, True),  # Anchorage
        (42.3314, -83.0458, True),  # Detroit
        (42.3149, -83.0364, False),  # Windsor, ON, across the river
        (32.5149, -117.0382, False),  # Tijuana
        (43.6532, -79.3832, False),  # Toronto
        (19.4326, -99.1332, False),  # Mexico City
        (35.0, -60.0, False),  # Atlantic
    ],
)
def test_boundary(usa, lat, lon, expected):
    assert usa.contains(lat, lon) is expected


def test_boundary_tolerance_absorbs_simplified_coastline(usa):
    pier = (34.0094, -118.4973)  # Santa Monica Pier, over the water
    assert not usa.contains(*pier)
    assert usa.contains(*pier, tolerance_miles=0.25)


def test_polyline_decode():
    # Example from the format documentation (precision 5).
    points = decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
    assert [(round(lat, 5), round(lon, 5)) for lat, lon in points] == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_simplify_drops_collinear_points_and_keeps_shape():
    line = np.array([[0, 0], [1, 0.0001], [2, 0], [3, 5], [4, 10]], dtype=float)
    simplified = simplify(line, tolerance=0.01)
    assert simplified.tolist() == [[0, 0], [2, 0], [4, 10]]


def test_haversine():
    assert haversine_miles(40.6413, -73.7781, 33.9416, -118.4085) == pytest.approx(2470, rel=0.005)  # JFK-LAX
