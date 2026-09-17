from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, cast

from aibot import WSClient, WSClientOptions
from dotenv import find_dotenv, load_dotenv

from integrated_agent.gateway import AgentGateway
from integrated_agent.gateway.intent import DeepSeekIntentModel
from integrated_agent.runtimes.acp import AcpAgentRuntime
from integrated_agent.runtimes.agent import (
    AgentlyAgentRuntime,
    WorkspaceFileService,
)
from integrated_agent.runtimes.question.client import QuestionServiceRuntime
from integrated_agent.storage import ArtifactStore
from integrated_agent.transports.wecom import (
    WeComAssistant,
    WeComEventPresenter,
    WeComMediaClient,
)


ROOT = Path(__file__).parents[2]


def build_im_assistant(root: Path = ROOT) -> WeComAssistant:
    project_env = root / ".env"
    load_dotenv(
        project_env if project_env.is_file() else find_dotenv(usecwd=True)
    )
    settings = {
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "WECOM_BOT_ID": os.environ.get("WECOM_BOT_ID", ""),
        "WECOM_BOT_SECRET": os.environ.get("WECOM_BOT_SECRET", ""),
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError(
            "缺少运行配置："
            + ", ".join(missing)
            + "。请先在 .env 中填写这些值。"
        )
    question_service_url = os.environ.get(
        "QUESTION_SERVICE_URL",
        "http://127.0.0.1:8000",
    )
    artifact_base_url = os.environ.get(
        "ARTIFACT_BASE_URL",
        question_service_url,
    )
    sandbox = cast(
        Literal["auto", "docker", "trusted_local"],
        os.environ.get("AGENT_SANDBOX", "docker"),
    )
    client = WSClient(
        WSClientOptions(
            bot_id=settings["WECOM_BOT_ID"],
            secret=settings["WECOM_BOT_SECRET"],
        )
    )
    media = WeComMediaClient(client)
    artifact_store = ArtifactStore(root / "workspace/artifacts")
    file_service = WorkspaceFileService(root / "workspace")
    native_agent = AgentlyAgentRuntime(
        artifact_store=artifact_store,
        public_base_url=artifact_base_url,
        skills_root=root / "skills",
        registry_root=root / "workspace/skills_registry",
        workspace_root=root / "workspace",
        file_service=file_service,
        sandbox=sandbox,
    )
    gateway = AgentGateway(
        intent_model=DeepSeekIntentModel(),
        runtimes={
            "agent": native_agent,
            "question": QuestionServiceRuntime(question_service_url),
            "codex": AcpAgentRuntime(),
        },
    )
    presenter = WeComEventPresenter(
        client=client,
        media=media,
        artifact_store=artifact_store,
    )
    return WeComAssistant(
        client=client,
        gateway=gateway,
        file_service=file_service,
        presenter=presenter,
    )

