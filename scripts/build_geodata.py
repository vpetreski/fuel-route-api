"""Build the offline geography files in data/ from US Census sources.

    python scripts/build_geodata.py            # download sources into a temp dir
    python scripts/build_geodata.py --source-dir ~/Downloads/census

Outputs (committed, so the app never needs these downloads at runtime):
    data/us_places.csv.gz     places and minor civil divisions: state, name, lat, lon, rank
    data/us_zips.csv.gz       ZIP code tabulation areas: zip, lat, lon
    data/us_boundary.json.gz  state outlines (1:500,000, simplified) for point-in-USA checks

A place's Census "internal point" can sit offshore: San Francisco's is on the Farallon Islands, part of
the city. Those few places are re-anchored with one Nominatim lookup each (cached in --source-dir).

Needs `pyshp` (dev dependency) to read the boundary shapefile.
"""

import argparse
import csv
import gzip
import io
import itertools
import json
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from geo.boundary import Boundary  # noqa: E402
from geo.simplify import simplify  # noqa: E402

GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_{}_national.zip"
BOUNDARY = "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_state_500k.zip"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "fuel-route-api build script (+https://github.com/vpetreski/fuel-route-api)"
BOUNDARY_TOLERANCE_DEGREES = 0.0006  # ~60 m
MIN_ISLAND_DEGREES = 0.003  # islets smaller than ~300 m are dropped
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Trailing legal/statistical descriptors in Gazetteer names ("Abbeville city", "Abanda CDP").
PLACE_SUFFIX = re.compile(
    r"\s+(city and borough|(consolidated|metro|metropolitan|unified) government|urban county|"
    r"city|town|village|borough|municipality|cdp|comunidad|zona urbana|corporation)$",
    re.IGNORECASE,
)
BALANCE = re.compile(r"\s*\(balance\)$", re.IGNORECASE)
# Minor civil divisions worth keeping: real towns and townships, not statistical areas (CCDs, barrios...).
COUSUB_KINDS = ("township", "town", "city", "borough", "village", "plantation")

RANK_INCORPORATED, RANK_CDP, RANK_COUSUB, RANK_ALIAS = 0, 1, 2, 3


def fetch(url: str, source_dir: Path) -> Path:
    target = source_dir / url.rsplit("/", 1)[-1]
    if not target.exists():
        print(f"downloading {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "fuel-route-api build script"})
        with urllib.request.urlopen(request, timeout=120) as response:
            target.write_bytes(response.read())
    return target


def read_gazetteer(path: Path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".txt"))
        text = archive.read(name).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    for row in reader:
        yield {key.strip(): (value or "").strip() for key, value in row.items()}


def place_rows(path: Path):
    for row in read_gazetteer(path):
        full = BALANCE.sub("", row["NAME"])
        # LSAD 00 means the name carries no descriptor ("Carson City" is the whole name).
        name = full if row["LSAD"] == "00" else PLACE_SUFFIX.sub("", full)
        rank = RANK_INCORPORATED if row["FUNCSTAT"] in {"A", "B", "C"} else RANK_CDP
        yield row["USPS"], name, row["INTPTLAT"], row["INTPTLONG"], rank
        # Consolidated governments carry compound names: "Nashville-Davidson", "Louisville/Jefferson County".
        short = re.split(r"[-/]", name)[0].strip()
        if short and short != name:
            yield row["USPS"], short, row["INTPTLAT"], row["INTPTLONG"], RANK_ALIAS
        # "Boise City" is usually written "Boise"; Hawaii's "Urban Honolulu" is "Honolulu".
        if name.endswith(" City") and len(name) > 5:
            yield row["USPS"], name[:-5], row["INTPTLAT"], row["INTPTLONG"], RANK_ALIAS
        if name.startswith("Urban "):
            yield row["USPS"], name[6:], row["INTPTLAT"], row["INTPTLONG"], RANK_ALIAS


def cousub_rows(path: Path):
    for row in read_gazetteer(path):
        if row["FUNCSTAT"] in {"S", "F", "N"}:
            continue
        words = row["NAME"].rsplit(" ", 1)
        if len(words) != 2 or words[1].lower() not in COUSUB_KINDS:
            continue
        name = re.sub(r"\s+charter$", "", words[0], flags=re.IGNORECASE)
        yield row["USPS"], name, row["INTPTLAT"], row["INTPTLONG"], RANK_COUSUB


