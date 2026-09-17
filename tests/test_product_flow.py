from __future__ import annotations

import pytest

from integrated_agent.runtimes.question.analysis import (
    QuestionAnalysisCapability,
)
from integrated_agent.runtimes.question.models import TaskRequest
from integrated_agent.runtimes.question.stores import InMemoryEventStore
from integrated_agent.runtimes.question.worker import (
    QuestionWorkflowWorker,
    WorkerDependencies,
)
from tests.fakes import fake_question_runner


@pytest.mark.asyncio
async def test_product_flow_returns_answer_evidence_and_trace(tmp_path) -> None:
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
    result = await worker.execute_complex_task(
        TaskRequest(task_id="task-001", question="分析 2025 年 618 经营增长质量")
    )

    assert result.status == "completed"
    assert result.data_snapshot_id == "lesson23-analysis-b7ad59fddab30331"
    assert result.claims[0].evidence_ids == ["evidence-1"]
    assert result.evidence_refs[0].startswith("sqlite-result://")
    assert result.charts[0].categories == ["净营收", "支付 GMV"]
    assert result.charts[0].unit == "万元"
    assert [series.name for series in result.charts[0].series] == ["2024", "2025"]
    assert result.trace_ref.startswith("file://")
    assert [event.event_type for event in events.list_for("task-001")] == [
        "task.accepted",
        "stage.started",
        "evidence.ready",
        "stage.completed",
        "stage.started",
        "chart.ready",
        "answer.ready",
        "stage.completed",
    ]
