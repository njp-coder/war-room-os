"""Database adapters. One interface for every database agents read, so checks, repro and monitoring work the same everywhere.

Each adapter answers:
  run(query)            one read-only query in its own language, capped rows, timed out
  introspect()          {table: {"columns": {name: {"type", "nullable"}}, "pk": [...]}}
  foreign_keys()        [{"from": "t.c", "to": "t.c"}]
  null_check / empty_check(table, col)  -> a query that returns one row with a count, plus an opaque `where`
  sample(table, cols, where, n)          -> a query listing affected records
  perf()                cumulative per-statement stats for the monitoring agent (or None if the engine has none)

Read-only is enforced in layers that fit each engine:
  SQL (Postgres, MySQL, SQLite, SQL Server, Snowflake, BigQuery): sqlglot allows exactly one SELECT; the session is read-only
    where the engine supports it; statement timeouts; BigQuery also caps bytes billed.
  MongoDB: JSON query with find / count / aggregate only; write and JavaScript stages rejected; maxTimeMS; secondary reads.
  DynamoDB: PartiQL SELECT only through ExecuteStatement; item and page caps so a scan can't run away.
Use a read-only user regardless. These layers are a backstop, not a permission model.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import sqlglot
from sqlalchemy import create_engine, event, inspect, text

from . import netguard

MAX_ROWS = 200
TIMEOUT_MS = 5000
SCAN_ITEMS = 5000          # DynamoDB: most items a count may read
BQ_MAX_BYTES = 1_000_000_000  # BigQuery: 1 GB billed per query at most

WRITES = (sqlglot.exp.Insert, sqlglot.exp.Update, sqlglot.exp.Delete, sqlglot.exp.Create, sqlglot.exp.Drop,
          sqlglot.exp.Alter, sqlglot.exp.Merge, sqlglot.exp.Command, sqlglot.exp.TruncateTable)

KINDS = {
    "postgres": {"label": "PostgreSQL", "example": "postgresql://readonly:...@host:5432/db", "monitor": True},
    "mysql": {"label": "MySQL", "example": "mysql://readonly:...@host:3306/db", "monitor": True},
    "sqlite": {"label": "SQLite", "example": "sqlite:////path/to/file.db", "monitor": False},
    "tsql": {"label": "SQL Server", "example": "mssql://readonly:...@host:1433/db", "monitor": True},
    "snowflake": {"label": "Snowflake", "example": "snowflake://user:...@account/db/schema?warehouse=WH&role=READONLY", "monitor": True},
    "bigquery": {"label": "BigQuery", "example": "bigquery://project/dataset?credentials_path=/path/key.json&location=US", "monitor": True},
    "mongodb": {"label": "MongoDB", "example": "mongodb://readonly:...@host:27017/db", "monitor": True},
    "dynamodb": {"label": "DynamoDB", "example": "dynamodb://ACCESS_KEY:SECRET@us-east-1  (or dynamodb://us-east-1 to use the AWS profile)", "monitor": False},
}


def for_url(url: str):
    netguard.safe_db_url(url)  # never the server's own disk or private network: see netguard.py
    scheme = url.split("://", 1)[0].lower()
    if scheme.startswith("mongodb"):
        return MongoAdapter(url)
    if scheme == "dynamodb":
        return DynamoAdapter(url)
    return SqlAdapter(url)


# ============================================================ SQL

class SqlAdapter:
    family = "sql"

    def __init__(self, url: str):
        self.url = url
        self.anchor = time.time()  # engines without cumulative counters report "since we connected"
        self.eng, self.kind = self._engine(url)

    def _engine(self, url: str):
        s = url.split("://", 1)[0].lower()
        rest = url.split("://", 1)[1] if "://" in url else ""
        if s in ("postgres", "postgresql", "postgresql+psycopg"):
            # No startup options: poolers (Neon, Supabase, PgBouncer) reject them. Read-only and the timeout are set per
            # transaction instead, which also can't leak to another client sharing the pooled server connection.
            # prepare_threshold=None: transaction poolers can't be relied on to keep prepared statements.
            eng = create_engine("postgresql+psycopg://" + rest, connect_args={"prepare_threshold": None},
                                pool_pre_ping=True, pool_reset_on_return="commit")  # COMMIT so our reads never count as failed transactions

            @event.listens_for(eng, "begin")
            def _ro_tx(conn):
                # One round trip: this runs before every transaction, and hosted databases can be far away.
                conn.exec_driver_sql(f"SET TRANSACTION READ ONLY; SET LOCAL statement_timeout = {int(TIMEOUT_MS)}")
            return eng, "postgres"
        if s in ("mysql", "mysql+pymysql"):
            eng = create_engine("mysql+pymysql://" + rest, pool_pre_ping=True, pool_reset_on_return="commit")

            @event.listens_for(eng, "connect")
            def _ro(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute(f"SET SESSION max_execution_time={TIMEOUT_MS}")
                cur.close()
            return eng, "mysql"
        if s == "sqlite":
            path = url.split("///", 1)[1]
            return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true"), "sqlite"
        if s in ("mssql", "sqlserver", "mssql+pymssql"):
            # SQL Server has no read-only session switch: the parser and a read-only login do the work.
            eng = create_engine("mssql+pymssql://" + rest, connect_args={"timeout": TIMEOUT_MS // 1000, "login_timeout": 10},
                                pool_pre_ping=True, pool_reset_on_return="commit")

            @event.listens_for(eng, "connect")
            def _ms(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute(f"SET LOCK_TIMEOUT {TIMEOUT_MS}")
                cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")  # never take locks that block production writers
                cur.close()
            return eng, "tsql"
        if s == "snowflake":
            eng = create_engine(url, pool_pre_ping=True)

            @event.listens_for(eng, "connect")
            def _sf(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {max(TIMEOUT_MS // 1000, 1)}")
                cur.close()
            return eng, "snowflake"
        if s == "bigquery":
            q = parse_qs(urlparse(url).query)
            sep = "&" if "?" in url else "?"
            full = url if "maximum_bytes_billed" in q else f"{url}{sep}maximum_bytes_billed={BQ_MAX_BYTES}"
            return create_engine(full), "bigquery"
        raise ValueError("Unsupported database. Use postgres://, mysql://, sqlite:///, mssql://, snowflake://, bigquery://, mongodb:// or dynamodb://")

    # ---- queries ----
    def validate(self, sql: str) -> str:
        """Exactly one read-only SELECT. Adds a row limit if there isn't one."""
        stmts = [s for s in sqlglot.parse(sql, read=self.kind) if s is not None]
        if len(stmts) != 1:
            raise ValueError("Exactly one statement is allowed")
        st = stmts[0]
        if not isinstance(st, (sqlglot.exp.Select, sqlglot.exp.Union, sqlglot.exp.With)):
            raise ValueError("Only SELECT queries are allowed")
        if any(isinstance(n, WRITES) for n in st.walk()):
            raise ValueError("Writes and DDL are not allowed")
        if isinstance(st, sqlglot.exp.Select) and not st.args.get("limit") and not st.args.get("top"):
            st = st.limit(MAX_ROWS)
        return st.sql(dialect=self.kind)

    def run(self, sql: str, trusted: bool = False) -> dict:
        """`trusted` skips the parser for the adapter's own fixed monitoring queries. The session stays read-only."""
        safe = sql if trusted else self.validate(sql)
        t0 = time.perf_counter()
        with self.eng.connect() as c:
            if self.kind == "sqlite":
                c.exec_driver_sql("PRAGMA query_only = ON")
            res = c.execute(text(safe))
            cols = list(res.keys())
            rows = [list(r) for r in res.fetchmany(MAX_ROWS)]
            c.commit()
        return {"sql": safe, "columns": cols, "rows": rows, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def _t(self, sql: str) -> str:
        if self.kind == "snowflake":
            # SQLAlchemy reports Snowflake's uppercase names in lowercase; quoting them would make them case-sensitive and wrong.
            sql = re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", sql)
        return sqlglot.transpile(sql, read="postgres", write=self.kind)[0]

    # ---- schema ----
    # Reflection runs on one connection (one transaction), and reads every table in one query where the dialect can:
    # table by table is a round trip each, which is most of a minute against a database on another continent.
    def introspect(self) -> dict:
        with self.eng.connect() as c:
            insp = inspect(c)
            names = insp.get_table_names()
            try:
                cols = {k[1]: v for k, v in insp.get_multi_columns().items()}
                pks = {k[1]: v for k, v in insp.get_multi_pk_constraint().items()}
            except NotImplementedError:
                cols = {t: insp.get_columns(t) for t in names}
                pks = {t: insp.get_pk_constraint(t) for t in names}
        return {t: {"columns": {c["name"]: {"type": str(c["type"]), "nullable": c.get("nullable", True)} for c in cols.get(t, [])},
                    "pk": (pks.get(t) or {}).get("constrained_columns") or []} for t in names}

    def foreign_keys(self) -> list[dict]:
        out = []
        with self.eng.connect() as c:
            insp = inspect(c)
            try:
                fks = {k[1]: v for k, v in insp.get_multi_foreign_keys().items()}
            except NotImplementedError:
                fks = {}
                for t in insp.get_table_names():
                    try:
                        fks[t] = insp.get_foreign_keys(t)
                    except Exception:
                        pass  # BigQuery and Snowflake often don't expose or enforce them
            except Exception:
                return out
        for t, lst in fks.items():
            for fk in lst:
                out += [{"from": f"{t}.{a}", "to": f"{fk['referred_table']}.{b}"} for a, b in zip(fk["constrained_columns"], fk["referred_columns"])]
        return out

    def indexes(self, table: str) -> list[list[str]]:
        with self.eng.connect() as c:
            insp = inspect(c)
            out = [i["column_names"] for i in insp.get_indexes(table)]
            pk = (insp.get_pk_constraint(table) or {}).get("constrained_columns")
        return out + ([pk] if pk else [])

    # ---- checks ----
    def null_check(self, table: str, col: str) -> tuple[str, str]:
        return self._t(f'SELECT count(*) FROM "{table}" WHERE "{col}" IS NULL'), f'"{col}" IS NULL'

    def empty_check(self, table: str, col: str) -> tuple[str, str]:
        return self._t(f"SELECT count(*) FROM \"{table}\" WHERE \"{col}\" = ''"), f"\"{col}\" = ''"

    def sample(self, table: str, cols: list[str], where: str, n: int = 5) -> str:
        return self._t(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}" WHERE {where} LIMIT {n}')

    # ---- performance ----
    def perf(self) -> dict | None:
        fn = {"postgres": self._perf_pg, "mysql": self._perf_mysql, "tsql": self._perf_mssql,
              "snowflake": self._perf_snowflake, "bigquery": self._perf_bigquery}.get(self.kind)
        return fn() if fn else None

    def _rows(self, sql: str) -> list[dict]:
        r = self.run(sql, trusted=True)
        return [dict(zip([c.lower() for c in r["columns"]], row)) for row in r["rows"]]

    def _perf_pg(self) -> dict:
        snap = {"statements": {}, "active": [], "blocked": 0, "conn": 0, "max_conn": 0, "rollbacks": 0}
        try:
            for r in self._rows("""SELECT queryid::text AS id, query, calls, total_exec_time AS total_ms FROM pg_stat_statements
                                   WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                                   ORDER BY total_exec_time DESC LIMIT 200"""):
                snap["statements"][r["id"]] = {"query": r["query"], "calls": int(r["calls"]), "total_ms": float(r["total_ms"])}
        except Exception as e:
            snap["statements_error"] = f"pg_stat_statements unavailable: {str(e)[:120]}"
        snap["active"] = [{"pid": r["pid"], "seconds": float(r["seconds"] or 0), "query": r["query"], "wait": r["wait_event_type"]}
                          for r in self._rows("""SELECT pid, EXTRACT(EPOCH FROM now() - query_start) AS seconds, query, wait_event_type
                                                 FROM pg_stat_activity WHERE state = 'active' AND pid <> pg_backend_pid()
                                                 AND datname = current_database() ORDER BY query_start LIMIT 50""")]
        snap["blocked"] = sum(1 for a in snap["active"] if a["wait"] == "Lock")
        snap["conn"] = int(self._rows("SELECT count(*) AS n FROM pg_stat_activity")[0]["n"])
        snap["max_conn"] = int(self._rows("SELECT setting AS n FROM pg_settings WHERE name = 'max_connections'")[0]["n"])
        snap["rollbacks"] = int(self._rows("SELECT xact_rollback AS n FROM pg_stat_database WHERE datname = current_database()")[0]["n"])
        return snap

    def _perf_mysql(self) -> dict:
        snap = {"statements": {}, "active": [], "blocked": 0, "conn": 0, "max_conn": 0, "rollbacks": 0}
        for r in self._rows("""SELECT DIGEST AS id, DIGEST_TEXT AS query, COUNT_STAR AS calls, SUM_TIMER_WAIT/1000000000 AS total_ms,
                               SUM_ERRORS AS errors FROM performance_schema.events_statements_summary_by_digest
                               WHERE SCHEMA_NAME = DATABASE() ORDER BY SUM_TIMER_WAIT DESC LIMIT 200"""):
            snap["statements"][r["id"]] = {"query": r["query"], "calls": int(r["calls"]), "total_ms": float(r["total_ms"]), "errors": int(r["errors"] or 0)}
        snap["active"] = [{"pid": r["id"], "seconds": float(r["time"]), "query": r["info"], "wait": r["state"]}
                          for r in self._rows("SELECT ID, TIME, INFO, STATE FROM information_schema.processlist WHERE COMMAND <> 'Sleep' AND INFO IS NOT NULL")]
        snap["blocked"] = sum(1 for a in snap["active"] if a["wait"] and "lock" in str(a["wait"]).lower())
        return snap

    def _perf_mssql(self) -> dict:
        """Needs VIEW SERVER STATE (or VIEW DATABASE PERFORMANCE STATE on Azure SQL)."""
        snap = {"statements": {}, "active": [], "blocked": 0, "conn": 0, "max_conn": 0, "rollbacks": 0}
        for r in self._rows("""SELECT TOP 200 CONVERT(varchar(32), qs.query_hash, 2) AS id, MAX(SUBSTRING(st.text, 1, 2000)) AS query,
                                      SUM(qs.execution_count) AS calls, SUM(qs.total_elapsed_time) / 1000.0 AS total_ms
                               FROM sys.dm_exec_query_stats qs CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
                               WHERE st.dbid = DB_ID() OR st.dbid IS NULL
                               GROUP BY qs.query_hash ORDER BY SUM(qs.total_elapsed_time) DESC"""):
            snap["statements"][r["id"]] = {"query": r["query"], "calls": int(r["calls"]), "total_ms": float(r["total_ms"])}
        snap["active"] = [{"pid": r["session_id"], "seconds": float(r["seconds"] or 0), "query": r["query"], "wait": "Lock" if r["blocking_session_id"] else r["wait_type"]}
                          for r in self._rows("""SELECT r.session_id, r.total_elapsed_time / 1000.0 AS seconds, r.blocking_session_id, r.wait_type,
                                                        SUBSTRING(st.text, 1, 2000) AS query
                                                 FROM sys.dm_exec_requests r CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) st
                                                 WHERE r.session_id <> @@SPID AND r.database_id = DB_ID()""")]
        snap["blocked"] = sum(1 for a in snap["active"] if a["wait"] == "Lock")
        snap["conn"] = int(self._rows("SELECT COUNT(*) AS n FROM sys.dm_exec_sessions WHERE is_user_process = 1")[0]["n"])
        snap["max_conn"] = int(self._rows("SELECT @@MAX_CONNECTIONS AS n")[0]["n"])
        return snap

    def _perf_snowflake(self) -> dict:
        """Snowflake keeps history, not counters: cumulative since we connected, grouped by parameterized query."""
        start = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.anchor))
        snap = {"statements": {}, "active": [], "blocked": 0, "conn": 0, "max_conn": 0, "rollbacks": 0}
        for r in self._rows(f"""SELECT query_parameterized_hash AS id, ANY_VALUE(query_text) AS query, COUNT(*) AS calls,
                                      SUM(total_elapsed_time) AS total_ms, SUM(IFF(execution_status = 'FAIL', 1, 0)) AS errors
                               FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(END_TIME_RANGE_START => '{start}'::timestamp_ltz, RESULT_LIMIT => 10000))
                               WHERE query_type = 'SELECT' OR query_type LIKE 'INSERT%' OR query_type LIKE 'UPDATE%'
                               GROUP BY 1 ORDER BY total_ms DESC LIMIT 200"""):
            snap["statements"][str(r["id"])] = {"query": r["query"], "calls": int(r["calls"]), "total_ms": float(r["total_ms"] or 0), "errors": int(r["errors"] or 0)}
        snap["active"] = [{"pid": r["query_id"], "seconds": float(r["seconds"] or 0), "query": r["query_text"], "wait": r["execution_status"]}
                          for r in self._rows("""SELECT query_id, query_text, execution_status, DATEDIFF('second', start_time, CURRENT_TIMESTAMP()) AS seconds
                                                 FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT => 1000))
                                                 WHERE execution_status IN ('RUNNING', 'QUEUED', 'BLOCKED')""")]
        snap["blocked"] = sum(1 for a in snap["active"] if a["wait"] == "BLOCKED")
        return snap

    def _perf_bigquery(self) -> dict:
        q = parse_qs(urlparse(self.url).query)
        region = f"region-{(q.get('location') or ['us'])[0].lower()}"
        start = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.anchor))
        snap = {"statements": {}, "active": [], "blocked": 0, "conn": 0, "max_conn": 0, "rollbacks": 0}
        for r in self._rows(f"""SELECT query_info.query_hashes.normalized_literals AS id, ANY_VALUE(query) AS query, COUNT(*) AS calls,
                                      SUM(TIMESTAMP_DIFF(end_time, start_time, MILLISECOND)) AS total_ms, COUNTIF(error_result IS NOT NULL) AS errors
                               FROM `{region}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
                               WHERE creation_time >= TIMESTAMP('{start}') AND job_type = 'QUERY' AND state = 'DONE'
                               GROUP BY 1 ORDER BY total_ms DESC LIMIT 200"""):
            if r["id"]:
                snap["statements"][r["id"]] = {"query": r["query"], "calls": int(r["calls"]), "total_ms": float(r["total_ms"] or 0), "errors": int(r["errors"] or 0)}
        snap["active"] = [{"pid": r["job_id"], "seconds": float(r["seconds"] or 0), "query": r["query"], "wait": r["state"]}
                          for r in self._rows(f"""SELECT job_id, query, state, TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), start_time, SECOND) AS seconds
                                                  FROM `{region}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
                                                  WHERE state IN ('RUNNING', 'PENDING') AND creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)""")]
        return snap