def onshore(rows, boundary: Boundary, source_dir: Path):
    """Replace offshore internal points with the place's position from Nominatim."""
    cache_file = source_dir / "nominatim-anchors.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    fixed = 0
    for state, name, lat, lon, rank in rows:
        if not boundary.contains(float(lat), float(lon), tolerance_miles=0.25):
            key = f"{name}|{state}"
            if key not in cache:
                time.sleep(1.1)
                query = urllib.parse.urlencode(
                    {"city": name, "state": state, "countrycodes": "us", "format": "jsonv2", "limit": 1}
                )
                request = urllib.request.Request(f"{NOMINATIM}?{query}", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=30) as response:
                    hits = json.load(response)
                cache[key] = [hits[0]["lat"], hits[0]["lon"]] if hits else None
                cache_file.write_text(json.dumps(cache, indent=0))
            if cache[key] and boundary.contains(float(cache[key][0]), float(cache[key][1]), tolerance_miles=0.25):
                lat, lon = cache[key]
                fixed += 1
        yield state, name, lat, lon, rank
    print(f"re-anchored {fixed} offshore place points")


def write_places(places: Path, cousubs: Path, boundary: Boundary, source_dir: Path) -> int:
    rows = sorted({*place_rows(places), *cousub_rows(cousubs)}, key=lambda r: (r[0], r[1].lower(), r[4]))
    rows = list(onshore(rows, boundary, source_dir))
    with gzip.open(DATA_DIR / "us_places.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["state", "name", "latitude", "longitude", "rank"])
        writer.writerows(rows)
    return len(rows)


def write_zips(zcta: Path) -> int:
    rows = [(r["GEOID"], r["INTPTLAT"], r["INTPTLONG"]) for r in read_gazetteer(zcta)]
    with gzip.open(DATA_DIR / "us_zips.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["zip", "latitude", "longitude"])
        writer.writerows(rows)
    return len(rows)


def write_boundary(path: Path) -> Boundary:
    import shapefile  # pyshp

    rings = []
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp)
        reader = shapefile.Reader(str(next(Path(tmp).glob("*.shp"))))
        for shape in reader.shapes():
            points = np.asarray(shape.points)
            bounds = list(shape.parts) + [len(points)]
            for start, end in itertools.pairwise(bounds):
                ring = points[start:end]
                tiny = np.ptp(ring[:, 0]) < MIN_ISLAND_DEGREES and np.ptp(ring[:, 1]) < MIN_ISLAND_DEGREES
                if len(ring) < 4 or tiny:
                    continue
                ring = simplify(ring, BOUNDARY_TOLERANCE_DEGREES)
                if len(ring) >= 4:
                    rings.append(np.round(ring, 4).tolist())
    payload = {
        "source": BOUNDARY.rsplit("/", 1)[-1],
        "note": "US Census cartographic boundaries, states and territories, lon/lat rings (even-odd rule)",
        "rings": rings,
    }
    with gzip.open(DATA_DIR / "us_boundary.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    print(f"us_boundary.json.gz: {len(rings)} rings, {sum(len(r) for r in rings)} vertices")
    return Boundary([np.asarray(r) for r in rings])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", type=Path, help="directory holding (or receiving) the Census downloads")
    args = parser.parse_args()

    source_dir = args.source_dir or Path(tempfile.mkdtemp(prefix="census-"))
    source_dir.mkdir(parents=True, exist_ok=True)
    places = fetch(GAZETTEER.format("place"), source_dir)
    cousubs = fetch(GAZETTEER.format("cousubs"), source_dir)
    zcta = fetch(GAZETTEER.format("zcta"), source_dir)
    boundary = fetch(BOUNDARY, source_dir)

    usa = write_boundary(boundary)
    print(f"us_places.csv.gz: {write_places(places, cousubs, usa, source_dir)} names")
    print(f"us_zips.csv.gz: {write_zips(zcta)} ZIP codes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
