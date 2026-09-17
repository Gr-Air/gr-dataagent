from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from .runner import run_question


RunQuestion = Callable[..., Awaitable[dict[str, Any]]]


class QuestionAnalysisCapability:
    def __init__(
        self,
        *,
        logs_root: Path,
        runner: RunQuestion = run_question,
    ) -> None:
        self.logs_root = logs_root
        self.runner = runner

    async def analyze(
        self,
        *,
        task_id: str,
        question: str,
    ) -> dict[str, Any]:
        output_directory = self.logs_root / task_id
        run = await self.runner(
            question,
            task_id=task_id,
            output_directory=output_directory,
        )
        run["trace_ref"] = (output_directory / "run.json").resolve().as_uri()
        return run
