"""Database tools — read and write operations across configured DB connections."""

from __future__ import annotations

import json
import logging
from typing import Any

from qa_agent.buddy.tool import BaseTool, RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.tools.database")

# Connection registry: name → DSN string
# Populated from environment or buddy config at startup
_CONNECTIONS: dict[str, str] = {}


def register_connection(name: str, dsn: str) -> None:
    _CONNECTIONS[name] = dsn


def _engine(name: str):
    from sqlalchemy import create_engine, text as sa_text
    dsn = _CONNECTIONS.get(name)
    if not dsn:
        raise ValueError(f"No DB connection registered with name '{name}'. "
                         f"Available: {list(_CONNECTIONS.keys()) or ['none configured']}")
    return create_engine(dsn), sa_text


def _rows_to_dicts(result) -> list[dict]:
    keys = list(result.keys())
    return [dict(zip(keys, row)) for row in result.fetchall()]


# ─────────────────────────────────────────────
# READ tools
# ─────────────────────────────────────────────

class ListConnectionsTool(BaseTool):
    name = "db_list_connections"
    description = "List all configured database connection names available to TestBuddy."
    risk_level = RiskLevel.READ
    input_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, params: dict) -> ToolResult:
        if not _CONNECTIONS:
            return ToolResult(ok=True, data={"connections": [],
                                              "note": "No DB connections configured. "
                                                      "Add DB_CONN_<name>=<dsn> environment variables."})
        return ToolResult(ok=True, data={"connections": list(_CONNECTIONS.keys())})


