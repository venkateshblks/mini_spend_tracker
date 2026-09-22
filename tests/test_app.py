import os
from uuid import uuid4

import psycopg
from psycopg import sql
from dotenv import load_dotenv

import pytest

from app import create_app
from database import get_db, init_db


@pytest.fixture(scope="session")
def database_url():
    load_dotenv()
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL to a disposable PostgreSQL database; see README.")
    if url == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must differ from the app DATABASE_URL.")
    return url


@pytest.fixture
def app(database_url):
    # Each test owns only its randomly named schema; never truncate public tables.
    schema = "test_" + uuid4().hex
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            app = create_app({"TESTING": True, "DATABASE_URL": database_url, "DATABASE_SCHEMA": schema})
            with app.app_context():
                init_db()
            yield app
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def client(app):
    return app.test_client()


def add(client, amount="12.50", category="food", date="2026-02-10", **extra):
    response = client.post("/expenses", json=dict(amount=amount, category=category, date=date, **extra))
    assert response.status_code == 201, response.json
    return response.json


def test_create_and_persist_after_app_restart(client, app):
    expense = add(client, category="  FOOD ", note=" lunch ")
    assert expense == {"id": 1, "amount": "12.50", "category": "food", "date": "2026-02-10", "note": "lunch"}
    restarted = create_app({"TESTING": True, "DATABASE_URL": app.config["DATABASE_URL"],
                            "DATABASE_SCHEMA": app.config["DATABASE_SCHEMA"]}).test_client()
    assert restarted.get("/expenses").json == [expense]
    with app.app_context():
        assert get_db().execute("SELECT amount_cents FROM expenses").fetchone()["amount_cents"] == 1250


@pytest.mark.parametrize("field,value", [
    ("amount", 0), ("amount", -1), ("amount", True), ("amount", None),
    ("amount", "1.001"), ("amount", "NaN"), ("amount", "Infinity"),
    ("amount", "1000000000.00"), ("amount", "1e999999"), ("amount", []),
    ("amount", "1.00000000000000000000000000001"),
    ("category", "food\x00"), ("note", "note\x00"), ("category", "  "), ("category", "x" * 51), ("category", "\u0130" * 50), ("category", 4),
    ("date", "2026-02-29"), ("date", "2026-2-01"), ("date", "2026-02-01T12:00:00"),
    ("note", "x" * 501), ("note", None), ("unexpected", "value"),
])
def test_invalid_expense_does_not_write(client, field, value):
    payload = {"amount": "12.50", "category": "food", "date": "2026-02-10", field: value}
    response = client.post("/expenses", json=payload)
    assert response.status_code == 400
    assert response.json["error"]["message"]
    assert client.get("/expenses").json == []


@pytest.mark.parametrize("field", ["amount", "category", "date"])
def test_required_fields(client, field):
    payload = {"amount": "12.50", "category": "food", "date": "2026-02-10"}
    del payload[field]
    assert client.post("/expenses", json=payload).status_code == 400


def test_request_errors_are_json(client):
    for response, expected in [
        (client.post("/expenses", data="{", content_type="application/json"), 400),
        (client.post("/expenses", json=[]), 400),
        (client.post("/expenses", data="hello"), 415),
        (client.post("/expenses", data="x" * 17000, content_type="application/json"), 413),
        (client.get("/missing"), 404),
        (client.delete("/expenses"), 405),
    ]:
        assert response.status_code == expected
        assert response.json["error"]["code"] == expected


def test_filter_dates_are_inclusive_and_combined_with_category(client):
    add(client, date="2026-01-31")
    first = add(client, date="2026-02-01")
    last = add(client, date="2026-02-28")
    add(client, category="transport", date="2026-02-15")
    add(client, date="2026-03-01")
    response = client.get("/expenses?category=FOOD&start_date=2026-02-01&end_date=2026-02-28")
    assert response.json == [last, first]
    assert len(client.get("/expenses?start_date=2026-03-01").json) == 1
    assert len(client.get("/expenses?end_date=2026-01-31").json) == 1
    assert len(client.get("/expenses?category=transport").json) == 1
    assert client.get("/expenses?category=missing").json == []


@pytest.mark.parametrize("query", [
    "start_date=oops", "end_date=2026-04-31", "category=", "start_date=2026-03-01&end_date=2026-02-01",
    "category=food&category=bills", "start=2026-01-01",
])
def test_invalid_filters(client, query):
    assert client.get("/expenses?" + query).status_code == 400


