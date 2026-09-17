from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from integrated_agent.gateway import GatewayEvent, GatewayRequest


class QuestionServiceRuntime:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def stream(
        self,
        request: GatewayRequest,
    ) -> AsyncIterator[GatewayEvent]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            trust_env=False,
        ) as client:
            response = await client.post(
                "/v1/tasks",
                json={
                    "question": request.text,
                    "requester": request.session_id,
                    "channel": "gateway",
                },
            )
            response.raise_for_status()
            accepted = cast(dict[str, Any], response.json())
            yield GatewayEvent(
                "run.created",
                {
                    "runtime_key": "question",
                    "task_id": accepted["task_id"],
                },
            )
            answer_sent = False
            async with client.stream(
                "GET",
                str(accepted["events_url"]),
            ) as stream:
                stream.raise_for_status()
                event_type = ""
                data_lines: list[str] = []
                async for line in stream.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line.removeprefix("event:").strip()
                    elif line.startswith("data:"):
                        data_lines.append(
                            line.removeprefix("data:").strip()
                        )
                    elif not line and event_type:
                        payload = json.loads("\n".join(data_lines))
                        data = cast(dict[str, Any], payload.get("data", {}))
                        if event_type in {"stage.started", "stage.completed"}:
                            yield GatewayEvent(
                                "status.update",
                                {
                                    "event_type": event_type,
                                    "stage": data.get("stage"),
                                },
                            )
                        elif event_type == "chart.ready":
                            yield GatewayEvent("chart.ready", data)
                        elif event_type == "evidence.ready":
                            yield GatewayEvent("evidence.ready", data)
                        elif event_type == "answer.ready":
                            answer = str(data.get("answer", ""))
                            if answer:
                                answer_sent = True
                                yield GatewayEvent(
                                    "message.delta",
                                    {"delta": answer},
                                )
                        elif event_type == "task.failed":
                            yield GatewayEvent("run.failed", data)
                            return
                        elif event_type == "task.completed":
                            if not answer_sent:
                                snapshot = (
                                    await client.get(
                                        str(accepted["task_url"])
                                    )
                                ).json()
                                answer = str(snapshot["result"]["answer"])
                                yield GatewayEvent(
                                    "message.delta",
                                    {"delta": answer},
                                )
                            yield GatewayEvent(
                                "run.completed",
                                {
                                    "runtime_key": "question",
                                    "task_id": accepted["task_id"],
                                },
                            )
                            return
                        event_type = ""
                        data_lines = []

