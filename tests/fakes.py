from __future__ import annotations

from pathlib import Path
from typing import Any


async def fake_question_runner(
    question: str,
    *,
    task_id: str,
    output_directory: Path,
) -> dict[str, Any]:
    del question
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "run.json").write_text("{}", encoding="utf-8")
    return {
        "task_id": task_id,
        "status": "completed",
        "data_snapshot_id": "lesson23-analysis-b7ad59fddab30331",
        "final_answer": {
            "answer": "2025 年 618 规模增长，但缺货损失上升，增长质量需要继续观察。",
            "claims": [
                {
                    "claim_id": "claim-1",
                    "text": "2025 年 618 的净营收高于 2024 年。",
                    "evidence_ids": ["evidence-1"],
                }
            ],
        },
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "query_id": "query-1",
                "result_ref": (
                    "sqlite-result://lesson23-analysis-b7ad59fddab30331/query-1"
                ),
                "summary": "返回两年 618 净营收对比。",
                "analysis_goal": "比较 618 经营规模",
                "columns": [
                    "paid_gmv_cents_2024",
                    "paid_gmv_cents_2025",
                    "net_revenue_cents_2024",
                    "net_revenue_cents_2025",
                ],
                "rows_preview": [
                    {
                        "paid_gmv_cents_2024": 249_810_000,
                        "paid_gmv_cents_2025": 293_020_000,
                        "net_revenue_cents_2024": 231_830_000,
                        "net_revenue_cents_2025": 273_690_000,
                    }
                ],
                "column_semantics": {
                    column: {
                        "kind": "currency",
                        "storage_unit": "人民币分",
                        "display_unit": "人民币元或万元",
                    }
                    for column in [
                        "paid_gmv_cents_2024",
                        "paid_gmv_cents_2025",
                        "net_revenue_cents_2024",
                        "net_revenue_cents_2025",
                    ]
                },
            }
        ],
    }
