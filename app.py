"""A single-user expense API. Run with: flask --app app run."""

import calendar
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import psycopg
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import BadRequest, HTTPException

from database import get_db, register_database


def money(cents):
    """Return a fixed-point JSON string without a floating-point conversion."""
    return f"{cents // 100}.{cents % 100:02d}"


def parse_date(value, field="date"):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise BadRequest(f"{field} must be a date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{field} must be a valid calendar date.") from None


def category(value):
    if not isinstance(value, str) or "\x00" in value or not 1 <= len(value.strip()) <= 50:
        raise BadRequest("category must be 1–50 characters and contain no null bytes.")
    normalized = value.strip().lower()
    if len(normalized) > 50:
        raise BadRequest("category must be at most 50 characters after normalization.")
    return normalized


def amount_cents(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise BadRequest("amount must be a positive decimal with at most two decimal places.")
    # Bound input size before parsing arbitrary numeric strings.
    if len(str(value)) > 32:
        raise BadRequest("amount is too large.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not Decimal("0") < amount <= Decimal("999999999.99"):
            raise InvalidOperation
        if amount != amount.quantize(Decimal("0.01")):
            raise InvalidOperation
        return int(amount * 100)
    except InvalidOperation:
        raise BadRequest("amount must be 0.01–999999999.99 with at most two decimal places.") from None


def percent_change(current, previous):
    if previous == 0:
        return None
    return float((Decimal(current - previous) * 100 / Decimal(previous)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ))


def check_query(allowed):
    if set(request.args) - allowed:
        raise BadRequest("Unknown query parameter.")
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        raise BadRequest("Query parameters must not be repeated.")


def create_app(config=None):
    load_dotenv()  # Existing environment variables take precedence over .env.
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        DATABASE_SCHEMA="public",
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        app.config.update(config)
    register_database(app)

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()
        response.data = app.json.dumps({"error": {"code": error.code, "message": error.description}})
        response.content_type = "application/json"
        return response

    @app.errorhandler(psycopg.Error)
    def database_error(error):
        app.logger.error("Database operation failed (%s)", type(error).__name__)
        return jsonify(error={"code": 503, "message": "Database unavailable. Please try again."}), 503

    @app.errorhandler(500)
    def server_error(error):
        return jsonify(error={"code": 500, "message": "An unexpected server error occurred."}), 500

    @app.get("/health")
    def health():
        get_db().execute("SELECT 1 FROM expenses LIMIT 1").fetchone()
        return jsonify(status="ok")

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/expenses")
    def create_expense():
        data = request.get_json()
        if not isinstance(data, dict):
            raise BadRequest("Request body must be a JSON object.")
        if set(data) - {"amount", "category", "note", "date"}:
            raise BadRequest("Unknown expense field.")
        cents = amount_cents(data.get("amount"))
        normalized_category = category(data.get("category"))
        expense_date = parse_date(data.get("date")).isoformat()
        note = data.get("note", "")
        if not isinstance(note, str) or "\x00" in note or len(note) > 500:
            raise BadRequest("note must be a string of at most 500 characters with no null bytes.")
        db = get_db()
        with db.transaction():
            cursor = db.execute(
                "INSERT INTO expenses(amount_cents, category, note, date) VALUES (%s, %s, %s, %s) RETURNING id",
                (cents, normalized_category, note.strip(), expense_date),
            )
            expense_id = cursor.fetchone()["id"]
        return jsonify(id=expense_id, amount=money(cents), category=normalized_category,
                       note=note.strip(), date=expense_date), 201

    @app.get("/expenses")
    def list_expenses():
        check_query({"category", "start_date", "end_date", "limit", "offset"})
        paging = {}
        for key, default, minimum, maximum in (("limit", "20", 1, 100), ("offset", "0", 0, 2147483647)):
            raw = request.args.get(key, default)
            if not re.fullmatch(r"[0-9]{1,10}", raw) or not minimum <= int(raw) <= maximum:
                raise BadRequest(f"{key} must be an integer between {minimum} and {maximum}.")
            paging[key] = int(raw)
        limit, offset = paging["limit"], paging["offset"]
        clauses, params = [], []
        if "category" in request.args:
            clauses.append("category = %s")
            params.append(category(request.args["category"]))
        dates = {}
        for key, operator in (("start_date", ">="), ("end_date", "<=")):
            if key in request.args:
                dates[key] = parse_date(request.args[key], key).isoformat()
                clauses.append(f"date {operator} %s")
                params.append(dates[key])
        if dates.get("start_date", "") > dates.get("end_date", "9999-12-31"):
            raise BadRequest("start_date must not be after end_date.")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # Fetch only one page plus a look-ahead row; no full-table count needed.
        rows = get_db().execute(
            "SELECT * FROM expenses" + where + " ORDER BY date DESC, id DESC LIMIT %s OFFSET %s",
            params + [limit + 1, offset],
        ).fetchall()
        expenses = [dict(id=row["id"], amount=money(row["amount_cents"]),
                         category=row["category"], note=row["note"], date=row["date"].isoformat())
                    for row in rows[:limit]]
        return jsonify(expenses=expenses, limit=limit, offset=offset, has_more=len(rows) > limit)

    @app.get("/summary")
    def summary():
        check_query({"month"})
        month = request.args.get("month", date.today().strftime("%Y-%m"))
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
            raise BadRequest("month must use YYYY-MM format.")
        first = parse_date(month + "-01", "month")
        if first.year == 1 and first.month == 1:
            raise BadRequest("month must be later than 0001-01 to compare with a previous month.")
        previous_first = date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        # One query gives a consistent snapshot of both months.
        rows = get_db().execute("""
            SELECT to_char(date, 'YYYY-MM') AS month, category, SUM(amount_cents) AS cents
            FROM expenses WHERE date >= %s AND date <= %s GROUP BY month, category
        """, (previous_first.isoformat(), last.isoformat())).fetchall()
        current, previous = {}, {}
        for row in rows:
            (current if row["month"] == month else previous)[row["category"]] = int(row["cents"])
        total, previous_total = sum(current.values()), sum(previous.values())
        return jsonify(month=month, total_spend=money(total),
                       spend_by_category={key: money(value) for key, value in sorted(current.items())},
                       previous_month=previous_first.isoformat()[:7], previous_month_total=money(previous_total),
                       month_over_month_change=money(abs(total - previous_total)) if total >= previous_total else "-" + money(previous_total - total),
                       month_over_month_change_percent=percent_change(total, previous_total))

    return app
