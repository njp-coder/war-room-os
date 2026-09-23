"""Relational state: tenancy, releases, bugs, tests, graph. SQLite for the hackathon."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .paths import DATA

PATH = DATA / "warroom.db"
_lock = threading.RLock()
con = sqlite3.connect(PATH, check_same_thread=False)
con.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs(id TEXT PRIMARY KEY, name TEXT, kind TEXT);             -- kind: company | agency
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, org TEXT, name TEXT, email TEXT, title TEXT, org_role TEXT,
  shift TEXT DEFAULT 'on', hours_today INT DEFAULT 0, client_facing INT DEFAULT 0, github TEXT);
CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY, org TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, org TEXT, client TEXT, name TEXT, description TEXT,
  created REAL, demo INT DEFAULT 0, staging_url TEXT, settings TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS members(project TEXT, user TEXT, role TEXT, PRIMARY KEY(project, user));
                                     -- role: owner | lead | engineer | tester | support | client_guest | viewer
CREATE TABLE IF NOT EXISTS repos(project TEXT, full_name TEXT, default_branch TEXT, status TEXT, progress TEXT,
  synced REAL, PRIMARY KEY(project, full_name));
CREATE TABLE IF NOT EXISTS blobs(project TEXT, repo TEXT, path TEXT, sha TEXT, PRIMARY KEY(project, repo, path));
CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY, project TEXT, name TEXT, stage TEXT, window_start TEXT,
  window_end TEXT, scope_type TEXT, scope_value TEXT, migration INT DEFAULT 0, rollback_plan TEXT DEFAULT '',
  created REAL, closed REAL, postmortem TEXT);
CREATE TABLE IF NOT EXISTS bugs(id TEXT PRIMARY KEY, project TEXT, release TEXT, env TEXT, title TEXT, body TEXT,
  severity TEXT, priority TEXT, suggested_priority TEXT, blocker INT DEFAULT 0, status TEXT, reporter TEXT,
  reporter_kind TEXT, assignee TEXT, created REAL, verified_by TEXT, links TEXT DEFAULT '[]', evidence TEXT DEFAULT '',
  stage TEXT DEFAULT 'reported', proposed TEXT, proposed_why TEXT, eta_agent REAL, eta_agent_why TEXT, eta_owner REAL,
  repro TEXT DEFAULT '', relay TEXT DEFAULT '[]', accepted_at REAL, fixed_at REAL);
CREATE TABLE IF NOT EXISTS risks(id TEXT PRIMARY KEY, project TEXT, release TEXT, target TEXT, kind TEXT, level TEXT,
  note TEXT, status TEXT DEFAULT 'untested', tested_by TEXT, tested_at REAL);
CREATE TABLE IF NOT EXISTS tests(id TEXT PRIMARY KEY, project TEXT, release TEXT, risk TEXT, charter TEXT, kind TEXT,
  target TEXT, status TEXT, by TEXT, result TEXT, run_at REAL,
  owner_kind TEXT DEFAULT 'agent', claimed_by TEXT, how TEXT DEFAULT '[]', source TEXT DEFAULT '', help TEXT DEFAULT '',
  bug TEXT, created REAL);
CREATE TABLE IF NOT EXISTS recipes(id TEXT PRIMARY KEY, project TEXT, target TEXT, title TEXT, steps TEXT, origin TEXT,
  author TEXT, created REAL);
CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY, project TEXT, owner_type TEXT, owner_id TEXT, kind TEXT, name TEXT,
  path TEXT, text TEXT, by TEXT, by_kind TEXT, at REAL);
CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, project TEXT, owner_type TEXT, owner_id TEXT, author TEXT, author_kind TEXT,
  text TEXT, ask TEXT, at REAL);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, project TEXT, release TEXT, text TEXT, by TEXT, created REAL,
  superseded_by TEXT);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, release TEXT, ts REAL,
  actor TEXT, kind TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS nodes(project TEXT, id TEXT, type TEXT, label TEXT, props TEXT, PRIMARY KEY(project, id));
CREATE TABLE IF NOT EXISTS edges(project TEXT, src TEXT, dst TEXT, type TEXT, valid_from REAL, valid_to REAL,
  source TEXT, PRIMARY KEY(project, src, dst, type));
CREATE TABLE IF NOT EXISTS pr_files(project TEXT, pr TEXT, path TEXT, hunks TEXT, PRIMARY KEY(project, pr, path));
CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, project TEXT, title TEXT, severity TEXT, status TEXT, trigger TEXT,
  rule TEXT, fingerprint TEXT, opened_by TEXT, opened_at REAL, resolved_at REAL, summary TEXT, evidence TEXT DEFAULT '{}',
  relay TEXT DEFAULT '[]', owner TEXT, proposal TEXT);
CREATE TABLE IF NOT EXISTS signals(project TEXT, at REAL, rule TEXT, fingerprint TEXT, value REAL, label TEXT);
CREATE INDEX IF NOT EXISTS signals_fp ON signals(project, fingerprint, at);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project TEXT, agent TEXT, owner_type TEXT, owner_id TEXT, task TEXT,
  policy TEXT, status TEXT, steps TEXT DEFAULT '[]', started REAL, ended REAL, tokens INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS reviews(project TEXT, pr TEXT, who TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS cards(project TEXT, id TEXT, idx TEXT, text TEXT, meta TEXT, PRIMARY KEY(project, id));
CREATE INDEX IF NOT EXISTS edges_src ON edges(project, src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(project, dst);
"""

with _lock:
    con.executescript(SCHEMA)
    con.commit()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args: tuple = ()):
    with _lock:
        con.execute(sql, args)
        con.commit()


def xmany(sql: str, rows: list[tuple]):
    with _lock:
        con.executemany(sql, rows)
        con.commit()


def event(project: str, release: str | None, actor: str, kind: str, data: dict | None = None):
    x("INSERT INTO events(project, release, ts, actor, kind, data) VALUES(?,?,?,?,?,?)",
      (project, release, time.time(), actor, kind, json.dumps(data or {})))


def role_of(user: str, project: str) -> str | None:
    u = one("SELECT org_role FROM users WHERE id=?", (user,))
    if u and u["org_role"] == "admin":
        p = one("SELECT org FROM projects WHERE id=?", (project,))
        me = one("SELECT org FROM users WHERE id=?", (user,))
        if p and me and p["org"] == me["org"]:
            return "owner"
    m = one("SELECT role FROM members WHERE project=? AND user=?", (project, user))
    return m["role"] if m else None
