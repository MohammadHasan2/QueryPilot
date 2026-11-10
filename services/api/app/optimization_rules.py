import json
from typing import Any

from pglast.parser import parse_sql_json


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _has_column_reference(node: Any) -> bool:
    return any(
        "ColumnRef" in item
        for item in _walk(node)
        if isinstance(item, dict)
    )


def analyze_sql_patterns(sql: str) -> list[dict[str, Any]]:
    parsed = json.loads(parse_sql_json(sql))
    statements = parsed.get("stmts", [])

    if len(statements) != 1:
        return []

    statement = statements[0].get("stmt", {})

    if not isinstance(statement, dict) or not statement:
        return []

    statement_type = next(iter(statement))
    node = statement[statement_type]
    findings: list[dict[str, Any]] = []

    def add(
        issue: str,
        severity: str,
        explanation: str,
        recommendation: str,
        tradeoffs: list[str] | None = None,
        validation_steps: list[str] | None = None,
    ):
        findings.append({
            "issue": issue,
            "severity": severity,
            "explanation": explanation,
            "recommendation": recommendation,
            "tradeoffs": tradeoffs or [],
            "validation_steps": validation_steps or [],
            "evidence_chunk_ids": [],
        })

    # --------------------------------------------------
    # SELECT
    # --------------------------------------------------
    if statement_type == "SelectStmt":

        for target in node.get("targetList", []):
            if any(
                "A_Star" in item
                for item in _walk(target)
                if isinstance(item, dict)
            ):
                add(
                    "SELECT * detected",
                    "low",
                    "The query selects every available column.",
                    "Select only the columns required by the application.",
                    ["Requires knowing which columns the application needs."],
                    ["Verify the required output columns."],
                )
                break

        if not node.get("whereClause"):
            add(
                "No WHERE clause",
                "medium",
                "The query has no filtering condition and may process many rows.",
                "Add a WHERE condition if only a subset of rows is required.",
                ["Filtering changes the result set."],
                ["Verify the intended result set."],
            )

        if node.get("limitCount") and not node.get("sortClause"):
            add(
                "LIMIT without ORDER BY",
                "low",
                "The query limits rows without specifying their order.",
                "Consider adding ORDER BY using columns that define the intended ordering.",
                ["Sorting may add processing cost."],
                ["Verify the intended ordering."],
            )

        if node.get("distinctClause"):
            add(
                "DISTINCT detected",
                "low",
                "DISTINCT removes duplicate result rows and may require additional processing.",
                "Check whether duplicate elimination is necessary.",
                ["Removing DISTINCT can change query results."],
                ["Verify duplicate behavior before changing the query."],
            )

        where_clause = node.get("whereClause")

        if where_clause:
            for item in _walk(where_clause):
                if not isinstance(item, dict):
                    continue

                bool_expr = item.get("BoolExpr")

                if (
                    isinstance(bool_expr, dict)
                    and bool_expr.get("boolop") == "OR_EXPR"
                ):
                    add(
                        "OR predicate detected",
                        "low",
                        "The WHERE clause contains an OR condition.",
                        "Review whether alternative predicates or UNION would be appropriate.",
                        ["Rewriting OR can change duplicate handling and semantics."],
                        ["Compare results before adopting a rewrite."],
                    )
                    break

            for item in _walk(where_clause):
                if not isinstance(item, dict):
                    continue

                function = item.get("FuncCall")

                if (
                    isinstance(function, dict)
                    and _has_column_reference(function)
                ):
                    add(
                        "Function applied to a filtered column",
                        "medium",
                        "A function in a filter may prevent use of an ordinary index on the underlying column.",
                        "Consider an expression index or an equivalent predicate.",
                        ["Expression indexes require storage and maintenance."],
                        ["Check index compatibility and query plans when available."],
                    )
                    break

            for item in _walk(where_clause):
                if not isinstance(item, dict):
                    continue

                expression = item.get("A_Expr")

                if not isinstance(expression, dict):
                    continue

                operators = [
                    part.get("String", {}).get("sval", "")
                    for part in expression.get("name", [])
                    if isinstance(part, dict)
                ]

                if not any(op in {"~~", "~~*"} for op in operators):
                    continue

                right = expression.get("rexpr", {})
                constant = (
                    right.get("A_Const", {})
                    if isinstance(right, dict)
                    else {}
                )
                value = constant.get("sval", {}).get("sval", "")

                if isinstance(value, str) and value.startswith("%"):
                    add(
                        "Leading wildcard LIKE detected",
                        "medium",
                        "A leading wildcard generally prevents a standard B-tree index from supporting prefix matching.",
                        "Consider trigram indexing or another search strategy if substring search is required.",
                        ["Additional indexes consume storage and increase write overhead."],
                        ["Evaluate the search requirement and suitable index options."],
                    )
                    break

    # --------------------------------------------------
    # INSERT / UPSERT
    # --------------------------------------------------
    elif statement_type == "InsertStmt":

        source = node.get("selectStmt")
        conflict = node.get("onConflictClause")
        source_select = (
            source.get("SelectStmt", {})
            if isinstance(source, dict)
            else {}
        )

        # VALUES is represented internally as a SelectStmt too.
        is_insert_select = (
            isinstance(source_select, dict)
            and bool(source_select)
            and not source_select.get("valuesLists")
        )

        if is_insert_select:
            add(
                "INSERT ... SELECT detected",
                "low",
                "The statement inserts rows produced by another query.",
                "Review the source query's filtering and ensure the required rows are selected.",
                ["Source-query changes can affect inserted data."],
                ["Verify source-query results before insertion."],
            )

        if conflict:
            add(
                "UPSERT / conflict handling detected",
                "low",
                "The INSERT statement includes ON CONFLICT handling.",
                "Verify that the conflict target matches the intended unique constraint or index.",
                ["Conflict handling can add index lookups and update work."],
                ["Test both the insert and conflict paths."],
            )

        else:
            add(
                "INSERT without conflict handling",
                "low",
                "The statement does not specify ON CONFLICT behavior.",
                "If duplicate-key conflicts are possible, consider whether ON CONFLICT handling is required.",
                ["Conflict handling is only appropriate when it matches application requirements."],
                ["Verify unique constraints and duplicate-handling requirements."],
            )

    # --------------------------------------------------
    # UPDATE
    # --------------------------------------------------
    elif statement_type == "UpdateStmt":

        if not node.get("whereClause"):
            add(
                "UPDATE without WHERE",
                "high",
                "The UPDATE statement has no filtering condition and may modify every row.",
                "Confirm that every row should be updated. Otherwise, add an appropriate WHERE condition.",
                ["Adding a filter changes which rows are updated."],
                ["Review the intended target rows before any execution."],
            )

        if node.get("fromClause"):
            add(
                "UPDATE ... FROM detected",
                "low",
                "The UPDATE uses additional FROM relations.",
                "Verify that the join produces the intended source row for each target row.",
                ["Multiple matching source rows can make update behavior problematic."],
                ["Check join cardinality and target-row matching."],
            )

    # --------------------------------------------------
    # DELETE
    # --------------------------------------------------
    elif statement_type == "DeleteStmt":

        if not node.get("whereClause"):
            add(
                "DELETE without WHERE",
                "high",
                "The DELETE statement has no filtering condition and may delete every row.",
                "Confirm that all rows should be deleted. Otherwise, add an appropriate WHERE condition.",
                ["A WHERE condition changes which rows are deleted."],
                ["Review the intended target rows before any execution."],
            )

        if node.get("usingClause"):
            add(
                "DELETE ... USING detected",
                "low",
                "The DELETE uses additional relations to identify rows.",
                "Verify that the join conditions identify exactly the intended target rows.",
                ["Join mistakes can affect more rows than intended."],
                ["Review join conditions and target-row matching."],
            )

    # --------------------------------------------------
    # CREATE INDEX
    # --------------------------------------------------
    elif statement_type == "IndexStmt":

        if node.get("unique"):
            add(
                "Unique index creation",
                "low",
                "The statement creates a unique index.",
                "Verify that existing data satisfies the uniqueness requirement.",
                ["Unique indexes reject duplicate indexed values and add write overhead."],
                ["Check duplicate values and the intended uniqueness rule."],
            )
        else:
            add(
                "Index creation",
                "low",
                "The statement creates a database index.",
                "Confirm that the indexed columns and index method match actual query patterns.",
                ["Indexes consume storage and can slow INSERT, UPDATE and DELETE operations."],
                ["Review representative queries and existing indexes."],
            )

        if node.get("whereClause"):
            add(
                "Partial index detected",
                "low",
                "The index applies only to rows matching its predicate.",
                "Verify that important queries use predicates compatible with the partial index.",
                ["Queries outside the predicate cannot use this index for the same purpose."],
                ["Compare query predicates with the index predicate."],
            )

    # --------------------------------------------------
    # CREATE TABLE
    # --------------------------------------------------
    elif statement_type == "CreateStmt":

        elements = node.get("tableElts", [])

        has_primary_key = any(
            "PRIMARY_KEY" in json.dumps(element).upper()
            for element in elements
        )

        if not has_primary_key:
            add(
                "No primary key declaration detected",
                "low",
                "The CREATE TABLE statement does not visibly declare a primary key.",
                "Review whether the table needs a primary key or another suitable uniqueness constraint.",
                ["A primary key adds a uniqueness requirement and an index."],
                ["Review entity identity and uniqueness requirements."],
            )

    # --------------------------------------------------
    # ALTER TABLE
    # --------------------------------------------------
    elif statement_type == "AlterTableStmt":

        add(
            "Schema modification detected",
            "medium",
            "ALTER TABLE changes the structure of an existing table.",
            "Review the operation's locking, rewrite and compatibility implications for your PostgreSQL version.",
            ["Some alterations can require significant locks or table rewrites."],
            ["Review PostgreSQL documentation and plan an appropriate maintenance window."],
        )

    # --------------------------------------------------
    # DROP
    # --------------------------------------------------
    elif statement_type == "DropStmt":

        add(
            "Destructive DROP operation",
            "high",
            "DROP removes a database object such as a table, index or other schema object.",
            "Verify the target object, dependencies and recovery requirements before execution.",
            ["The operation can remove objects required by other database components."],
            ["Review dependencies and backups before execution."],
        )

    # --------------------------------------------------
    # TRUNCATE
    # --------------------------------------------------
    elif statement_type == "TruncateStmt":

        add(
            "Destructive TRUNCATE operation",
            "high",
            "TRUNCATE removes all rows from the specified tables.",
            "Confirm that removing every row is intended and review foreign-key and identity behavior.",
            ["TRUNCATE has different locking and trigger behavior from DELETE."],
            ["Review dependencies and recovery requirements before execution."],
        )

    return findings