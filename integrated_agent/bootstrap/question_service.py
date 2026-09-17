from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from integrated_agent.runtimes.question.analysis import (
    QuestionAnalysisCapability,
)
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


ROOT = Path(__file__).parents[2]


def build_question_service(root: Path = ROOT) -> QuestionTaskService:
    events = InMemoryEventStore()
    worker = QuestionWorkflowWorker(
        WorkerDependencies(
            question_analysis=QuestionAnalysisCapability(
                logs_root=root / "logs"
            ),
            events=events,
        )
    )
    return QuestionTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=events,
        worker_count=4,
        queue_capacity=32,
    )


def create_production_app() -> FastAPI:
    return create_question_api(
        build_question_service(),
        static_root=ROOT / "static",
        artifacts_root=ROOT / "workspace/artifacts",
    )
