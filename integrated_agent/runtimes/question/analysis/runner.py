"""问数应用 v2 的 CLI 主入口。"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from integrated_agent.config import PROJECT_ROOT, load_model_settings

from .workflows.main_flow import run_question as run_flow


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_ROOT / "data/business_analysis.sqlite"
DEFAULT_LOGS_DIR = PROJECT_ROOT / "logs/question-data"


async def run_question(
    question: str,
    *,
    task_id: str,
    output_directory: Path,
) -> dict[str, Any]:
    """使用 v2 完成一次问数并保存 Trace。"""

    load_model_settings()
    return await run_flow(
        question,
        task_id=task_id,
        database_path=DATABASE_PATH,
        output_directory=output_directory,
    )


def _new_task_id() -> str:
    return datetime.now().strftime("manual-%Y%m%d-%H%M%S-%f")


async def _ask_once(question: str, task_id: str, output_directory: Path) -> None:
    run = await run_question(
        question,
        task_id=task_id,
        output_directory=output_directory,
    )
    print(run["final_answer"]["answer"])
    print(f"Trace: {output_directory}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="问数应用 v2")
    parser.add_argument("--question", help="人工测试单个问题")
    parser.add_argument("--task-id", help="人工测试任务标识")
    parser.add_argument("--output-dir", type=Path, help="Trace 输出目录")
    args = parser.parse_args()

    if args.question:
        task_id = args.task_id or _new_task_id()
        output_directory = args.output_dir or DEFAULT_LOGS_DIR / task_id
        await _ask_once(args.question, task_id, output_directory)
        return

    print("问数应用 v2 已启动，输入 exit 或 quit 结束。")
    while True:
        question = input("问题：").strip()
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue
        task_id = _new_task_id()
        await _ask_once(question, task_id, DEFAULT_LOGS_DIR / task_id)


if __name__ == "__main__":
    asyncio.run(main())
