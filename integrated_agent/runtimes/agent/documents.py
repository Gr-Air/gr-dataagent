from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from agently.core import TaskWorkspace

from integrated_agent.storage import ArtifactStore, StoredArtifact

from .renderers import DOCUMENT_MIME_TYPES, render_document


DOCUMENT_SKILL_IDS = ("docx", "xlsx", "pdf", "pptx")


class DocumentArtifactAction:
    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store

    @staticmethod
    def normalize_title(value: object) -> str:
        title = re.sub(
            r"[^0-9A-Za-z._\-\u4e00-\u9fff]+",
            "_",
            str(value).strip(),
        ).strip("._")
        return (title or "企业经营分析")[:80]

    @staticmethod
    def validate_skill_id(skill_id: str) -> str:
        if skill_id not in DOCUMENT_SKILL_IDS:
            raise ValueError(f"未授权的文档 Skill：{skill_id}")
        return skill_id

    @staticmethod
    def normalize_sections(
        value: object,
    ) -> list[tuple[str, list[str]]]:
        if not isinstance(value, list):
            raise ValueError("模型未返回 sections 列表")
        sections: list[tuple[str, list[str]]] = []
        for item in value[:12]:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading", "")).strip()[:120]
            raw_paragraphs = item.get("paragraphs", [])
            if not heading or not isinstance(raw_paragraphs, list):
                continue
            paragraphs = [
                str(paragraph).strip()[:2_000]
                for paragraph in raw_paragraphs[:20]
                if str(paragraph).strip()
            ]
            if paragraphs:
                sections.append((heading, paragraphs))
        if not sections:
            raise ValueError("模型未返回可写入文件的章节")
        return sections

    async def execute(
        self,
        *,
        task_workspace: TaskWorkspace,
        skill_id: str,
        title: str,
        sections_json: str,
    ) -> dict[str, object]:
        skill_id = self.validate_skill_id(skill_id)
        if len(sections_json.encode("utf-8")) > 64_000:
            raise ValueError("模型生成的文档结构超过 64KB 上限")
        sections = self.normalize_sections(json.loads(sections_json))
        content = render_document(skill_id, title, sections)
        workspace_filename = f"outputs/{uuid4().hex}-{title}.{skill_id}"
        write_result = await task_workspace.materialize_file(
            workspace_filename,
            content,
            source={"owner": "document_artifact_action", "skill_id": skill_id},
            media_type=DOCUMENT_MIME_TYPES[skill_id],
        )
        workspace_path = task_workspace.resolve_file_path(write_result.path)
        file_info = cast(
            dict[str, Any],
            task_workspace.inspect_file(write_result.path),
        )
        if (
            int(file_info["bytes"]) != len(content)
            or str(file_info["sha256"]) != write_result.sha256
        ):
            raise RuntimeError("TaskWorkspace 文件回读校验失败")
        workspace_content = workspace_path.read_bytes()
        if sha256(workspace_content).hexdigest() != write_result.sha256:
            raise RuntimeError("TaskWorkspace 文件发布前摘要校验失败")
        artifact = self.artifact_store.save(
            filename=f"{title}.{skill_id}",
            content=workspace_content,
            mime_type=DOCUMENT_MIME_TYPES[skill_id],
        )
        return {
            "artifact_id": artifact.artifact_id,
            "filename": artifact.filename,
            "size_bytes": artifact.size_bytes,
            "workspace_path": write_result.path,
            "workspace_sha256": write_result.sha256,
        }

    def resolve_action_result(
        self,
        action_result: dict[str, Any],
    ) -> StoredArtifact:
        if action_result.get("status") != "success":
            raise RuntimeError(
                str(action_result.get("error") or "文件 Action 执行失败")
            )
        output = cast(dict[str, object], action_result.get("result"))
        artifact = self.artifact_store.resolve(
            str(output["artifact_id"]),
            str(output["filename"]),
        )
        if artifact is None:
            raise RuntimeError("文件 Action 已完成，但产物无法回读")
        return artifact

