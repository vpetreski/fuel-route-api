"""The located US truck stops as numpy arrays, loaded once per process."""

import threading
from dataclasses import dataclass

import numpy as np

from .models import TruckStop


@dataclass(frozen=True)
class StationIndex:
    ids: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    price: np.ndarray
    details: list[dict]  # name, address, city, state, precision; same order as the arrays

    def __len__(self) -> int:
        return len(self.ids)

    @classmethod
    def from_database(cls) -> "StationIndex":
        rows = list(
            TruckStop.objects.filter(country="US", latitude__isnull=False)
            .order_by("opis_id")
            .values_list(
                "opis_id",
                "name",
                "address",
                "city",
                "state",
                "latitude",
                "longitude",
                "retail_price",
                "location_precision",
            )
        )
        return cls(
            ids=np.array([r[0] for r in rows], dtype=np.int64),
            latitude=np.array([r[5] for r in rows], dtype=float),
            longitude=np.array([r[6] for r in rows], dtype=float),
            price=np.array([float(r[7]) for r in rows], dtype=float),
            details=[
                {"opis_id": r[0], "name": r[1], "address": r[2], "city": r[3], "state": r[4], "precision": r[8]}
                for r in rows
            ],
        )


_lock = threading.Lock()
_index: StationIndex | None = None


def get_station_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                index = StationIndex.from_database()
                if not len(index):
                    return index  # not imported yet: look again next time
                _index = index
    return _index


def reset_station_index() -> None:
    """Forget the loaded stations (after a re-import, and between tests)."""
    global _index
    with _lock:
        _index = None
