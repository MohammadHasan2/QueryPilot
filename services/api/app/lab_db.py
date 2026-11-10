
import json
from typing import Any

import psycopg

from app.settings import settings


def run_explain(sql: str) -> dict[str, Any]:
    password = settings.sql_lab_readonly_password
    if not password:
        raise RuntimeError("SQL_LAB_READONLY_PASSWORD is not configured.")

    with psycopg.connect(
        host="127.0.0.1",
        port=55432,
        dbname="querypilot_lab",
        user="querypilot_reader",
        password=password,
        connect_timeout=3,
        sslmode="disable",
    ) as conn:
        with conn.transaction():
            # Must be the first transaction command.
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '3000ms'")
            conn.execute("SET LOCAL lock_timeout = '1000ms'")

            cursor = conn.execute(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
            )
            row = cursor.fetchone()

            if row is None:
                raise RuntimeError("PostgreSQL returned no execution plan.")

            plan = row[0]
            if isinstance(plan, str):
                plan = json.loads(plan)

            return {
                "plan": plan,
                "database": "querypilot_lab",
                "analysis_mode": "EXPLAIN ANALYZE",
                "statement_timeout_ms": 3000,
            }
