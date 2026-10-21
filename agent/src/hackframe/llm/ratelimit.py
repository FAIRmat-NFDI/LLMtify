"""Client-side rate limiting at the HTTP layer.

Every request a chat client makes (incl. each step of a tool-calling loop) passes
through this transport, so limits hold even when workflows fan out in parallel.
Used for shared endpoints like the HU Berlin LLMs (no parallel requests, ~10-15 RPM).
"""

import asyncio
import time
from collections import deque

import httpx


class RateLimitedTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        rpm: int | None = None,
        max_concurrency: int | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.rpm = rpm
        self.max_concurrency = max_concurrency
        self.inner = inner or httpx.AsyncHTTPTransport(retries=2)
        self._sent: deque[float] = deque()
        self._sem: asyncio.Semaphore | None = None
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _primitives(self) -> tuple[asyncio.Semaphore | None, asyncio.Lock]:
        loop = asyncio.get_running_loop()
        if loop is not self._loop:  # (re)create per event loop
            self._loop = loop
            self._lock = asyncio.Lock()
            self._sem = asyncio.Semaphore(self.max_concurrency) if self.max_concurrency else None
        assert self._lock is not None
        return self._sem, self._lock

    async def _wait_for_rpm_slot(self, lock: asyncio.Lock) -> None:
        if not self.rpm:
            return
        async with lock:
            while True:
                now = time.monotonic()
                while self._sent and now - self._sent[0] >= 60:
                    self._sent.popleft()
                if len(self._sent) < self.rpm:
                    self._sent.append(now)
                    return
                await asyncio.sleep(60 - (now - self._sent[0]) + 0.05)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        sem, lock = self._primitives()
        if sem is None:
            await self._wait_for_rpm_slot(lock)
            return await self.inner.handle_async_request(request)
        async with sem:
            await self._wait_for_rpm_slot(lock)
            response = await self.inner.handle_async_request(request)
            await response.aread()  # finish the body before releasing the slot
            return response

    async def aclose(self) -> None:
        await self.inner.aclose()
