from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agently import TriggerFlow, TriggerFlowRuntimeData

from .analysis import QuestionAnalysisCapability
from .charts import build_charts
from .models import Claim, EvidenceRef, TaskRequest, TaskResult
from .stores import InMemoryEventStore


@dataclass
class WorkerDependencies:
    question_analysis: QuestionAnalysisCapability
    events: InMemoryEventStore


def _deps(data: TriggerFlowRuntimeData) -> WorkerDependencies:
    return cast(
        WorkerDependencies,
        data.execution.require_runtime_resource("worker_dependencies"),
    )


def _request(data: TriggerFlowRuntimeData) -> TaskRequest:
    return TaskRequest.model_validate(data.get_state("request"))


async def _stage(
    data: TriggerFlowRuntimeData,
    event_type: str,
    stage: str,
    **values: Any,
) -> None:
    request = _request(data)
    await _deps(data).events.publish(
        request.task_id,
        event_type,
        {"stage": stage, **values},
    )


question_flow = TriggerFlow(name="enterprise-question-service")


@question_flow.chunk
async def analyze_question(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = TaskRequest.model_validate(data.input)
    await data.async_set_state("request", request.model_dump(mode="json"), emit=False)
    await _deps(data).events.publish(request.task_id, "task.accepted")
    await _stage(data, "stage.started", "analyze_question")
    run = await _deps(data).question_analysis.analyze(
        task_id=request.task_id,
        question=request.question,
    )
    await data.async_set_state("question_analysis_run", run, emit=False)
    for evidence in cast(list[dict[str, Any]], run.get("evidence", [])):
        await _deps(data).events.publish(
            request.task_id,
            "evidence.ready",
            {
                "evidence_id": evidence.get("evidence_id"),
                "query_id": evidence.get("query_id"),
                "result_ref": evidence.get("result_ref"),
            },
        )
    await _stage(
        data,
        "stage.completed",
        "analyze_question",
        pipeline_status=run.get("status"),
        data_snapshot_id=run.get("data_snapshot_id"),
    )
    return request.model_dump(mode="json")


@question_flow.chunk
async def publish_answer(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = _request(data)
    await _stage(data, "stage.started", "publish_answer")
    run = cast(dict[str, Any], data.get_state("question_analysis_run"))
    final_answer = cast(dict[str, Any], run["final_answer"])
    claims = [
        Claim.model_validate(item)
        for item in cast(list[dict[str, Any]], final_answer.get("claims", []))
    ]
    evidence = [
        EvidenceRef(
            evidence_id=str(item["evidence_id"]),
            query_id=str(item["query_id"]),
            result_ref=str(item["result_ref"]),
            summary=str(item.get("summary", "")),
        )
        for item in cast(list[dict[str, Any]], run.get("evidence", []))
        if item.get("result_ref")
    ]
    charts = build_charts(
        cast(list[dict[str, Any]], run.get("evidence", []))
    )
    result = TaskResult(
        task_id=request.task_id,
        status="partial" if run.get("status") == "partial" else "completed",
        answer=str(final_answer["answer"]),
        claims=claims,
        evidence=evidence,
        evidence_refs=[item.result_ref for item in evidence],
        charts=charts,
        data_snapshot_id=str(run["data_snapshot_id"]),
        trace_ref=str(run["trace_ref"]),
    )
    await data.async_set_state("result", result.model_dump(mode="json"), emit=False)
    await _deps(data).events.publish(
        request.task_id,
        "chart.ready",
        {
            "chart_count": len(result.charts),
            "charts": [
                chart.model_dump(mode="json")
                for chart in result.charts
            ],
        },
    )
    await _deps(data).events.publish(
        request.task_id,
        "answer.ready",
        {
            "answer": result.answer,
            "claim_count": len(result.claims),
            "evidence_count": len(result.evidence),
            "chart_count": len(result.charts),
        },
    )
    await _stage(data, "stage.completed", "publish_answer")
    return result.model_dump(mode="json")


question_flow.to(analyze_question).to(publish_answer)


class QuestionWorkflowWorker:
    def __init__(self, dependencies: WorkerDependencies) -> None:
        self.dependencies = dependencies

    async def execute_complex_task(self, request: TaskRequest) -> TaskResult:
        execution = question_flow.create_execution(
            auto_close=False,
            runtime_resources={"worker_dependencies": self.dependencies},
        )
        await execution.async_start(request.model_dump(mode="json"))
        state = await execution.async_close()
        return TaskResult.model_validate(state["result"])
