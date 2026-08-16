from collections import deque
from collections.abc import Collection
import threading


_CONTENT_KINDS = frozenset({"statement", "editorial"})


class CrawlPriorityQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues = {content_kind: deque() for content_kind in _CONTENT_KINDS}

    def _queue(self, content_kind: str) -> deque[str]:
        if content_kind not in _CONTENT_KINDS:
            raise ValueError("invalid-content-kind")
        return self._queues[content_kind]

    def prioritize(self, content_kind: str, content_id: str) -> None:
        with self._lock:
            queue = self._queue(content_kind)
            if content_id in queue:
                queue.remove(content_id)
            queue.appendleft(content_id)


    def enqueue_many(
        self,
        content_kind: str,
        content_ids: Collection[str],
    ) -> None:
        with self._lock:
            queue = self._queue(content_kind)
            incoming: list[str] = []
            seen: set[str] = set()
            for content_id in content_ids:
                if content_id in seen:
                    continue
                seen.add(content_id)
                incoming.append(content_id)
            existing = [content_id for content_id in queue if content_id not in seen]
            queue.clear()
            queue.extend(incoming)
            queue.extend(existing)

    def snapshot(self, content_kind: str) -> list[str]:
        with self._lock:
            return list(self._queue(content_kind))

    def pop_next(
        self,
        content_kind: str,
        remaining_ids: Collection[str],
    ) -> str | None:
        with self._lock:
            queue = self._queue(content_kind)
            while queue:
                content_id = queue.popleft()
                if content_id in remaining_ids:
                    return content_id
        return None
