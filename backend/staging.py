"""Read-only staging database per project.

Safety, in layers:
  1. The connection string is encrypted at rest (Fernet key in data/secret.key, mode 600) and never sent to the UI.
  2. Every statement is parsed with sqlglot: exactly one SELECT (or WITH ... SELECT), no writes or DDL anywhere in the tree.
  3. The session itself is read-only where the engine allows it, with a statement timeout, and results are capped.
     Every engine (Postgres, MySQL, SQLite, SQL Server, Snowflake, BigQuery, MongoDB, DynamoDB) goes through adapters.py.
Checks are generated from the schema the context pipeline parsed from code, matched against the live tables.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import sqlglot
from cryptography.fernet import Fernet

from . import db, graph
from .paths import DATA

KEYFILE = DATA / "secret.key"
MAX_ROWS = 200
TIMEOUT_MS = 5000
_engines: dict[str, object] = {}
_schema: dict[str, dict] = {}

db.x("CREATE TABLE IF NOT EXISTS secrets(project TEXT, kind TEXT, value BLOB, PRIMARY KEY(project, kind))")


def _fernet() -> Fernet:
    if not KEYFILE.exists():
        KEYFILE.write_bytes(Fernet.generate_key())
        os.chmod(KEYFILE, 0o600)
    return Fernet(KEYFILE.read_bytes())


# kind: "staging_db" (data checks, repro, test accounts) or "monitor_db" (performance views on production)
def save_url(project: str, url: str, kind: str = "staging_db"):
    db.x("INSERT OR REPLACE INTO secrets VALUES(?,?,?)", (project, kind, _fernet().encrypt(url.encode())))
    _engines.pop(f"{project}:{kind}", None)
    _schema.pop(project, None)


def verify_and_save(project: str, url: str, kind: str = "staging_db", check=None):
    """Test a URL before it's stored, so a failing one is never briefly 'connected'. Keeps the tested adapter.
    `check(adapter)` does the connection-specific test; its result is returned."""
    from .adapters import for_url
    a = for_url(url)
    result = check(a) if check else a.introspect()
    save_url(project, url, kind)
    _engines[f"{project}:{kind}"] = a
    if kind == "staging_db" and not check:
        _schema[project] = result
    return result


def remove(project: str, kind: str = "staging_db"):
    db.x("DELETE FROM secrets WHERE project=? AND kind=?", (project, kind))
    _engines.pop(f"{project}:{kind}", None)
    _schema.pop(project, None)


def _url(project: str, kind: str = "staging_db") -> str | None:
    row = db.one("SELECT value FROM secrets WHERE project=? AND kind=?", (project, kind))
    return _fernet().decrypt(row["value"]).decode() if row else None


def connected(project: str, kind: str = "staging_db") -> bool:
    return _url(project, kind) is not None


def masked(project: str, kind: str = "staging_db") -> str | None:
    url = _url(project, kind)
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:****@", url) if url else None


def adapter(project: str, kind: str = "staging_db"):
    """The database behind a connection, whatever engine it is. See adapters.py for how each stays read-only."""
    from .adapters import for_url
    key = f"{project}:{kind}"
    if key in _engines:
        return _engines[key]
    url = _url(project, kind)
    if not url:
        raise LookupError("No database connected for this project")
    _engines[key] = for_url(url)
    return _engines[key]


def engine(project: str, kind: str = "staging_db"):
    """SQLAlchemy engine, SQL databases only."""
    a = adapter(project, kind)
    if a.family != "sql":
        raise TypeError(f"{a.kind} has no SQL engine")
    return a.eng


def dialect(project: str, kind: str = "staging_db") -> str:
    return adapter(project, kind).kind


def validate(sql: str, dia: str) -> str:
    """Kept for callers that validate without a connection (SQL only)."""
    from .adapters import SqlAdapter
    a = SqlAdapter.__new__(SqlAdapter)
    a.kind = dia
    return a.validate(sql)


def run(project: str, query: str, kind: str = "staging_db", trusted: bool = False) -> dict:
    return adapter(project, kind).run(query, trusted=trusted)


def introspect(project: str, refresh: bool = False) -> dict:
    if project in _schema and not refresh:
        return _schema[project]
    tables = adapter(project).introspect()
    _schema[project] = tables
    return tables


def foreign_keys(project: str) -> list[dict]:
    return adapter(project).foreign_keys()


def _match_table(project: str, name: str) -> str | None:
    live = introspect(project)
    low = {t.lower(): t for t in live}
    for cand in (name, name.lower(), name.rstrip("s"), name.lower() + "s", re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower(),
                 re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() + "s"):
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def coverage(project: str, refresh: bool = True) -> dict:
    """Which tables the code knows about exist in staging."""
    live = introspect(project, refresh=refresh)
    code_tables = [r["label"] for r in db.q("SELECT label FROM nodes WHERE project=? AND type='table'", (project,))]
    matched = {t: _match_table(project, t) for t in code_tables}
    return {"live_tables": len(live), "code_tables": len(code_tables), "matched": sum(1 for v in matched.values() if v),
            "unmatched": [t for t, v in matched.items() if not v][:10]}


# ---------------- checks generated from the schema ----------------

def field_checks(project: str, field: str) -> list[dict]:
    """field = table.column from the code's schema. Returns SQL checks that should return 0."""
    if not connected(project):
        return []
    table, _, col = field.partition(".")
    live_t = _match_table(project, table)
    if not live_t:
        return []
    live = introspect(project)[live_t]
    live_c = next((c for c in live["columns"] if c.lower() == col.lower()), None)
    if not live_c:
        return []
    node = graph.node(project, f"field:{field}") or {"props": {}}
    required_in_code = node["props"].get("nullable") is False
    a = adapter(project)
    ctype = live["columns"][live_c]["type"].upper()
    checks = []
    if required_in_code or live["columns"][live_c]["nullable"]:
        sql, where = a.null_check(live_t, live_c)
        checks.append({"title": f"{live_t}.{live_c} has " + ("missing values" if a.family == "document" else "NULLs") + (" but the code requires it" if required_in_code else ""),
                       "sql": sql, "where": where, "severe": required_in_code})
    if any(k in ctype for k in ("CHAR", "TEXT", "STRING", "STR")):
        sql, where = a.empty_check(live_t, live_c)
        checks.append({"title": f"{live_t}.{live_c} has empty strings", "sql": sql, "where": where, "severe": required_in_code})
    for c in checks:
        c["table"], c["column"] = live_t, live_c
    return checks


def sample_query(project: str, check: dict, n: int = 5, with_column: bool = True) -> str:
    """A query listing the records a check found, in the connected database's own language."""
    live = introspect(project)[check["table"]]
    pk = live["pk"][0] if live["pk"] else check["column"]
    cols = [pk] + ([check["column"]] if with_column and check["column"] != pk else [])
    return adapter(project).sample(check["table"], cols, check["where"], n)


def sample_accounts(project: str, check: dict, n: int = 3) -> list:
    try:
        return [r[0] for r in run(project, sample_query(project, check, n, with_column=False))["rows"]]
    except Exception:
        return []
