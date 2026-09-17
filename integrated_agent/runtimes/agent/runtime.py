from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote

from agently import Agently
from agently.builtins.actions import Browse, Search
from agently.core import SkillLibrary

from integrated_agent.config import load_model_settings
from integrated_agent.gateway import GatewayEvent, GatewayRequest
from integrated_agent.storage import ArtifactStore, StoredArtifact

from .documents import DOCUMENT_SKILL_IDS, DocumentArtifactAction
from .files import WorkspaceFileService


AgentFactory = Callable[[str], Any]
SandboxMode = Literal["auto", "docker", "trusted_local"]


class AgentlyAgentRuntime:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        public_base_url: str,
        skills_root: Path,
        workspace_root: Path,
        registry_root: Path | None = None,
        file_service: WorkspaceFileService | None = None,
        agent_factory: AgentFactory | None = None,
        sandbox: SandboxMode = "docker",
    ) -> None:
        self.artifact_store = artifact_store
        self.public_base_url = public_base_url.rstrip("/")
        self.skills_root = skills_root
        self.workspace_root = workspace_root
        self.registry_root = (
            registry_root or workspace_root / "skills_registry"
        )
        self.file_service = file_service or WorkspaceFileService(workspace_root)
        self.agent_factory = agent_factory or Agently.create_agent
        self._uses_default_agent_factory = agent_factory is None
        self.sandbox: SandboxMode = sandbox
        self.document_action = DocumentArtifactAction(artifact_store)
        self.skill_library = SkillLibrary(self.registry_root)
        self.skill_revision_refs = tuple(
            self.skill_library.install(
                skills_root / skill_id,
                trust="trusted",
            ).revision_ref
            for skill_id in DOCUMENT_SKILL_IDS
        )

    @staticmethod
    def _safe_session_id(session_id: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", session_id).strip("._")
        return cleaned[:100] or "session"

    @staticmethod
    def _proxy() -> str | None:
        return next(
            (
                os.environ[key]
                for key in (
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "https_proxy",
                    "all_proxy",
                )
                if os.environ.get(key)
            ),
            None,
        )

    def _build_agent(self, session_id: str) -> Any:
        if self._uses_default_agent_factory:
            load_model_settings()
        agent = self.agent_factory(
            f"enterprise-agent-{self._safe_session_id(session_id)}"
        )
        agent.skill_library = self.skill_library
        session_workspace = (
            self.workspace_root / "agent_sessions" / self._safe_session_id(session_id)
        )
        agent.use_task_workspace(session_workspace, mode="read_write")
        agent.use_actions(
            [
                Search(
                    proxy=self._proxy(),
                    timeout=20,
                    backend="auto",
                    region="cn-zh",
                ),
                Browse(
                    proxy=self._proxy(),
                    timeout=20,
                    fallback_order=("bs4",),
                    enable_playwright=False,
                    enable_bs4=True,
                    max_content_length=12_000,
                ),
            ]
        )
        agent.enable_task_workspace_file_actions(
            read=True,
            write=True,
            search=True,
            list_files=True,
            export=True,
        )
        agent.enable_python(sandbox=self.sandbox)
        agent.enable_shell(
            root=session_workspace,
            commands=[
                "pwd",
                "ls",
                "cat",
                "head",
                "tail",
                "wc",
                "python",
                "python3",
            ],
            sandbox=self.sandbox,
        )

        @agent.action_func
        async def create_document_file(
            skill_id: str,
            title: str,
            sections_json: str,
        ) -> dict[str, object]:
            """把结构化章节写入 TaskWorkspace，校验后发布为下载制品。"""
            return await self.document_action.execute(
                task_workspace=agent.task_workspace,
                skill_id=skill_id,
                title=title,
                sections_json=sections_json,
            )

        agent.use_actions(create_document_file)
        return agent

    def _download_url(self, artifact: StoredArtifact) -> str:
        return (
            f"{self.public_base_url}/v1/artifacts/"
            f"{artifact.artifact_id}/{quote(artifact.filename)}"
        )

    def _artifact_event(
        self,
        artifact: StoredArtifact,
        *,
        provider: str,
    ) -> GatewayEvent:
        return GatewayEvent(
            "artifact.ready",
            {
                "artifact_id": artifact.artifact_id,
                "filename": artifact.filename,
                "path": str(artifact.path),
                "mime_type": artifact.mime_type,
                "size_bytes": artifact.size_bytes,
                "download_url": self._download_url(artifact),
                "provider": provider,
            },
        )

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        yield GatewayEvent(
            "run.created",
            {
                "runtime_key": "agent",
                "session_id": request.session_id,
                "attachment_count": len(request.attachments),
            },
        )
        try:
            if request.attachments:
                async for event in self._stream_attachments(request):
                    yield event
            else:
                async for event in self._stream_agent_request(request):
                    yield event
        except Exception as exc:
            yield GatewayEvent(
                "run.failed",
                {
                    "runtime_key": "agent",
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

    async def _stream_attachments(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        for attachment in request.attachments:
            yield GatewayEvent(
                "status.update",
                {
                    "stage": "file_ingestion",
                    "filename": attachment.filename,
                },
            )
            result = self.file_service.process(attachment.path)
            yield GatewayEvent(
                "message.delta",
                {
                    "delta": result.text,
                    "operation_key": result.operation_key,
                },
            )
            if result.artifact_path is not None:
                artifact = self.artifact_store.save(
                    filename=result.artifact_path.name,
                    content=result.artifact_path.read_bytes(),
                    mime_type=result.artifact_mime_type,
                )
                yield self._artifact_event(
                    artifact,
                    provider="workspace-file-action",
                )
        yield GatewayEvent(
            "run.completed",
            {"runtime_key": "agent"},
        )

    async def _stream_agent_request(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        agent = self._build_agent(request.session_id)
        skill_plan = cast(
            dict[str, Any],
            await agent.async_resolve_skills_plan(
                request.text,
                skills=list(self.skill_revision_refs),
                mode="model_decision",
            ),
        )
        selected = cast(
            list[dict[str, Any]],
            skill_plan.get("selected_skills", []),
        )
        if selected:
            async for event in self._stream_document_request(
                agent,
                request,
                selected,
            ):
                yield event
            return
        async for event in self._stream_general_request(agent, request):
            yield event

    async def _stream_general_request(
        self,
        agent: Any,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        execution = (
            agent.create_execution()
            .input(request.text)
            .instruct(
                [
                    "根据任务需要自主调用已挂载的 Actions。",
                    "外部事实先 search，再 browse 关键页面。",
                    "计算或命令执行只能使用已授权的沙盒 Action。",
                    "不要声称执行了没有 Action 记录的操作。",
                ]
            )
            .output(
                {
                    "reply": (
                        str,
                        "给用户的中文回复；使用外部资料时说明来源",
                        "not_null",
                    )
                }
            )
            .strategy("direct")
        )
        result = execution.get_result()
        async for message in result.get_async_generator(type="instant"):
            if (
                getattr(message, "path", None) == "reply"
                and getattr(message, "delta", None)
            ):
                yield GatewayEvent(
                    "message.delta",
                    {"delta": str(message.delta)},
                )
        yield GatewayEvent(
            "run.completed",
            {"runtime_key": "agent"},
        )

    async def _stream_document_request(
        self,
        agent: Any,
        request: GatewayRequest,
        selected: list[dict[str, Any]],
    ) -> AsyncIterator[GatewayEvent]:
        if len(selected) != 1:
            raise ValueError("一次文档任务必须恰好选择一个文档 Skill")
        revision_ref = str(selected[0].get("revision_ref", ""))
        package = self.skill_library.resolve(revision_ref)
        skill_id = self.document_action.validate_skill_id(package.skill_id)
        yield GatewayEvent(
            "status.update",
            {
                "stage": "skill_selected",
                "skill_id": skill_id,
                "revision_ref": revision_ref,
            },
        )
        execution = (
            agent.create_execution()
            .input(
                {
                    "request": request.text,
                    "session_id": request.session_id,
                }
            )
            .instruct(
                [
                    "按照已由模型选择、并由宿主确认的文档 Skill 规划内容。",
                    "只返回结构化内容，不要声称文件已经生成。",
                    "title 使用简洁中文标题。",
                    "sections 至少包含摘要和后续建议。",
                ]
            )
            .output(
                {
                    "title": (str, "文件标题，不含扩展名", True),
                    "summary": (str, "返回给用户的一句话摘要", True),
                    "sections": [
                        {
                            "heading": (str, "章节标题", True),
                            "paragraphs": (
                                [str],
                                "本章节要写入文件的段落",
                                True,
                            ),
                        }
                    ],
                },
                format="json",
            )
            .use_skills([revision_ref], mode="required")
            .strategy("direct")
        )
        planned = cast(dict[str, Any], await execution.async_get_data())
        title = self.document_action.normalize_title(planned.get("title"))
        sections = self.document_action.normalize_sections(
            planned.get("sections")
        )
        action_result = cast(
            dict[str, Any],
            await agent.action.async_execute_action(
                "create_document_file",
                {
                    "skill_id": skill_id,
                    "title": title,
                    "sections_json": json.dumps(
                        [
                            {
                                "heading": heading,
                                "paragraphs": paragraphs,
                            }
                            for heading, paragraphs in sections
                        ],
                        ensure_ascii=False,
                    ),
                },
                purpose=f"执行 {skill_id} Skill 的文件生成步骤",
            ),
        )
        artifact = self.document_action.resolve_action_result(action_result)
        summary = str(planned.get("summary", "")).strip()
        yield GatewayEvent(
            "message.delta",
            {"delta": summary or f"已生成并校验《{title}》。"},
        )
        yield self._artifact_event(
            artifact,
            provider="agently-agent-skill-action",
        )
        yield GatewayEvent(
            "run.completed",
            {
                "runtime_key": "agent",
                "skill_id": skill_id,
                "artifact_count": 1,
            },
        )
