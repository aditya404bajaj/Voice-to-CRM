"""Small database layer with one API for MySQL and SQLite.

All SQL in the app is written once with %s placeholders. For SQLite the
placeholders are converted to ?. Rows always come back as plain dicts, and
date/datetime/Decimal values are converted to str/int so templates behave
the same on both databases.
"""
import datetime
import decimal
import re
import sqlite3
from collections import namedtuple
from pathlib import Path

import click
from flask import current_app, g

ExecResult = namedtuple("ExecResult", "lastrowid rowcount")


class DatabaseError(RuntimeError):
    """Raised for connection / setup problems with a readable message."""


def _normalize(value):
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _normalize_row(row):
    return {key: _normalize(val) for key, val in dict(row).items()}


class Connection:
    """Thin wrapper around a sqlite3 or mysql.connector connection."""

    def __init__(self, raw, backend):
        self.raw = raw
        self.backend = backend

    def _prepare(self, sql):
        return sql.replace("%s", "?") if self.backend == "sqlite" else sql

    def _cursor(self):
        if self.backend == "mysql":
            return self.raw.cursor(dictionary=True, buffered=True)
        return self.raw.cursor()

    def all(self, sql, params=()):
        cur = self._cursor()
        try:
            cur.execute(self._prepare(sql), tuple(params))
            return [_normalize_row(r) for r in cur.fetchall()]
        finally:
            cur.close()

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql, params=(), default=0):
        row = self.one(sql, params)
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    def execute(self, sql, params=()):
        cur = self._cursor()
        try:
            cur.execute(self._prepare(sql), tuple(params))
            return ExecResult(cur.lastrowid, cur.rowcount)
        finally:
            cur.close()

    def commit(self):
        self.raw.commit()

    def rollback(self):
        try:
            self.raw.rollback()
        except Exception:  # connection may already be gone
            pass

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------

def _connect_sqlite(config):
    path = Path(config["SQLITE_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path), timeout=15)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA journal_mode = WAL")
    return Connection(raw, "sqlite")


def _connect_mysql(config, with_database=True):
    try:
        import mysql.connector
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise DatabaseError(
            "MySQL is selected but mysql-connector-python is not installed. "
            "Run: pip install -r requirements.txt"
        ) from exc

    params = dict(
        host=config["MYSQL_HOST"],
        port=config["MYSQL_PORT"],
        user=config["MYSQL_USER"],
        password=config["MYSQL_PASSWORD"],
        charset="utf8mb4",
        use_unicode=True,
        connection_timeout=10,
        autocommit=False,
    )
    if with_database:
        params["database"] = config["MYSQL_DATABASE"]
    try:
        return Connection(mysql.connector.connect(**params), "mysql")
    except mysql.connector.Error as exc:
        raise DatabaseError(
            f"Could not connect to MySQL at {config['MYSQL_HOST']}:{config['MYSQL_PORT']} "
            f"as '{config['MYSQL_USER']}' ({exc}). Check MYSQL_* values in your .env file."
        ) from exc


def connect(config):
    backend = config["DB_BACKEND"]
    if backend == "mysql":
        if not config.get("MYSQL_DATABASE"):
            raise DatabaseError("DB_BACKEND=mysql needs MYSQL_DATABASE in your .env file.")
        return _connect_mysql(config)
    if backend == "sqlite":
        return _connect_sqlite(config)
    raise DatabaseError(f"Unknown DB_BACKEND '{backend}'. Use 'mysql' or 'sqlite'.")


# ---------------------------------------------------------------------------
# Per-request connection
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = connect(current_app.config)
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        if exc is not None:
            conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------

def _statements(sql_text):
    """Split a .sql file into single statements (comments removed)."""
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        stmt = stmt.strip()
        if stmt:
            yield stmt


def _move_single_recordings(conn):
    """Older versions kept one recording in field_logs.audio_file. Copy it into
    log_media (once) so existing visits keep their recording."""
    conn.execute(
        """INSERT INTO log_media (log_id, filename, kind)
           SELECT l.id, l.audio_file,
                  CASE WHEN SUBSTR(l.audio_file, 1, 2) = 'v_' THEN 'video' ELSE 'audio' END
           FROM field_logs l
           WHERE l.audio_file IS NOT NULL AND l.audio_file <> ''
             AND NOT EXISTS (SELECT 1 FROM log_media m
                             WHERE m.log_id = l.id AND m.filename = l.audio_file)"""
    )
    conn.execute("UPDATE field_logs SET audio_file = NULL WHERE audio_file IS NOT NULL")


def init_schema(config):
    """Create the database (MySQL only) and all tables. Safe to run repeatedly."""
    backend = config["DB_BACKEND"]
    schema_file = Path(config["SCHEMA_DIR"]) / f"schema_{backend}.sql"
    if not schema_file.exists():
        raise DatabaseError(f"Schema file not found: {schema_file}")
    sql_text = schema_file.read_text(encoding="utf-8")

    if backend == "mysql":
        name = config.get("MYSQL_DATABASE")
        if not name:
            raise DatabaseError("DB_BACKEND=mysql needs MYSQL_DATABASE in your .env file.")
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise DatabaseError("MYSQL_DATABASE may only contain letters, numbers and underscores.")
        admin = _connect_mysql(config, with_database=False)
        try:
            admin.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            admin.commit()
        finally:
            admin.close()

    conn = connect(config)
    try:
        for stmt in _statements(sql_text):
            head = stmt.upper()
            if head.startswith("CREATE DATABASE") or head.startswith("USE "):
                continue  # database creation is handled above
            conn.execute(stmt)
        _move_single_recordings(conn)
        conn.commit()
    finally:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database and tables (safe to run again)."""
        init_schema(app.config)
        click.echo(f"Database ready ({app.config['DB_BACKEND']}).")
