"""问数项目的 TriggerFlow 拓扑与单次运行入口。"""

from pathlib import Path
from typing import Any, cast

from agently import TriggerFlow

from ..trace_log import (
    TraceLog,
    register_framework_hook,
    save_run,
    unregister_framework_hook,
)
from ..utils.catalog import build_rewrite_catalog, load_catalog
from .chunks.execute_sql import execute_sql
from .chunks.final_answer import compose_final_answer
from .chunks.generate_sql import generate_sql
from .chunks.normalize import normalize_results
from .chunks.rewrite import rewrite_question


PIPELINE_VERSION = "lesson24-v2-2.5.0"
PROMPT_VERSION = "3.5.0"

QUESTION_DATA_FLOW = TriggerFlow(name="question-data-v2")
(
    QUESTION_DATA_FLOW.to(rewrite_question)
    .for_each(concurrency=4)
    .to(generate_sql)
    .end_for_each()
    .for_each(concurrency=4)
    .to(execute_sql)
    .end_for_each()
    .to(normalize_results)
    .to(compose_final_answer)
)


async def run_question(
    question: str,
    *,
    task_id: str,
    database_path: Path,
    output_directory: Path,
    max_concurrency: int = 4,
) -> dict[str, Any]:
    """运行一次完整问数 Flow，并保存结果与 Trace。"""

    if not question.strip():
        raise ValueError("question must not be empty")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be positive")

    catalog = load_catalog(database_path)
    rewrite_catalog = build_rewrite_catalog(catalog)
    metadata = cast(dict[str, Any], catalog["metadata"])
    snapshot_id = str(metadata["data_snapshot_id"])
    execution = QUESTION_DATA_FLOW.create_execution(
        concurrency=max_concurrency,
        runtime_resources={
            "catalog": catalog,
            "rewrite_catalog": rewrite_catalog,
            "database_path": database_path,
            "data_snapshot_id": snapshot_id,
            "result_directory": output_directory / "results",
        },
        auto_close=False,
    )
    trace = TraceLog(task_id, execution.id)
    execution.update_runtime_resources({"trace": trace})
    hook_name = register_framework_hook(trace)
    try:
        await execution.async_start({"question": question})
        state = await execution.async_close()
    finally:
        unregister_framework_hook(hook_name)

    rewrite = cast(dict[str, Any], state["rewrite"])
    order = {
        str(item["subquestion_id"]): index
        for index, item in enumerate(rewrite["subquestions"])
    }
    sql_tasks = sorted(
        cast(list[dict[str, Any]], state["sql_tasks"]),
        key=lambda item: order[str(item["subquestion_id"])],
    )
    query_results = sorted(
        cast(list[dict[str, Any]], state["query_results"]),
        key=lambda item: order[str(item["subquestion_id"])],
    )
    evidence = sorted(
        cast(list[dict[str, Any]], state["evidence"]),
        key=lambda item: order[str(item["subquestion_id"])],
    )
    final_answer = cast(dict[str, Any], state["final_answer"])
    final_failed = bool(state.get("final_failed", False))
    branch_failed = any(item["status"] == "failed" for item in sql_tasks) or any(
        item["status"] in {"failed", "skipped"} for item in query_results
    )
    status = "failed" if final_failed else "partial" if branch_failed else "completed"
    run = {
        "task_id": task_id,
        "execution_id": execution.id,
        "status": status,
        "question": question,
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "dataset_version": str(metadata.get("analysis_export_version", "unknown")),
        "data_snapshot_id": snapshot_id,
        "rewrite": rewrite,
        "sql_tasks": sql_tasks,
        "query_results": query_results,
        "evidence": evidence,
        "final_answer": final_answer,
        "events": trace.events,
    }
    save_run(run, output_directory)
    return run


__all__ = ["PIPELINE_VERSION", "PROMPT_VERSION", "QUESTION_DATA_FLOW", "run_question"]
