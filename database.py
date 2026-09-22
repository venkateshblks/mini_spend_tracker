"""PostgreSQL connections and explicit, non-destructive schema initialization."""
from pathlib import Path

import click
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from flask import current_app, g


def get_db():
    if "db" not in g:
        connection = psycopg.connect(
            current_app.config["DATABASE_URL"],
            connect_timeout=10,
            autocommit=True,
            row_factory=dict_row,
            prepare_threshold=None,  # Avoid automatic server-side prepared statements.
        )
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(
                sql.Identifier(current_app.config["DATABASE_SCHEMA"])
            ))
        except Exception:
            connection.close()
            raise
        g.db = connection
    return g.db


def init_db():
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    db = get_db()
    with db.transaction():
        db.execute(schema, prepare=False)


def register_database(app):
    url = app.config.get("DATABASE_URL")
    if not isinstance(url, str) or not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("Set DATABASE_URL to a PostgreSQL connection URL in .env or the environment.")

    @app.teardown_appcontext
    def close_db(error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.cli.command("init-db")
    def init_db_command():
        """Create missing tables and indexes without deleting expense data."""
        try:
            init_db()
        except psycopg.Error:
            raise click.ClickException("Database initialization failed. Check DATABASE_URL and database availability.") from None
        click.echo("PostgreSQL schema initialized.")
