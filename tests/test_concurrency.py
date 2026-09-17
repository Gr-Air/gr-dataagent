from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from integrated_agent.runtimes.question.models import (
    Claim,
    TaskCreate,
    TaskRequest,
    TaskResult,
)
from integrated_agent.runtimes.question.service import (
    QuestionTaskService,
    ServiceBusyError,
)
from integrated_agent.runtimes.question.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)


class BlockingWorker:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.release = asyncio.Event()

    async def execute_complex_task(self, request: TaskRequest) -> TaskResult:
        self.started.append(request.task_id)
        await self.release.wait()
        return TaskResult(
            task_id=request.task_id,
            status="completed",
            answer="ok",
            claims=[Claim(claim_id="c1", text="ok")],
            evidence=[],
            evidence_refs=[],
            data_snapshot_id="lesson23-analysis-b7ad59fddab30331",
            trace_ref=Path(__file__).resolve().as_uri(),
        )


@pytest.mark.asyncio
async def test_worker_pool_executes_independent_tasks_concurrently() -> None:
    worker = BlockingWorker()
    service = QuestionTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=InMemoryEventStore(),
        worker_count=3,
        queue_capacity=8,
    )
    await service.start()
    try:
        await asyncio.gather(
            *(service.submit(TaskCreate(question=f"q-{index}")) for index in range(3))
        )
        for _ in range(100):
            if len(worker.started) == 3:
                break
            await asyncio.sleep(0.01)
        assert len(worker.started) == 3
        worker.release.set()
        await service.join()
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_full_queue_fails_fast_instead_of_blocking_submit() -> None:
    worker = BlockingWorker()
    service = QuestionTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=InMemoryEventStore(),
        worker_count=1,
        queue_capacity=1,
    )
    first = await service.submit(TaskCreate(question="q-1"))
    with pytest.raises(ServiceBusyError):
        await service.submit(TaskCreate(question="q-2"))
    snapshot = await service.get(first.task_id)
    assert snapshot is not None and snapshot.status == "accepted"
