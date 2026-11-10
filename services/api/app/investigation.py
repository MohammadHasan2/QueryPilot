
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal, TypedDict

import httpx
from groq import Groq
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.budget_manager import (
    BudgetServiceError,
    reserve_budget,
    settle_budget,
)
from app.optimization_rules import analyze_sql_patterns
from app.settings import settings
from app.sql_safety import classify_sql


class Finding(BaseModel):
    issue: str
    severity: Literal["low", "medium", "high"]
    explanation: str
    recommendation: str
    tradeoffs: list[str] = Field(default_factory=list)
    validation_steps: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    overall_recommendation: str
    optimized_sql: str = ""
    limitations: list[str] = Field(default_factory=list)


REPORT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "explanation": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "tradeoffs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "validation_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_chunk_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "issue",
                    "severity",
                    "explanation",
                    "recommendation",
                    "tradeoffs",
                    "validation_steps",
                    "evidence_chunk_ids",
                ],
                "additionalProperties": False,
            },
        },
        "overall_recommendation": {"type": "string"},
        "optimized_sql": {"type": "string"},
        "limitations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "summary",
        "findings",
        "overall_recommendation",
        "optimized_sql",
        "limitations",
    ],
    "additionalProperties": False,
}


class InvestigationState(TypedDict, total=False):
    sql: str
    normalized_sql: str
    statement_info: dict[str, Any]
    deterministic_findings: list[dict[str, Any]]
    analysis: dict[str, Any]
    evidence: list[dict[str, Any]]
    optimization_cases: list[dict[str, Any]]
    retrieval_status: str
    ai_status: str
    report: dict[str, Any]
    error: str | None
    error_message: str | None


def _retrieve_evidence(
    state: InvestigationState,
) -> dict[str, Any]:
    """Retrieve PostgreSQL documentation and optimization cases."""

    if (
        not settings.supabase_url
        or not settings.supabase_function_secret
    ):
        return {
            "evidence": [],
            "optimization_cases": [],
            "retrieval_status": "unavailable",
        }

    statement_info = state["statement_info"]
    findings = state.get("deterministic_findings", [])

    findings_context = [
        {
            "issue": item.get("issue"),
            "severity": item.get("severity"),
            "explanation": item.get("explanation"),
            "recommendation": item.get("recommendation"),
        }
        for item in findings[:12]
    ]

    query = (
        f"PostgreSQL {statement_info['statement_type']} "
        f"performance optimization "
        f"{' '.join(
            str(item.get('issue', ''))
            for item in findings[:8]
        )} "
        f"{state['normalized_sql']}"
    )

    endpoint = (
        f"{settings.supabase_url.rstrip('/')}"
        "/functions/v1/search-knowledge"
    )

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-querypilot-secret": (
                        settings.supabase_function_secret
                    ),
                },
                json={
                    "query": query,
                    "sql": state["normalized_sql"],
                    "statement_type": statement_info["statement_type"],
                    "detected_patterns": [
                        str(item.get("issue", ""))
                        for item in findings
                        if item.get("issue")
                    ],
                    "top_k": 4,
                    "retrieval_mode": "hybrid",
                },
            )

            response.raise_for_status()
            payload = response.json()

        if isinstance(payload, dict):
            raw_evidence = (
                payload.get("results")
                or payload.get("matches")
                or payload.get("evidence")
                or []
            )

            raw_cases = payload.get("optimization_cases") or []

        elif isinstance(payload, list):
            raw_evidence = payload
            raw_cases = []

        else:
            raw_evidence = []
            raw_cases = []

        if not isinstance(raw_evidence, list):
            raw_evidence = []

        if not isinstance(raw_cases, list):
            raw_cases = []

        evidence = [
            item
            for item in raw_evidence[:8]
            if isinstance(item, dict)
        ]

        optimization_cases = [
            item
            for item in raw_cases[:5]
            if isinstance(item, dict)
        ]

        retrieval_status = (
            "ok"
            if evidence or optimization_cases
            else "no_results"
        )

        return {
            "evidence": evidence,
            "optimization_cases": optimization_cases,
            "retrieval_status": retrieval_status,
        }

    except (httpx.HTTPError, ValueError) as exc:
        return {
            "retrieval_status": "unavailable",
            "evidence": [],
            "optimization_cases": [],
            "retrieval_error": f"{type(exc).__name__}: {exc}",
        }