class ListTablesTool(BaseTool):
    name = "db_list_tables"
    description = "List all tables in a database connection."
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "connection": {"type": "string", "description": "Connection name from db_list_connections"},
        },
        "required": ["connection"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            from sqlalchemy import inspect as sa_inspect
            engine, _ = _engine(params["connection"])
            with engine.connect():
                inspector = sa_inspect(engine)
                tables = inspector.get_table_names()
                return ToolResult(ok=True, data={"tables": tables, "count": len(tables)})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class DescribeTableTool(BaseTool):
    name = "db_describe_table"
    description = "Show the schema (columns, types, nullable, primary keys) for a database table."
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "connection": {"type": "string"},
            "table": {"type": "string"},
        },
        "required": ["connection", "table"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            from sqlalchemy import inspect as sa_inspect
            engine, _ = _engine(params["connection"])
            inspector = sa_inspect(engine)
            cols = inspector.get_columns(params["table"])
            pk = inspector.get_pk_constraint(params["table"])
            return ToolResult(ok=True, data={
                "table": params["table"],
                "primary_keys": pk.get("constrained_columns", []),
                "columns": [
                    {"name": c["name"], "type": str(c["type"]), "nullable": c.get("nullable", True)}
                    for c in cols
                ],
            })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class QueryTool(BaseTool):
    name = "db_query"
    description = (
        "Execute a SELECT SQL query and return results as JSON. "
        "Always use SELECT — this tool blocks non-SELECT statements. "
        "Use LIMIT to avoid huge result sets. Max 500 rows returned."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "connection": {"type": "string"},
            "sql": {"type": "string", "description": "SELECT statement"},
            "params": {"type": "object", "description": "Query parameters dict (optional)"},
        },
        "required": ["connection", "sql"],
    }

    def execute(self, params: dict) -> ToolResult:
        sql = (params.get("sql") or "").strip()
        if not sql.upper().startswith("SELECT"):
            return ToolResult(ok=False, error="Only SELECT statements allowed via db_query. Use db_execute for writes.")
        try:
            engine, sa_text = _engine(params["connection"])
            with engine.connect() as conn:
                result = conn.execute(sa_text(sql), params.get("params") or {})
                rows = _rows_to_dicts(result)[:500]
                return ToolResult(ok=True, data={"row_count": len(rows), "rows": rows})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class CountRowsTool(BaseTool):
    name = "db_count"
    description = "Count rows in a table, optionally with a WHERE condition."
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "connection": {"type": "string"},
            "table": {"type": "string"},
            "where": {"type": "string", "description": "Optional WHERE clause (without the WHERE keyword)"},
        },
        "required": ["connection", "table"],
    }

    def execute(self, params: dict) -> ToolResult:
        table = params["table"]
        where_clause = params.get("where", "").strip()
        sql = f"SELECT COUNT(*) AS cnt FROM {table}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        try:
            engine, sa_text = _engine(params["connection"])
            with engine.connect() as conn:
                result = conn.execute(sa_text(sql))
                row = result.fetchone()
                count = row[0] if row else 0
                return ToolResult(ok=True, data={"table": table, "count": count, "where": where_clause or None})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class ExplainQueryTool(BaseTool):
    name = "db_explain"
    description = "Run EXPLAIN ANALYZE on a SQL query to diagnose performance issues."
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "connection": {"type": "string"},
            "sql": {"type": "string"},
        },
        "required": ["connection", "sql"],
    }

    def execute(self, params: dict) -> ToolResult:
        sql = params.get("sql", "").strip()
        try:
            engine, sa_text = _engine(params["connection"])
            with engine.connect() as conn:
                result = conn.execute(sa_text(f"EXPLAIN ANALYZE {sql}"))
                rows = [row[0] for row in result.fetchall()]
                return ToolResult(ok=True, data={"plan": "\n".join(rows)})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class TakeSnapshotTool(BaseTool):
    name = "db_snapshot"
    description = (
        "Export rows from a table (optionally filtered) to a JSON snapshot. "
        "Use before any write to create a rollback point."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "connection": {"type": "string"},
            "table": {"type": "string"},
            "where": {"type": "string", "description": "Optional WHERE clause"},
            "limit": {"type": "integer", "default": 1000},
        },
        "required": ["connection", "table"],
    }

    def execute(self, params: dict) -> ToolResult:
        table = params["table"]
        limit = params.get("limit", 1000)
        where = params.get("where", "").strip()
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" LIMIT {limit}"
        try:
            engine, sa_text = _engine(params["connection"])
            with engine.connect() as conn:
                result = conn.execute(sa_text(sql))
                rows = _rows_to_dicts(result)
                return ToolResult(ok=True, data={
                    "table": table,
                    "row_count": len(rows),
                    "rows": rows,
                    "note": "Save this snapshot before executing any write.",
                })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


# ─────────────────────────────────────────────
# WRITE tool
# ─────────────────────────────────────────────

class ExecuteSQLTool(BaseTool):
    name = "db_execute"
    description = (
        "Execute a non-SELECT SQL statement (INSERT, UPDATE, DELETE). "
        "REQUIRES user approval. Always take a db_snapshot first to enable rollback. "
        "Returns rows affected."
    )
    risk_level = RiskLevel.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "connection": {"type": "string"},
            "sql": {"type": "string", "description": "INSERT / UPDATE / DELETE statement"},
            "params": {"type": "object", "description": "Bind parameters (optional)"},
        },
        "required": ["connection", "sql"],
    }

    def execute(self, params: dict) -> ToolResult:
        sql = (params.get("sql") or "").strip()
        if sql.upper().startswith("SELECT"):
            return ToolResult(ok=False, error="Use db_query for SELECT statements.")
        try:
            engine, sa_text = _engine(params["connection"])
            with engine.begin() as conn:
                result = conn.execute(sa_text(sql), params.get("params") or {})
                return ToolResult(ok=True, data={
                    "rows_affected": result.rowcount,
                    "sql": sql,
                })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


def all_db_tools() -> list[BaseTool]:
    return [
        ListConnectionsTool(),
        ListTablesTool(),
        DescribeTableTool(),
        QueryTool(),
        CountRowsTool(),
        ExplainQueryTool(),
        TakeSnapshotTool(),
        ExecuteSQLTool(),
    ]
