from __future__ import annotations

import argparse
import asyncio
import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import httpx
import uvicorn

from integrated_agent.runtimes.question.models import Claim, TaskRequest, TaskResult
from integrated_agent.runtimes.question.service import QuestionTaskService
from integrated_agent.runtimes.question.stores import (
    InMemoryEventStore,
    InMemoryTaskStore,
)
from integrated_agent.transports.http import create_question_api


ROOT = Path(__file__).parent


class TimedWorker:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.active = 0
        self.peak_active = 0
        self._lock = asyncio.Lock()

    async def execute_complex_task(self, request: TaskRequest) -> TaskResult:
        async with self._lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            await asyncio.sleep(self.delay_seconds)
            return TaskResult(
                task_id=request.task_id,
                status="completed",
                answer="load-test-ok",
                claims=[Claim(claim_id="load-claim", text="load-test-ok")],
                evidence=[],
                evidence_refs=[],
                data_snapshot_id="lesson23-analysis-b7ad59fddab30331",
                trace_ref=(ROOT / "load_test.py").resolve().as_uri(),
            )
        finally:
            async with self._lock:
                self.active -= 1


@dataclass(frozen=True)
class Scenario:
    name: str
    request_count: int
    client_concurrency: int
    worker_count: int
    queue_capacity: int
    worker_delay_seconds: float


def parse_scenario() -> tuple[Scenario, Path]:
    parser = argparse.ArgumentParser(
        description="用可调参数压测问数服务的任务受理、SSE终态和背压行为"
    )
    parser.add_argument("--name", default="custom-load")
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--client-concurrency", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--queue-capacity", type=int, default=32)
    parser.add_argument("--worker-delay-ms", type=float, default=50.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "fixtures/concurrency_report.json",
    )
    args = parser.parse_args()
    positive_values = {
        "requests": args.requests,
        "client-concurrency": args.client_concurrency,
        "workers": args.workers,
        "queue-capacity": args.queue_capacity,
        "worker-delay-ms": args.worker_delay_ms,
    }
    invalid = [name for name, value in positive_values.items() if value <= 0]
    if invalid:
        parser.error(f"以下参数必须大于0：{', '.join(invalid)}")
    return (
        Scenario(
            name=args.name,
            request_count=args.requests,
            client_concurrency=args.client_concurrency,
            worker_count=args.workers,
            queue_capacity=args.queue_capacity,
            worker_delay_seconds=args.worker_delay_ms / 1_000,
        ),
        args.output,
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * ratio))
    return ordered[index]


async def run_scenario(scenario: Scenario) -> dict[str, Any]:
    worker = TimedWorker(scenario.worker_delay_seconds)
    events = InMemoryEventStore()
    service = QuestionTaskService(
        worker=worker,
        tasks=InMemoryTaskStore(),
        events=events,
        worker_count=scenario.worker_count,
        queue_capacity=scenario.queue_capacity,
    )
    app = create_question_api(
        service,
        static_root=ROOT / "static",
        artifacts_root=ROOT / "workspace/artifacts",
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)

    start = time.perf_counter()
    latencies_ms: list[float] = []
    accepted: list[dict[str, Any]] = []
    rejected = 0
    semaphore = asyncio.Semaphore(scenario.client_concurrency)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=30.0,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=scenario.client_concurrency + 10,
                max_keepalive_connections=scenario.client_concurrency + 10,
            ),
        ) as client:
            async def submit(index: int) -> None:
                nonlocal rejected
                async with semaphore:
                    request_started = time.perf_counter()
                    response = await client.post(
                        "/v1/tasks",
                        json={"question": f"load question {index}"},
                    )
                    latencies_ms.append(
                        (time.perf_counter() - request_started) * 1_000
                    )
                    if response.status_code == 202:
                        accepted.append(response.json())
                    elif response.status_code == 503:
                        rejected += 1
                    else:
                        raise RuntimeError(
                            f"unexpected status {response.status_code}: "
                            f"{response.text}"
                        )

            await asyncio.gather(
                *(submit(index) for index in range(scenario.request_count))
            )

            async def consume_events(task: dict[str, Any]) -> bool:
                response = await client.get(str(task["events_url"]))
                return (
                    response.status_code == 200
                    and "event: task.completed" in response.text
                )

            sse_results = await asyncio.gather(
                *(consume_events(task) for task in accepted)
            )
        elapsed = time.perf_counter() - start
    finally:
        server.should_exit = True
        await server_task

    completed = sum(sse_results)
    return {
        "name": scenario.name,
        "request_count": scenario.request_count,
        "accepted": len(accepted),
        "rejected_503": rejected,
        "sse_completed": completed,
        "worker_count": scenario.worker_count,
        "queue_capacity": scenario.queue_capacity,
        "worker_delay_seconds": scenario.worker_delay_seconds,
        "observed_peak_workers": worker.peak_active,
        "elapsed_seconds": round(elapsed, 4),
        "completed_throughput_per_second": round(completed / elapsed, 2),
        "submit_latency_ms": {
            "p50": round(median(latencies_ms), 3),
            "p95": round(_percentile(latencies_ms, 0.95), 3),
            "max": round(max(latencies_ms, default=0.0), 3),
        },
    }


async def main(scenario: Scenario, output: Path) -> None:
    report = {
        "method": (
            "real localhost HTTP + FastAPI + bounded QuestionTaskService + concurrent "
            "SSE replay; deterministic worker isolates service-layer pressure"
        ),
        "scenario": await run_scenario(scenario),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    selected_scenario, report_path = parse_scenario()
    asyncio.run(main(selected_scenario, report_path))
