from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class GatewayAttachment:
    path: Path
    filename: str
    mime_type: str | None = None


@dataclass(frozen=True)
class GatewayRequest:
    text: str
    session_id: str
    attachments: tuple[GatewayAttachment, ...] = ()


@dataclass(frozen=True)
class GatewayEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_key: str


class IntentModel(Protocol):
    async def classify(
        self,
        *,
        text: str,
        offered: list[dict[str, str]],
    ) -> RouteDecision: ...


class AgentRuntime(Protocol):
    def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayEvent]: ...

