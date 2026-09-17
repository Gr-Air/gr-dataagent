"""从经营分析数据库生产分阶段使用的业务 Catalog。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


MANAGEMENT_TABLES = {"dataset_metadata", "metric_dictionary"}

DIMENSION_SOURCES = {
    "campaign_name": ("order_performance", "campaign_name"),
    "channel": ("order_performance", "channel"),
    "channel_kind": ("order_performance", "channel_kind"),
    "category": ("order_line_performance", "category"),
    "city": ("order_performance", "city"),
    "region": ("order_performance", "region"),
    "product": ("order_line_performance", "product"),
    "warehouse": ("inventory_daily_analysis", "warehouse"),
    "supplier": ("supply_performance", "supplier"),
    "event_type": ("inventory_exception", "event_type"),
    "reference_type": ("inventory_exception", "reference_type"),
    "order_month": ("order_performance", "order_month"),
    "event_month": ("lost_demand_daily", "event_month"),
    "inventory_month": ("inventory_daily_analysis", "inventory_month"),
    "created_month": ("supply_performance", "created_month"),
}

DATASET_PROFILES: dict[str, dict[str, Any]] = {
    "order_performance": {
        "description": "订单级销售、收入、成本和退款表现",
        "grain": "一行一笔订单",
        "time_field": "order_date",
        "dimensions": [
            "order_year", "order_month", "region", "city", "channel",
            "channel_kind", "campaign_name", "final_state",
        ],
        "measures": [
            "gross_cents", "discount_cents", "paid_gmv_cents",
            "recognized_revenue_cents", "refund_cents", "net_revenue_cents",
            "cogs_cents", "gross_profit_cents", "refunded_order",
        ],
        "metrics": [
            "paid_gmv_cents", "discount_cents", "recognized_revenue_cents",
            "refund_cents", "net_revenue_cents", "cogs_cents",
            "gross_profit_cents", "gross_margin_rate", "refunded_order_rate",
        ],
    },
    "order_line_performance": {
        "description": "订单商品行级销售、收入、成本和退款表现",
        "grain": "一行一笔订单中的一个商品行",
        "time_field": "order_date",
        "dimensions": [
            "order_year", "order_month", "region", "city", "channel",
            "channel_kind", "campaign_name", "product", "category",
            "supplier",
        ],
        "measures": [
            "quantity", "gross_cents", "discount_cents", "paid_gmv_cents",
            "recognized_revenue_cents", "refund_cents", "net_revenue_cents",
            "cogs_cents", "gross_profit_cents", "refunded_line",
        ],
        "metrics": [
            "paid_gmv_cents", "discount_cents", "recognized_revenue_cents",
            "refund_cents", "net_revenue_cents", "cogs_cents",
            "gross_profit_cents", "gross_margin_rate",
        ],
    },
    "funnel_daily": {
        "description": "日、城市、渠道和活动粒度的访问与转化漏斗",
        "grain": "一行代表一天、一个城市、一个渠道和一个活动",
        "time_field": "event_date",
        "dimensions": [
            "event_year", "event_month", "region", "city", "channel",
            "channel_kind", "campaign_name",
        ],
        "measures": [
            "sessions", "viewed_sessions", "cart_sessions", "checkout_sessions",
            "paid_sessions", "abandoned_sessions", "lost_sessions",
        ],
        "metrics": ["conversion_rate"],
    },
    "lost_demand_daily": {
        "description": "缺货造成的真实流失、替代购买和收入损失",
        "grain": "一行代表一天、一个城市、渠道、活动和商品",
        "time_field": "event_date",
        "dimensions": [
            "event_year", "event_month", "region", "city", "channel",
            "channel_kind", "campaign_name", "product", "category",
        ],
        "measures": [
            "gross_lost_events", "true_lost_events", "substituted_events",
            "gross_lost_demand_cents", "estimated_lost_revenue_cents",
            "recovered_by_substitution_cents",
        ],
        "metrics": ["estimated_lost_revenue_cents", "substitution_rate"],
    },
    "inventory_daily_analysis": {
        "description": "仓库商品的每日库存快照和断货事实",
        "grain": "一行代表一天、一个仓库和一个商品",
        "time_field": "inventory_date",
        "dimensions": [
            "inventory_year", "inventory_month", "warehouse", "product",
            "category", "supplier",
        ],
        "measures": [
            "on_hand_qty", "reserved_qty", "available_qty", "stockout_flag",
            "moving_average_cost_cents",
        ],
        "metrics": ["stockout_days"],
    },
    "supply_performance": {
        "description": "采购商品行的订购、到货和交期履约表现",
        "grain": "一行一笔采购单商品行",
        "time_field": "created_date",
        "dimensions": [
            "created_year", "created_month", "supplier", "warehouse", "product",
            "category", "final_state",
        ],
        "measures": [
            "ordered_qty", "received_qty", "planned_lead_days",
            "actual_lead_days", "delay_days", "fill_rate",
        ],
        "metrics": ["actual_lead_days", "delay_days", "fill_rate"],
    },
    "inventory_exception": {
        "description": "质量冻结、盘点调整等库存异常事件",
        "grain": "一行一个库存异常事件",
        "time_field": "event_date",
        "dimensions": [
            "event_year", "event_month", "event_type", "warehouse", "product",
            "category", "supplier", "reference_type",
        ],
        "measures": [
            "delta_on_hand", "after_on_hand", "after_available", "unit_cost_cents",
        ],
        "metrics": [],
    },
}


def _connect_readonly(database_path: Path) -> sqlite3.Connection:
    path = database_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_catalog(database_path: Path) -> dict[str, Any]:
    """从当前 SQLite 快照生产宿主保存的完整业务 Catalog。"""

    with _connect_readonly(database_path) as connection:
        all_table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        table_names = [
            name for name in all_table_names if name not in MANAGEMENT_TABLES
        ]
        tables: dict[str, list[dict[str, str]]] = {}
        columns_by_table: dict[str, set[str]] = {}
        for table in table_names:
            rows = list(connection.execute(f'PRAGMA table_info("{table}")'))
            tables[table] = [
                {"name": str(row[1]), "type": str(row[2]).upper()} for row in rows
            ]
            columns_by_table[table] = {str(row[1]) for row in rows}

        metadata = {
            str(key): str(value)
            for key, value in connection.execute(
                "SELECT key, value FROM dataset_metadata ORDER BY key"
            )
        }
        metrics = [
            {
                "metric_name": str(row[0]),
                "display_name": str(row[1]),
                "source_table": str(row[2]),
                "expression_hint": str(row[3]),
                "unit": str(row[4]),
                "time_basis": str(row[5]),
                "description": str(row[6]),
            }
            for row in connection.execute(
                "SELECT metric_name, display_name, source_table, expression_hint, "
                "unit, time_basis, description "
                "FROM metric_dictionary ORDER BY metric_name"
            )
        ]
        dimension_values = {
            name: [
                str(row[0])
                for row in connection.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL ORDER BY "{column}" LIMIT 40'
                )
            ]
            for name, (table, column) in DIMENSION_SOURCES.items()
            if table in columns_by_table and column in columns_by_table[table]
        }
        campaign_periods: dict[str, dict[str, dict[str, str]]] = {}
        if "order_performance" in tables:
            for name, year, start_date, end_date in connection.execute(
                "SELECT campaign_name, order_year, MIN(order_date), MAX(order_date) "
                "FROM order_performance "
                "WHERE campaign_name IS NOT NULL AND TRIM(campaign_name) <> '' "
                "GROUP BY campaign_name, order_year "
                "ORDER BY campaign_name, order_year"
            ):
                campaign_periods.setdefault(str(name), {})[str(year)] = {
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                }

    missing_profiles = sorted(set(tables) - set(DATASET_PROFILES))
    if missing_profiles:
        raise ValueError(
            "business table has no dataset profile: " + ", ".join(missing_profiles)
        )

    return {
        "tables": tables,
        "datasets": {name: dict(DATASET_PROFILES[name]) for name in tables},
        "metrics": metrics,
        "dimension_values": dimension_values,
        "campaign_periods": campaign_periods,
        "metadata": metadata,
    }


def build_rewrite_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """为问题理解提供完整但紧凑的业务能力视图。"""

    metadata = catalog["metadata"]
    datasets = [
        {"table": table, **profile}
        for table, profile in catalog["datasets"].items()
    ]
    metric_tables = {
        str(metric["metric_name"]): [
            table
            for table, profile in catalog["datasets"].items()
            if metric["metric_name"] in profile["metrics"]
        ]
        for metric in catalog["metrics"]
    }
    metrics = [
        {
            "metric_name": metric["metric_name"],
            "display_name": metric["display_name"],
            "available_tables": metric_tables[str(metric["metric_name"])],
            "unit": metric["unit"],
            "time_basis": metric["time_basis"],
            "description": metric["description"],
        }
        for metric in catalog["metrics"]
    ]
    return {
        "data_range": {
            "start_date": metadata.get("start_date"),
            "end_date": metadata.get("end_date"),
        },
        "datasets": datasets,
        "metrics": metrics,
        "dimension_values": catalog["dimension_values"],
        "campaign_periods": catalog["campaign_periods"],
    }


def _select_sql_tables(
    catalog: dict[str, Any],
    required_metrics: set[str],
    dimensions: set[str],
) -> list[str]:
    profiles = catalog["datasets"]
    exact: list[str] = []
    for table, profile in profiles.items():
        available_metrics = set(profile["metrics"]) | set(profile["measures"])
        if required_metrics <= available_metrics and dimensions <= set(
            profile["dimensions"]
        ):
            exact.append(table)
    if exact:
        return [min(exact, key=lambda table: len(profiles[table]["dimensions"]))]

    uncovered = set(required_metrics)
    selected: list[str] = []
    while uncovered:
        candidates = [
            (
                len(uncovered & (set(profile["metrics"]) | set(profile["measures"]))),
                len(dimensions & set(profile["dimensions"])),
                table,
            )
            for table, profile in profiles.items()
            if table not in selected
        ]
        coverage, _, table = max(candidates, default=(0, 0, ""))
        if coverage == 0:
            break
        selected.append(table)
        profile = profiles[table]
        uncovered -= set(profile["metrics"]) | set(profile["measures"])

    if selected:
        return selected
    dimension_matches = [
        table
        for table, profile in profiles.items()
        if dimensions <= set(profile["dimensions"])
    ]
    return dimension_matches or list(catalog["tables"])


def infer_column_semantics(column: str) -> dict[str, str]:
    """根据稳定列名契约推导证据单位和展示规则。"""

    normalized = column.lower()
    rate_markers = (
        "_rate",
        "_ratio",
        "_share",
        "_contribution",
        "conversion",
    )
    amount_markers = (
        "_cents",
        "gmv",
        "revenue",
        "gross_profit",
        "cogs",
        "discount",
        "refund_amount",
    )
    if (
        normalized.endswith("_pp")
        or "percentage_point" in normalized
        or "rate_change" in normalized
        or "rate_diff" in normalized
        or "margin_change" in normalized
        or "margin_diff" in normalized
    ):
        return {
            "kind": "percentage_point",
            "storage_unit": "小数百分点",
            "display_unit": "个百分点",
        }
    if any(marker in normalized for marker in rate_markers):
        return {
            "kind": "ratio",
            "storage_unit": "小数比率",
            "display_unit": "%",
        }
    if any(marker in normalized for marker in amount_markers):
        return {
            "kind": "currency",
            "storage_unit": "人民币分",
            "display_unit": "人民币元或万元",
        }
    if normalized.endswith("_days") or "lead_days" in normalized:
        return {
            "kind": "duration",
            "storage_unit": "天",
            "display_unit": "天",
        }
    return {
        "kind": "plain",
        "storage_unit": "原始值",
        "display_unit": "原始值",
    }


def format_evidence_value(column: str, value: Any) -> str:
    """把原始查询值转换为最终回答可直接引用的带单位文本。"""

    if value is None:
        return "无数据"
    semantics = infer_column_semantics(column)
    kind = semantics["kind"]
    if kind == "currency" and isinstance(value, (int, float)):
        amount_cents = float(value)
        if abs(amount_cents) >= 1_000_000:
            return f"{amount_cents / 1_000_000:,.2f} 万元"
        return f"{amount_cents / 100:,.2f} 元"
    if kind in {"ratio", "percentage_point"} and isinstance(value, (int, float)):
        scaled = float(value) * 100
        suffix = "%" if kind == "ratio" else " 个百分点"
        precision = 4 if 0 < abs(scaled) < 0.01 else 2
        return f"{scaled:.{precision}f}{suffix}"
    if kind == "duration" and isinstance(value, (int, float)):
        return f"{float(value):.2f} 天"
    return str(value)


def build_sql_catalog(
    catalog: dict[str, Any], subquestion: dict[str, Any]
) -> dict[str, Any]:
    """为一个 SQL 子问题选择所需的物理表、公式和值域。"""

    required_metrics = {
        str(item) for item in subquestion.get("required_metrics", [])
    }
    dimensions = {str(item) for item in subquestion.get("dimensions", [])}
    selected_tables = _select_sql_tables(catalog, required_metrics, dimensions)
    selected_profiles = {
        table: catalog["datasets"][table] for table in selected_tables
    }
    available_dimensions = {
        dimension
        for profile in selected_profiles.values()
        for dimension in profile["dimensions"]
    }
    return {
        "data_range": {
            "start_date": catalog["metadata"].get("start_date"),
            "end_date": catalog["metadata"].get("end_date"),
        },
        "tables": {table: catalog["tables"][table] for table in selected_tables},
        "datasets": selected_profiles,
        "metrics": [
            metric
            for metric in catalog["metrics"]
            if metric["metric_name"] in required_metrics
        ],
        "dimension_values": {
            name: values
            for name, values in catalog["dimension_values"].items()
            if name in available_dimensions
        },
        "campaign_periods": catalog["campaign_periods"],
    }


__all__ = [
    "build_rewrite_catalog",
    "build_sql_catalog",
    "format_evidence_value",
    "infer_column_semantics",
    "load_catalog",
]
