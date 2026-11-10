from dataclasses import dataclass
from typing import Any

import httpx

from app.settings import settings


class BudgetServiceError(RuntimeError):
    """Raised when the shared Supabase budget service cannot be used."""


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    status: str
    reason: str | None = None
    retry_after_seconds: int | None = None
    reservation_id: str | None = None


def _rpc(name: str, payload: dict[str, Any]) -> Any:
    if not settings.supabase_url:
        raise BudgetServiceError("SUPABASE_URL is not configured.")

    if not settings.supabase_service_role_key:
        raise BudgetServiceError(
            "SUPABASE_SERVICE_ROLE_KEY is not configured."
        )

    endpoint = (
        f"{settings.supabase_url.rstrip('/')}"
        f"/rest/v1/rpc/{name}"
    )

    service_key = settings.supabase_service_role_key

    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                endpoint,
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    except (httpx.HTTPError, ValueError) as exc:
        raise BudgetServiceError(
            f"Supabase budget RPC '{name}' failed."
        ) from exc


def _rpc_object(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise BudgetServiceError("Budget RPC returned no result.")
        payload = payload[0]

    if not isinstance(payload, dict):
        raise BudgetServiceError("Budget RPC returned an invalid result.")

    return payload


def reserve_budget(
    model: str,
    estimated_tokens: int,
    investigation_id: str | None = None,
) -> BudgetDecision:
    if estimated_tokens <= 0:
        raise ValueError("estimated_tokens must be positive.")

    if estimated_tokens > 8000:
        return BudgetDecision(
            allowed=False,
            status="budget_exhausted",
            reason="The request exceeds the app-level 8000 TPM ceiling.",
        )

    raw = _rpc(
        "reserve_groq_budget",
        {
            "p_model": model,
            "p_estimated_tokens": estimated_tokens,
            "p_investigation_id": investigation_id,
        },
    )

    result = _rpc_object(raw)

    allowed = result.get("allowed") is True
    status = str(
        result.get("status")
        or ("reserved" if allowed else "budget_exhausted")
    )

    retry_after = result.get("retry_after_seconds")
    try:
        retry_after = (
            int(retry_after) if retry_after is not None else None
        )
    except (TypeError, ValueError):
        retry_after = None

    reservation_id = result.get("reservation_id")

    if allowed and not reservation_id:
        raise BudgetServiceError(
            "Budget RPC allowed a request but returned no reservation ID."
        )

    return BudgetDecision(
        allowed=allowed,
        status=status,
        reason=result.get("reason"),
        retry_after_seconds=retry_after,
        reservation_id=(
            str(reservation_id) if reservation_id else None
        ),
    )


def settle_budget(
    reservation_id: str,
    actual_tokens: int,
    status: str,
    provider_status_code: int | None = None,
    latency_ms: int | None = None,
) -> None:
    if actual_tokens < 0:
        raise ValueError("actual_tokens cannot be negative.")

    raw = _rpc(
        "settle_groq_budget",
        {
            "p_reservation_id": reservation_id,
            "p_actual_tokens": actual_tokens,
            "p_status": status,
            "p_provider_status_code": provider_status_code,
            "p_latency_ms": latency_ms,
        },
    )

    result = _rpc_object(raw)

    if result.get("settled") is not True:
        reason = result.get("reason") or "unknown"
        raise BudgetServiceError(
            f"Supabase budget settlement was not confirmed: {reason}."
        )