"""Rate limiting and transient retries for memory inference calls."""

import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from azure.core.exceptions import (
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)

logger = logging.getLogger(__name__)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
Result = TypeVar("Result")


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


def call_with_retries[Result](
    fn: Callable[[], Result], description: str, limiter: RateLimiter
) -> Result:
    last_exc: Exception = RuntimeError(f"{description}: no attempts made")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        limiter.acquire()
        try:
            return fn()
        except HttpResponseError as exc:
            if exc.status_code not in RETRYABLE_STATUS:
                logger.error("%s failed (non-retryable): %s", description, exc)
                raise
            last_exc = exc
        except (ServiceRequestError, ServiceResponseError) as exc:
            last_exc = exc
        if attempt < MAX_ATTEMPTS:
            wait = 2 ** (attempt - 1)
            logger.warning("%s failed, retrying in %ds: %s", description, wait, last_exc)
            time.sleep(wait)
    logger.error("%s failed after %d attempts: %s", description, MAX_ATTEMPTS, last_exc)
    raise last_exc
