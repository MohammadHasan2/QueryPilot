from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_validate_select():
    response = client.post(
        "/api/sql/validate",
        json={"sql": "SELECT id, message FROM lab_status"},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["valid"] is True
    assert data["statement_type"] == "SELECT"
    assert data["execution_allowed"] is False
    assert data["analysis_mode"] == "static_sql_only"


def test_validate_write_statements():
    statements = [
        ("INSERT INTO orders (status) VALUES ('pending')", "INSERT"),
        ("UPDATE orders SET status = 'shipped' WHERE id = 1", "UPDATE"),
        ("DELETE FROM orders WHERE id = 1", "DELETE"),
    ]

    for sql, expected_type in statements:
        response = client.post(
            "/api/sql/validate",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text
        assert response.json()["statement_type"] == expected_type
        assert response.json()["execution_allowed"] is False


def test_validate_schema_and_destructive_statements():
    statements = [
        ("CREATE INDEX idx_orders_status ON orders (status)", "CREATE INDEX"),
        ("DROP TABLE archive_orders", "DROP"),
        ("TRUNCATE TABLE orders", "TRUNCATE"),
    ]

    for sql, expected_type in statements:
        response = client.post(
            "/api/sql/validate",
            json={"sql": sql},
        )

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["statement_type"] == expected_type
        assert data["execution_allowed"] is False

    assert client.post(
        "/api/sql/validate",
        json={"sql": "DROP TABLE archive_orders"},
    ).json()["risk_level"] == "critical"


def test_reject_multiple_statements():
    response = client.post(
        "/api/sql/validate",
        json={"sql": "SELECT 1; SELECT 2"},
    )

    assert response.status_code == 400


def test_reject_select_into():
    response = client.post(
        "/api/sql/validate",
        json={"sql": "SELECT * INTO copied_status FROM lab_status"},
    )

    assert response.status_code == 400


def test_reject_invalid_sql():
    response = client.post(
        "/api/sql/validate",
        json={"sql": "SELECT FROM WHERE"},
    )

    assert response.status_code == 400


def test_reject_blocked_statement():
    response = client.post(
        "/api/sql/validate",
        json={"sql": "COPY orders TO '/tmp/orders.csv'"},
    )

    assert response.status_code == 400