from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .models import ChartSeries, ChartSpec


MAX_CHARTS = 3
MAX_CATEGORIES = 12
MAX_SERIES = 4

LABELS = {
    "paid_gmv_cents": "支付 GMV",
    "net_revenue_cents": "净营收",
    "gross_profit_cents": "毛利",
    "refund_amount_cents": "退款金额",
    "shortage_loss_cents": "缺货损失",
    "order_count": "订单量",
    "refund_rate": "退款率",
    "gross_margin_rate": "毛利率",
    "fulfillment_rate": "履约率",
    "conversion_rate": "转化率",
}


def _label(column: str) -> str:
    normalized = re.sub(r"_(20\d{2})$", "", column)
    if normalized in LABELS:
        return LABELS[normalized]
    return normalized.replace("_cents", "").replace("_", " ")


def _unit(kind: str) -> str:
    return {
        "currency": "万元",
        "ratio": "%",
        "percentage_point": "个百分点",
        "duration": "天",
    }.get(kind, "数值")


def _scale(value: Any, kind: str) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if kind == "currency":
        return round(numeric / 1_000_000, 4)
    if kind in {"ratio", "percentage_point"}:
        return round(numeric * 100, 4)
    return round(numeric, 4)


def _split_year_column(column: str) -> tuple[str, str] | None:
    year_at_end = re.fullmatch(r"(.+)_(20\d{2})", column)
    if year_at_end is not None:
        return year_at_end.group(1), year_at_end.group(2)

    year_before_suffix = re.fullmatch(
        r"(.+)_(20\d{2})(_(?:cents|rate|count|days))",
        column,
    )
    if year_before_suffix is None:
        return None
    metric, year, suffix = year_before_suffix.groups()
    return f"{metric}{suffix}", year


def _year_comparison(
    evidence: dict[str, Any],
    *,
    chart_index: int,
) -> list[ChartSpec]:
    rows = list(evidence.get("rows_preview", []))
    if not rows:
        return []
    first_row = dict(rows[0])
    semantics = dict(evidence.get("column_semantics", {}))
    grouped: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    for column, raw_value in first_row.items():
        year_column = _split_year_column(str(column))
        if year_column is None:
            continue
        base, year = year_column
        if base.startswith(("delta_", "yoy_", "growth_")):
            continue
        kind = str(dict(semantics.get(column, {})).get("kind", "plain"))
        value = _scale(raw_value, kind)
        if value is not None:
            grouped[kind][f"{base}:{year}"] = (base, value)

    charts: list[ChartSpec] = []
    evidence_id = str(evidence.get("evidence_id", ""))
    for kind, values in grouped.items():
        bases = sorted({base for base, _value in values.values()})
        years = sorted({key.rsplit(":", 1)[1] for key in values})
        if len(years) < 2 or not bases:
            continue
        series = [
            ChartSeries(
                name=year,
                values=[
                    values.get(f"{base}:{year}", (base, 0.0))[1]
                    for base in bases
                ],
            )
            for year in years[:MAX_SERIES]
        ]
        charts.append(
            ChartSpec(
                chart_id=f"chart-{chart_index + len(charts) + 1}",
                title=f"{evidence.get('analysis_goal') or '经营指标'}年度对比",
                chart_type="bar",
                unit=_unit(kind),
                categories=[_label(base) for base in bases],
                series=series,
                evidence_ids=[evidence_id] if evidence_id else [],
            )
        )
    return charts


def _row_comparison(
    evidence: dict[str, Any],
    *,
    chart_index: int,
) -> list[ChartSpec]:
    rows = [dict(row) for row in list(evidence.get("rows_preview", []))]
    if len(rows) < 2:
        return []
    columns = [str(column) for column in evidence.get("columns", [])]
    semantics = dict(evidence.get("column_semantics", {}))
    dimension = next(
        (
            column
            for column in columns
            if any(not isinstance(row.get(column), (int, float)) for row in rows)
        ),
        None,
    )
    if dimension is None:
        return []
    measures: dict[str, list[str]] = defaultdict(list)
    for column in columns:
        if column == dimension:
            continue
        kind = str(dict(semantics.get(column, {})).get("kind", "plain"))
        if all(_scale(row.get(column), kind) is not None for row in rows):
            measures[kind].append(column)

    charts: list[ChartSpec] = []
    evidence_id = str(evidence.get("evidence_id", ""))
    categories = [str(row.get(dimension, "")) for row in rows[:MAX_CATEGORIES]]
    for kind, selected in measures.items():
        series = [
            ChartSeries(
                name=_label(column),
                values=[
                    _scale(row.get(column), kind) or 0.0
                    for row in rows[:MAX_CATEGORIES]
                ],
            )
            for column in selected[:MAX_SERIES]
        ]
        if not series:
            continue
        charts.append(
            ChartSpec(
                chart_id=f"chart-{chart_index + len(charts) + 1}",
                title=str(evidence.get("analysis_goal") or "经营指标对比"),
                chart_type="line" if len(categories) > 6 else "bar",
                unit=_unit(kind),
                categories=categories,
                series=series,
                evidence_ids=[evidence_id] if evidence_id else [],
            )
        )
    return charts


def build_charts(evidence: list[dict[str, Any]]) -> list[ChartSpec]:
    charts: list[ChartSpec] = []
    for item in evidence:
        charts.extend(_year_comparison(item, chart_index=len(charts)))
        if len(charts) >= MAX_CHARTS:
            break
        charts.extend(_row_comparison(item, chart_index=len(charts)))
        if len(charts) >= MAX_CHARTS:
            break
    return charts[:MAX_CHARTS]
