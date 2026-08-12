"""
rate_limit.py
-------------
Central SlowAPI limiter for the StudySync backend.

The API binds to 127.0.0.1 and is only ever reached through the Caddy
reverse proxy, so ``request.client.host`` is always ``127.0.0.1`` and useless
for identifying clients. This key function therefore uses the first
``X-Forwarded-For`` hop (added by Caddy) and falls back to the socket peer
for direct/local connections.

A default per-client limit protects every route; expensive or
brute-force-prone endpoints (sync push, PDF generation) are decorated with
tighter limits in their routers. The default can be raised/lowered through
``STUDYSYNC_RATE_LIMIT_PER_MINUTE`` (used by the test suite to avoid
tripping the limiter while hammering a TestClient).
"""

import logging
import os

from fastapi import Request
from slowapi import Limiter


def _client_key(request: Request) -> str:
    """Identify the real client for rate-limit accounting.

    Caddy is the only reverse proxy and always sets ``X-Forwarded-For``, so
    the first hop is the browser/device. For direct connections (local dev)
    use the socket peer address.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


# 120 requests / minute / client is generous for a front-desk app (the
# attendance page and dashboard poll every 5 s, i.e. ~24/min) while still
# catching runaway loops, scraping and key brute-forcing.
DEFAULT_LIMIT = os.environ.get("STUDYSYNC_RATE_LIMIT_PER_MINUTE", "120/minute")

# Validate the configured limit string at startup. ``limits.parse_many``
# raises ValueError for anything that isn't a valid "<count>/<granularity>"
# string (e.g. a bare number, which a mis-typed .env could contain). slowapi
# does not handle that error gracefully -- it funnels it through the 429
# handler and crashes with "'ValueError' object has no attribute 'detail'"
# on *every* request. Fall back to the default so a bad value can never take
# the whole API down.
try:
    from limits import parse_many as _validate_limit_string

    _validate_limit_string(DEFAULT_LIMIT)
except ValueError:
    logging.getLogger("studysync").warning(
        "Invalid STUDYSYNC_RATE_LIMIT_PER_MINUTE=%r (expected e.g. '120/minute'); "
        "falling back to the default limit.",
        DEFAULT_LIMIT,
    )
    DEFAULT_LIMIT = "120/minute"

limiter = Limiter(key_func=_client_key, default_limits=[DEFAULT_LIMIT])
