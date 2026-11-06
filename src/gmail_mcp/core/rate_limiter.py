"""Weighted token-bucket rate limiter and operation-class concurrency controller."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from gmail_mcp.config import GmailConfig
from gmail_mcp.core.quota_manager import QuotaManager

OperationClass = Literal["read", "write", "send"]


class WeightedTokenBucket:
    """In-memory weighted token bucket replenishing continuously up to capacity."""

    def __init__(self, capacity_per_minute: int) -> None:
        self.capacity = float(max(1, capacity_per_minute))
        self.tokens = float(self.capacity)
        self.refill_rate_per_sec = self.capacity / 60.0
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int, max_wait_seconds: float = 15.0) -> float:
        """Consume `weight` tokens, waiting if needed up to `max_wait_seconds`."""
        needed = float(max(1, weight))
        if needed > self.capacity:
            raise ValueError(f"Requested token weight ({needed}) exceeds bucket capacity ({self.capacity}).")

        waited_total = 0.0
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self.last_refill)
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate_per_sec)
                self.last_refill = now

                if self.tokens >= needed:
                    self.tokens -= needed
                    return waited_total

                deficit = needed - self.tokens
                sleep_needed = deficit / self.refill_rate_per_sec

            if waited_total + sleep_needed > max_wait_seconds:
                raise TimeoutError(
                    f"Rate limiter token bucket exhausted: needed {needed} units, "
                    f"require {sleep_needed:.2f}s wait (max_wait_seconds={max_wait_seconds}s)."
                )
            step = min(sleep_needed, 0.5)
            await asyncio.sleep(step)
            waited_total += step


class RateLimiter:
    """Coordinates operation-class semaphores, weighted token buckets, and outgoing send pacing."""

    def __init__(self, config: GmailConfig, quota_manager: QuotaManager) -> None:
        self.config = config
        self.quota_manager = quota_manager

        self.user_bucket = WeightedTokenBucket(config.effective_user_quota_per_minute)
        self.project_bucket = WeightedTokenBucket(config.effective_project_quota_per_minute)
        self.attachment_bandwidth_bucket = WeightedTokenBucket(
            int(config.attachment_max_bytes * 4)  # 100 MB/min attachment throughput guard
        )

        self._read_sem = asyncio.Semaphore(max(1, config.read_concurrency))
        self._write_sem = asyncio.Semaphore(max(1, config.write_concurrency))
        self._send_sem = asyncio.Semaphore(max(1, config.send_concurrency))
        self._send_pacing_lock = asyncio.Lock()
        self._last_send_monotonic: float | None = None
        self._active_counts: dict[str, int] = {"read": 0, "write": 0, "send": 0}
        self._peak_counts: dict[str, int] = {"read": 0, "write": 0, "send": 0}

    def _get_semaphore(self, op_class: OperationClass) -> asyncio.Semaphore:
        if op_class == "send":
            return self._send_sem
        if op_class == "write":
            return self._write_sem
        return self._read_sem

    @asynccontextmanager
    async def throttle(
        self,
        method: str,
        op_class: OperationClass = "read",
        fmt: str | None = None,
        units_override: int | None = None,
        attachment_bytes: int = 0,
        enforce_send_interval: bool = True,
    ) -> AsyncIterator[int]:
        """Acquire operation-class concurrency slot, token bucket units, and persistent SQLite quota record."""
        sem = self._get_semaphore(op_class)
        async with sem:
            self._active_counts[op_class] += 1
            if self._active_counts[op_class] > self._peak_counts[op_class]:
                self._peak_counts[op_class] = self._active_counts[op_class]
            try:
                if op_class == "send" and enforce_send_interval and self.config.send_interval_seconds > 0:
                    async with self._send_pacing_lock:
                        if self._last_send_monotonic is not None:
                            elapsed = time.monotonic() - self._last_send_monotonic
                            remaining = self.config.send_interval_seconds - elapsed
                            if remaining > 0:
                                await asyncio.sleep(remaining)
                        self._last_send_monotonic = time.monotonic()

                cost_est = self.quota_manager.estimate_cost(method, count=1, fmt=fmt)["total_units"]
                units = units_override if units_override is not None else cost_est

                await self.user_bucket.acquire(units)
                await self.project_bucket.acquire(units)
                if attachment_bytes > 0:
                    await self.attachment_bandwidth_bucket.acquire(
                        min(attachment_bytes, int(self.attachment_bandwidth_bucket.capacity))
                    )

                consumed = self.quota_manager.check_and_consume_quota(method, units=units, fmt=fmt)
                yield consumed
            finally:
                self._active_counts[op_class] -= 1

    @property
    def concurrency_metrics(self) -> dict[str, dict[str, int]]:
        return {
            "active": dict(self._active_counts),
            "peak": dict(self._peak_counts),
            "configured_limits": {
                "read": self.config.read_concurrency,
                "write": self.config.write_concurrency,
                "send": self.config.send_concurrency,
            },
        }
