from __future__ import annotations

from integrated_agent.runtimes.question.charts import build_charts


def test_build_charts_groups_year_columns_by_unit() -> None:
    charts = build_charts(
        [
            {
                "evidence_id": "e-1",
                "analysis_goal": "比较年度规模",
                "rows_preview": [
                    {
                        "paid_gmv_cents_2024": 249_810_000,
                        "paid_gmv_cents_2025": 293_020_000,
                        "refund_rate_2024": 0.031,
                        "refund_rate_2025": 0.037,
                    }
                ],
                "column_semantics": {
                    "paid_gmv_cents_2024": {"kind": "currency"},
                    "paid_gmv_cents_2025": {"kind": "currency"},
                    "refund_rate_2024": {"kind": "ratio"},
                    "refund_rate_2025": {"kind": "ratio"},
                },
            }
        ]
    )

    assert len(charts) == 2
    assert charts[0].unit == "万元"
    assert charts[0].series[1].values == [293.02]
    assert charts[1].unit == "%"
    assert charts[1].series[0].values == [3.1]
