"""Load the truck stop price file and the station coordinates into the database."""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stations.importer import read_stations
from stations.index import reset_station_index
from stations.models import FuelPrice, TruckStop


class Command(BaseCommand):
    help = "Replace all truck stops and prices with the contents of the price file (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", type=Path, default=settings.DATA_DIR / "fuel-prices-for-be-assessment.csv")
        parser.add_argument("--locations", type=Path, default=settings.DATA_DIR / "station_locations.csv")

    def handle(self, *args, **options):
        try:
            records = read_stations(options["csv"])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        locations = self.read_locations(options["locations"])

        stops = []
        for record in records:
            lat, lon, precision = locations.get(record.opis_id, (None, None, ""))
            stops.append(
                TruckStop(
                    opis_id=record.opis_id,
                    name=record.name,
                    address=record.address,
                    city=record.city,
                    state=record.state,
                    country=record.country,
                    retail_price=record.retail_price,
                    latitude=lat,
                    longitude=lon,
                    location_precision=precision,
                )
            )

        with transaction.atomic():
            FuelPrice.objects.all().delete()
            TruckStop.objects.all().delete()
            TruckStop.objects.bulk_create(stops, batch_size=1000)
            ids = dict(TruckStop.objects.values_list("opis_id", "id"))
            FuelPrice.objects.bulk_create(
                (
                    FuelPrice(truck_stop_id=ids[record.opis_id], rack_id=row.rack_id, retail_price=row.retail_price)
                    for record in records
                    for row in record.prices
                ),
                batch_size=2000,
            )
        reset_station_index()

        us = [s for s in stops if s.country == "US"]
        located = [s for s in us if s.latitude is not None]
        exits = sum(1 for s in located if s.location_precision == TruckStop.Precision.EXIT)
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(stops):,} truck stops ({sum(len(r.prices) for r in records):,} price rows). "
                f"US: {len(us):,}, located {len(located):,} ({exits:,} at exit precision). "
                f"Outside the US (not used for planning): {len(stops) - len(us):,}."
            )
        )

    @staticmethod
    def read_locations(path: Path) -> dict[int, tuple[float, float, str]]:
        if not path.exists():
            return {}
        with open(path, newline="") as fh:
            return {
                int(row["opis_id"]): (float(row["latitude"]), float(row["longitude"]), row["precision"])
                for row in csv.DictReader(fh)
            }
