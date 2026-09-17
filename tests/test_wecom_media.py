from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from integrated_agent.transports.wecom.media import (
    CHUNK_SIZE,
    WeComMediaClient,
)


class FakeWSClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def reply(
        self,
        frame: dict[str, Any],
        body: dict[str, Any],
        command: str,
    ) -> dict[str, Any]:
        self.requests.append((command, body))
        if command == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if command == "aibot_upload_media_finish":
            return {
                "body": {
                    "media_id": "media-1",
                    "type": "file",
                }
            }
        return {"body": {}}

    async def send_message(
        self,
        chat_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.messages.append((chat_id, body))
        return {"errcode": 0}


@pytest.mark.asyncio
async def test_uploads_chunks_then_sends_native_file_message(
    tmp_path: Path,
) -> None:
    path = tmp_path / "经营复盘.docx"
    path.write_bytes(b"x" * (CHUNK_SIZE + 7))
    client = FakeWSClient()

    media = await WeComMediaClient(client).send_file(  # type: ignore[arg-type]
        "chat-1",
        path,
    )

    assert media.media_id == "media-1"
    assert [command for command, _ in client.requests] == [
        "aibot_upload_media_init",
        "aibot_upload_media_chunk",
        "aibot_upload_media_chunk",
        "aibot_upload_media_finish",
    ]
    assert client.requests[0][1]["filename"] == "经营复盘.docx"
    assert client.requests[0][1]["total_chunks"] == 2
    assert client.messages == [
        (
            "chat-1",
            {
                "msgtype": "file",
                "file": {"media_id": "media-1"},
            },
        )
    ]