def _get_retry_after_seconds(
    exc: Exception,
) -> int | None:
    """Read Retry-After without sleeping or retrying."""

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)

    if not headers:
        return None

    value = headers.get("retry-after")

    if not value:
        return None

    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(value)

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)

        seconds = (
            retry_at - datetime.now(timezone.utc)
        ).total_seconds()

        return max(0, int(seconds))

    except (TypeError, ValueError, OverflowError):
        return None


def _generate_report(
    state: InvestigationState,
) -> dict[str, Any]:
    """Generate a budget-controlled structured report."""

    if not settings.groq_api_key:
        return {
            "ai_status": "ai_deferred",
            "report": None,
            "error": "MissingGroqAPIKey",
            "error_message": "GROQ_API_KEY is not configured.",
        }

    evidence = state.get("evidence", [])
    optimization_cases = state.get("optimization_cases", [])

    statement_info = state.get("statement_info")

    if not statement_info:
        statement_info = classify_sql(
            state["normalized_sql"]
        )

    deterministic_findings = state.get(
        "deterministic_findings",
        [],
    )

    evidence_context = []

    for item in evidence[:8]:
        content = (
            item.get("content")
            or item.get("chunk_text")
            or item.get("text")
            or ""
        )

        evidence_context.append({
            "chunk_id": item.get("chunk_id") or item.get("id"),
            "title": (
                item.get("source_title")
                or item.get("title")
            ),
            "source_url": (
                item.get("source_url")
                or item.get("url")
            ),
            "section": (
                item.get("section_title")
                or item.get("section")
            ),
            "content": str(content)[:280],
        })

    optimization_cases_context = []

    for item in optimization_cases[:5]:
        optimization_cases_context.append({
            "case_id": item.get("case_id"),
            "title": item.get("title"),
            "problem_pattern": item.get("problem_pattern"),
            "relevant_conditions": item.get(
                "relevant_conditions"
            ),
            "suggested_optimization": item.get(
                "suggested_optimization"
            ),
            "expected_tradeoffs": item.get(
                "expected_tradeoffs"
            ),
            "validation_requirements": item.get(
                "validation_requirements"
            ),
            "supporting_source_ids": item.get(
                "supporting_source_ids",
                [],
            ),
        })

    prompt_data = {
        "sql": state["normalized_sql"],
        "statement": statement_info,
        "execution_policy": {
            "execution_allowed": False,
            "analysis_mode": "static_sql_only",
        },
        "deterministic_findings": deterministic_findings,
        "retrieved_documentation": evidence_context,
        "retrieved_optimization_cases": optimization_cases_context,
    }

    user_prompt = json.dumps(
        prompt_data,
        indent=2,
        ensure_ascii=False,
        default=str,
    )

    system_prompt = """
You are QueryPilot, a PostgreSQL SQL performance
and optimization investigation assistant.

Analyze the supplied SQL, deterministic findings,
retrieved PostgreSQL documentation, and structured
optimization cases.

The retrieved documentation contains official PostgreSQL
knowledge. The optimization cases contain curated examples
of common SQL performance and correctness patterns.

Use both sources when they are relevant to the submitted SQL.

IMPORTANT SAFETY AND ACCURACY RULES:

- QueryPilot NEVER executes submitted SQL.
- Do not claim that SQL was executed or tested.
- Analyze the SQL statically.
- Do not invent tables, columns, indexes, constraints,
  row counts, schema details, or database configuration.
- Distinguish definite findings from hypotheses.
- Treat deterministic findings as signals to investigate,
  not automatically as proof of a performance problem.
- Use retrieved documentation and optimization cases when relevant.
- Do not blindly apply a retrieved optimization case.
  Check whether its conditions match the submitted SQL.
- Do not invent evidence chunk IDs.
- evidence_chunk_ids must contain only actual chunk IDs
  from retrieved_documentation.
- Optimization case IDs are not chunk IDs.
- Do not claim an optimization is faster without benchmark evidence.
- Explain write, storage, locking, and maintenance trade-offs
  when recommending changes.
- For destructive operations, clearly explain their impact
  and recommend review before execution.
- Never recommend executing destructive SQL automatically.
- Do not rewrite SQL if required schema information is missing.
- optimized_sql must be an empty string when a safe rewrite
  cannot be justified.
- Preserve the original SQL semantics when proposing a rewrite.

Return valid JSON with exactly these top-level fields:
summary, findings, overall_recommendation,
optimized_sql, limitations.

Each finding must contain:
issue, severity, explanation, recommendation,
tradeoffs, validation_steps, evidence_chunk_ids.

severity must be low, medium, or high.
All list fields must contain strings.
Do not include Markdown or text outside the JSON.

OUTPUT FORMAT — STRICT REQUIREMENTS:

Return one valid JSON object with exactly these five top-level keys:
- summary: string
- findings: array of objects
- overall_recommendation: string
- optimized_sql: string
- limitations: array of strings

Every finding MUST contain exactly these keys:
- issue: string
- severity: "low", "medium", or "high"
- explanation: string
- recommendation: string
- tradeoffs: array of strings
- validation_steps: array of strings
- evidence_chunk_ids: array of strings

IMPORTANT:
- tradeoffs MUST always be a JSON array, never a string.
- validation_steps MUST always be a JSON array, never a string.
- evidence_chunk_ids MUST always be a JSON array, never a string.
- findings MUST always be an array, even when empty.
- overall_recommendation is REQUIRED, even when findings is empty.
- limitations MUST always be an array.
- Use [] for empty arrays.
- Use "" for an empty optimized_sql.
- Do not omit any required field.
- Do not add extra fields.
- Return JSON only. No Markdown or surrounding explanation.

FINDING ACCURACY RULES:

- Only report findings supported by deterministic_findings.
- Never create a finding based solely on a retrieved optimization case
  or a hypothetical database condition.
- If deterministic_findings is empty, findings MUST be [].
- Potential missing indexes, sequential scans, stale statistics,
  and inefficient execution plans are hypotheses unless supported
  by actual schema or execution-plan evidence.
- Put relevant unverified possibilities in limitations or
  overall_recommendation, clearly labeled as hypotheses.

SQL VALIDATION SAFETY:

- Never recommend EXPLAIN ANALYZE for DELETE, UPDATE, INSERT,
  MERGE, or other statements that modify database state.
- Never recommend executing submitted SQL as an automatic test.
- For write statements, plain EXPLAIN without ANALYZE may be
  recommended to inspect a plan without executing the statement.
- Never claim that a query uses a particular index or scan
  without supporting schema or plan evidence.
"""

    max_completion_tokens = settings.groq_max_completion_tokens

    prompt_characters = len(system_prompt) + len(user_prompt)

    # Conservative approximation: roughly 3 characters per token.
    estimated_input_tokens = (prompt_characters + 2) // 3 + 128

    estimated_total_tokens = (
        estimated_input_tokens + max_completion_tokens
    )

    if estimated_total_tokens > 8000:
        return {
            "ai_status": "budget_exhausted",
            "report": None,
            "error": "PromptExceedsTokenCeiling",
            "error_message": (
                "The estimated request exceeds the app-level "
                "8000-token-per-minute ceiling. "
                "Static SQL analysis remains available."
            ),
        }

    try:
        decision = reserve_budget(
            model=settings.groq_model,
            estimated_tokens=estimated_total_tokens,
        )
    except BudgetServiceError as exc:
        return {
            "ai_status": "ai_deferred",
            "report": None,
            "error": "BudgetServiceUnavailable",
            "error_message": str(exc),
        }

    if not decision.allowed:
        allowed_statuses = {
            "budget_exhausted",
            "rate_limited",
        }

        ai_status = (
            decision.status
            if decision.status in allowed_statuses
            else "budget_exhausted"
        )

        message = decision.reason or (
            "The shared Groq budget does not currently "
            "allow this request."
        )

        if decision.retry_after_seconds is not None:
            message += (
                f" Retry after approximately "
                f"{decision.retry_after_seconds} seconds."
            )

        return {
            "ai_status": ai_status,
            "report": None,
            "error": "GroqBudgetDenied",
            "error_message": message,
        }

    reservation_id = decision.reservation_id
    started_at = time.perf_counter()

    try:
        client = Groq(
            api_key=settings.groq_api_key,
            timeout=30.0,
            max_retries=0,
        )

        completion = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=0.2,
            max_completion_tokens=max_completion_tokens,
            response_format={"type": "json_object"},
        )

    except Exception as exc:
        latency_ms = int(
            (time.perf_counter() - started_at) * 1000
        )

        status_code = getattr(exc, "status_code", None)

        if status_code == 429:
            retry_after = _get_retry_after_seconds(exc)

            try:
                settle_budget(
                    reservation_id=reservation_id,
                    actual_tokens=0,
                    status="rate_limited",
                    provider_status_code=429,
                    latency_ms=latency_ms,
                )
            except BudgetServiceError:
                pass

            message = (
                "Groq rate limit reached. "
                "No automatic retry was attempted."
            )

            if retry_after is not None:
                message += (
                    f" Retry after approximately {retry_after} seconds."
                )

            return {
                "ai_status": "rate_limited",
                "report": None,
                "error": "GroqRateLimit",
                "error_message": message,
            }

        try:
            settle_budget(
                reservation_id=reservation_id,
                actual_tokens=0,
                status="failed",
                provider_status_code=status_code,
                latency_ms=latency_ms,
            )
        except BudgetServiceError:
            pass

        return {
            "ai_status": "ai_deferred",
            "report": None,
            "error": type(exc).__name__,
            "error_message": str(exc)[:500],
        }

    latency_ms = int(
        (time.perf_counter() - started_at) * 1000
    )

    usage = getattr(completion, "usage", None)
    actual_tokens = getattr(usage, "total_tokens", None)

    if actual_tokens is None:
        actual_tokens = estimated_total_tokens

    try:
        settle_budget(
            reservation_id=reservation_id,
            actual_tokens=int(actual_tokens),
            status="completed",
            provider_status_code=200,
            latency_ms=latency_ms,
        )
    except BudgetServiceError as exc:
        return {
            "ai_status": "ai_deferred",
            "report": None,
            "error": "BudgetSettlementFailed",
            "error_message": (
                "Groq responded, but usage reconciliation failed. "
                f"{str(exc)}"
            ),
        }

    try:
        if not completion.choices:
            raise ValueError("Groq returned no completion choices.")

        choice = completion.choices[0]
        content = choice.message.content

        if not content or not content.strip():
            raise ValueError(
                "Groq returned empty content. "
                f"Finish reason: {choice.finish_reason}"
            )

        parsed = json.loads(content)
        report = InvestigationReport.model_validate(parsed)

        return {
            "ai_status": "ok",
            "report": report.model_dump(),
            "error": None,
            "error_message": None,
        }

    except Exception as exc:
        return {
            "ai_status": "ai_deferred",
            "report": None,
            "error": type(exc).__name__,
            "error_message": str(exc)[:500],
        }


