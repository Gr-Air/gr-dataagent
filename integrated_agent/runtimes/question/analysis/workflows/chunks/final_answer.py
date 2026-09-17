"""阶段五：只依据本次证据形成最终回答。"""

from pathlib import Path
from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from ...trace_log import TraceLog


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/final_answer.yaml"
REVIEW_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts/final_answer_review.yaml"
)


def _append_missing_gross_profit(
    answer: dict[str, Any],
    *,
    question: str,
    evidence: list[dict[str, Any]],
) -> None:
    if "毛利" not in question or "毛利率" not in question:
        return
    answer_text = str(answer.get("answer", ""))
    if "毛利" in answer_text.replace("毛利率", ""):
        return

    facts: list[str] = []
    evidence_ids: list[str] = []
    for item in evidence:
        for row in item.get("display_rows_preview", []):
            if not isinstance(row, dict):
                continue
            gross_profit = next(
                (
                    str(value)
                    for column, value in row.items()
                    if "gross_profit_cents" in str(column)
                    and "change" not in str(column)
                    and "diff" not in str(column)
                ),
                None,
            )
            if gross_profit is None:
                continue
            label = next(
                (
                    str(value)
                    for column, value in row.items()
                    if str(column)
                    in {"category", "channel", "city", "product", "order_year"}
                ),
                "当前范围",
            )
            facts.append(f"{label} {gross_profit}")
            evidence_ids.append(str(item["evidence_id"]))
    if not facts:
        return

    verified_text = "；".join(facts)
    answer["answer"] = f"{answer_text} 毛利核对：{verified_text}。"
    answer.setdefault("claims", []).append(
        {
            "claim_id": "host-verified-gross-profit",
            "text": f"毛利核对：{verified_text}",
            "evidence_ids": sorted(set(evidence_ids)),
        }
    )


async def compose_final_answer(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    evidence = cast(list[dict[str, Any]], list(data.input))
    trace = cast(TraceLog, data.require_resource("trace"))

    final_failed = False
    final_error: BaseException | None = None
    try:
        result = await (
            Agently.create_agent(name="lesson24-v2-final")
            .load_yaml_prompt(
                PROMPT_PATH,
                mappings={
                    "question": str(data.get_state("question")),
                    "evidence": evidence,
                    "evidence_ids": [item["evidence_id"] for item in evidence],
                    "data_snapshot_ids": sorted(
                        {str(item["data_snapshot_id"]) for item in evidence}
                    ),
                },
            )
            .create_execution()
            .async_start()
        )
        draft_answer = dict(result)
        try:
            reviewed = await (
                Agently.create_agent(name="lesson24-v2-final-review")
                .load_yaml_prompt(
                    REVIEW_PROMPT_PATH,
                    mappings={
                        "question": str(data.get_state("question")),
                        "evidence": evidence,
                        "draft_answer": draft_answer,
                        "evidence_ids": [
                            item["evidence_id"] for item in evidence
                        ],
                    },
                )
                .create_execution()
                .async_start()
            )
            answer = dict(reviewed)
        except BaseException:
            answer = draft_answer
        answer.setdefault("claims", [])
        answer.setdefault("limitations", [])
        if not answer["limitations"]:
            answer["limitations"] = [
                "结论仅适用于当前数据快照及问题指定的时间、指标和维度范围。"
            ]
        offered = {item["evidence_id"] for item in evidence}
        unknown = {
            evidence_id
            for claim in answer["claims"]
            for evidence_id in claim.get("evidence_ids", [])
            if evidence_id not in offered
        }
        if unknown:
            raise ValueError(
                f"unknown evidence references: {', '.join(sorted(unknown))}"
            )
        _append_missing_gross_profit(
            answer,
            question=str(data.get_state("question")),
            evidence=evidence,
        )
    except BaseException as exc:
        final_failed = True
        final_error = exc
        answer = {
            "answer": "本次运行未能生成带有效证据引用的最终结论。",
            "claims": [],
            "limitations": [f"final stage failed: {type(exc).__name__}: {exc}"],
        }
    trace.log(
        layer="business",
        event_type="business.answer.composed",
        status="failed" if final_failed else "completed",
        subject_id=trace.task_id,
        input={"evidence_ids": [item["evidence_id"] for item in evidence]},
        output=answer,
        facts={
            "claim_count": len(answer["claims"]),
            "evidence_count": len(evidence),
            "limitation_count": len(answer["limitations"]),
        },
        error=final_error,
    )

    await data.async_set_state("final_answer", answer, emit=False)
    await data.async_set_state("final_failed", final_failed, emit=False)
    return answer


__all__ = [
    "REVIEW_PROMPT_PATH",
    "_append_missing_gross_profit",
    "compose_final_answer",
]
