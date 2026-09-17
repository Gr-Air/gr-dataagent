"""阶段一：列全业务要求，再拆成可查询的分析目标。"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...trace_log import TraceLog


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/rewrite.yaml"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    requirement_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    time_scopes: list[str] = Field(min_length=1)
    comparisons: list[str] = Field(min_length=1)


class AnalysisTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_id: str = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)
    goal: str = Field(min_length=1)
    question: str = Field(min_length=1)
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    time_scopes: list[str] = Field(min_length=1)
    comparisons: list[str] = Field(min_length=1)


class RewriteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    normalized_question: str = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    targets: list[AnalysisTarget] = Field(min_length=1)

    @model_validator(mode="after")
    def requirements_are_covered(self) -> "RewriteDraft":
        requirement_ids = [item.requirement_id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement_id values must be unique")

        target_ids = [item.target_id for item in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("target_id values must be unique")

        known = set(requirement_ids)
        referenced = {
            requirement_id
            for target in self.targets
            for requirement_id in target.requirement_ids
        }
        unknown = referenced - known
        if unknown:
            raise ValueError(
                "targets reference unknown requirement ids: "
                + ", ".join(sorted(unknown))
            )
        missing = known - referenced
        if missing:
            raise ValueError(
                "requirements are not covered by targets: "
                + ", ".join(sorted(missing))
            )
        return self


def to_rewrite_result(draft: RewriteDraft) -> dict[str, Any]:
    """转换为后续五阶段继续使用的直接字典。"""

    asks_contribution = "贡献" in draft.normalized_question
    asks_delta_contribution = any(
        marker in draft.normalized_question for marker in ("增量贡献", "增长来源")
    )
    regular_period_comparison = "普通时期" in draft.normalized_question
    stockout_hotspot_question = (
        "断货天数" in draft.normalized_question
        and "仓库" in draft.normalized_question
        and "商品" in draft.normalized_question
    )
    gmv_bridge_question = (
        "GMV" in draft.normalized_question
        and "净营收" in draft.normalized_question
        and "折扣" in draft.normalized_question
        and "退款" in draft.normalized_question
    )
    monthly_peak_question = (
        "峰值" in draft.normalized_question and "大促" in draft.normalized_question
    )
    annual_health_question = "经营增长是否健康" in draft.normalized_question
    year_match = re.search(r"(20\d{2})\s*年", draft.normalized_question)
    comparison_year = year_match.group(1) if year_match else "同年"
    for target in draft.targets:
        target_text = f"{target.goal} {target.question}"
        if (
            "毛利率" in draft.normalized_question
            and {"net_revenue_cents", "gross_profit_cents"} & set(target.metrics)
            and "gross_margin_rate" not in target.metrics
        ):
            target.metrics.append("gross_margin_rate")
        if (
            annual_health_question
            and {"net_revenue_cents", "gross_profit_cents"} & set(target.metrics)
            and "gross_margin_rate" not in target.metrics
        ):
            target.metrics.append("gross_margin_rate")
        if (
            monthly_peak_question
            and "net_revenue_cents" in target.metrics
            and "order_month" in target.dimensions
            and "campaign_name" not in target.dimensions
        ):
            target.dimensions.append("campaign_name")
        if gmv_bridge_question and "net_revenue_cents" in target.metrics:
            for metric in (
                "gross_cents",
                "discount_cents",
                "paid_gmv_cents",
                "recognized_revenue_cents",
                "refund_cents",
            ):
                if metric not in target.metrics:
                    target.metrics.append(metric)
        if (
            "折扣率" in draft.normalized_question
            and "discount_cents" in target.metrics
            and "discount_rate" not in target.metrics
        ):
            target.metrics.append("discount_rate")
        if regular_period_comparison:
            supply_metrics = {"actual_lead_days", "delay_days", "fill_rate"}
            if supply_metrics & set(target.metrics):
                period_rule = (
                    f"{comparison_year}年：活动期使用活动触达月份，"
                    "普通期使用该年其他月份"
                )
            else:
                period_rule = (
                    f"{comparison_year}年：活动期使用原问题指定 campaign_name，"
                    "普通期使用 campaign_name IS NULL 的全年记录"
                )
            target.time_scopes = [period_rule]
            target.question = f"{target.question}；统计口径：{period_rule}"
            if period_rule not in target.comparisons:
                target.comparisons.append(period_rule)
        if (
            asks_contribution
            and "net_revenue_cents" in target.metrics
            and target.dimensions
        ):
            contribution_metric = (
                "net_revenue_delta_contribution_rate"
                if asks_delta_contribution
                else "net_revenue_contribution_rate"
            )
            if contribution_metric not in target.metrics:
                target.metrics.append(contribution_metric)
            contribution_comparison = (
                "基期到当前期的净营收增量及其占总增量比例"
                if asks_delta_contribution
                else "当前期净营收金额及其占总净营收比例"
            )
            if contribution_comparison not in target.comparisons:
                target.comparisons.append(contribution_comparison)
        if (
            "stockout_days" in target.metrics
            and "product" in target.dimensions
            and (
                stockout_hotspot_question
                or any(marker in target_text for marker in ("热点", "具体商品"))
            )
        ):
            for dimension in ("warehouse", "category"):
                if dimension not in target.dimensions:
                    target.dimensions.append(dimension)

    subquestions = [
        {
            "subquestion_id": target.target_id,
            "question": target.question,
            "analysis_goal": (
                f"{target.goal}；比较关系：{'；'.join(target.comparisons)}"
            ),
            "required_metrics": target.metrics,
            "dimensions": target.dimensions,
            "time_scope": "；".join(target.time_scopes),
        }
        for target in draft.targets
    ]
    return {
        **draft.model_dump(mode="json"),
        "subquestions": subquestions,
    }


async def rewrite_question(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
    payload = cast(dict[str, Any], data.input)
    question = str(payload["question"])
    catalog = cast(dict[str, Any], data.require_resource("rewrite_catalog"))
    trace = cast(TraceLog, data.require_resource("trace"))

    try:
        result = await (
            Agently.create_agent(name="lesson24-v2-rewrite")
            .load_yaml_prompt(
                PROMPT_PATH,
                mappings={
                    "question": question,
                    "catalog": catalog,
                },
            )
            .create_execution()
            .async_start()
        )
        rewrite = to_rewrite_result(RewriteDraft.model_validate(dict(result)))
        subquestions = cast(list[dict[str, Any]], rewrite["subquestions"])
    except BaseException as exc:
        trace.log(
            layer="business",
            event_type="business.question.rewritten",
            status="failed",
            subject_id=trace.task_id,
            input={"question": question},
            error=exc,
        )
        raise

    trace.log(
        layer="business",
        event_type="business.question.rewritten",
        status="completed",
        subject_id=trace.task_id,
        input={"question": question},
        output=rewrite,
        facts={
            "requirement_count": len(rewrite["requirements"]),
            "subquestion_count": len(subquestions),
        },
    )
    await data.async_set_state("question", question, emit=False)
    await data.async_set_state("rewrite", rewrite, emit=False)
    await data.async_set_state("sql_tasks", [], emit=False)
    await data.async_set_state("query_results", [], emit=False)
    return subquestions


__all__ = [
    "AnalysisTarget",
    "Requirement",
    "RewriteDraft",
    "rewrite_question",
    "to_rewrite_result",
]
