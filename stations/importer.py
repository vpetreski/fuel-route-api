"""Read and clean the OPIS truck stop price file.

The file has one row per (stop, price observation): 8,151 rows for 6,738 stops. Rows for the same
OPIS id share the address but not always the name ("PILOT TRAVEL CENTER #1243" / "PILOT #1243") or
the price (up to $0.90 apart), so they are grouped here and every price row is kept.
"""

import contextlib
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

COLUMNS = ("OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price")
CANADIAN_PROVINCES = frozenset({"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"})
PRICE_PLACES = Decimal("0.00000001")


@dataclass(frozen=True)
class PriceRow:
    rack_id: int
    retail_price: Decimal


@dataclass(frozen=True)
class StationRecord:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    prices: tuple[PriceRow, ...]

    @property
    def country(self) -> str:
        return "CA" if self.state in CANADIAN_PROVINCES else "US"

    @property
    def retail_price(self) -> Decimal:
        """Mean of the observed prices: one noisy row should not make a stop look cheap."""
        total = sum((p.retail_price for p in self.prices), Decimal(0))
        return (total / len(self.prices)).quantize(PRICE_PLACES)


def clean_text(value: str) -> str:
    """Collapse whitespace and repair UTF-8 text that was decoded as Windows-1252 ("Stuckeyâ€™s")."""
    value = " ".join(value.split())
    if "â€" in value or "Ã" in value:
        with contextlib.suppress(UnicodeError):
            value = value.encode("cp1252").decode("utf-8")
    return value


def read_stations(path: Path) -> list[StationRecord]:
    rows_by_stop: dict[int, list[dict[str, str]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = set(COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        seen = set()
        for line, raw in enumerate(reader, start=2):
            row = {key: clean_text(raw[key] or "") for key in COLUMNS}
            fingerprint = tuple(row.values())
            if fingerprint in seen:  # exact duplicate line
                continue
            seen.add(fingerprint)
            try:
                opis_id = int(row["OPIS Truckstop ID"])
                int(row["Rack ID"])
                price = Decimal(row["Retail Price"])
            except (ValueError, InvalidOperation):
                raise ValueError(f"{path}:{line}: unreadable id or price in {raw}") from None
            if not price.is_finite() or price <= 0:
                raise ValueError(f"{path}:{line}: price must be positive, got {row['Retail Price']}")
            rows_by_stop[opis_id].append(row)

    return [_merge(opis_id, rows) for opis_id, rows in sorted(rows_by_stop.items())]


def _merge(opis_id: int, rows: list[dict[str, str]]) -> StationRecord:
    first = rows[0]
    # Most frequent spelling of the name; ties go to the more descriptive (longer) one.
    names = Counter(row["Truckstop Name"] for row in rows)
    name = max(names, key=lambda n: (names[n], len(n)))
    prices = tuple(PriceRow(int(row["Rack ID"]), Decimal(row["Retail Price"])) for row in rows)
    return StationRecord(
        opis_id=opis_id,
        name=name,
        address=first["Address"],
        city=first["City"],
        state=first["State"].upper(),
        prices=prices,
    )
