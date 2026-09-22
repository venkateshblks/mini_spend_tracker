# Mini Spend Tracker

A small single-user Python REST API with PostgreSQL persistence (Supabase supported) and a plain HTML/JavaScript UI. Add expenses, filter the list, compare monthly totals, and flag categories whose spending grew by more than 20%.

## Connect Supabase and run locally

Requires Python 3.10+ and a PostgreSQL database. The frontend is served by Flask; no separate build is required.

1. In your Supabase project, open **Connect > Session pooler** and copy the PostgreSQL URI (port 5432). The session pooler supports IPv4 hosts, unlike the default direct endpoint on some projects. Use the exact host and username shown in your project, not a guessed region.
2. Copy `.env.example` to `.env` (a blank `.env` may already exist locally). Set `DATABASE_URL` to the URI, replace the password placeholder with your **database password**, and include `?sslmode=require`. URL-encode special characters in the password. This is not the Supabase project URL or an anon/service-role API key. Do not commit or share `.env`.
3. Install and initialize:

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux instead: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m flask --app app init-db
python -m flask --app app run
```

Open http://127.0.0.1:5000. Environment variables override `.env`. The app requires `DATABASE_URL`; it does not silently fall back to SQLite. `init-db` creates missing tables/indexes without deleting existing data. Existing SQLite files are preserved but are not read or automatically imported.

**Where to see your data:** in Supabase, open **Table Editor > public > expenses**. The `amount_cents` column stores 125.50 as 12550. For a readable amount, use the SQL Editor:

```sql
SELECT id, amount_cents / 100.0 AS amount, category, note, date
FROM public.expenses
ORDER BY date DESC, id DESC;
```

Row Level Security is enabled with no public policies, so anonymous Supabase Data API clients cannot access this table. The Flask server uses the owner connection from the pooler URI; never put that URI in frontend JavaScript. This is still a shared single-user demo: the Flask expense endpoints themselves have no authentication, so use sample expenses for a public demo.

See [Supabase connection documentation](https://supabase.com/docs/guides/database/connecting-to-postgres).

## Deploy on Render

1. Push this project to your GitHub repository, excluding `.env`, `.venv`, and `instance`.
2. In Render choose **New > Blueprint**, connect the repository, and use the included `render.yaml`. It selects a free Python web service.
3. When prompted, set the secret `DATABASE_URL` to the same Supabase **Session pooler** URI with `sslmode=require`.
4. Deploy. The start command initializes the schema, then starts Waitress. Open the generated public URL and add a sample expense; it should appear in Supabase Table Editor. `/health` verifies the database/table is reachable.

For manual Web Service setup, use:

```text
Build: pip install -r requirements.txt
Start: python -m flask --app app init-db && waitress-serve --host=0.0.0.0 --port=$PORT --call app:create_app
Health check: /health
```

Render stores no database files: expenses live in Supabase, so Render restarts do not erase them. Free Render services can spin down and have cold starts; free Supabase projects can pause after inactivity. Visit and check the demo before the interview. [Render free service limits](https://render.com/docs/free) · [Supabase free plan](https://supabase.com/pricing).

## Tests with real PostgreSQL

Tests require `TEST_DATABASE_URL` pointing to a **separate disposable database**. They create/drop uniquely named schemas and do not truncate public tables. They deliberately fail without test database configuration; they do not substitute an in-memory database or mocks for integration coverage.

If Docker is installed, the included Compose file provides local PostgreSQL:

```sh
docker compose up -d --wait
```

Set the test URL in `.env` or your shell:

```text
TEST_DATABASE_URL=postgresql://spend:spend_local_only@127.0.0.1:5433/spend_tracker_test
```

```sh
python -m pytest -q
```

The local-only Compose credentials are disposable examples, not cloud credentials. Use a separate local database for application development if you do not want to use Supabase. GitHub Actions runs this suite against a PostgreSQL service using the included workflow.

Tests cover persistence across app instances, exact monetary totals, validation, inclusive filters, SQL parameter binding, calendar boundaries, zero baselines, category insights, PostgreSQL constraints, transaction rollback, repeatable initialization, and database failure responses.

## API

Amounts are positive, at most `999999999.99`, with at most two fractional digits. JSON numbers are accepted; decimal strings are recommended and returned so clients do not lose precision. All entries use one implicit currency. Dates must be real calendar dates in `YYYY-MM-DD`; future dates are allowed. Categories are trimmed and lowercased (1–50 characters). Notes are optional strings of at most 500 characters. Unknown fields and query parameters are rejected to catch mistakes.

### `POST /expenses`

```json
{"amount": "125.50", "category": "food", "note": "Lunch", "date": "2026-09-22"}
```

Returns `201` with `id`, `amount`, `category`, `note`, and `date`. Example using curl (use `curl.exe` in PowerShell):

```sh
curl -X POST http://127.0.0.1:5000/expenses -H 'Content-Type: application/json' -d '{"amount":"125.50","category":"food","note":"Lunch","date":"2026-09-22"}'
```

### `GET /expenses`

Returns an array ordered by date descending, then ID descending. Optional filters combine with AND; both date boundaries are inclusive:

```text
/expenses?category=food&start_date=2026-09-01&end_date=2026-09-30
```

Either date boundary may be used alone. A reversed date range is rejected.

### `GET /summary?month=2026-09`

The **total and category breakdown cover the selected calendar month**, defaulting to the server's current month. They are compared with the entire preceding calendar month, including December for January. An ongoing month therefore compares its recorded spending so far with the previous full month, not the same elapsed number of days.

Example response, assuming 100.00 was spent on food in August and 125.50 in September:

```json
{
  "month": "2026-09",
  "total_spend": "125.50",
  "spend_by_category": {"food": "125.50"},
  "previous_month": "2026-08",
  "previous_month_total": "100.00",
  "month_over_month_change": "25.50",
  "month_over_month_change_percent": 25.5,
  "insights": [{"category": "food", "change_percent": 25.5}]
}
```

Percentage change is `(current - previous) / previous * 100`, rounded to two decimals. If previous spending is zero, percentage change is `null`, even when both months are empty. Categories without a prior baseline are not flagged. Exactly 20% growth is not flagged. Month `0001-01` is rejected because it has no representable previous month.

Errors have a consistent shape:

```json
{"error": {"code": 400, "message": "start_date must not be after end_date."}}
```

Validation/malformed JSON returns `400`, a non-JSON POST returns `415`, a body over 16 KiB returns `413`, and database failures return a generic `503`. Missing paths and unsupported methods return JSON `404`/`405` errors.

## Design decisions

- Flask and Psycopg 3 keep SQL explicit and easy to explain. Each request opens a PostgreSQL connection that closes afterward; Supabase's session pooler handles the upstream pool. Autocommit avoids idle read transactions, while inserts and schema initialization use explicit transactions.
- Money is validated with `Decimal`, stored as integer cents, and summed as integers. No silent rounding on input and no floating-point monetary totals.
- The schema has a primary key, non-null fields, amount/length checks, and date plus category/date indexes. A native PostgreSQL DATE column validates stored dates; the API also validates dates before executing SQL. BIGINT cents support the documented amount limit, and identity IDs are returned using RETURNING. SQL values use bound parameters; inserts commit atomically.
- Both months are aggregated in one query for a consistent summary snapshot. Month comparisons are deterministic when `month` is supplied.
- The same-origin UI uses `fetch`, displays loading/errors/empty states, and refreshes the saved expense's month. It inserts user content with `textContent` and prevents older refreshes from overwriting a newer month selection.

## With more time

Add user accounts and per-user authorization before exposing personal spending publicly, pagination for large lists, edit/delete with an audit trail, schema migrations, backups, and a configured currency/timezone. Add automated browser tests, application connection pooling for heavier workloads, and a least-privilege runtime database role separate from the schema owner.

Render deployment configuration is included; deployment still requires your repository and Supabase connection configuration. Authentication is not included: everyone with access to the Flask API shares the same expenses.

## AI-use note for submission

Codex assisted with implementation, documentation, and automated tests. During that work, the design used integer cents instead of floating-point monetary storage and defined explicit zero-baseline and month-boundary behavior. At my request, the implementation was changed from SQLite to PostgreSQL with Supabase/Render configuration and real PostgreSQL tests.
Before submitting, add one sentence describing what **you personally reviewed, changed, or rejected**; do not claim changes or understanding you have not verified. Be ready to explain the validation, SQL queries, percentage calculation, and tests.
