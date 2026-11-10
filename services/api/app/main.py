from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.investigation import investigate
from app.optimization_rules import analyze_sql_patterns
from app.sql_safety import classify_sql

from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="QueryPilot API",
    version="0.5.0",
    description=(
        "Static PostgreSQL SQL analysis and performance "
        "recommendations API"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://querypilot.pages.dev"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
class SQLValidationRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "querypilot-api",
    }


@app.post("/api/sql/validate")
def validate_sql(
    request: SQLValidationRequest,
) -> dict[str, object]:
    try:
        statement_info = classify_sql(request.sql)
    except HTTPException:
        raise

    return {
        "valid": True,
        "statement_type": statement_info["statement_type"],
        "category": statement_info["category"],
        "risk_level": statement_info["risk_level"],
        "sql": statement_info["sql"],
        "execution_allowed": False,
        "analysis_mode": "static_sql_only",
    }


@app.post("/api/analysis")
def analyze_sql(
    request: SQLValidationRequest,
) -> dict[str, object]:
    try:
        statement_info = classify_sql(request.sql)
        normalized_sql = statement_info["sql"]

        findings = analyze_sql_patterns(normalized_sql)

        return {
            "valid": True,
            "statement_type": statement_info["statement_type"],
            "sql": normalized_sql,
            "analysis": {
                "mode": "static_sql_only",
                "database": None,
                "execution_performed": False,
                "execution_plan_available": False,
                "execution_time_ms": None,
                "planning_time_ms": None,
                "statement_type": statement_info["statement_type"],
                "category": statement_info["category"],
                "risk_level": statement_info["risk_level"],
                "ast_type": statement_info["ast_type"],
                "finding_count": len(findings),
            },
            "deterministic_findings": findings,
        }

    except HTTPException:
        raise


@app.post("/api/investigate")
def investigate_sql(
    request: SQLValidationRequest,
) -> dict[str, object]:
    try:
        return investigate(request.sql)
    except HTTPException:
        raise