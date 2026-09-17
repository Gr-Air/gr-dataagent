"""阶段三：执行一个已预检的 SQL 工作项。"""

import asyncio
from pathlib import Path
from typing import Any, cast

from agently import TriggerFlowRuntimeData

from ...trace_log import TraceLog
from ...utils.database import execute_sql as run_sql


async def execute_sql(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    task = cast(dict[str, Any], data.input)
    trace = cast(TraceLog, data.require_resource("trace"))
    result = await asyncio.to_thread(
        run_sql,
        cast(Path, data.require_resource("database_path")),
        task,
        cast(Path, data.require_resource("result_directory")),
    )
    await data.async_append_state("query_results", result, emit=False)
    completed = result["status"] == "completed"
    trace.log(
        layer="business",
        event_type="business.sql.executed",
        status="completed" if completed else "failed",
        subject_id=str(task["subquestion_id"]),
        input={
            "subquestion_id": task["subquestion_id"],
            "sql": task.get("sql"),
            "attempt_count": task.get("attempt_count"),
        },
        output=result,
        facts={
            "query_id": result.get("query_id"),
            "row_count": result.get("row_count"),
            "result_ref": result.get("result_ref"),
            "sql_fingerprint": result.get("sql_fingerprint"),
            "data_snapshot_id": result.get("data_snapshot_id"),
        },
        error=(
            None
            if completed
            else RuntimeError(result.get("error") or f"query {result['status']}")
        ),
    )
    return result


__all__ = ["execute_sql"]
