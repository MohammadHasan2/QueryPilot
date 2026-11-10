import pytest
from fastapi import HTTPException

from app.sql_safety import validate_readonly_select


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT id, message FROM lab_status",
        "WITH recent AS (SELECT 1 AS value) SELECT value FROM recent",
    ],
)
def test_accepts_select_queries(sql):
    assert validate_readonly_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "UPDATE lab_status SET message = 'changed'",
        "DELETE FROM lab_status",
        "DROP TABLE lab_status",
        "SELECT 1; SELECT 2",
        "SELECT * INTO copied_status FROM lab_status",
        "SELECT * FROM lab_status FOR UPDATE",
        "SELECT pg_sleep(10)",
        "WITH changed AS (DELETE FROM lab_status RETURNING *) SELECT * FROM changed",
    ],
)
def test_rejects_unsafe_queries(sql):
    with pytest.raises(HTTPException):
        validate_readonly_select(sql)


def test_rejects_invalid_sql():
    with pytest.raises(HTTPException):
        validate_readonly_select("SELECT FROM WHERE")
