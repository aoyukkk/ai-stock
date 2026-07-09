from __future__ import annotations

from collections import deque

from alerts.schemas import AlertEvent


class AlertStore:
    def __init__(self, max_size: int = 1000) -> None:
        self._events: deque[AlertEvent] = deque(maxlen=max_size)

    def add(self, event: AlertEvent) -> AlertEvent:
        self._events.appendleft(event)
        return event

    def add_many(self, events: list[AlertEvent]) -> list[AlertEvent]:
        for event in reversed(events):
            self._events.appendleft(event)
        return events

    def recent(self, limit: int = 50) -> list[AlertEvent]:
        return list(self._events)[:limit]

    def clear(self) -> None:
        self._events.clear()


GLOBAL_ALERT_STORE = AlertStore()
