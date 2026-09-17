from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aibot import WSClient, generate_req_id


UPLOAD_MEDIA_INIT = "aibot_upload_media_init"
UPLOAD_MEDIA_CHUNK = "aibot_upload_media_chunk"
UPLOAD_MEDIA_FINISH = "aibot_upload_media_finish"
CHUNK_SIZE = 512 * 1024
MAX_FILE_SIZE = 20 * 1024 * 1024


@dataclass(frozen=True)
class UploadedMedia:
    media_id: str
    media_type: str


class WeComMediaClient:
    def __init__(self, client: WSClient) -> None:
        self.client = client

    async def upload_file(self, path: Path) -> UploadedMedia:
        content = path.read_bytes()
        if not content:
            raise ValueError("不能向企业微信上传空文件")
        if len(content) > MAX_FILE_SIZE:
            raise ValueError("企业微信文件消息不能超过 20MB")

        chunks = [
            content[offset : offset + CHUNK_SIZE]
            for offset in range(0, len(content), CHUNK_SIZE)
        ]
        init_result = await self._request(
            UPLOAD_MEDIA_INIT,
            {
                "type": "file",
                "filename": path.name,
                "total_size": len(content),
                "total_chunks": len(chunks),
                "md5": hashlib.md5(content).hexdigest(),
            },
        )
        upload_id = str(init_result.get("body", {}).get("upload_id", ""))
        if not upload_id:
            raise RuntimeError("企业微信没有返回 upload_id")

        for chunk_index, chunk in enumerate(chunks):
            await self._upload_chunk(
                upload_id=upload_id,
                chunk_index=chunk_index,
                chunk=chunk,
            )

        finish_result = await self._request(
            UPLOAD_MEDIA_FINISH,
            {"upload_id": upload_id},
        )
        body = finish_result.get("body", {})
        media_id = str(body.get("media_id", ""))
        if not media_id:
            raise RuntimeError("企业微信没有返回 media_id")
        return UploadedMedia(
            media_id=media_id,
            media_type=str(body.get("type", "file")),
        )

    async def send_file(self, chat_id: str, path: Path) -> UploadedMedia:
        media = await self.upload_file(path)
        await self.client.send_message(
            chat_id,
            {
                "msgtype": "file",
                "file": {"media_id": media.media_id},
            },
        )
        return media

    async def _upload_chunk(
        self,
        *,
        upload_id: str,
        chunk_index: int,
        chunk: bytes,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                await self._request(
                    UPLOAD_MEDIA_CHUNK,
                    {
                        "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "base64_data": base64.b64encode(chunk).decode("ascii"),
                    },
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(
            f"企业微信文件分片 {chunk_index} 上传失败"
        ) from last_error

    async def _request(
        self,
        command: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        frame = {"headers": {"req_id": generate_req_id(command)}}
        result = await self.client.reply(frame, body, command)
        return dict(result)
