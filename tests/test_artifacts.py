from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from integrated_agent.runtimes.question.service import QuestionTaskService
from integrated_agent.runtimes.question.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.storage import ArtifactStore
from integrated_agent.transports.http import create_question_api


class IdleWorker:
    async def execute_complex_task(self, request):
        raise AssertionError(request)


def test_generated_artifact_can_be_downloaded(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    artifact = ArtifactStore(artifacts_root).save(
        filename="经营复盘.docx",
        content=b"document-bytes",
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
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

    with TestClient(app) as client:
        response = client.get(
            f"/v1/artifacts/{artifact.artifact_id}/{artifact.filename}"
        )

    assert response.status_code == 200
    assert response.content == b"document-bytes"
    assert "attachment;" in response.headers["content-disposition"]
