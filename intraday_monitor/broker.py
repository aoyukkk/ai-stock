from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from threading import Condition
from time import monotonic
from typing import Iterator


class RequestPriority(IntEnum):
    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4


class BrokerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerLease:
    priority: RequestPriority
    purpose: str
    waited_ms: int


class MarketDataRequestBroker:
    """Process-local serial broker; P0 blocks new monitor work and gets the next lease."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._active = False
        self._p0_waiters = 0
        self._midday_reserved = False
        self._cancel_epoch = 0
        self._stats = {"granted": 0, "rejected": 0, "preempted": 0, "p0_wait_ms": 0}

    @contextmanager
    def request(self, priority: RequestPriority, purpose: str, *, timeout_seconds: float = 20) -> Iterator[BrokerLease]:
        started = monotonic()
        with self._condition:
            epoch = self._cancel_epoch
            if priority >= RequestPriority.P2 and (self._midday_reserved or self._p0_waiters):
                self._stats["rejected"] += 1
                raise BrokerUnavailable("MIDDAY_RESOURCE_PREEMPTION")
            if priority is RequestPriority.P0:
                self._p0_waiters += 1
                self._midday_reserved = True
                self._cancel_epoch += 1
                self._stats["preempted"] += 1
            try:
                while self._active or (priority > RequestPriority.P0 and self._p0_waiters):
                    remaining = timeout_seconds - (monotonic() - started)
                    if remaining <= 0:
                        self._stats["rejected"] += 1
                        raise BrokerUnavailable("PROVIDER_BROKER_TIMEOUT")
                    self._condition.wait(remaining)
                if priority >= RequestPriority.P2 and epoch != self._cancel_epoch:
                    self._stats["rejected"] += 1
                    raise BrokerUnavailable("MIDDAY_RESOURCE_PREEMPTION")
                self._active = True
                waited_ms = int((monotonic() - started) * 1000)
                self._stats["granted"] += 1
                if priority is RequestPriority.P0:
                    self._stats["p0_wait_ms"] = waited_ms
            finally:
                if priority is RequestPriority.P0:
                    self._p0_waiters -= 1
            try:
                yield BrokerLease(priority, purpose, waited_ms)
            finally:
                self._active = False
                self._condition.notify_all()

    def reserve_midday(self) -> None:
        with self._condition:
            self._midday_reserved = True
            self._cancel_epoch += 1
            self._condition.notify_all()

    def release_midday(self) -> None:
        with self._condition:
            self._midday_reserved = False
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int | bool]:
        with self._condition:
            return {**self._stats, "active": self._active, "midday_reserved": self._midday_reserved, "p0_waiters": self._p0_waiters}


market_data_broker = MarketDataRequestBroker()
