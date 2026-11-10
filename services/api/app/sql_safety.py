
import json
from typing import Any

from fastapi import HTTPException
from pglast.parser import ParseError, parse_sql_json


# SQL statement types supported for static analysis.
STATEMENT_TYPES = {
    "SelectStmt": ("SELECT", "read", "low"),
    "InsertStmt": ("INSERT", "write", "medium"),
    "UpdateStmt": ("UPDATE", "write", "medium"),
    "DeleteStmt": ("DELETE", "write", "medium"),
    "MergeStmt": ("MERGE", "write", "medium"),
    "IndexStmt": ("CREATE INDEX", "schema", "medium"),
    "CreateStmt": ("CREATE TABLE", "schema", "high"),
    "CreateTableAsStmt": ("CREATE TABLE AS", "schema", "high"),
    "AlterTableStmt": ("ALTER TABLE", "schema", "high"),
    "DropStmt": ("DROP", "destructive", "critical"),
    "TruncateStmt": ("TRUNCATE", "destructive", "critical"),
    "ViewStmt": ("CREATE VIEW", "schema", "medium"),
    "CreateMatViewStmt": ("CREATE MATERIALIZED VIEW", "schema", "high"),
    "RefreshMatViewStmt": ("REFRESH MATERIALIZED VIEW", "write", "high"),
    "TransactionStmt": ("TRANSACTION", "transaction", "medium"),
    "CreateSchemaStmt": ("CREATE SCHEMA", "schema", "high"),
    "CreateSeqStmt": ("CREATE SEQUENCE", "schema", "medium"),
    "AlterSeqStmt": ("ALTER SEQUENCE", "schema", "high"),
}

BLOCKED_STATEMENTS = {
    "CopyStmt",
    "CallStmt",
    "DoStmt",
    "ExecuteStmt",
    "PrepareStmt",
    "DeallocateStmt",
    "VariableSetStmt",
    "VariableShowStmt",
    "LoadStmt",
    "ListenStmt",
    "NotifyStmt",
    "UnlistenStmt",
    "DiscardStmt",
    "GrantStmt",
    "GrantRoleStmt",
    "RevokeStmt",
    "LockStmt",
    "VacuumStmt",
    "ClusterStmt",
    "ReindexStmt",
    "CreateFunctionStmt",
    "AlterFunctionStmt",
    "CreateExtensionStmt",
    "AlterSystemStmt",
}

BLOCKED_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "lo_import",
    "lo_export",
    "nextval",
    "setval",
    "set_config",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_advisory_lock",
    "pg_advisory_xact_lock",
}


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _parse_sql(sql: str) -> tuple[str, dict[str, Any]]:
    if not sql or not sql.strip():
        raise HTTPException(
            status_code=400,
            detail="SQL query cannot be empty.",
        )

    try:
        parsed = json.loads(parse_sql_json(sql))
    except (ParseError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="SQL syntax is invalid.",
        ) from exc

    statements = parsed.get("stmts") if isinstance(parsed, dict) else None

    if not isinstance(statements, list) or len(statements) != 1:
        raise HTTPException(
            status_code=400,
            detail="Exactly one SQL statement is allowed.",
        )

    statement = statements[0].get("stmt", {})
    if not isinstance(statement, dict) or len(statement) != 1:
        raise HTTPException(
            status_code=400,
            detail="Unable to identify SQL statement type.",
        )

    root_type = next(iter(statement))
    normalized_sql = sql.strip().rstrip(";").strip()

    return normalized_sql, statement


def classify_sql(sql: str) -> dict[str, Any]:
    """
    Classify one PostgreSQL statement for static analysis only.

    This function never executes SQL.
    """

    normalized_sql, statement = _parse_sql(sql)
    root_type = next(iter(statement))

    if root_type in BLOCKED_STATEMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"{root_type} is not supported for analysis.",
        )

    metadata = STATEMENT_TYPES.get(root_type)

    if metadata is None:
        raise HTTPException(
            status_code=400,
            detail=f"SQL statement type '{root_type}' is not supported.",
        )

    statement_type, category, risk_level = metadata
    root_node = statement[root_type]
    for node in _walk(root_node):
        func_call = node.get("FuncCall")

        if not isinstance(func_call, dict):
            continue

        names = func_call.get("funcname", [])
        function_name = ""

        if names and isinstance(names[-1], dict):
            last_name = names[-1]
            function_name = (
                last_name.get("String", {}).get("sval", "")
                or last_name.get("sval", "")
            ).lower()

        if function_name in BLOCKED_FUNCTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Function '{function_name}' is not supported for analysis.",
            )

    # Reject data-modifying CTEs inside SELECT statements for now.
    if root_type == "SelectStmt":
        nested_write_types = {
            "InsertStmt",
            "UpdateStmt",
            "DeleteStmt",
            "MergeStmt",
        }

        for node in _walk(root_node):
            if isinstance(node, dict) and nested_write_types.intersection(node):
                raise HTTPException(
                    status_code=400,
                    detail="Data-modifying CTEs require separate analysis.",
                )
            if "intoClause" in node:
                raise HTTPException(
                    status_code=400,
                    detail="SELECT INTO is not supported for analysis.",
                )

            if "lockingClause" in node:
                raise HTTPException(
                    status_code=400,
                    detail="Row-locking clauses are not supported for analysis.",
                )

    return {
        "sql": normalized_sql,
        "statement_type": statement_type,
        "category": category,
        "risk_level": risk_level,
        "analysis_mode": "static_sql_only",
        "execution_allowed": False,
        "ast_type": root_type,
    }


def validate_readonly_select(sql: str) -> str:
    """
    Backward-compatible SELECT-only validation.
    Retained for existing validation endpoints and tests.
    """

    result = classify_sql(sql)

    if result["statement_type"] != "SELECT":
        raise HTTPException(
            status_code=400,
            detail="Only SELECT statements are allowed by this endpoint.",
        )

    _, statement = _parse_sql(sql)
    root = statement["SelectStmt"]

    for node in _walk(root):
        if not isinstance(node, dict):
            continue

        if "intoClause" in node:
            raise HTTPException(
                status_code=400,
                detail="SELECT INTO is not allowed.",
            )

        if "lockingClause" in node:
            raise HTTPException(
                status_code=400,
                detail="Row-locking clauses are not allowed.",
            )

        func_call = node.get("FuncCall")

        if isinstance(func_call, dict):
            names = func_call.get("funcname", [])
            function_name = ""

            if names and isinstance(names[-1], dict):
                last_name = names[-1]
                function_name = (
                    last_name.get("String", {}).get("sval", "")
                    or last_name.get("sval", "")
                ).lower()

            if function_name in BLOCKED_FUNCTIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Function '{function_name}' is not allowed.",
                )

    return result["sql"]
