from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from integrated_agent.gateway import (
    AgentGateway,
    GatewayAttachment,
    GatewayEvent,
    GatewayRequest,
    RouteDecision,
)
from integrated_agent.runtimes.acp import AcpAgentRuntime


class ScriptedIntentModel:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = list(decisions)
        self.offered_history: list[list[dict[str, str]]] = []

    async def classify(
        self,
        *,
        text: str,
        offered: list[dict[str, str]],
    ) -> RouteDecision:
        del text
        self.offered_history.append(offered)
        return RouteDecision(runtime_key=self.decisions.pop(0))


class FakeRuntime:
    def __init__(self, name: str) -> None:
        self.name = name

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        yield GatewayEvent(
            "message.delta",
            {
                "delta": (
                    f"{self.name}:{request.session_id}:{request.text}:"
                    f"{len(request.attachments)}"
                )
            },
        )
        yield GatewayEvent("run.completed", {})


class FakeAcpSession:
    def __init__(self, session_id: str) -> None:
        self.session_id: str | None = session_id
        self.prompts: list[str] = []
        self.closed = False

    async def prompt_stream(self, text: str) -> AsyncIterator[str]:
        self.prompts.append(text)
        yield f"{self.session_id}:{text}"

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_auto_route_offers_execution_runtimes_not_agent_features() -> None:
    intent_model = ScriptedIntentModel(["agent", "question", "agent"])
    gateway = AgentGateway(
        intent_model=intent_model,
        runtimes={
            "agent": FakeRuntime("agent"),
            "question": FakeRuntime("question"),
            "codex": FakeRuntime("codex"),
        },
    )
    search = [
        event
        async for event in gateway.stream(
            GatewayRequest("查一下今天的行业消息", "s1")
        )
    ]
    question = [
        event
        async for event in gateway.stream(
            GatewayRequest("分析去年营收", "s2")
        )
    ]
    document = [
        event
        async for event in gateway.stream(
            GatewayRequest("生成复盘文档", "s3")
        )
    ]

    assert search[0].data == {"runtime_key": "agent", "mode": "auto"}
    assert question[0].data == {
        "runtime_key": "question",
        "mode": "auto",
    }
    assert document[0].data == {"runtime_key": "agent", "mode": "auto"}
    assert search[1].data["delta"].startswith("agent:")
    assert question[1].data["delta"].startswith("question:")
    assert gateway.current_runtime("s1") == "auto"
    assert all(
        {item["runtime_key"] for item in offered}
        == {"agent", "question"}
        for offered in intent_model.offered_history
    )


@pytest.mark.asyncio
async def test_attachment_is_host_routed_to_native_agent_runtime(
    tmp_path: Path,
) -> None:
    source = tmp_path / "report.md"
    source.write_text("# report", encoding="utf-8")
    intent_model = ScriptedIntentModel([])
    gateway = AgentGateway(
        intent_model=intent_model,
        runtimes={
            "agent": FakeRuntime("agent"),
            "question": FakeRuntime("question"),
        },
    )
    events = [
        event
        async for event in gateway.stream(
            GatewayRequest(
                "处理上传文件",
                "s1",
                attachments=(
                    GatewayAttachment(source, source.name),
                ),
            )
        )
    ]

    assert events[0].data == {
        "runtime_key": "agent",
        "mode": "attachment",
    }
    assert events[1].data["delta"].endswith(":1")
    assert intent_model.offered_history == []


@pytest.mark.asyncio
async def test_codex_requires_explicit_switch_and_state_is_per_session() -> None:
    gateway = AgentGateway(
        intent_model=ScriptedIntentModel(["agent"]),
        runtimes={
            "agent": FakeRuntime("agent"),
            "question": FakeRuntime("question"),
            "codex": FakeRuntime("codex"),
        },
    )
    reply = await gateway.handle_command("/agent codex", session_id="s1")
    assert "codex" in reply
    assert gateway.current_runtime("s1") == "codex"
    assert gateway.current_runtime("s2") == "auto"
    events = [
        event
        async for event in gateway.stream(
            GatewayRequest("修改代码", "s1")
        )
    ]
    assert events[1].data["delta"].startswith("codex:")


@pytest.mark.asyncio
async def test_acp_sessions_are_isolated_by_gateway_session() -> None:
    created: list[FakeAcpSession] = []

    def factory() -> FakeAcpSession:
        session = FakeAcpSession(f"acp-{len(created) + 1}")
        created.append(session)
        return session

    runtime = AcpAgentRuntime(factory)
    first = [
        event
        async for event in runtime.stream(GatewayRequest("第一轮", "im-a"))
    ]
    second = [
        event
        async for event in runtime.stream(GatewayRequest("第二轮", "im-a"))
    ]
    other = [
        event
        async for event in runtime.stream(
            GatewayRequest("另一会话", "im-b")
        )
    ]

    assert len(created) == 2
    assert first[1].data["delta"] == "acp-1:第一轮"
    assert second[1].data["delta"] == "acp-1:第二轮"
    assert other[1].data["delta"] == "acp-2:另一会话"

    await runtime.close()
    assert all(item.closed for item in created)

