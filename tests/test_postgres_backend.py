"""Tests for ``PostgresBackend`` that don't require a live database.

The DB-touching paths are exercised end-to-end in Phase 4 against the
real playground pod. Here we cover:

- Protocol conformance (no driver / no env needed).
- SQL-safety: ``execute_readonly_sql`` must reject anything that isn't
  a plain SELECT *before* opening a connection.
- Driver / env error messages are clear.
"""
from __future__ import annotations

import sys
import types

import pytest


def test_protocol_conformance():
    """The class satisfies the ScansBackend Protocol structurally."""
    from beamtimehero_cli.spec_data.backend import ScansBackend
    from beamtimehero_cli.spec_data.postgres_backend import PostgresBackend
    assert isinstance(PostgresBackend(), ScansBackend)


# ---------------------------------------------------------------------------
# execute_readonly_sql safety
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "SELECT 1",
    "select * from t",
    "  SELECT a FROM b WHERE c = 1  ",
    "SELECT a FROM b WHERE name = 'bob'",
])
def test_execute_readonly_sql_accepts_select(monkeypatch, query):
    """Bare SELECTs make it past the guard. With no DB env set, the guard
    should pass and the connection attempt then fails with a clear
    error — proving the guard wasn't the rejecter."""
    from beamtimehero_cli.spec_data.postgres_backend import PostgresBackend
    monkeypatch.delenv("DB_HOST", raising=False)
    backend = PostgresBackend()
    result = backend.execute_readonly_sql(query)
    # Without DB env we expect the connect step to fail, not the guard.
    assert result.get("ok") is False
    err = result.get("error", "")
    assert "Postgres env vars" in err or "psycopg2" in err


@pytest.mark.parametrize("query", [
    "DROP TABLE x",
    "drop table x",
    "DELETE FROM x",
    "UPDATE x SET y=1",
    "INSERT INTO x VALUES (1)",
    "TRUNCATE x",
    "ALTER TABLE x ADD COLUMN y INT",
    "CREATE TABLE z (id int)",
    "GRANT ALL ON x TO y",
    "REVOKE ALL ON x FROM y",
    "SELECT 1; DROP TABLE x",
])
def test_execute_readonly_sql_rejects_writes(query):
    from beamtimehero_cli.spec_data.postgres_backend import PostgresBackend
    backend = PostgresBackend()
    result = backend.execute_readonly_sql(query)
    assert result.get("ok") is False
    assert "Forbidden" in result.get("error", "") or "SELECT" in result.get("error", "")


def test_execute_readonly_sql_rejects_non_select():
    from beamtimehero_cli.spec_data.postgres_backend import PostgresBackend
    backend = PostgresBackend()
    result = backend.execute_readonly_sql("SHOW TABLES")
    assert result.get("ok") is False
    assert "SELECT" in result.get("error", "")


# ---------------------------------------------------------------------------
# Missing-config error messages
# ---------------------------------------------------------------------------

def test_missing_driver_raises_value_error(monkeypatch):
    """A missing optional driver names the extra that installs it."""
    from beamtimehero_cli.spec_data import postgres_backend as pb
    # None in sys.modules makes `import psycopg2` raise ImportError, so this
    # branch is reachable even where `[postgres]` is installed.
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    with pytest.raises(ValueError, match="psycopg2 not installed"):
        pb._connect()


def test_missing_env_vars_raises_value_error(monkeypatch):
    """`_connect` should report missing env vars by name, not crash."""
    from beamtimehero_cli.spec_data import postgres_backend as pb
    # psycopg2 lives in the `[postgres]` extra, which CI does not install.
    # Stub it so the env-var check is what this test actually reaches,
    # rather than the driver guard ahead of it.
    monkeypatch.setitem(sys.modules, "psycopg2", types.ModuleType("psycopg2"))
    for k in ("DB_HOST", "DB_NAME", "DB_USER"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="env vars not set"):
        pb._connect()
