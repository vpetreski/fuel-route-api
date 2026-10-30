"""Find coordinates for every US truck stop and write data/station_locations.csv.

The price file has no coordinates and its addresses are highway descriptions ("I-44, EXIT 283 &
US-69"), which street geocoders cannot resolve. Two passes, best first:

1. exit: the address gives an exit number ("EXIT 283") and OpenStreetMap has a motorway junction
   with that number within 25 miles of the stop's town. Exit numbers are mileposts that restart in
   every state, so the town picks the right one; two exits with the same number that close
   together are rare, and the nearest one wins.
2. city: the town's centroid from the Census Gazetteer, or from Nominatim for the few
   unincorporated places the Gazetteer lacks.

One Overpass query (all numbered junctions) and about a hundred Nominatim lookups, so it is run
once and the output is committed; `import_fuel_prices` only reads the CSV. Responses are cached
in --cache-dir.
"""

import csv
import json
import re
import time
from pathlib import Path

import numpy as np
import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from geo.distance import haversine_miles
from geo.gazetteer import Gazetteer
from stations.importer import read_stations

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
JUNCTIONS_QUERY = '[out:json][timeout:600];node["highway"="motorway_junction"]["ref"](24.0,-125.0,49.8,-66.5);out;'
EXIT = re.compile(r"\bEXITS?\s*#?\s*(\d{1,3})\s*-?\s*([A-Z])?(?![A-Z0-9])", re.IGNORECASE)
MAX_EXIT_DISTANCE_MILES = 25.0


def normalize_exit(ref: str) -> str:
    return re.sub(r"[\s-]", "", ref).upper()


class Command(BaseCommand):
    help = __doc__.split("\n\n")[0]

    def add_arguments(self, parser):
        parser.add_argument("--csv", type=Path, default=settings.DATA_DIR / "fuel-prices-for-be-assessment.csv")
        parser.add_argument("--output", type=Path, default=settings.DATA_DIR / "station_locations.csv")
        parser.add_argument("--cache-dir", type=Path, default=settings.BASE_DIR / ".cache" / "locate")
        parser.add_argument("--no-exits", action="store_true", help="skip the OpenStreetMap exit pass")

    def handle(self, *args, **options):
        self.cache_dir = options["cache_dir"]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.OUTBOUND_USER_AGENT

        stations = [s for s in read_stations(options["csv"]) if s.country == "US"]
        gazetteer = Gazetteer(settings.DATA_DIR / "us_places.csv.gz", settings.DATA_DIR / "us_zips.csv.gz")

        towns = {}
        for station in stations:
            key = (station.city, station.state)
            if key not in towns:
                place = gazetteer.place(*key)
                towns[key] = (place.latitude, place.longitude) if place else self.nominatim_town(*key)

        located = {}
        for station in stations:
            if point := towns[(station.city, station.state)]:
                located[station.opis_id] = (*point, "city")

        if not options["no_exits"]:
            self.refine_exits(stations, located)

        with open(options["output"], "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["opis_id", "latitude", "longitude", "precision"])
            for opis_id in sorted(located):
                lat, lon, precision = located[opis_id]
                writer.writerow([opis_id, f"{lat:.6f}", f"{lon:.6f}", precision])

        exits = sum(1 for *_, p in located.values() if p == "exit")
        self.stdout.write(
            f"{len(stations)} US stops: {exits} at exit precision, {len(located) - exits} at town precision, "
            f"{len(stations) - len(located)} not located -> {options['output']}"
        )

    # --- town centroids for places missing from the Gazetteer -------------------------------------

    def nominatim_town(self, city: str, state: str):
        spellings = dict.fromkeys([city, re.sub(r"^Saint ", "St. ", city), re.sub(r"^Mc ", "Mc", city)])
        queries = [{"city": city, "state": state}] + [{"q": f"{name}, {state}"} for name in spellings]
        for query in queries:
            if point := self.nominatim(query):
                return point
        return None

    def nominatim(self, query: dict):
        cache = self.cache_dir / "nominatim.json"
        known = json.loads(cache.read_text()) if cache.exists() else {}
        key = json.dumps(query, sort_keys=True)
        if key not in known:
            time.sleep(settings.OUTBOUND_MIN_INTERVAL_SECONDS + 0.1)
            response = self.session.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={**query, "countrycodes": "us", "format": "jsonv2", "limit": 1},
                timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            hits = response.json()
            known[key] = [float(hits[0]["lat"]), float(hits[0]["lon"])] if hits else None
            cache.write_text(json.dumps(known, indent=0, sort_keys=True))
            self.stdout.write(f"nominatim {query}: {known[key]}")
        return tuple(known[key]) if known[key] else None

    # --- exit-level positions ---------------------------------------------------------------------

    def refine_exits(self, stations, located):
        refs, lats, lons = self.junctions()
        numbers = np.char.rstrip(refs, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        wanted = distances = 0
        spread = []
        for station in stations:
            match = EXIT.search(station.address)
            if not match or station.opis_id not in located:
                continue
            wanted += 1
            ref = normalize_exit(match.group(1) + (match.group(2) or ""))
            # "EXIT 144" may be mapped as 144A/144B: fall back to the number alone.
            mask = refs == ref
            if not mask.any():
                mask = numbers == match.group(1)
            if not mask.any():
                continue
            town_lat, town_lon, _ = located[station.opis_id]
            d = haversine_miles(town_lat, town_lon, lats[mask], lons[mask])
            best = int(np.argmin(d))
            if d[best] <= MAX_EXIT_DISTANCE_MILES:
                located[station.opis_id] = (float(lats[mask][best]), float(lons[mask][best]), "exit")
                distances += 1
                spread.append(float(d[best]))
        if spread:
            q = np.percentile(spread, [50, 90, 99])
            self.stdout.write(
                f"exit matches: {distances} of {wanted} addresses with an exit number; distance from the town "
                f"centroid: median {q[0]:.1f} mi, p90 {q[1]:.1f}, p99 {q[2]:.1f}"
            )

    def junctions(self):
        """Every numbered motorway junction in the contiguous US: (refs, latitudes, longitudes)."""
        cache = self.cache_dir / "overpass-junctions.json"
        if not cache.exists():
            for attempt in range(4):
                response = self.session.post(OVERPASS_URL, data={"data": JUNCTIONS_QUERY}, timeout=660)
                if response.status_code == 200:
                    cache.write_text(response.text)
                    break
                self.stdout.write(f"overpass: HTTP {response.status_code}, retrying")
                time.sleep(60 * (attempt + 1))
            else:
                raise RuntimeError("Overpass did not answer")
        refs, lats, lons = [], [], []
        for element in json.loads(cache.read_text())["elements"]:
            for ref in element["tags"]["ref"].split(";"):
                refs.append(normalize_exit(ref))
                lats.append(element["lat"])
                lons.append(element["lon"])
        self.stdout.write(f"{len(refs)} numbered motorway junctions")
        return np.array(refs, dtype=str), np.array(lats), np.array(lons)
