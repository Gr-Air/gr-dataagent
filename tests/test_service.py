from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from integrated_agent.runtimes.question.analysis import (
    QuestionAnalysisCapability,
)
from integrated_agent.runtimes.question.models import TaskRequest, TaskResult
from integrated_agent.runtimes.question.service import QuestionTaskService
from integrated_agent.runtimes.question.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.runtimes.question.worker import (
    QuestionWorkflowWorker,
    WorkerDependencies,
)
from integrated_agent.transports.http import create_question_api
from tests.fakes import fake_question_runner


def build_service(tmp_path: Path) -> QuestionTaskService:
    events = InMemoryEventStore()
    worker = QuestionWorkflowWorker(
        WorkerDependencies(
            question_analysis=QuestionAnalysisCapability(
                logs_root=tmp_path,
                runner=fake_question_runner,
            ),
            events=events,
        )
    )
    return QuestionTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=events,
    )


def test_web_api_accepts_task_and_replays_stable_events(tmp_path: Path) -> None:
    app = create_question_api(
        build_service(tmp_path),
        static_root=Path(__file__).parents[1] / "static",
        artifacts_root=tmp_path / "artifacts",
    )
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "问数智能体流式服务" in client.get("/").text
        accepted = client.post(
            "/v1/tasks",
            json={"question": "分析 2025 年 618 经营增长质量"},
        )
        assert accepted.status_code == 202
        payload = accepted.json()
        snapshot: dict[str, object] = {}
        for _ in range(100):
            snapshot = client.get(payload["task_url"]).json()
            if snapshot["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert snapshot["status"] == "completed"
        stream = client.get(payload["events_url"])
        assert stream.status_code == 200
        assert "event: task.submitted" in stream.text
        assert "event: evidence.ready" in stream.text
        assert "event: answer.ready" in stream.text
        assert "event: task.completed" in stream.text


def test_api_returns_503_when_admission_queue_is_full(tmp_path: Path) -> None:
    class NeverCompletesWorker:
        async def execute_complex_task(self, request: TaskRequest) -> TaskResult:
            del request
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    service = QuestionTaskService(
        worker=NeverCompletesWorker(),
        tasks=InMemoryTaskStore(),
        events=InMemoryEventStore(),
        worker_count=1,
        queue_capacity=1,
    )
    app = create_question_api(
        service,
        static_root=Path(__file__).parents[1] / "static",
        artifacts_root=tmp_path / "artifacts",
    )
    with TestClient(app) as client:
        first = client.post("/v1/tasks", json={"question": "q-1"})
        second = client.post("/v1/tasks", json={"question": "q-2"})
        third = client.post("/v1/tasks", json={"question": "q-3"})
        assert first.status_code == 202
        assert second.status_code == 202
        assert third.status_code == 503
        assert third.headers["retry-after"] == "1"
