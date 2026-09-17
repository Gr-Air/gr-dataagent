from __future__ import annotations

from integrated_agent.runtimes.question.charts import build_charts


def test_build_charts_accepts_year_before_storage_unit() -> None:
    evidence = [
        {
            "evidence_id": "e-real-sql",
            "analysis_goal": "比较 618 经营规模",
            "rows_preview": [
                {
                    "net_revenue_2025_cents": 273_691_390,
                    "net_revenue_2024_cents": 231_829_111,
                    "net_revenue_growth_cents": 41_862_279,
                    "net_revenue_growth_rate": 0.1806,
                    "paid_gmv_2025_cents": 293_023_204,
                    "paid_gmv_2024_cents": 249_805_977,
                    "paid_gmv_growth_cents": 43_217_227,
                    "paid_gmv_growth_rate": 0.173,
                }
            ],
            "column_semantics": {
                column: {"kind": "currency"}
                for column in [
                    "net_revenue_2025_cents",
                    "net_revenue_2024_cents",
                    "paid_gmv_2025_cents",
                    "paid_gmv_2024_cents",
                ]
            },
        }
    ]

    charts = build_charts(evidence)

    assert len(charts) == 1
    assert charts[0].categories == ["净营收", "支付 GMV"]
    assert [series.name for series in charts[0].series] == ["2024", "2025"]
    assert charts[0].series[1].values == [273.6914, 293.0232]
