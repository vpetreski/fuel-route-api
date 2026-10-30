"""Load the in-memory data before the first request, so the first trip is as fast as the rest."""

import logging

from django.db import DatabaseError, connections

from stations.index import get_station_index

from .locations import gazetteer, usa

logger = logging.getLogger(__name__)


def warm_up() -> None:
    gazetteer().lookup("Chicago, IL")
    usa()
    try:
        stations = len(get_station_index())
    except DatabaseError:  # not migrated yet
        stations = 0
    connections.close_all()  # worker processes forked after this open their own
    logger.info("ready: %s stations loaded", stations)
