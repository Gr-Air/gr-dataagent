from __future__ import annotations

from typing import Any

from aibot import WSClient, generate_req_id

from integrated_agent.gateway import (
    AgentGateway,
    GatewayAttachment,
    GatewayRequest,
)
from integrated_agent.runtimes.agent import WorkspaceFileService

from .presenter import WeComEventPresenter


class WeComAssistant:
    def __init__(
        self,
        *,
        client: WSClient,
        gateway: AgentGateway,
        file_service: WorkspaceFileService,
        presenter: WeComEventPresenter,
    ) -> None:
        self.client = client
        self.gateway = gateway
        self.file_service = file_service
        self.presenter = presenter
        client.on("authenticated")(self.on_authenticated)
        client.on("message.text")(self.on_text)
        client.on("message.file")(self.on_file)

    @staticmethod
    def session_id(frame: dict[str, Any]) -> str:
        body = frame.get("body", {})
        return str(
            body.get("chatid")
            or body.get("from", {}).get("userid")
            or frame.get("req_id")
        )

    def on_authenticated(self) -> None:
        print("企业微信综合智能助理已认证，默认使用自动路由。")

    async def on_text(self, frame: dict[str, Any]) -> None:
        body = frame.get("body", {})
        text = (body.get("text", {}) or {}).get("content", "").strip()
        if not text:
            return
        session_id = self.session_id(frame)
        if text.startswith("/agent"):
            reply = await self.gateway.handle_command(
                text,
                session_id=session_id,
            )
            stream_id = generate_req_id("agent")
            await self.client.reply_stream(
                frame,
                stream_id,
                reply,
                True,
            )
            return
        request = GatewayRequest(text=text, session_id=session_id)
        await self.presenter.reply(
            frame,
            self.gateway.stream(request),
            session_id=session_id,
        )

    async def on_file(self, frame: dict[str, Any]) -> None:
        body = frame.get("body", {})
        file_info = body.get("file", {}) or {}
        download_url = str(file_info.get("url", ""))
        aes_key = str(file_info.get("aeskey", "")) or None
        if not download_url:
            stream_id = generate_req_id("agent")
            await self.client.reply_stream(
                frame,
                stream_id,
                "文件消息缺少下载地址。",
                True,
            )
            return
        session_id = self.session_id(frame)
        try:
            content, downloaded_name = await self.client.download_file(
                download_url,
                aes_key,
            )
            filename = str(
                downloaded_name
                or file_info.get("filename")
                or file_info.get("name")
                or "upload.bin"
            )
            source_path = self.file_service.save_upload(
                session_id=session_id,
                filename=filename,
                content=content,
            )
        except Exception as exc:
            stream_id = generate_req_id("agent")
            await self.client.reply_stream(
                frame,
                stream_id,
                f"文件下载或保存失败：{exc}",
                True,
            )
            return
        request = GatewayRequest(
            text=f"处理上传文件：{filename}",
            session_id=session_id,
            attachments=(
                GatewayAttachment(
                    path=source_path,
                    filename=filename,
                ),
            ),
        )
        await self.presenter.reply(
            frame,
            self.gateway.stream(request),
            session_id=session_id,
        )

    def run(self) -> None:
        self.client.run()

