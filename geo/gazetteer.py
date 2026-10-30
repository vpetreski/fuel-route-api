"""Offline lookup of US places and ZIP codes, built from the US Census Gazetteer files."""

import csv
import gzip
import re
import unicodedata
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "PR": "Puerto Rico", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}  # fmt: skip
_STATE_BY_NAME = {name.lower(): code for code, name in STATES.items()}

_ABBREVIATIONS = (
    (re.compile(r"^n\.?\s+"), "north "),
    (re.compile(r"^s\.?\s+"), "south "),
    (re.compile(r"^e\.?\s+"), "east "),
    (re.compile(r"^w\.?\s+"), "west "),
    (re.compile(r"\bst\.?\s+"), "saint "),
    (re.compile(r"\bste\.?\s+"), "sainte "),
    (re.compile(r"\bft\.?\s+"), "fort "),
    (re.compile(r"\bmt\.?\s+"), "mount "),
)
_COUNTRY_SUFFIX = re.compile(r",?\s*(usa|u\.s\.a\.|us|u\.s\.|united states( of america)?)\s*$", re.IGNORECASE)
_ZIP = re.compile(r"^(\d{5})(?:-\d{4})?$")


def name_key(name: str) -> str:
    """Canonical key for matching place names: "St. Louis", "Saint Louis" and "SAINT  LOUIS" agree.

    Spaces are dropped as well, so "De Forest"/"DeForest" and "Mc Calla"/"McCalla" match.
    """
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.strip().lower().replace("&", " and ")
    text = re.sub(r"[-/]", " ", text)
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    return re.sub(r"[^a-z0-9]", "", text)


def state_code(text: str) -> str | None:
    text = text.strip().rstrip(".")
    if text.upper() in STATES:
        return text.upper()
    return _STATE_BY_NAME.get(" ".join(text.lower().split()))


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


class Gazetteer:
    """In-memory index over data/us_places.csv.gz and data/us_zips.csv.gz (loaded on first use)."""

    def __init__(self, places_path: Path, zips_path: Path):
        self.places_path = Path(places_path)
        self.zips_path = Path(zips_path)

    @cached_property
    def _places(self) -> dict[tuple[str, str], Place]:
        index: dict[tuple[str, str], tuple[int, Place]] = {}
        with gzip.open(self.places_path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (name_key(row["name"]), row["state"])
                rank = int(row["rank"])
                if key not in index or rank < index[key][0]:
                    place = Place(row["name"], row["state"], float(row["latitude"]), float(row["longitude"]))
                    index[key] = (rank, place)
        return {key: place for key, (_, place) in index.items()}

    @cached_property
    def _zips(self) -> dict[str, Place]:
        with gzip.open(self.zips_path, "rt", encoding="utf-8", newline="") as fh:
            return {
                row["zip"]: Place(row["zip"], "", float(row["latitude"]), float(row["longitude"]))
                for row in csv.DictReader(fh)
            }

    def place(self, name: str, state: str) -> Place | None:
        return self._places.get((name_key(name), state.upper()))

    def zip_code(self, code: str) -> Place | None:
        match = _ZIP.match(code.strip())
        return self._zips.get(match.group(1)) if match else None

    def lookup(self, query: str) -> Place | None:
        """Resolve "City, ST", "City, State", "City ST" or a ZIP code; None when not recognized."""
        text = _COUNTRY_SUFFIX.sub("", " ".join(query.split())).strip(" ,")
        if not text:
            return None
        if place := self.zip_code(text):
            return place

        if "," in text:
            city, _, rest = text.rpartition(",")
            # "Denver, CO 80202" -> state "CO"
            rest_words = rest.split()
            if rest_words and _ZIP.match(rest_words[-1]):
                rest_words = rest_words[:-1]
            state = state_code(" ".join(rest_words))
            if state and city.strip():
                return self.place(city, state)
            return None

        words = text.split()
        for size in (2, 1):  # "Kansas City Missouri", "Kansas City New York"... and "Tomah WI"
            state = state_code(" ".join(words[-size:])) if len(words) > size else None
            if state and (place := self.place(" ".join(words[:-size]), state)):
                return place
        return None