def _analyze_sql(
    state: InvestigationState,
) -> dict[str, Any]:
    """Perform static SQL analysis without executing the SQL."""

    statement_info = classify_sql(state["sql"])
    normalized_sql = statement_info["sql"]

    deterministic_findings = analyze_sql_patterns(
        normalized_sql
    )

    analysis = {
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
        "finding_count": len(deterministic_findings),
    }

    return {
        "normalized_sql": normalized_sql,
        "statement_info": statement_info,
        "deterministic_findings": deterministic_findings,
        "analysis": analysis,
    }


def _build_workflow():
    workflow = StateGraph(InvestigationState)

    workflow.add_node("analyze_sql", _analyze_sql)
    workflow.add_node("retrieve_evidence", _retrieve_evidence)
    workflow.add_node("generate_report", _generate_report)

    workflow.add_edge(START, "analyze_sql")
    workflow.add_edge("analyze_sql", "retrieve_evidence")
    workflow.add_edge("retrieve_evidence", "generate_report")
    workflow.add_edge("generate_report", END)

    return workflow.compile()


_investigation_graph = _build_workflow()


def investigate(sql: str) -> dict[str, Any]:
    """Run the bounded, static SQL investigation workflow."""

    result = _investigation_graph.invoke({
        "sql": sql,
    })

    statement_info = result["statement_info"]

    return {
        "valid": True,
        "statement_type": statement_info["statement_type"],
        "sql": result["normalized_sql"],
        "analysis": result["analysis"],
        "deterministic_findings": result.get(
            "deterministic_findings",
            [],
        ),
        "retrieval_status": result.get(
            "retrieval_status",
            "unavailable",
        ),
        "evidence": result.get("evidence", []),
        "optimization_cases": result.get(
            "optimization_cases",
            [],
        ),
        "ai_status": result.get(
            "ai_status",
            "ai_deferred",
        ),
        "report": result.get("report"),
        "error": result.get("error"),
        "error_message": result.get("error_message"),
    }
