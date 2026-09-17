"""记录问数业务事实，并从 Agently EventCenter 获得框架事实。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

from agently import Agently, RuntimeEvent


TraceLayer = Literal["business", "framework"]
TraceStatus = Literal["started", "completed", "failed", "retrying", "observed"]

FRAMEWORK_EVENT_TYPES = frozenset(
    {
        "triggerflow.execution_started",
        "triggerflow.execution_completed",
        "triggerflow.execution_failed",
        "chunk.started",
        "chunk.completed",
        "chunk.failed",
        "model.request_started",
        "model.requesting",
        "model.retrying",
        "model.completed",
        "model.failed",
        "model.parse_failed",
        "model.request_failed",
        "model.validation_failed",
        "model.meta",
    }
)

_TRACE_LAYERS = {"business", "framework"}
_TRACE_STATUSES = {"started", "completed", "failed", "retrying", "observed"}
_MODEL_FACT_KEYS = (
    "agent_name",
    "response_id",
    "attempt_index",
    "request_run_id",
    "model_run_id",
    "provider",
    "provider_family",
    "model",
    "request_url",
    "duration_ms",
    "usage",
    "usage_summary",
    "retry_count",
    "validation_reason",
    "validation_stop",
    "validation_no_retry",
    "side_channel",
    "error",
)


def bounded(
    value: Any,
    *,
    string_limit: int = 2_000,
    item_limit: int = 50,
    depth: int = 0,
) -> Any:
    """把事实转换为可写入 JSON 的有限值。"""

    if depth >= 8:
        return "<max-depth>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): bounded(
                item,
                string_limit=string_limit,
                item_limit=item_limit,
                depth=depth + 1,
            )
            for key, item in list(value.items())[:item_limit]
        }
    if isinstance(value, (list, tuple)):
        return [
            bounded(
                item,
                string_limit=string_limit,
                item_limit=item_limit,
                depth=depth + 1,
            )
            for item in value[:item_limit]
        ]
    if isinstance(value, str) and len(value) > string_limit:
        return value[:string_limit] + "…"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return bounded(
            model_dump(mode="json"),
            string_limit=string_limit,
            item_limit=item_limit,
            depth=depth + 1,
        )
    return str(value)


def value_shape(value: Any) -> dict[str, Any]:
    """只描述框架输入输出的形状，不复制业务值。"""

    if isinstance(value, Mapping):
        keys = sorted(str(key) for key in value.keys())
        return {"type": "object", "keys": keys[:20], "size": len(keys)}
    if isinstance(value, (list, tuple)):
        return {"type": "array", "size": len(value)}
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    return {"type": type(value).__name__}


def _error_payload(error: Any) -> dict[str, Any] | None:
    if error is None:
        return None
    if isinstance(error, BaseException):
        return {"type": type(error).__name__, "message": str(error)}
    if isinstance(error, Mapping):
        return bounded(error)
    model_dump = getattr(error, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        if isinstance(payload, Mapping):
            return bounded(
                {
                    "type": payload.get("type"),
                    "message": payload.get("message"),
                    "retryable": payload.get("retryable"),
                    "fatal": payload.get("fatal"),
                    "code": payload.get("code"),
                }
            )
    return {"type": type(error).__name__, "message": str(error)}


class TraceLog:
    """一次 TriggerFlow execution 的有序事实列表。"""

    def __init__(self, task_id: str, execution_id: str) -> None:
        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        if not execution_id.strip():
            raise ValueError("execution_id must not be empty")
        self.task_id = task_id
        self.execution_id = execution_id
        self.events: list[dict[str, Any]] = []
        self._flow_root_run_id: str | None = None

    def log(
        self,
        *,
        layer: TraceLayer,
        event_type: str,
        status: TraceStatus,
        subject_id: str | None = None,
        input: Any = None,
        output: Any = None,
        facts: Mapping[str, Any] | None = None,
        error: Any = None,
        runtime: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """以同一结构追加一条业务或框架事实。"""

        if layer not in _TRACE_LAYERS:
            raise ValueError(f"unsupported trace layer: {layer}")
        if status not in _TRACE_STATUSES:
            raise ValueError(f"unsupported trace status: {status}")
        if not event_type.strip():
            raise ValueError("event_type must not be empty")
        event = {
            "event_id": f"{self.task_id}:event:{len(self.events) + 1:04d}",
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "layer": layer,
            "event_type": event_type,
            "status": status,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "subject_id": subject_id,
            "input": bounded(input) if input is not None else None,
            "output": bounded(output) if output is not None else None,
            "facts": bounded(dict(facts or {})),
            "error": _error_payload(error),
            "runtime": bounded(dict(runtime)) if runtime is not None else None,
        }
        self.events.append(event)
        return event


def _framework_status(event_type: str) -> TraceStatus:
    if event_type.endswith("_started") or event_type == "chunk.started":
        return "started"
    if event_type.endswith("_completed") or event_type in {
        "chunk.completed",
        "model.completed",
    }:
        return "completed"
    if event_type == "model.retrying":
        return "retrying"
    if event_type.endswith("_failed") or event_type in {
        "chunk.failed",
        "model.failed",
        "model.parse_failed",
        "model.request_failed",
        "model.validation_failed",
    }:
        return "failed"
    return "observed"


def _event_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp / 1_000, tz=timezone.utc).isoformat()


def _model_facts(payload: Mapping[str, Any]) -> dict[str, Any]:
    telemetry = payload.get("model_request_telemetry")
    source = telemetry if isinstance(telemetry, Mapping) else payload
    facts = {key: source[key] for key in _MODEL_FACT_KEYS if source.get(key) is not None}
    if facts.get("model") is None:
        request = payload.get("request")
        if isinstance(request, Mapping):
            request_options = request.get("request_options")
            if isinstance(request_options, Mapping) and request_options.get("model") is not None:
                facts["model"] = request_options["model"]
    return facts


def _record_framework_event(trace: TraceLog, event: RuntimeEvent) -> None:
    run = event.run
    if event.event_type not in FRAMEWORK_EVENT_TYPES:
        return
    if run is None:
        return
    belongs_to_flow = run.execution_id == trace.execution_id
    if event.event_type.startswith("model."):
        belongs_to_flow = belongs_to_flow or (
            trace._flow_root_run_id is not None
            and run.root_run_id == trace._flow_root_run_id
        )
    if not belongs_to_flow:
        return
    if event.event_type == "triggerflow.execution_started":
        trace._flow_root_run_id = run.root_run_id or run.run_id

    payload = event.payload if isinstance(event.payload, Mapping) else {}
    input_value: Any = None
    output_value: Any = None
    facts: dict[str, Any] = {}
    subject_id = run.run_id

    if event.event_type.startswith("chunk."):
        subject_id = run.run_id
        facts = {
            key: payload[key]
            for key in (
                "chunk_id",
                "chunk_name",
                "operator_kind",
                "trigger_event",
                "trigger_type",
                "signal_source",
                "returned_pause_signal",
            )
            if payload.get(key) is not None
        }
        if "input" in payload:
            input_value = value_shape(payload["input"])
        if "output" in payload:
            output_value = value_shape(payload["output"])
    elif event.event_type.startswith("model."):
        facts = _model_facts(payload)
        subject_id = str(
            facts.get("response_id")
            or payload.get("response_id")
            or run.response_id
            or run.run_id
        )
    else:
        subject_id = run.execution_id or run.run_id
        flow_name = run.meta.get("flow_name")
        if flow_name is not None:
            facts["flow_name"] = flow_name

    runtime = {
        "event_id": event.event_id,
        "source": event.source,
        "level": event.level,
        "message": event.message,
        "timestamp_ms": event.timestamp,
        "run": run.model_dump(mode="json"),
    }
    trace.log(
        layer="framework",
        event_type=event.event_type,
        status=_framework_status(event.event_type),
        subject_id=subject_id,
        input=input_value,
        output=output_value,
        facts=facts,
        error=event.error,
        runtime=runtime,
        timestamp=_event_timestamp(event.timestamp),
    )


def register_framework_hook(trace: TraceLog) -> str:
    """为一次 execution 注册严格白名单 Hook。"""

    hook_name = f"lesson24-v2.trace.{trace.execution_id}"

    def capture(event: RuntimeEvent) -> None:
        _record_framework_event(trace, event)

    Agently.event_center.register_hook(
        capture,
        event_types=sorted(FRAMEWORK_EVENT_TYPES),
        hook_name=hook_name,
    )
    return hook_name


def unregister_framework_hook(hook_name: str) -> None:
    Agently.event_center.unregister_hook(hook_name)


def task_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": event["task_id"],
            "execution_id": event["execution_id"],
            "event_type": event["event_type"],
            "status": event["status"],
            "timestamp": event["timestamp"],
            "error": event["error"],
        }
        for event in events
        if str(event.get("event_type", "")).startswith("triggerflow.execution_")
    ]


def stage_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        if not str(event.get("event_type", "")).startswith("chunk."):
            continue
        facts = event.get("facts") or {}
        if facts.get("operator_kind") not in {None, "chunk"}:
            continue
        stage = str(facts.get("chunk_name") or event.get("subject_id") or "unknown")
        current = grouped.setdefault(
            stage,
            {
                "stage": stage,
                "status": "started",
                "instance_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "started_at": event.get("timestamp"),
                "completed_at": None,
                "input_shapes": [],
                "output_shapes": [],
            },
        )
        if event.get("event_type") == "chunk.started":
            current["instance_count"] += 1
        if event.get("status") == "completed":
            current["completed_count"] += 1
            current["completed_at"] = event.get("timestamp")
        if event.get("status") == "failed":
            current["failed_count"] += 1
            current["completed_at"] = event.get("timestamp")
        if event.get("input") is not None and event["input"] not in current["input_shapes"]:
            current["input_shapes"].append(event["input"])
        if event.get("output") is not None and event["output"] not in current["output_shapes"]:
            current["output_shapes"].append(event["output"])
        current["status"] = (
            "failed"
            if current["failed_count"]
            else "completed"
            if current["completed_count"] >= current["instance_count"] > 0
            else "started"
        )
    return list(grouped.values())


def operation_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "subject_id": event["subject_id"],
            "status": event["status"],
            "timestamp": event["timestamp"],
            "input": event["input"],
            "output": event["output"],
            "facts": event["facts"],
            "error": event["error"],
        }
        for event in events
        if event.get("layer") == "business"
    ]


def attempt_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, Any], dict[str, Any]] = {}
    for event in events:
        if not str(event.get("event_type", "")).startswith("model."):
            continue
        facts = dict(event.get("facts") or {})
        key = (str(event.get("subject_id") or "unknown"), facts.get("attempt_index"))
        current = grouped.setdefault(
            key,
            {
                "subject_id": key[0],
                "attempt_index": key[1],
                "status": event.get("status"),
                "started_at": None,
                "completed_at": None,
                "facts": {},
                "error": None,
                "events": [],
            },
        )
        current["events"].append(event["event_type"])
        for fact_name, fact_value in facts.items():
            if (
                fact_name in {"provider", "provider_family"}
                and current["facts"].get(fact_name)
                and fact_value == "AgentlyResponseParser"
            ):
                continue
            current["facts"][fact_name] = fact_value
        if event.get("status") == "started":
            current["started_at"] = event.get("timestamp")
        if event.get("status") in {"completed", "failed", "retrying"}:
            current["status"] = event["status"]
            current["completed_at"] = event.get("timestamp")
        if event.get("error") is not None:
            current["error"] = event["error"]
    return list(grouped.values())


def sql_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in operation_view(events)
        if event["event_type"] in {"business.sql.prepared", "business.sql.executed"}
    ]


def evidence_view(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in operation_view(events)
        if event["event_type"]
        in {"business.evidence.created", "business.answer.composed"}
    ]


def save_run(run: dict[str, Any], output_directory: Path) -> None:
    """在 execution 结束后保存运行结果与六种 Trace 视图。"""

    output_directory.mkdir(parents=True, exist_ok=True)
    serializable_run = {
        str(key): bounded(value)
        for key, value in run.items()
        if key != "events"
    }
    events = [bounded(event) for event in run.get("events", [])]
    serializable_run["events"] = events
    (output_directory / "run.json").write_text(
        json.dumps(serializable_run, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )
    summary = {
        "task_id": serializable_run["task_id"],
        "execution_id": serializable_run.get("execution_id"),
        "status": serializable_run["status"],
        "question": serializable_run["question"],
        "data_snapshot_id": serializable_run["data_snapshot_id"],
        "subquestion_count": len(serializable_run["rewrite"]["subquestions"]),
        "query_count": len(serializable_run["query_results"]),
        "evidence_count": len(serializable_run["evidence"]),
        "event_count": len(events),
        "tasks": task_view(events),
        "stages": stage_view(events),
        "operations": operation_view(events),
        "attempts": attempt_view(events),
        "sql_facts": sql_view(events),
        "evidence_facts": evidence_view(events),
        "final_answer": serializable_run["final_answer"],
    }
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = [
    "FRAMEWORK_EVENT_TYPES",
    "TraceLog",
    "attempt_view",
    "bounded",
    "evidence_view",
    "operation_view",
    "register_framework_hook",
    "save_run",
    "sql_view",
    "stage_view",
    "task_view",
    "unregister_framework_hook",
    "value_shape",
]
