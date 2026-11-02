"""In-process pub/sub for live progress (feeds the /ws/events WebSocket).

Event shapes (all JSON-serialisable dicts, `type` is the discriminator):
  {"type": "run.created", "run": RunSummary}
  {"type": "run.status",  "run_id", "status", "message"?}
  {"type": "run.log",     "run_id", "line"}
  {"type": "group.updated", "group_id", "status", "result"?}
"""
from __future__ import annotations

import queue
import threading
from typing import Any


class EventBus:
    def __init__(self, maxsize: int = 10000):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(self._maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # slow consumer: drop
                pass


NULL_BUS = EventBus()
