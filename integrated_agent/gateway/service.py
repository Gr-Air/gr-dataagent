from __future__ import annotations

from collections.abc import AsyncIterator

from .contracts import (
    AgentRuntime,
    GatewayEvent,
    GatewayRequest,
    IntentModel,
)


class AgentGateway:
    AUTO_RUNTIMES = {
        "agent": "通用任务，包括搜索、Skills、Actions、文件处理与沙盒计算",
        "question": "查询企业经营数据库、计算指标或分析经营表现",
    }

    def __init__(
        self,
        *,
        intent_model: IntentModel,
        runtimes: dict[str, AgentRuntime],
    ) -> None:
        missing = set(self.AUTO_RUNTIMES) - set(runtimes)
        if missing:
            raise ValueError(f"missing auto-route runtimes: {sorted(missing)}")
        self.intent_model = intent_model
        self.runtimes = dict(runtimes)
        self._session_runtimes: dict[str, str] = {}

    def current_runtime(self, session_id: str) -> str:
        return self._session_runtimes.get(session_id, "auto")

    async def handle_command(self, text: str, *, session_id: str) -> str:
        parts = text.split(maxsplit=1)
        if parts[0] != "/agent":
            raise ValueError("unsupported command")
        requested = parts[1].strip() if len(parts) == 2 else ""
        if not requested:
            current = self.current_runtime(session_id)
            return (
                f"当前运行时：{current}。"
                "用法：/agent auto | agent | question | codex"
            )
        if requested != "auto" and requested not in self.runtimes:
            raise ValueError(f"unknown runtime: {requested}")
        self._session_runtimes[session_id] = requested
        return f"已切换到 {requested} 运行时。"

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        runtime_key = self.current_runtime(request.session_id)
        route_mode = "manual"

        if request.attachments:
            runtime_key = "agent"
            route_mode = "attachment"
        elif runtime_key == "auto":
            offered = [
                {"runtime_key": key, "description": description}
                for key, description in self.AUTO_RUNTIMES.items()
            ]
            decision = await self.intent_model.classify(
                text=request.text,
                offered=offered,
            )
            runtime_key = decision.runtime_key
            if runtime_key not in self.AUTO_RUNTIMES:
                raise ValueError(
                    f"intent model selected unauthorized runtime: {runtime_key}"
                )
            route_mode = "auto"

        yield GatewayEvent(
            "route.selected",
            {"runtime_key": runtime_key, "mode": route_mode},
        )
        runtime = self.runtimes.get(runtime_key)
        if runtime is None:
            raise ValueError(f"runtime is unavailable: {runtime_key}")
        async for event in runtime.stream(request):
            yield event

