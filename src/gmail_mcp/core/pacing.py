"""Anti-spam outbound pacing and corporate domain clustering guards."""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from email.utils import getaddresses
from typing import Any, TypeVar

T = TypeVar("T")


def extract_domain(recipient: str | list[str] | None) -> str:
    """Extract normalized lowercase domain from an email address or header string."""
    if not recipient:
        return ""
    raw = ", ".join(recipient) if isinstance(recipient, list) else str(recipient)
    for _, addr in getaddresses([raw]):
        if "@" in addr:
            return addr.rsplit("@", 1)[-1].strip().lower()
    if "@" in raw:
        return raw.rsplit("@", 1)[-1].strip(" <>\"'").lower()
    return ""


def interleave_by_domain(items: list[T], domain_fn: Callable[[T], str]) -> list[T]:
    """Reorder items round-robin by recipient domain to maximize spacing between same-company sends."""
    buckets: dict[str, deque[T]] = defaultdict(deque)
    domain_order: list[str] = []
    for item in items:
        dom = domain_fn(item) or "_unknown"
        if dom not in buckets:
            domain_order.append(dom)
        buckets[dom].append(item)

    interleaved: list[T] = []
    active = deque(domain_order)
    while active:
        dom = active.popleft()
        q = buckets[dom]
        if q:
            interleaved.append(q.popleft())
            if q:
                active.append(dom)
    return interleaved


class NonBlockingPacer:
    """
    Enforces anti-spam sending velocity and per-domain hourly clustering limits:
    - Max `max_per_hour` (default 40) total sends in any rolling `window_seconds` (3600s).
    - Max `max_per_domain_per_hour` (default 2) sends per recipient domain in any rolling `window_seconds` (3600s).
    - Randomized human-like inter-send jitter (`min_jitter_seconds`..`max_jitter_seconds`).
    - Dynamic domain interleaving so other domains proceed while one domain cools down.
    """

    def __init__(
        self,
        *,
        max_per_hour: int = 40,
        max_per_domain_per_hour: int = 2,
        window_seconds: float = 3600.0,
        min_jitter_seconds: float = 60.0,
        max_jitter_seconds: float = 120.0,
        history: list[tuple[str, float]] | None = None,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.max_per_hour = max(1, int(max_per_hour))
        self.max_per_domain_per_hour = max(1, int(max_per_domain_per_hour))
        self.window_seconds = max(1.0, float(window_seconds))
        self.min_jitter_seconds = max(0.0, float(min_jitter_seconds))
        self.max_jitter_seconds = max(self.min_jitter_seconds, float(max_jitter_seconds))
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn

        self._global_ts: deque[float] = deque()
        self._domain_ts: dict[str, deque[float]] = defaultdict(deque)
        for recip, ts in sorted(history or [], key=lambda x: x[1]):
            dom = extract_domain(recip)
            self._global_ts.append(ts)
            if dom:
                self._domain_ts[dom].append(ts)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._global_ts and self._global_ts[0] <= cutoff:
            self._global_ts.popleft()
        for q in list(self._domain_ts.values()):
            while q and q[0] <= cutoff:
                q.popleft()

    def _jitter(self) -> float:
        if self.max_jitter_seconds <= 0.0:
            return 0.0
        return random.uniform(self.min_jitter_seconds, self.max_jitter_seconds)

    async def run_paced_queue(
        self,
        items: list[T],
        *,
        domain_fn: Callable[[T], str],
        send_fn: Callable[[T], Awaitable[Any]],
        should_stop_fn: Callable[[], bool] | None = None,
    ) -> None:
        """Process `items` respecting rolling hourly global & per-domain caps with round-robin interleaving."""
        pending = deque(interleave_by_domain(items, domain_fn))
        sent_in_batch = 0

        while pending:
            if should_stop_fn and should_stop_fn():
                break

            now = self._time_fn()
            self._prune(now)

            # 1. Check global hourly cap
            if len(self._global_ts) >= self.max_per_hour:
                wait_sec = max(0.1, (self._global_ts[0] + self.window_seconds) - now) + (self._jitter() * 0.25)
                await self._sleep_fn(wait_sec)
                continue

            # 2. Pick first pending item whose domain has < max_per_domain_per_hour in rolling window
            chosen_idx: int | None = None
            for idx, candidate in enumerate(pending):
                dom = domain_fn(candidate)
                if not dom or len(self._domain_ts[dom]) < self.max_per_domain_per_hour:
                    chosen_idx = idx
                    break

            if chosen_idx is None:
                # All remaining items are at domains currently at their 2/hr cap; sleep until earliest opens
                earliest_unlock: float | None = None
                for candidate in pending:
                    dom = domain_fn(candidate)
                    q = self._domain_ts.get(dom)
                    if q:
                        unlock_at = q[0] + self.window_seconds
                        if earliest_unlock is None or unlock_at < earliest_unlock:
                            earliest_unlock = unlock_at
                wait_sec = max(0.1, (earliest_unlock - now) if earliest_unlock else 1.0) + (self._jitter() * 0.25)
                await self._sleep_fn(wait_sec)
                continue

            # 3. Apply human-like inter-send jitter between sends in this batch
            if sent_in_batch > 0 and self.max_jitter_seconds > 0.0:
                await self._sleep_fn(self._jitter())
                now = self._time_fn()
                self._prune(now)

            # Pop the chosen item
            if chosen_idx == 0:
                item = pending.popleft()
            else:
                pending.rotate(-chosen_idx)
                item = pending.popleft()
                pending.rotate(chosen_idx)

            await send_fn(item)
            sent_at = self._time_fn()
            dom = domain_fn(item)
            self._global_ts.append(sent_at)
            if dom:
                self._domain_ts[dom].append(sent_at)
            sent_in_batch += 1
