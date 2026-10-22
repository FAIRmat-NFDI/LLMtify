"""RateLimitedTransport: serial requests + RPM cap (no network)."""

import asyncio
import time

import httpx

from matgentify.llm.ratelimit import RateLimitedTransport


async def test_max_concurrency_one_serializes_requests() -> None:
    active, peak = 0, 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json={})

    transport = RateLimitedTransport(max_concurrency=1, inner=httpx.MockTransport(slow))
    async with httpx.AsyncClient(transport=transport) as c:
        await asyncio.gather(*(c.get("http://x/") for _ in range(4)))
    assert peak == 1


async def test_rpm_window_blocks_extra_requests(monkeypatch) -> None:
    transport = RateLimitedTransport(rpm=2, inner=httpx.MockTransport(lambda r: httpx.Response(200)))
    async with httpx.AsyncClient(transport=transport) as c:
        await c.get("http://x/")
        await c.get("http://x/")
        # third request must wait for the 60 s window -> times out here
        try:
            await asyncio.wait_for(c.get("http://x/"), timeout=0.2)
            raise AssertionError("3rd request should have been delayed")
        except asyncio.TimeoutError:
            pass
        # pretend the window passed
        transport._sent = type(transport._sent)(t - 61 for t in transport._sent)
        start = time.monotonic()
        await c.get("http://x/")
        assert time.monotonic() - start < 0.5
