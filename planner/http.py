"""Outbound HTTP to the public OpenStreetMap services.

Their usage policies ask for an identifying User-Agent and no more than one request per second,
so all calls go through one session that sets the header and spaces requests per host.
"""

import threading
import time
from urllib.parse import urlsplit

import requests
from django.conf import settings

_session = requests.Session()
_lock = threading.Lock()
_last_request: dict[str, float] = {}


def _wait_turn(url: str) -> None:
    interval = settings.OUTBOUND_MIN_INTERVAL_SECONDS
    if interval <= 0:
        return
    host = urlsplit(url).netloc
    with _lock:
        delay = _last_request.get(host, float("-inf")) + interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last_request[host] = time.monotonic()


def request(method: str, url: str, **kwargs) -> requests.Response:
    """Send one request; raises requests.RequestException on network errors and timeouts."""
    _wait_turn(url)
    headers = {"User-Agent": settings.OUTBOUND_USER_AGENT, **kwargs.pop("headers", {})}
    return _session.request(method, url, headers=headers, timeout=settings.OUTBOUND_TIMEOUT_SECONDS, **kwargs)
