
from types import SimpleNamespace

import pytest

from app import investigation
from app.budget_manager import BudgetDecision, BudgetServiceError


def make_state():
    return {
        "normalized_sql": "SELECT 1",
        "analysis": {
            "plan": [
                {
                    "Plan": {"Node Type": "Result"},
                    "Execution Time": 0.02,
                }
            ]
        },
        "evidence": [],
    }


def allow_reservation():
    return BudgetDecision(
        allowed=True,
        status="reserved",
        reservation_id="test-reservation-id",
    )


def make_completion():
    report_json = (
        '{"summary":"Query analyzed.",'
        '"findings":[] ,'
        '"overall_recommendation":"No changes required.",'
        '"limitations":[]}'
    )

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=report_json),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=50),
    )


def configure_groq(monkeypatch):
    monkeypatch.setattr(
        investigation.settings,
        "groq_api_key",
        "test-api-key",
    )


def test_budget_denial_prevents_groq_call(monkeypatch):
    configure_groq(monkeypatch)

    monkeypatch.setattr(
        investigation,
        "reserve_budget",
        lambda **kwargs: BudgetDecision(
            allowed=False,
            status="budget_exhausted",
            reason="TPM limit reached",
        ),
    )

    monkeypatch.setattr(
        investigation,
        "Groq",
        lambda **kwargs: pytest.fail("Groq must not be called"),
    )

    result = investigation._generate_report(make_state())

    assert result["ai_status"] == "budget_exhausted"
    assert result["report"] is None


def test_budget_service_failure_fails_closed(monkeypatch):
    configure_groq(monkeypatch)

    def fail_reservation(**kwargs):
        raise BudgetServiceError("Supabase unavailable")

    monkeypatch.setattr(
        investigation,
        "reserve_budget",
        fail_reservation,
    )

    monkeypatch.setattr(
        investigation,
        "Groq",
        lambda **kwargs: pytest.fail("Groq must not be called"),
    )

    result = investigation._generate_report(make_state())

    assert result["ai_status"] == "ai_deferred"
    assert result["error"] == "BudgetServiceUnavailable"


def test_successful_request_settles_actual_usage(monkeypatch):
    configure_groq(monkeypatch)

    reservations = []
    settlements = []

    def fake_reserve(**kwargs):
        reservations.append(kwargs)
        return allow_reservation()

    def fake_settle(**kwargs):
        settlements.append(kwargs)

    class FakeGroq:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0

            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: make_completion()
                )
            )

    monkeypatch.setattr(investigation, "reserve_budget", fake_reserve)
    monkeypatch.setattr(investigation, "settle_budget", fake_settle)
    monkeypatch.setattr(investigation, "Groq", FakeGroq)

    result = investigation._generate_report(make_state())

    assert result["ai_status"] == "ok"
    assert result["report"]["summary"] == "Query analyzed."

    assert len(reservations) == 1
    assert reservations[0]["estimated_tokens"] <= 8000

    assert len(settlements) == 1
    assert settlements[0]["reservation_id"] == "test-reservation-id"
    assert settlements[0]["actual_tokens"] == 50
    assert settlements[0]["status"] == "completed"


def test_rate_limit_is_settled_without_retry(monkeypatch):
    configure_groq(monkeypatch)

    settlements = []

    class FakeRateLimitError(Exception):
        status_code = 429

        response = SimpleNamespace(
            headers={"retry-after": "30"}
        )

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: (
                        _raise_rate_limit()
                    )
                )
            )

    def _raise_rate_limit():
        raise FakeRateLimitError("Rate limit reached")

    monkeypatch.setattr(
        investigation,
        "reserve_budget",
        lambda **kwargs: allow_reservation(),
    )

    monkeypatch.setattr(
        investigation,
        "settle_budget",
        lambda **kwargs: settlements.append(kwargs),
    )

    monkeypatch.setattr(investigation, "Groq", FakeGroq)

    result = investigation._generate_report(make_state())

    assert result["ai_status"] == "rate_limited"
    assert len(settlements) == 1
    assert settlements[0]["status"] == "rate_limited"
    assert settlements[0]["provider_status_code"] == 429


def test_settlement_failure_defers_report(monkeypatch):
    configure_groq(monkeypatch)

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: make_completion()
                )
            )

    def fail_settlement(**kwargs):
        raise BudgetServiceError("Settlement unavailable")

    monkeypatch.setattr(
        investigation,
        "reserve_budget",
        lambda **kwargs: allow_reservation(),
    )
    monkeypatch.setattr(
        investigation,
        "settle_budget",
        fail_settlement,
    )
    monkeypatch.setattr(investigation, "Groq", FakeGroq)

    result = investigation._generate_report(make_state())

    assert result["ai_status"] == "ai_deferred"
    assert result["error"] == "BudgetSettlementFailed"
    assert result["report"] is None
