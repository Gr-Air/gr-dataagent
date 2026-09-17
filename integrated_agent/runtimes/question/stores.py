from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from .models import TaskEvent, TaskResult, TaskSnapshot


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._records: dict[str, TaskSnapshot] = {}
        self._lock = asyncio.Lock()

    async def create(self, task_id: str) -> TaskSnapshot:
        async with self._lock:
            if task_id in self._records:
                raise ValueError(f"duplicate task id: {task_id}")
            record = TaskSnapshot(task_id=task_id, status="accepted")
            self._records[task_id] = record
            return record

    async def get(self, task_id: str) -> TaskSnapshot | None:
        async with self._lock:
            return self._records.get(task_id)

    async def delete(self, task_id: str) -> None:
        async with self._lock:
            self._records.pop(task_id, None)

    async def mark_running(self, task_id: str) -> TaskSnapshot:
        return await self._replace(task_id, status="running")

    async def complete(self, task_id: str, result: TaskResult) -> TaskSnapshot:
        return await self._replace(task_id, status="completed", result=result)

    async def fail(self, task_id: str, error: str) -> TaskSnapshot:
        return await self._replace(task_id, status="failed", error=error)

    async def _replace(self, task_id: str, **updates: Any) -> TaskSnapshot:
        async with self._lock:
            current = self._records.get(task_id)
            if current is None:
                raise KeyError(task_id)
            updated = current.model_copy(update=updates)
            self._records[task_id] = updated
            return updated


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: dict[str, list[TaskEvent]] = defaultdict(list)
        self._condition = asyncio.Condition()

    async def publish(self, task_id: str, event_type: str, data: dict[str, Any] | None = None) -> TaskEvent:
        async with self._condition:
            event = TaskEvent(
                task_id=task_id,
                sequence=len(self._events[task_id]) + 1,
                event_type=event_type,
                data=data or {},
            )
            self._events[task_id].append(event)
            self._condition.notify_all()
            return event

    def list_for(self, task_id: str) -> list[TaskEvent]:
        return list(self._events.get(task_id, []))

    async def wait_for_change(self, task_id: str, after_sequence: int, timeout: float = 10.0) -> list[TaskEvent]:
        async with self._condition:
            if len(self._events.get(task_id, [])) <= after_sequence:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: len(self._events.get(task_id, [])) > after_sequence
                    ),
                    timeout=timeout,
                )
            return self.list_for(task_id)[after_sequence:]
