"""Rate limiting and transient retries for outbound model calls."""

import logging
import threading
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 30  # never sleep longer than this on a server's request


class RateLimiter:
    def __init__(self, rate: float, capacity: float):
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens < 1:
                time.sleep((1 - self._tokens) / self._rate)
                self._tokens = 0
            else:
                self._tokens -= 1


def _retry_after(exc: httpx.HTTPStatusError) -> float | None:
    value = exc.response.headers.get("retry-after", "")
    return min(float(value), MAX_RETRY_AFTER) if value.replace(".", "", 1).isdigit() else None


def call_with_retries[Result](
    fn: Callable[[], Result], description: str, limiter: RateLimiter
) -> Result:
    """Run `fn`, retrying timeouts, connection errors and 429/5xx answers with backoff.

    Any other HTTP error (a bad request, a rejected key) is raised at once: retrying cannot help.
    """
    last_exc: Exception = RuntimeError(f"{description}: no attempts made")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        limiter.acquire()
        wait = 2 ** (attempt - 1)
        try:
            return fn()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in RETRYABLE_STATUS:
                logger.error("%s failed (non-retryable): %s", description, exc)
                raise
            last_exc = exc
            wait = _retry_after(exc) or wait
        except httpx.TransportError as exc:  # timeouts, refused connections, dropped streams
            last_exc = exc
        if attempt < MAX_ATTEMPTS:
            logger.warning("%s failed, retrying in %ss: %s", description, wait, last_exc)
            time.sleep(wait)
    logger.error("%s failed after %d attempts: %s", description, MAX_ATTEMPTS, last_exc)
    raise last_exc
