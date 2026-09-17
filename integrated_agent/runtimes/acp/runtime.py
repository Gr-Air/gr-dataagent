from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Protocol
from uuid import uuid4

from integrated_agent.gateway import GatewayEvent, GatewayRequest

from .client import CodexAcpClient


class AcpSession(Protocol):
    session_id: str | None

    def prompt_stream(self, text: str) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


class AcpAgentRuntime:
    def __init__(
        self,
        client_factory: Callable[[], AcpSession] | None = None,
    ) -> None:
        self._client_factory = client_factory or (
            lambda: CodexAcpClient(
                cwd=os.environ.get("CODEX_CWD"),
                auto_approve=(
                    os.environ.get("CODEX_AUTO_APPROVE", "").lower()
                    in {"1", "true", "yes"}
                ),
            )
        )
        self._clients: dict[str, AcpSession] = {}

    def _get_client(self, session_id: str) -> AcpSession:
        client = self._clients.get(session_id)
        if client is None:
            client = self._client_factory()
            self._clients[session_id] = client
        return client

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        client = self._get_client(request.session_id)
        yield GatewayEvent(
            "run.created",
            {
                "runtime_key": "codex",
                "run_id": uuid4().hex,
                "acp_session_id": client.session_id,
            },
        )
        try:
            async for delta in client.prompt_stream(request.text):
                yield GatewayEvent(
                    "message.delta",
                    {"delta": delta},
                )
        except Exception as exc:
            yield GatewayEvent(
                "run.failed",
                {
                    "runtime_key": "codex",
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return
        yield GatewayEvent(
            "run.completed",
            {
                "runtime_key": "codex",
                "acp_session_id": client.session_id,
            },
        )

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