# ============================================================ MongoDB

BANNED_STAGES = {"$out", "$merge", "$function", "$accumulator", "$where", "$currentOp", "$listSessions", "$planCacheClear"}


class MongoAdapter:
    """Queries are JSON: {"collection": c, "filter": {...}, "projection": {...}, "sort": {...}, "limit": n}
    or {"collection": c, "count": {...filter}} or {"collection": c, "pipeline": [...]}."""
    family = "document"
    kind = "mongodb"

    def __init__(self, url: str):
        from pymongo import MongoClient
        self.url = url
        self.anchor = time.time()
        self.client = MongoClient(url, readPreference="secondaryPreferred", serverSelectionTimeoutMS=5000,
                                  socketTimeoutMS=TIMEOUT_MS + 2000, appname="war-room-os")
        name = urlparse(url).path.lstrip("/") or None
        self.db = self.client.get_database(name) if name else self.client.get_default_database()

    def validate(self, query: str | dict) -> dict:
        try:
            q = json.loads(query) if isinstance(query, str) else query
        except json.JSONDecodeError:
            raise ValueError('MongoDB queries are JSON, e.g. {"collection": "orders", "filter": {"status": "open"}}') from None
        if not isinstance(q, dict) or not isinstance(q.get("collection"), str):
            raise ValueError('A MongoDB query is JSON with a "collection" and one of "filter", "count" or "pipeline"')
        ops = [k for k in ("filter", "count", "pipeline") if k in q]
        if len(ops) > 1:
            raise ValueError("Use one of filter, count or pipeline")

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in BANNED_STAGES:
                        raise ValueError(f"{k} is not allowed: agents only read")
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(q)
        return q

    def run(self, query: str | dict, trusted: bool = False) -> dict:
        q = self.validate(query)
        coll = self.db[q["collection"]]
        t0 = time.perf_counter()
        if "count" in q:
            n = coll.count_documents(q["count"] or {}, maxTimeMS=TIMEOUT_MS)
            cols, rows = ["count"], [[n]]
        elif "pipeline" in q:
            pipe = list(q["pipeline"]) + [{"$limit": MAX_ROWS}]
            docs = list(coll.aggregate(pipe, maxTimeMS=TIMEOUT_MS))
            cols, rows = _tabulate(docs)
        else:
            cur = coll.find(q.get("filter") or {}, q.get("projection"), max_time_ms=TIMEOUT_MS).limit(min(int(q.get("limit") or MAX_ROWS), MAX_ROWS))
            if q.get("sort"):
                cur = cur.sort(list(q["sort"].items()))
            cols, rows = _tabulate(list(cur))
        return {"sql": json.dumps(q), "columns": cols, "rows": rows, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def introspect(self) -> dict:
        """Collections have no schema: infer fields from a sample. A field missing or null in some documents is nullable."""
        out = {}
        for name in self.db.list_collection_names():
            if name.startswith("system."):
                continue
            docs = list(self.db[name].aggregate([{"$sample": {"size": 200}}], maxTimeMS=TIMEOUT_MS))
            seen: dict[str, dict] = {}
            for d in docs:
                for k, v in _flatten(d).items():
                    s = seen.setdefault(k, {"types": set(), "present": 0, "null": 0})
                    s["present"] += 1
                    s["types"].add(type(v).__name__)
                    if v is None:
                        s["null"] += 1
            out[name] = {"columns": {k: {"type": "/".join(sorted(t for t in s["types"] if t != "NoneType")) or "null",
                                         "nullable": s["null"] > 0 or s["present"] < len(docs)} for k, s in seen.items()},
                         "pk": ["_id"], "sampled": len(docs)}
        return out

    def foreign_keys(self) -> list[dict]:
        """No declared references in MongoDB. Infer `<name>_id` fields that point at a collection called <name>(s)."""
        schema = self.introspect()
        names = {n.lower(): n for n in schema}
        out = []
        for t, s in schema.items():
            for c in s["columns"]:
                m = re.match(r"(.+?)_?[iI]d$", c)
                if m and c != "_id":
                    target = names.get(m.group(1).lower()) or names.get(m.group(1).lower() + "s")
                    if target and target != t:
                        out.append({"from": f"{t}.{c}", "to": f"{target}._id"})
        return out

    def indexes(self, table: str) -> list[list[str]]:
        return [[k for k, _ in spec["key"]] for spec in self.db[table].index_information().values()]

    def null_check(self, table: str, col: str) -> tuple[str, str]:
        where = {col: None}  # matches null and missing
        return json.dumps({"collection": table, "count": where}), json.dumps(where)

    def empty_check(self, table: str, col: str) -> tuple[str, str]:
        where = {col: ""}
        return json.dumps({"collection": table, "count": where}), json.dumps(where)

    def sample(self, table: str, cols: list[str], where: str, n: int = 5) -> str:
        return json.dumps({"collection": table, "filter": json.loads(where), "projection": {c: 1 for c in cols}, "limit": n})

    def perf(self) -> dict | None:
        """Slow operations from the profiler (enable level 1 with slowms) and what's running now."""
        snap = {"statements": {}, "active": [], "blocked": 0, "conn": 0, "max_conn": 0, "rollbacks": 0}
        try:
            since = __import__("datetime").datetime.fromtimestamp(self.anchor, __import__("datetime").timezone.utc)
            for r in self.db["system.profile"].aggregate([
                    {"$match": {"ts": {"$gte": since}, "op": {"$in": ["query", "find", "command", "getmore", "update", "remove"]}}},
                    {"$group": {"_id": {"$ifNull": ["$queryHash", "$ns"]}, "ns": {"$first": "$ns"}, "cmd": {"$first": "$command"},
                                "calls": {"$sum": 1}, "total_ms": {"$sum": "$millis"}}},
                    {"$sort": {"total_ms": -1}}, {"$limit": 200}], maxTimeMS=TIMEOUT_MS):
                snap["statements"][str(r["_id"])] = {"query": f"{r['ns']}: {json.dumps(_jsonable(r.get('cmd') or {}))[:400]}",
                                                     "calls": int(r["calls"]), "total_ms": float(r["total_ms"])}
        except Exception as e:
            snap["statements_error"] = f"profiler unavailable (run db.setProfilingLevel(1, {{slowms: 100}})): {str(e)[:100]}"
        try:
            ops = self.client.admin.command("currentOp", {"active": True})["inprog"]
            snap["active"] = [{"pid": o.get("opid"), "seconds": float(o.get("secs_running") or 0), "query": json.dumps(_jsonable(o.get("command") or {}))[:400],
                               "wait": "Lock" if o.get("waitingForLock") else o.get("op")} for o in ops if o.get("ns", "").startswith(self.db.name + ".")]
            snap["blocked"] = sum(1 for a in snap["active"] if a["wait"] == "Lock")
            st = self.client.admin.command("serverStatus")
            snap["conn"] = int(st["connections"]["current"])
            snap["max_conn"] = int(st["connections"]["current"] + st["connections"]["available"])
        except Exception:
            pass
        return snap


# ============================================================ DynamoDB

class DynamoAdapter:
    """Queries are PartiQL SELECT statements run through ExecuteStatement, e.g.
    SELECT * FROM "orders" WHERE delivery_slot IS MISSING"""
    family = "document"
    kind = "dynamodb"

    def __init__(self, url: str):
        import boto3
        from botocore.config import Config
        u = urlparse(url)
        q = parse_qs(u.query)
        self.url = url
        self.anchor = time.time()
        kw = {"region_name": u.hostname or "us-east-1",
              "config": Config(connect_timeout=5, read_timeout=TIMEOUT_MS / 1000, retries={"max_attempts": 2})}
        if u.username:
            kw["aws_access_key_id"], kw["aws_secret_access_key"] = unquote(u.username), unquote(u.password or "")
        if q.get("endpoint"):
            kw["endpoint_url"] = q["endpoint"][0]  # DynamoDB Local or LocalStack
        self.client = boto3.client("dynamodb", **kw)

    def validate(self, stmt: str) -> str:
        s = stmt.strip().rstrip(";")
        if ";" in s:
            raise ValueError("Exactly one statement is allowed")
        if not re.match(r"(?is)^select\s", s):
            raise ValueError("Only PartiQL SELECT statements are allowed")
        return s

    def _pages(self, stmt: str, max_items: int):
        token, seen = None, 0
        while True:
            kw = {"Statement": stmt, "Limit": min(1000, max_items - seen)}
            if token:
                kw["NextToken"] = token
            r = self.client.execute_statement(**kw)
            items = r.get("Items", [])
            seen += len(items)
            yield items
            token = r.get("NextToken")
            if not token or seen >= max_items:
                return

    def run(self, stmt: str, trusted: bool = False) -> dict:
        s = self.validate(stmt)
        t0 = time.perf_counter()
        counting = bool(re.match(r'(?is)^select\s+count\(\*\)\s+from', s))
        if counting:
            # PartiQL for DynamoDB has no COUNT: page through (capped) and count what matches.
            inner = re.sub(r'(?is)^select\s+count\(\*\)', "SELECT *", s)
            n, capped = 0, False
            pages = self._pages(inner, SCAN_ITEMS)
            for items in pages:
                n += len(items)
            capped = n >= SCAN_ITEMS
            cols, rows = ["count"], [[n]]
            if capped:
                cols.append("note")
                rows[0].append(f"stopped after {SCAN_ITEMS} items")
        else:
            docs = [_from_dynamo(i) for items in self._pages(s, MAX_ROWS) for i in items][:MAX_ROWS]
            cols, rows = _tabulate(docs)
        return {"sql": s, "columns": cols, "rows": rows, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def introspect(self) -> dict:
        out = {}
        names = []
        for page in self.client.get_paginator("list_tables").paginate():
            names += page["TableNames"]
        for t in names:
            d = self.client.describe_table(TableName=t)["Table"]
            keys = [k["AttributeName"] for k in sorted(d["KeySchema"], key=lambda k: k["KeyType"] != "HASH")]
            items = [_from_dynamo(i) for i in self.client.scan(TableName=t, Limit=200).get("Items", [])]
            seen: dict[str, dict] = {}
            for it in items:
                for k, v in it.items():
                    s = seen.setdefault(k, {"types": set(), "present": 0, "null": 0})
                    s["present"] += 1
                    s["types"].add(type(v).__name__)
                    s["null"] += v is None
            cols = {k: {"type": "/".join(sorted(t_ for t_ in s["types"] if t_ != "NoneType")) or "null",
                        "nullable": k not in keys and (s["null"] > 0 or s["present"] < len(items))} for k, s in seen.items()}
            for k in keys:
                cols.setdefault(k, {"type": "key", "nullable": False})
            out[t] = {"columns": cols, "pk": keys, "sampled": len(items)}
        return out

    def foreign_keys(self) -> list[dict]:
        return []

    def indexes(self, table: str) -> list[list[str]]:
        d = self.client.describe_table(TableName=table)["Table"]
        out = [[k["AttributeName"] for k in d["KeySchema"]]]
        for g in d.get("GlobalSecondaryIndexes", []) + d.get("LocalSecondaryIndexes", []):
            out.append([k["AttributeName"] for k in g["KeySchema"]])
        return out

    def null_check(self, table: str, col: str) -> tuple[str, str]:
        where = f'"{col}" IS MISSING OR "{col}" IS NULL'
        return f'SELECT COUNT(*) FROM "{table}" WHERE {where}', where

    def empty_check(self, table: str, col: str) -> tuple[str, str]:
        where = f"\"{col}\" = ''"
        return f'SELECT COUNT(*) FROM "{table}" WHERE {where}', where

    def sample(self, table: str, cols: list[str], where: str, n: int = 5) -> str:
        return f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}" WHERE {where}'

    def perf(self) -> dict | None:
        return None  # latency and throttling live in CloudWatch: connect it through Datadog or Grafana as a source


# ============================================================ helpers

def _flatten(d: dict, prefix: str = "", depth: int = 0) -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and depth < 1:
            out.update(_flatten(v, key + ".", depth + 1))
        else:
            out[key] = v
    return out


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _tabulate(docs: list[dict]) -> tuple[list[str], list[list]]:
    flat = [_flatten(_jsonable(d)) for d in docs]
    cols: list[str] = []
    for f in flat:
        for k in f:
            if k not in cols:
                cols.append(k)
    return cols, [[f.get(c) for c in cols] for f in flat]


def _from_dynamo(item: dict) -> dict:
    from boto3.dynamodb.types import TypeDeserializer
    des = TypeDeserializer()
    out = {}
    for k, v in item.items():
        val = des.deserialize(v)
        if val.__class__.__name__ == "Decimal":
            val = int(val) if val == val.to_integral_value() else float(val)
        out[k] = val
    return out
