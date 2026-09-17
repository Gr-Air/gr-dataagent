from __future__ import annotations

from agently import Agently

from integrated_agent.config import load_model_settings

from .contracts import RouteDecision


class DeepSeekIntentModel:
    def __init__(self) -> None:
        load_model_settings()

    async def classify(
        self,
        *,
        text: str,
        offered: list[dict[str, str]],
    ) -> RouteDecision:
        result = await (
            Agently.create_agent("gateway-intent-router")
            .input({"message": text})
            .info({"offered_runtimes": offered})
            .instruct(
                [
                    "只在 offered_runtimes 中选择一个 runtime_key。",
                    "需要企业经营数据库计算、指标查询或经营分析时选 question。",
                    "其他通用任务选 agent，包括搜索、文件生成、Skills、Actions 和沙盒计算。",
                    "不得选择未提供的外部代码智能体。",
                ]
            )
            .output(
                {
                    "runtime_key": (
                        str,
                        "offered_runtimes 中的 runtime_key",
                        "not_null",
                    )
                },
                format="json",
            )
            .async_start()
        )
        return RouteDecision.model_validate(result)