def test_category_is_bound_as_sql_parameter(client):
    unusual = "food'; DROP TABLE expenses; --"
    expense = add(client, category=unusual)
    assert client.get("/expenses", query_string={"category": unusual}).json == [expense]
    assert len(client.get("/expenses").json) == 1


def test_summary_exact_money_and_year_boundary(client):
    add(client, "10.00", date="2025-12-31")
    add(client, "0.10", date="2026-01-01")
    add(client, "0.20", date="2026-01-31")
    add(client, "19.70", category="bills", date="2026-01-15")
    add(client, "999.00", date="2026-02-01")
    result = client.get("/summary?month=2026-01").json
    assert result["total_spend"] == "20.00"
    assert result["spend_by_category"] == {"food": "0.30", "bills": "19.70"}
    assert result["previous_month"] == "2025-12"
    assert result["previous_month_total"] == "10.00"
    assert result["month_over_month_change"] == "10.00"
    assert result["month_over_month_change_percent"] == 100.0
    assert result["insights"] == []  # New category has no baseline.


def test_insight_threshold_is_strict_and_category_specific(client):
    for name in ("food", "bills"):
        add(client, "100.00", category=name, date="2026-01-10")
    add(client, "120.00", category="food")
    add(client, "120.01", category="bills")
    assert client.get("/summary?month=2026-02").json["insights"] == [
        {"category": "bills", "change_percent": 20.01}
    ]


def test_empty_summary_and_zero_baseline(client):
    result = client.get("/summary?month=2026-02").json
    assert result["total_spend"] == "0.00"
    assert result["spend_by_category"] == {}
    assert result["month_over_month_change_percent"] is None
    add(client)
    result = client.get("/summary?month=2026-02").json
    assert result["month_over_month_change_percent"] is None
    assert result["insights"] == []


def test_month_with_no_spend_after_active_month(client):
    add(client, "25.01", date="2026-01-31")
    result = client.get("/summary?month=2026-02").json
    assert result["month_over_month_change"] == "-25.01"
    assert result["month_over_month_change_percent"] == -100.0


def test_leap_day_is_included(client):
    add(client, "10", date="2024-02-29")
    assert client.get("/summary?month=2024-02").json["total_spend"] == "10.00"


@pytest.mark.parametrize("month", ["2026-13", "2026-2", "0000-01", "0001-01", "bad", ""])
def test_invalid_summary_month(client, month):
    assert client.get("/summary", query_string={"month": month}).status_code == 400


def test_default_month_and_extreme_supported_month(client):
    from datetime import date

    assert client.get("/summary").json["month"] == date.today().strftime("%Y-%m")
    assert client.get("/summary?month=9999-12").status_code == 200
    assert client.get("/summary?month=2026-01&month=2026-02").status_code == 400
    assert client.get("/summary?unknown=1").status_code == 400


def test_ui_assets(client):
    assert b"Mini Spend Tracker" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_schema_initialization_is_repeatable(client, app):
    expense = add(client)
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0
    assert client.get("/expenses").json == [expense]


def test_health_checks_database(client):
    assert client.get("/health").json == {"status": "ok"}


def test_database_constraints_and_transaction_rollback(app):
    with app.app_context():
        db = get_db()
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                db.execute("INSERT INTO expenses(amount_cents, category, date) VALUES (100, 'food', '2026-01-01')")
                db.execute("INSERT INTO expenses(amount_cents, category, date) VALUES (-1, 'food', '2026-01-01')")
        assert db.execute("SELECT COUNT(*) AS count FROM expenses").fetchone()["count"] == 0
        assert db.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'expenses'::regclass").fetchone()["relrowsecurity"]


def test_database_failure_is_generic_json(client, monkeypatch):
    def unavailable():
        raise psycopg.OperationalError("private connection details")
    monkeypatch.setattr("app.get_db", unavailable)
    for path in ("/expenses", "/summary", "/health"):
        response = client.get(path)
        assert response.status_code == 503
        assert "private" not in response.get_data(as_text=True)


def test_missing_database_configuration():
    with pytest.raises(RuntimeError, match="Set DATABASE_URL"):
        create_app({"DATABASE_URL": ""})
