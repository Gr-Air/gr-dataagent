from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from agently import Agently
from agently.core import PluginManager
from agently.types.data.request import AgentlyRequestData
from agently.utils.Settings import Settings
from fastapi.testclient import TestClient

from integrated_agent.gateway import (
    AgentGateway,
    GatewayEvent,
    GatewayRequest,
    RouteDecision,
)
from integrated_agent.runtimes.agent import AgentlyAgentRuntime
from integrated_agent.runtimes.question.models import TaskRequest, TaskResult
from integrated_agent.runtimes.question.service import QuestionTaskService
from integrated_agent.runtimes.question.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.storage import ArtifactStore
from integrated_agent.transports.http import create_question_api


class ScriptedProtocolRequester:
    name = "ScriptedProtocolRequester"
    DEFAULT_SETTINGS: dict[str, Any] = {}
    prompts: list[str] = []
    selected_skill_key = "skill-option:1"

    def __init__(self, prompt: Any, settings: Any) -> None:
        self.prompt = prompt
        self.settings = settings

    @staticmethod
    def _on_register() -> None:
        pass

    @staticmethod
    def _on_unregister() -> None:
        pass

    def generate_request_data(self) -> AgentlyRequestData:
        prompt_text = self.prompt.to_text()
        type(self).prompts.append(prompt_text)
        output_prompt = self.prompt.get("output", {}) or {}
        output_keys = (
            list(output_prompt)
            if isinstance(output_prompt, dict)
            else []
        )
        return AgentlyRequestData(
            client_options={},
            headers={},
            data={
                "prompt_text": prompt_text,
                "input": self.prompt.get("input"),
                "info": self.prompt.get("info"),
                "output_keys": output_keys,
            },
            request_options={"stream": True},
            request_url="test://document-plan",
        )

    async def request_model(
        self,
        request_data: AgentlyRequestData,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        if "selected_keys" in request_data.data["output_keys"]:
            info = request_data.data.get("info") or {}
            if info.get("offered_skills"):
                selected_keys = [type(self).selected_skill_key]
            else:
                offered_blocks = info.get("offered_context_blocks") or []
                selected_keys = [
                    str(offered_blocks[0]["block_key"])
                ] if offered_blocks else []
            payload: dict[str, Any] = {"selected_keys": selected_keys}
        else:
            payload = {
                "title": "经营复盘",
                "summary": "经营复盘文件已经生成。",
                "sections": [
                    {
                        "heading": "摘要",
                        "paragraphs": ["本文件用于验证 Skills 文件回传链路。"],
                    },
                    {
                        "heading": "后续建议",
                        "paragraphs": ["打开下载文件并检查中文显示。"],
                    },
                ],
            }
        yield "message", json.dumps(payload, ensure_ascii=False)

    async def broadcast_response(
        self,
        response_generator: AsyncGenerator[tuple[str, Any], None],
    ) -> AsyncGenerator[tuple[str, Any], None]:
        response_text = ""
        async for event, data in response_generator:
            if event == "message":
                response_text += str(data)
                yield "delta", str(data)
        yield "done", response_text


def create_test_agent(agent_name: str) -> Any:
    settings = Settings(
        name=f"integrated-agent-{agent_name}-settings",
        parent=Agently.settings,
    )
    plugin_manager = PluginManager(
        settings,
        parent=Agently.plugin_manager,
        name=f"integrated-agent-{agent_name}-plugins",
    )
    plugin_manager.register(
        "ModelRequester",
        ScriptedProtocolRequester,
        activate=True,
    )
    return Agently.AgentType(
        plugin_manager,
        parent_settings=settings,
        name=f"integrated-agent-{agent_name}",
    )


class DocumentsIntent:
    async def classify(
        self,
        *,
        text: str,
        offered: list[dict[str, str]],
    ) -> RouteDecision:
        del text, offered
        return RouteDecision(runtime_key="agent")


class UnusedRuntime:
    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncGenerator[GatewayEvent, None]:
        raise AssertionError(request)
        yield


class IdleWorker:
    async def execute_complex_task(
        self,
        request: TaskRequest,
    ) -> TaskResult:
        raise AssertionError(request)


@pytest.fixture
def agent_runtime(tmp_path: Path) -> AgentlyAgentRuntime:
    ScriptedProtocolRequester.prompts = []
    ScriptedProtocolRequester.selected_skill_key = "skill-option:1"
    return AgentlyAgentRuntime(
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        public_base_url="http://127.0.0.1:8000",
        skills_root=Path(__file__).parents[1] / "skills",
        registry_root=tmp_path / "skills_registry",
        workspace_root=tmp_path / "workspace",
        agent_factory=create_test_agent,
        sandbox="trusted_local",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "prompt",
        "selected_skill_key",
        "skill_heading",
        "suffix",
        "signature",
    ),
    [
        (
            "生成一份《经营复盘》Word 文档",
            "skill-option:1",
            "DOCX 文件生成",
            ".docx",
            b"PK",
        ),
        (
            "生成一份《经营复盘》Excel 工作簿",
            "skill-option:2",
            "XLSX 文件生成",
            ".xlsx",
            b"PK",
        ),
        (
            "生成一份《经营复盘》PDF",
            "skill-option:3",
            "PDF 文件生成",
            ".pdf",
            b"%PDF-",
        ),
        (
            "生成一份《经营复盘》PPT",
            "skill-option:4",
            "PPTX 文件生成",
            ".pptx",
            b"PK",
        ),
    ],
)
async def test_agently_skill_generates_real_file(
    agent_runtime: AgentlyAgentRuntime,
    prompt: str,
    selected_skill_key: str,
    skill_heading: str,
    suffix: str,
    signature: bytes,
) -> None:
    ScriptedProtocolRequester.selected_skill_key = selected_skill_key
    events = [
        event
        async for event in agent_runtime.stream(
            GatewayRequest(prompt, "course-user")
        )
    ]
    artifact_event = next(
        event for event in events if event.type == "artifact.ready"
    )
    artifact_path = Path(str(artifact_event.data["path"]))

    assert artifact_event.data["provider"] == "agently-agent-skill-action"
    assert str(artifact_event.data["filename"]).endswith(suffix)
    assert artifact_path.read_bytes().startswith(signature)
    assert str(artifact_event.data["download_url"]).startswith(
        "http://127.0.0.1:8000/v1/artifacts/"
    )
    assert len(ScriptedProtocolRequester.prompts) == 4
    assert "offered_skills" in ScriptedProtocolRequester.prompts[0]
    assert skill_heading in ScriptedProtocolRequester.prompts[-1]
    workspace_files = tuple(
        agent_runtime.workspace_root.rglob(f"*{suffix}")
    )
    assert len(workspace_files) == 1
    assert "/agent_sessions/course-user/outputs/" in workspace_files[0].as_posix()


