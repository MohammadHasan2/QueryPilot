from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def disable_external_services(monkeypatch):
    """Keep these tests independent of Supabase and Groq."""
    from app import investigation

    monkeypatch.setattr(investigation.settings, "supabase_url", None)
    monkeypatch.setattr(
        investigation.settings,
        "supabase_function_secret",
        None,
    )
    monkeypatch.setattr(investigation.settings, "groq_api_key", None)


def test_select_returns_static_analysis_without_external_services(
    monkeypatch,
):
    disable_external_services(monkeypatch)

    response = client.post(
        "/api/investigate",
        json={"sql": "SELECT 1"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["valid"] is True
    assert data["statement_type"] == "SELECT"
    assert data["sql"] == "SELECT 1"
    assert data["analysis"]["execution_performed"] is False
    assert data["analysis"]["execution_plan_available"] is False
    assert data["analysis"]["mode"] == "static_sql_only"
    assert data["retrieval_status"] == "unavailable"
    assert data["evidence"] == []
    assert data["ai_status"] == "ai_deferred"
    assert data["report"] is None


def test_write_statements_are_accepted_for_static_analysis(monkeypatch):
    disable_external_services(monkeypatch)

    statements = [
        ("INSERT INTO orders (status) VALUES ('pending')", "INSERT"),
        ("UPDATE orders SET status = 'shipped' WHERE id = 1", "UPDATE"),
        ("DELETE FROM orders WHERE id = 1", "DELETE"),
    ]

    for sql, expected_type in statements:
        response = client.post(
            "/api/investigate",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["valid"] is True
        assert data["statement_type"] == expected_type
        assert data["analysis"]["execution_performed"] is False
        assert data["analysis"]["execution_plan_available"] is False
        assert data["analysis"]["mode"] == "static_sql_only"


def test_schema_statements_are_accepted_for_static_analysis(monkeypatch):
    disable_external_services(monkeypatch)

    statements = [
        (
            "CREATE INDEX idx_orders_status ON orders (status)",
            "CREATE INDEX",
        ),
        (
            "CREATE TABLE archive_orders (id INTEGER)",
            "CREATE TABLE",
        ),
    ]

    for sql, expected_type in statements:
        response = client.post(
            "/api/investigate",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["valid"] is True
        assert data["statement_type"] == expected_type
        assert data["analysis"]["execution_performed"] is False
        assert data["analysis"]["execution_plan_available"] is False
        assert data["analysis"]["mode"] == "static_sql_only"


def test_destructive_statements_are_analyzed_but_never_executed(
    monkeypatch,
):
    disable_external_services(monkeypatch)

    statements = [
        "DROP TABLE archive_orders",
        "TRUNCATE TABLE orders",
    ]

    for sql in statements:
        response = client.post(
            "/api/investigate",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["valid"] is True
        assert data["analysis"]["risk_level"] == "critical"
        assert data["analysis"]["execution_performed"] is False
        assert data["analysis"]["execution_plan_available"] is False
        assert data["analysis"]["mode"] == "static_sql_only"


def test_multiple_statements_are_rejected(monkeypatch):
    disable_external_services(monkeypatch)

    response = client.post(
        "/api/investigate",
        json={"sql": "SELECT 1; SELECT 2"},
    )

    assert response.status_code == 400


def test_blocked_statement_is_rejected(monkeypatch):
    disable_external_services(monkeypatch)

    response = client.post(
        "/api/investigate",
        json={"sql": "COPY orders TO '/tmp/orders.csv'"},
    )

    assert response.status_code == 400
