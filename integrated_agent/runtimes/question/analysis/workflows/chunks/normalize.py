"""阶段四：把查询结果整理为最终回答可引用的证据。"""

from typing import Any, cast

from agently import TriggerFlowRuntimeData

from ...trace_log import TraceLog
from ...utils.catalog import format_evidence_value, infer_column_semantics


async def normalize_results(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    query_results = cast(list[dict[str, Any]], list(data.input))
    trace = cast(TraceLog, data.require_resource("trace"))
    rewrite = cast(dict[str, Any], data.get_state("rewrite"))
    subquestions = {
        str(item["subquestion_id"]): item for item in rewrite["subquestions"]
    }
    evidence: list[dict[str, Any]] = []
    for result in query_results:
        if result["status"] != "completed" or result["result_ref"] is None:
            continue
        column_semantics = {
            column: infer_column_semantics(column) for column in result["columns"]
        }
        display_rows_preview = [
            {
                column: format_evidence_value(column, value)
                for column, value in row.items()
            }
            for row in result["rows_preview"]
        ]
        subquestion = subquestions[str(result["subquestion_id"])]
        item = {
            "evidence_id": f"e-{result['subquestion_id']}",
            "subquestion_id": result["subquestion_id"],
            "query_id": result["query_id"],
            "columns": result["columns"],
            "row_count": result["row_count"],
            "rows_preview": result["rows_preview"],
            "column_semantics": column_semantics,
            "display_rows_preview": display_rows_preview,
            "question": subquestion["question"],
            "analysis_goal": subquestion["analysis_goal"],
            "time_scope": subquestion["time_scope"],
            "dimensions": subquestion["dimensions"],
            "result_ref": result["result_ref"],
            "sql_fingerprint": result["sql_fingerprint"],
            "data_snapshot_id": result["data_snapshot_id"],
            "summary": (
                f"查询返回 {result['row_count']} 行，"
                f"列为 {', '.join(result['columns'])}"
            ),
        }
        evidence.append(item)
        trace.log(
            layer="business",
            event_type="business.evidence.created",
            status="completed",
            subject_id=str(item["evidence_id"]),
            input={
                "query_id": item["query_id"],
                "result_ref": item["result_ref"],
            },
            output=item,
            facts={
                "subquestion_id": item["subquestion_id"],
                "query_id": item["query_id"],
                "result_ref": item["result_ref"],
                "sql_fingerprint": item["sql_fingerprint"],
                "data_snapshot_id": item["data_snapshot_id"],
            },
        )

    await data.async_set_state("query_results", query_results, emit=False)
    await data.async_set_state("evidence", evidence, emit=False)
    return evidence


__all__ = ["normalize_results"]