@pytest.mark.asyncio
async def test_agent_runtime_emits_normalized_artifact_event(
    agent_runtime: AgentlyAgentRuntime,
) -> None:
    events = [
        event
        async for event in agent_runtime.stream(
            GatewayRequest(
                "生成一份《经营复盘》Word 文档",
                "course-user",
            )
        )
    ]

    assert [event.type for event in events] == [
        "run.created",
        "status.update",
        "message.delta",
        "artifact.ready",
        "run.completed",
    ]
    assert events[3].data["download_url"].startswith(
        "http://127.0.0.1:8000/"
    )
    assert events[3].data["provider"] == "agently-agent-skill-action"


@pytest.mark.asyncio
async def test_message_to_skill_action_and_download_is_end_to_end(
    tmp_path: Path,
) -> None:
    artifacts_root = tmp_path / "artifacts"
    agent_runtime = AgentlyAgentRuntime(
        artifact_store=ArtifactStore(artifacts_root),
        public_base_url="http://127.0.0.1:8000",
        skills_root=Path(__file__).parents[1] / "skills",
        registry_root=tmp_path / "skills_registry",
        workspace_root=tmp_path / "workspace",
        agent_factory=create_test_agent,
        sandbox="trusted_local",
    )
    gateway = AgentGateway(
        intent_model=DocumentsIntent(),
        runtimes={
            "agent": agent_runtime,
            "question": UnusedRuntime(),
        },
    )

    events = [
        event
        async for event in gateway.stream(
            GatewayRequest(
                "请生成一份《经营复盘》Word 文档",
                "wecom-user",
            )
        )
    ]
    artifact_event = next(
        event for event in events if event.type == "artifact.ready"
    )
    app = create_question_api(
        QuestionTaskService(
            worker=IdleWorker(),
            tasks=InMemoryTaskStore(),
            events=InMemoryEventStore(),
        ),
        static_root=Path(__file__).parents[1] / "static",
        artifacts_root=artifacts_root,
    )
    download_url = str(artifact_event.data["download_url"])

    with TestClient(app) as client:
        response = client.get(download_url)

    assert events[0].data == {"runtime_key": "agent", "mode": "auto"}
    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    assert artifact_event.data["filename"].endswith(".docx")
    assert "attachment;" in response.headers["content-disposition"]
