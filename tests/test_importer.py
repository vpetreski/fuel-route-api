import io
from decimal import Decimal

import pytest
from django.core.management import call_command

from stations.importer import clean_text, read_stations
from stations.models import FuelPrice, TruckStop

HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"


@pytest.fixture
def price_file(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        HEADER
        + '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.899\n'
        + '20,PILOT #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.699\n'
        + '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.899\n'
        + '71108,Stuckeyâ€™s Travel Center West,"I-10, EXIT 819 & FM-1663",Anahuac     ,TX,610,2.80733333\n'
        + "900,PETRO-CANADA,HWY 401,London,ON,1,4.5\n",
        encoding="utf-8",
    )
    return path


def test_groups_rows_by_stop(price_file):
    pilot, petro, stuckeys = read_stations(price_file)
    assert pilot.opis_id == 20
    assert pilot.name == "PILOT TRAVEL CENTER #1243"  # most frequent spelling
    assert len(pilot.prices) == 2  # the exact duplicate line is dropped
    assert pilot.retail_price == Decimal("3.79900000")  # mean of 3.899 and 3.699


def test_cleans_text_and_flags_canada(price_file):
    _, petro, stuckeys = read_stations(price_file)
    assert stuckeys.name == "Stuckey’s Travel Center West"
    assert stuckeys.city == "Anahuac"
    assert stuckeys.country == "US"
    assert petro.country == "CA"


def test_clean_text():
    assert clean_text("  KWIK   TRIP  ") == "KWIK TRIP"
    assert clean_text("Café") == "Café"


def test_rejects_bad_rows(tmp_path):
    bad_price = tmp_path / "bad.csv"
    bad_price.write_text(HEADER + "1,X,Y,Z,TX,1,free\n")
    with pytest.raises(ValueError, match="bad.csv:2"):
        read_stations(bad_price)
    negative = tmp_path / "negative.csv"
    negative.write_text(HEADER + "1,X,Y,Z,TX,1,-3\n")
    with pytest.raises(ValueError, match="positive"):
        read_stations(negative)
    missing = tmp_path / "missing.csv"
    missing.write_text("OPIS Truckstop ID,Retail Price\n1,3.0\n")
    with pytest.raises(ValueError, match="missing columns"):
        read_stations(missing)


@pytest.mark.django_db
def test_import_command_is_idempotent(price_file, tmp_path):
    locations = tmp_path / "locations.csv"
    locations.write_text("opis_id,latitude,longitude,precision\n20,32.95,-112.72,exit\n")
    for _ in range(2):
        call_command("import_fuel_prices", csv=price_file, locations=locations, stdout=io.StringIO())
    assert TruckStop.objects.count() == 3
    assert FuelPrice.objects.count() == 4
    pilot = TruckStop.objects.get(opis_id=20)
    assert (pilot.latitude, pilot.longitude, pilot.location_precision) == (32.95, -112.72, "exit")
    assert TruckStop.objects.get(opis_id=71108).latitude is None


@pytest.mark.django_db
def test_real_price_file_imports():
    call_command("import_fuel_prices", stdout=io.StringIO())
    assert TruckStop.objects.count() == 6738
    us = TruckStop.objects.filter(country="US")
    assert us.count() == 6626
    assert us.filter(latitude__isnull=False).count() >= 6600
