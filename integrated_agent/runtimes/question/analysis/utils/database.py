"""经营分析 SQL 的预检和只读执行。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from .catalog import build_rewrite_catalog, build_sql_catalog, load_catalog


ROW_LIMIT = 200
PREVIEW_ROWS = 50
PROGRESS_STEPS = 2_000_000

FORBIDDEN_SQL = re.compile(
    r"\b(?:ATTACH|ALTER|CREATE|DELETE|DETACH|DROP|INSERT|LOAD_EXTENSION|"
    r"PRAGMA|REINDEX|REPLACE|TRUNCATE|UPDATE|VACUUM)\b",
    re.IGNORECASE,
)
TABLE_REFERENCE = re.compile(
    r"\b(?:FROM|JOIN)\s+([\"`\[]?[A-Za-z_][\w]*[\"`\]]?)",
    re.IGNORECASE,
)
CTE_NAME = re.compile(r"(?:\bWITH|,)\s*([A-Za-z_][\w]*)\s+AS\s*\(", re.IGNORECASE)


def _connect_readonly(database_path: Path) -> sqlite3.Connection:
    path = database_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _sql_fingerprint(sql: str) -> str:
    normalized = " ".join(sql.strip().rstrip(";").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sql_issues(sql: str, known_tables: set[str]) -> list[str]:
    normalized = sql.strip()
    if not normalized:
        return ["SQL is empty"]

    issues: list[str] = []
    if "--" in normalized or "/*" in normalized or "*/" in normalized:
        issues.append("SQL comments are not allowed")
    statement = normalized[:-1].rstrip() if normalized.endswith(";") else normalized
    if ";" in statement:
        issues.append("only one SQL statement is allowed")
    if not re.match(r"^(?:SELECT\b|WITH\b)", statement, re.IGNORECASE):
        issues.append("SQL must start with SELECT or WITH")

    forbidden = sorted(
        {match.group(0).upper() for match in FORBIDDEN_SQL.finditer(statement)}
    )
    if forbidden:
        issues.append(f"forbidden SQL token: {', '.join(forbidden)}")

    cte_names = {match.group(1).lower() for match in CTE_NAME.finditer(statement)}
    referenced_tables = {
        match.group(1).strip('\"`[]') for match in TABLE_REFERENCE.finditer(statement)
    }
    unknown = sorted(
        table
        for table in referenced_tables
        if table.lower() not in known_tables and table.lower() not in cte_names
    )
    if unknown:
        issues.append(f"table is not in analysis catalog: {', '.join(unknown)}")
    if any(table.lower().startswith("sqlite_") for table in referenced_tables):
        issues.append("SQLite system tables are not queryable")
    return issues


def validate_sql(
    database_path: Path,
    subquestion_id: str,
    sql: str,
    snapshot_id: str,
    *,
    attempt_count: int,
) -> dict[str, Any]:
    """校验模型 SQL，并读取它在当前数据库中的真实输出列。"""

    catalog = load_catalog(database_path)
    actual_snapshot_id = str(catalog["metadata"].get("data_snapshot_id", ""))
    issues: list[str] = []
    if actual_snapshot_id != snapshot_id:
        issues.append(
            f"data snapshot mismatch: requested {snapshot_id}, "
            f"database contains {actual_snapshot_id}"
        )
    issues.extend(_sql_issues(sql, {name.lower() for name in catalog["tables"]}))

    columns: list[str] = []
    if not issues:
        statement = sql.strip().rstrip(";")
        try:
            with _connect_readonly(database_path) as connection:
                connection.execute("PRAGMA query_only = ON")
                connection.execute(f"EXPLAIN QUERY PLAN {statement}").fetchall()
                cursor = connection.execute(f"SELECT * FROM ({statement}) LIMIT 0")
                columns = [str(item[0]) for item in cursor.description or ()]
        except sqlite3.Error as exc:
            issues.append(f"{type(exc).__name__}: {exc}")
    if not columns and not issues:
        issues.append("query has no output columns")

    return {
        "subquestion_id": subquestion_id,
        "status": "failed" if issues else "ready",
        "sql": sql or None,
        "expected_columns": [] if issues else columns,
        "issues": issues,
        "attempt_count": attempt_count,
    }


def _failed_result(
    task: dict[str, Any],
    snapshot_id: str,
    *,
    status: Literal["failed", "skipped"],
    error: str,
    started_at: float,
) -> dict[str, Any]:
    sql = str(task.get("sql") or "")
    fingerprint = _sql_fingerprint(sql)
    return {
        "query_id": f"query-{fingerprint[:16]}",
        "subquestion_id": str(task["subquestion_id"]),
        "status": status,
        "sql": task.get("sql"),
        "columns": [],
        "row_count": 0,
        "rows_preview": [],
        "result_ref": None,
        "sql_fingerprint": fingerprint,
        "data_snapshot_id": snapshot_id,
        "elapsed_ms": (time.perf_counter() - started_at) * 1000,
        "truncated": False,
        "error": error,
    }


def execute_sql(
    database_path: Path,
    task: dict[str, Any],
    result_directory: Path,
) -> dict[str, Any]:
    """在只读连接中执行一个已通过预检的 SQL 工作项。"""

    started_at = time.perf_counter()
    catalog = load_catalog(database_path)
    snapshot_id = str(catalog["metadata"].get("data_snapshot_id", ""))
    sql = str(task.get("sql") or "")
    if task.get("status") != "ready" or not sql:
        return _failed_result(
            task,
            snapshot_id,
            status="skipped",
            error="; ".join(task.get("issues", [])) or "SQL task was not ready",
            started_at=started_at,
        )

    issues = _sql_issues(sql, {name.lower() for name in catalog["tables"]})
    if issues:
        return _failed_result(
            task,
            snapshot_id,
            status="failed",
            error="; ".join(issues),
            started_at=started_at,
        )

    fingerprint = _sql_fingerprint(sql)
    query_id = f"query-{fingerprint[:16]}"
    try:
        with _connect_readonly(database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.set_progress_handler(lambda: 1, PROGRESS_STEPS)
            cursor = connection.execute(sql.strip().rstrip(";"))
            columns = [str(item[0]) for item in cursor.description or ()]
            if columns != task["expected_columns"]:
                return _failed_result(
                    task,
                    snapshot_id,
                    status="failed",
                    error=(
                        f"expected columns {task['expected_columns']}, received {columns}"
                    ),
                    started_at=started_at,
                )
            fetched = cursor.fetchmany(ROW_LIMIT + 1)
            truncated = len(fetched) > ROW_LIMIT
            rows = [dict(row) for row in fetched[:ROW_LIMIT]]
    except sqlite3.Error as exc:
        return _failed_result(
            task,
            snapshot_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            started_at=started_at,
        )

    result_directory.mkdir(parents=True, exist_ok=True)
    (result_directory / f"{query_id}.json").write_text(
        json.dumps(
            {
                "query_id": query_id,
                "data_snapshot_id": snapshot_id,
                "columns": columns,
                "row_count": len(rows),
                "truncated": truncated,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "query_id": query_id,
        "subquestion_id": str(task["subquestion_id"]),
        "status": "completed",
        "sql": sql,
        "columns": columns,
        "row_count": len(rows),
        "rows_preview": rows[:PREVIEW_ROWS],
        "result_ref": f"sqlite-result://{snapshot_id}/{query_id}",
        "sql_fingerprint": fingerprint,
        "data_snapshot_id": snapshot_id,
        "elapsed_ms": (time.perf_counter() - started_at) * 1000,
        "truncated": truncated,
        "error": None,
    }


__all__ = [
    "build_rewrite_catalog",
    "build_sql_catalog",
    "execute_sql",
    "load_catalog",
    "validate_sql",
]
