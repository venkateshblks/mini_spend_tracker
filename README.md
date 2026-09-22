# Mini Spend Tracker

A Python Flask application for recording expenses, filtering transaction history, and comparing monthly spending. It uses PostgreSQL for persistence and a lightweight HTML, CSS, and JavaScript frontend.

## Run locally

Requires Python 3.10+ and PostgreSQL (local or hosted on Supabase).

1. Create and activate a virtual environment from the project directory:

   ```sh
   python -m venv .venv
   ```

   Windows PowerShell: `.venv\Scripts\Activate.ps1`  
   macOS/Linux: `source .venv/bin/activate`

2. Install dependencies:

   ```sh
   python -m pip install -r requirements-dev.txt
   ```

3. Copy `.env.example` to `.env` and set `DATABASE_URL` to your PostgreSQL connection URI. For Supabase, use **Connect → Session pooler** on port **5432**, replace the password placeholder, and include `sslmode=require`. URL-encode special characters in the password. The `.env` file is excluded from Git.

4. Initialize the schema and start the application:

   ```sh
   python -m flask --app app init-db
   python -m flask --app app run
   ```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). If the port is occupied, append `--port 5001` to the run command. Schema initialization can be repeated without deleting existing expenses.

## API and UI

| Endpoint | Purpose |
| --- | --- |
| `POST /expenses` | Create an expense with `amount`, `category`, `date`, and an optional `note`. |
| `GET /expenses` | Filter by `category`, `start_date`, and `end_date`; paginate with `limit` and `offset`. |
| `GET /summary?month=2026-09` | Return total spending, category totals, and the change from the previous month. |
| `GET /health` | Check database connectivity and table availability. |

Example expense:

```json
{"amount": "125.50", "category": "food", "note": "Lunch", "date": "2026-09-22"}
```

Dates use `YYYY-MM-DD`; date filters are inclusive. The expense list returns `{expenses, limit, offset, has_more}`, with 20 records by default and a maximum of 100 per request. Invalid input returns a JSON error with an appropriate HTTP status.

The UI supports adding expenses, category/date filtering, Previous/Next navigation, and monthly summaries. List filters do not change the monthly summary. This is a single-user demo with one implicit currency and no authentication.

## Tests

Tests run against a separate PostgreSQL database. With Docker installed, start the included test database:

```sh
docker compose up -d --wait
```

Add this local test connection to `.env`, keeping it separate from `DATABASE_URL`:

```text
TEST_DATABASE_URL=postgresql://spend:spend_local_only@127.0.0.1:5433/spend_tracker_test
```

Run the suite:

```sh
python -m pytest -q
```

Each test uses an isolated schema. Coverage includes persistence, input validation, monetary precision, filters, pagination, month boundaries, zero-spend comparisons, database constraints, and transaction rollback. GitHub Actions also runs the suite against PostgreSQL.

## Key design decisions

- **Flask and Psycopg:** a small application with explicit, parameterized SQL and a database connection closed after each request.
- **Exact monetary values:** amounts are validated with `Decimal`, stored as integer cents, and returned as decimal strings to avoid floating-point rounding errors.
- **Database integrity:** identity IDs, native dates, constraints, indexes, and atomic inserts keep storage consistent.
- **Database pagination:** SQL `LIMIT`/`OFFSET` retrieves one page plus one extra row to determine whether another page exists. Results are ordered by date and ID.
- **Monthly comparison:** the selected calendar month is compared with the full previous month. An ongoing month includes spending recorded so far; percentage change is `null` when previous spending is zero.

## Deployment

Use the included `render.yaml` to deploy on Render and set `DATABASE_URL` to the Supabase Session pooler URI. For manual setup:

```text
Build: pip install -r requirements.txt
Start: python -m flask --app app init-db && waitress-serve --host=0.0.0.0 --port=$PORT --call app:create_app
Health check: /health
```

## With more time

Add user authentication and per-user data access, expense editing/deletion, versioned database migrations, and automated browser tests. For larger datasets, use cursor pagination and application connection pooling. Add explicit currency/timezone settings and a least-privilege database role for the running application.
