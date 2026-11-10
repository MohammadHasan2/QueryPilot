from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_analysis_returns_static_analysis():
    response = client.post(
        "/api/analysis",
        json={"sql": "SELECT 1"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["valid"] is True
    assert data["statement_type"] == "SELECT"
    assert data["analysis"]["mode"] == "static_sql_only"
    assert data["analysis"]["execution_performed"] is False
    assert data["analysis"]["execution_plan_available"] is False
    assert data["analysis"]["database"] is None


def test_analysis_accepts_write_statements():
    statements = [
        ("INSERT INTO orders (status) VALUES ('pending')", "INSERT"),
        ("UPDATE orders SET status = 'shipped' WHERE id = 1", "UPDATE"),
        ("DELETE FROM orders WHERE id = 1", "DELETE"),
    ]

    for sql, expected_type in statements:
        response = client.post(
            "/api/analysis",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["statement_type"] == expected_type
        assert data["analysis"]["execution_performed"] is False


def test_analysis_accepts_schema_statements():
    statements = [
        ("CREATE INDEX idx_orders_status ON orders (status)", "CREATE INDEX"),
        ("CREATE TABLE archive_orders (id INTEGER)", "CREATE TABLE"),
    ]

    for sql, expected_type in statements:
        response = client.post(
            "/api/analysis",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["statement_type"] == expected_type
        assert data["analysis"]["execution_plan_available"] is False


def test_analysis_warns_about_destructive_statements():
    for sql in [
        "DROP TABLE archive_orders",
        "TRUNCATE TABLE orders",
    ]:
        response = client.post(
            "/api/analysis",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["analysis"]["risk_level"] == "critical"
        assert data["analysis"]["execution_performed"] is False


def test_analysis_rejects_multiple_statements():
    response = client.post(
        "/api/analysis",
        json={"sql": "SELECT 1; SELECT 2"},
    )

    assert response.status_code == 400