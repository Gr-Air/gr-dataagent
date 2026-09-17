"""阶段二：为一个子问题生成并预检 SQL。"""

import asyncio
from pathlib import Path
import re
from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from ...trace_log import TraceLog
from ...utils.catalog import build_sql_catalog
from ...utils.database import validate_sql


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/generate_sql.yaml"


def _semantic_sql_issues(subquestion: dict[str, Any], sql: str) -> list[str]:
    required_metrics = {
        str(metric) for metric in subquestion.get("required_metrics", [])
    }
    issues: list[str] = []
    rate_metrics = {metric for metric in required_metrics if "rate" in metric}
    if rate_metrics and re.search(
        r"(?:\b100(?:\.0+)?\s*\*|\*\s*100(?:\.0+)?\b)",
        sql,
        re.IGNORECASE,
    ):
        issues.append(
            "rate columns must return decimal ratios in the 0-1 contract; "
            "do not multiply by 100 in SQL"
        )
    if "discount_rate" in required_metrics and "gross_cents" not in sql:
        issues.append(
            "discount_rate must be SUM(discount_cents) / SUM(gross_cents)"
        )
    missing_metrics = sorted(
        metric for metric in required_metrics if metric not in sql
    )
    if missing_metrics:
        issues.append(
            "SQL does not expose required metric aliases: "
            + ", ".join(missing_metrics)
        )
    return issues


async def generate_sql(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    subquestion = cast(dict[str, Any], data.input)
    subquestion_id = str(subquestion["subquestion_id"])
    catalog = cast(dict[str, Any], data.require_resource("catalog"))
    sql_catalog = build_sql_catalog(catalog, subquestion)
    trace = cast(TraceLog, data.require_resource("trace"))
    repair: dict[str, Any] | None = None
    task: dict[str, Any] | None = None

    for attempt_index in (1, 2):
        try:
            result = await (
                Agently.create_agent(name=f"lesson24-v2-sql-{subquestion_id}")
                .load_yaml_prompt(
                    PROMPT_PATH,
                    mappings={
                        "subquestion": subquestion,
                        "catalog": sql_catalog,
                        "repair": repair,
                    },
                )
                .create_execution()
                .async_start()
            )
            model_output = dict(result)
            if model_output.get("subquestion_id") != subquestion_id:
                raise ValueError("SQL response changed subquestion_id")
            sql = str(model_output["sql"])
        except BaseException as exc:
            task = {
                "subquestion_id": subquestion_id,
                "status": "failed",
                "sql": None,
                "expected_columns": [],
                "issues": [f"{type(exc).__name__}: {exc}"],
                "attempt_count": attempt_index,
            }
            break

        task = await asyncio.to_thread(
            validate_sql,
            cast(Path, data.require_resource("database_path")),
            subquestion_id,
            sql,
            cast(str, data.require_resource("data_snapshot_id")),
            attempt_count=attempt_index,
        )
        semantic_issues = _semantic_sql_issues(subquestion, sql)
        if semantic_issues:
            task["status"] = "failed"
            task["expected_columns"] = []
            task["issues"].extend(semantic_issues)
        if task["status"] == "ready":
            break
        if attempt_index == 1:
            repair = {"previous_sql": sql, "issues": task["issues"]}

    if task is None:
        raise RuntimeError("SQL task was not produced")
    await data.async_append_state("sql_tasks", task, emit=False)
    error = None
    if task["status"] == "ready":
        status = "completed"
    else:
        status = "failed"
        error = RuntimeError("; ".join(task["issues"]))
    trace.log(
        layer="business",
        event_type="business.sql.prepared",
        status=status,
        subject_id=subquestion_id,
        input=subquestion,
        output=task,
        facts={
            "attempt_count": task["attempt_count"],
            "precheck_passed": task["status"] == "ready",
        },
        error=error,
    )
    return task


__all__ = ["_semantic_sql_issues", "generate_sql"]
