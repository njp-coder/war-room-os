"""Log and error sources: Sentry, Datadog, Grafana Loki, and plain log files.

Every source is polled into the same shape: error patterns (a message template with numbers, IDs and quoted values
masked), a count per poll window, a sample, and stack frames (file:line). Patterns feed the monitoring rules and
guardrails; frames link an error to the file, the function at that line, the PRs that changed it, and their authors.
Tokens are encrypted with the same key as database URLs and are never returned to the UI.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

from . import db, graph, netguard, staging

db.x("""CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, project TEXT, kind TEXT, name TEXT, config TEXT, enabled INT DEFAULT 1,
        status TEXT, last_polled REAL, cursor TEXT)""")
db.x("CREATE TABLE IF NOT EXISTS patterns(project TEXT, fingerprint TEXT, source TEXT, template TEXT, sample TEXT, level TEXT, frames TEXT, "
     "first_seen REAL, last_seen REAL, total INT, link TEXT, PRIMARY KEY(project, fingerprint))")

KINDS = {
    "logfile": {"label": "Log file", "fields": ["path"], "secret": None, "help": "A log file or folder on this server. New lines are read on each poll."},
    "sentry": {"label": "Sentry", "fields": ["org", "project", "base_url"], "secret": "Auth token (project:read, event:read)",
               "help": "Unresolved issues with their event counts and culprit frames."},
    "datadog": {"label": "Datadog", "fields": ["site", "query"], "secret": "API key and application key, separated by a colon",
                "help": "Log events matching the query, grouped into patterns."},
    "vercel": {"label": "Vercel", "fields": ["project", "team", "environment"], "secret": "Vercel access token",
               "help": "Runtime logs from a Vercel project: errors and 5xx responses, with stack traces mapped to your code. Environment: production or preview. "
                       "Vercel streams logs live, so War Room collects them from the moment you connect."},
    "loki": {"label": "Grafana Loki", "fields": ["url", "query"], "secret": "Bearer token (optional)",
             "help": "Lines from a LogQL query. Point it at Loki directly or at Grafana's datasource proxy."},
}

MASKS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"(?<![A-Za-z_])\d+(\.\d+)?"), "<n>"),  # also 4s, 250ms, v2 stays
]
LEVEL = re.compile(r"\b(ERROR|CRITICAL|FATAL|Exception|Traceback|panic|WARN(?:ING)?)\b", re.I)
FRAME_PY = re.compile(r'File "([^"]+)", line (\d+)')
FRAME_JS = re.compile(r"at (?:[\w.<>$]+ )?\(?([\w./@-]+\.(?:ts|tsx|js|jsx|mjs)):(\d+)(?::\d+)?\)?")


def template(msg: str) -> str:
    t = msg.strip().splitlines()[0][:300] if msg.strip() else ""
    t = re.sub(r"^\S*\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d\S*\s*", "", t)  # leading timestamp
    for rx, rep in MASKS:
        t = rx.sub(rep, t)
    return t


def frames(text: str) -> list[list]:
    out = [[f, int(n)] for f, n in FRAME_PY.findall(text)] + [[f, int(n)] for f, n in FRAME_JS.findall(text)]
    lib = ("site-packages", "node_modules", "/_vendor/", "/var/lang/", "dist-packages")  # library frames, including Vercel's bundle
    return [f for f in out if not any(x in f[0] for x in lib)][-5:]


# ---------------- CRUD ----------------

def add(project: str, kind: str, name: str, config: dict, secret: str | None) -> str:
    sid = db.uid("src")
    db.x("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)", (sid, project, kind, name or KINDS[kind]["label"], json.dumps(config), 1, "new", None, None))
    if secret:
        staging.save_url(project, secret, f"source:{sid}")
    return sid


def remove(project: str, sid: str):
    if sid in _vercel:
        _vercel.pop(sid).stopped = True
    db.x("DELETE FROM sources WHERE id=? AND project=?", (sid, project))
    staging.remove(project, f"source:{sid}")


def listing(project: str) -> list[dict]:
    out = []
    for s in db.q("SELECT * FROM sources WHERE project=?", (project,)):
        cfg = json.loads(s["config"] or "{}")
        out.append({"id": s["id"], "kind": s["kind"], "name": s["name"], "config": cfg, "status": s["status"], "last_polled": s["last_polled"],
                    "has_secret": staging.connected(project, f"source:{s['id']}"),
                    "patterns": db.one("SELECT count(*) n FROM patterns WHERE project=? AND source=?", (project, s["id"]))["n"]})
    return out


# ---------------- fetchers: each returns [{message, level, frames, link}] for new events ----------------

def _fetch_logfile(src: dict, cfg: dict, secret: str | None) -> tuple[list[dict], str]:
    path = netguard.safe_path(cfg["path"])
    files = sorted(path.glob("*.log")) if path.is_dir() else [path]
    cursor = json.loads(src["cursor"] or "{}")
    events = []
    for f in files:
        if not f.exists():
            continue
        size = f.stat().st_size
        start = cursor.get(str(f), 0)
        if start > size:
            start = 0  # rotated
        with open(f, errors="ignore") as fh:
            fh.seek(start)
            chunk = fh.read(5_000_000)
            cursor[str(f)] = fh.tell()
        block = []
        for line in chunk.splitlines():  # a traceback, its frames, and the exception line that ends it are one event
            if block and (line.startswith((" ", "\t")) or FRAME_PY.search(line) or FRAME_JS.search(line)):
                block.append(line)
                continue
            if block and block[0].startswith("Traceback") and re.search(r"(Error|Exception|ERROR|CRITICAL)", line):
                block.append(line)
                events.append(block)
                block = []
                continue
            if block:
                events.append(block)
            block = [line] if LEVEL.search(line) else []
        if block:
            events.append(block)
    out = []
    for b in events:
        text = "\n".join(b)
        head = next((l for l in reversed(b) if re.search(r"(Error|Exception|ERROR|CRITICAL|FATAL|panic)", l) and not l.startswith((" ", "\t"))), b[0])
        lvl = LEVEL.search(text)
        out.append({"message": head, "level": (lvl.group(1).upper() if lvl else "ERROR"), "frames": frames(text), "link": None})
    return out, json.dumps(cursor)


def _fetch_sentry(src: dict, cfg: dict, secret: str | None) -> tuple[list[dict], str]:
    base = netguard.safe_http(cfg.get("base_url") or "https://sentry.io", "Sentry URL").rstrip("/")
    r = httpx.get(f"{base}/api/0/projects/{cfg['org']}/{cfg['project']}/issues/", params={"statsPeriod": "24h", "query": "is:unresolved"},
                  headers={"Authorization": f"Bearer {secret}"}, timeout=20)
    r.raise_for_status()
    prev = json.loads(src["cursor"] or "{}")
    now, out = {}, []
    for issue in r.json():
        count = int(issue.get("count") or 0)
        now[issue["id"]] = count
        delta = count - prev.get(issue["id"], count if src["cursor"] else 0)
        meta = issue.get("metadata") or {}
        fr = [[meta["filename"], 0]] if meta.get("filename") else []
        for _ in range(max(delta, 0) if src["cursor"] else min(count, 1)):
            out.append({"message": f"{meta.get('type', issue.get('title', ''))}: {meta.get('value', '')}".strip(": ") or issue.get("title", ""),
                        "level": (issue.get("level") or "error").upper(), "frames": fr, "link": issue.get("permalink"),
                        "culprit": issue.get("culprit")})
    return out, json.dumps(now)


def _fetch_datadog(src: dict, cfg: dict, secret: str | None) -> tuple[list[dict], str]:
    api, app = (secret or ":").split(":", 1)
    site = (cfg.get("site") or "datadoghq.com").strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]+", site):  # built into a hostname below: no credentials or paths smuggled in
        raise ValueError("That isn't a Datadog site, e.g. datadoghq.com or datadoghq.eu")
    netguard.check_host(f"api.{site}")
    since = json.loads(src["cursor"] or "{}").get("to") or "now-5m"
    body = {"filter": {"query": cfg.get("query") or "status:error", "from": since, "to": "now"}, "page": {"limit": 1000}, "sort": "timestamp"}
    r = httpx.post(f"https://api.{site}/api/v2/logs/events/search", json=body, timeout=20,
                   headers={"DD-API-KEY": api, "DD-APPLICATION-KEY": app, "Content-Type": "application/json"})
    r.raise_for_status()
    out, last = [], since
    for ev in r.json().get("data", []):
        a = ev.get("attributes", {})
        msg = a.get("message") or ""
        err = (a.get("attributes") or {}).get("error") or {}
        out.append({"message": err.get("message") or msg, "level": (a.get("status") or "error").upper(),
                    "frames": frames(err.get("stack") or msg), "link": None})
        last = a.get("timestamp") or last
    return out, json.dumps({"to": last})


def _fetch_loki(src: dict, cfg: dict, secret: str | None) -> tuple[list[dict], str]:
    start = json.loads(src["cursor"] or "{}").get("ns") or str(int((time.time() - 300) * 1e9))
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    r = httpx.get(netguard.safe_http(cfg["url"], "Loki URL").rstrip("/") + "/loki/api/v1/query_range", headers=headers, timeout=20,
                  params={"query": cfg.get("query") or '{job=~".+"} |~ "(?i)error|exception"', "start": start, "limit": 1000, "direction": "forward"})
    r.raise_for_status()
    out, last = [], int(start)
    for stream in r.json().get("data", {}).get("result", []):
        for ts, line in stream.get("values", []):
            last = max(last, int(ts) + 1)
            lvl = LEVEL.search(line)
            out.append({"message": line, "level": lvl.group(1).upper() if lvl else "ERROR", "frames": frames(line), "link": None})
    return out, json.dumps({"ns": str(last)})


def _vercel_event(text: str, level: str, row: dict) -> dict:
    """One error from a Vercel request: our JSON log line (traceback in "exc") or Vercel's own plain capture."""
    if text.lstrip().startswith("{"):
        try:
            o = json.loads(text)
            text = (o.get("msg") or "") + ("\n" + o["exc"] if o.get("exc") else "")
            level = (o.get("level") or level).lower()
        except ValueError:
            pass
    lines = text.strip().splitlines() or [""]
    head = next((l.strip() for l in reversed(lines) if re.search(r"(Error|Exception)\b", l) and not l.startswith((" ", "\t"))), lines[0])
    return {"message": head, "level": level.upper(), "frames": frames(text), "link": None,
            "request": f"{row.get('requestMethod', '')} {row.get('requestPath', '')}".strip(), "status": row.get("statusCode")}


class _VercelStream:
    """Vercel's public logs API is a live stream per deployment, not a query. One of these per source keeps a stream open
    to each current deployment (production, or the latest previews), reconnects when Vercel closes it, follows new
    deployments, and buffers errors and 5xx responses until the next poll drains them."""
    MAX_BUFFER = 5000
    RESCAN_S = 60

    def __init__(self, sid: str, cfg: dict, secret: str, pid: str, team_q: dict):
        self.sid, self.cfg, self.secret, self.pid, self.team_q = sid, cfg, secret, pid, team_q
        self.h = {"Authorization": f"Bearer {secret}"}
        self.buf: list[dict] = []
        self.seen: dict[str, None] = {}  # rowIds already taken, insertion-ordered so it can be trimmed
        self.live: dict[str, threading.Thread] = {}
        self.error: str | None = None
        self.stopped = False
        self.lock = threading.Lock()
        threading.Thread(target=self._watch, daemon=True, name=f"vercel-{sid}").start()

    def deployments(self) -> list[str]:
        env = (self.cfg.get("environment") or "production").strip().lower()
        q = {**self.team_q, "projectId": self.pid, "target": "production" if env == "production" else "preview",
             "state": "READY", "limit": 1 if env == "production" else 3}
        r = httpx.get("https://api.vercel.com/v6/deployments", params=q, headers=self.h, timeout=20)
        r.raise_for_status()
        return [d["uid"] for d in r.json().get("deployments", [])]

    def _watch(self):
        while not self.stopped:
            try:
                current = set(self.deployments())
                self.error = None if current else "No ready deployment in that environment yet"
                for did in current:
                    if did not in self.live or not self.live[did].is_alive():
                        self.live[did] = threading.Thread(target=self._tail, args=(did,), daemon=True)
                        self.live[did].start()
                for did in list(self.live):
                    if did not in current:
                        self.live.pop(did)  # its _tail sees it's gone and ends
            except Exception as e:
                self.error = _http_error(e)
            time.sleep(self.RESCAN_S)

    def _tail(self, did: str):
        url = f"https://api.vercel.com/v1/projects/{self.pid}/deployments/{did}/runtime-logs"
        while not self.stopped and did in self.live:
            try:
                with httpx.stream("GET", url, params=self.team_q, headers=self.h, timeout=httpx.Timeout(20, read=None)) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if self.stopped or did not in self.live:
                            return
                        if line.strip():
                            self._take(line)
            except Exception as e:
                self.error = _http_error(e)
                time.sleep(15)
                continue
            time.sleep(2)  # Vercel ends streams after a while; pick up again

    def _take(self, line: str):
        try:
            row = json.loads(line)
        except ValueError:
            return
        rid = str(row.get("rowId") or "")
        if not rid or rid in self.seen:
            return
        self.seen[rid] = None
        if len(self.seen) > 20000:
            for k in list(self.seen)[:10000]:
                del self.seen[k]
        level = (row.get("level") or "").lower()
        msg = row.get("message") or ""
        status = int(row.get("responseStatusCode") or 0)
        ev = None
        if level in ("error", "fatal") or (msg.lstrip().startswith("{") and '"level": "ERROR"' in msg):
            ev = _vercel_event(msg, level or "error", {"requestMethod": row.get("requestMethod"), "requestPath": row.get("requestPath"),
                                                       "statusCode": status})
        elif row.get("source") == "request" and status >= 500:
            ev = {"message": f"{row.get('requestMethod', '')} {row.get('requestPath', '')} returned {status}", "level": "ERROR",
                  "frames": [], "link": None, "request": f"{row.get('requestMethod', '')} {row.get('requestPath', '')}".strip(),
                  "status": status, "bare_5xx": True}
        if ev:
            with self.lock:
                self.buf.append(ev)
                del self.buf[:-self.MAX_BUFFER]

    def drain(self) -> list[dict]:
        with self.lock:
            out, self.buf = self.buf, []
        # A request that logged its error also shows up as a bare 5xx row: count it once.
        explained = {e.get("request") for e in out if not e.get("bare_5xx")}
        return [e for e in out if not (e.get("bare_5xx") and e.get("request") in explained)]


_vercel: dict[str, _VercelStream] = {}


def _http_error(e: Exception) -> str:
    """A readable reason, without the request URL (it carries ids) or anything from the token."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return f"Vercel refused the token ({code}). Check it hasn't expired and its scope includes this project's team."
        if code == 404:
            return "Vercel couldn't find that project. Check the project name and team."
        return f"Vercel returned {code}"
    if isinstance(e, httpx.HTTPError):
        return f"Couldn't reach Vercel ({type(e).__name__})"
    return str(e)[:160]


def _fetch_vercel(src: dict, cfg: dict, secret: str | None) -> tuple[list[dict], str]:
    """Runtime logs of a Vercel project from its live log stream (see _VercelStream): errors and 5xx responses."""
    if not secret:
        raise ValueError("Add a Vercel access token")
    cur = json.loads(src["cursor"] or "{}")
    h = {"Authorization": f"Bearer {secret}"}
    team = (cfg.get("team") or "").strip()
    team_q = {"teamId": team} if team.startswith("team_") else ({"slug": team} if team else {})
    s = _vercel.get(src["id"])
    if s and s.secret != secret:
        s.stopped = True
        s = None
    if not s:
        try:
            r = httpx.get(f"https://api.vercel.com/v9/projects/{cfg['project'].strip()}", params=team_q, headers=h, timeout=20)
            r.raise_for_status()
            p = r.json()
            probe = _VercelStream.__new__(_VercelStream)  # check the token can list deployments before streaming
            probe.cfg, probe.pid, probe.team_q, probe.h = cfg, p["id"], team_q, h
            if not probe.deployments():
                raise ValueError(f"No ready {(cfg.get('environment') or 'production').strip()} deployment in {cfg['project'].strip()} yet")
        except httpx.HTTPError as e:
            raise ValueError(_http_error(e)) from None
        cur.update(pid=p["id"], owner=p["accountId"])
        s = _vercel[src["id"]] = _VercelStream(src["id"], cfg, secret, p["id"], team_q)
    if s.error and not s.live:
        raise ValueError(s.error)
    cur["streams"] = len([t for t in s.live.values() if t.is_alive()])
    return s.drain(), json.dumps(cur)


FETCH = {"logfile": _fetch_logfile, "sentry": _fetch_sentry, "datadog": _fetch_datadog, "loki": _fetch_loki, "vercel": _fetch_vercel}


def poll(project: str) -> list[dict]:
    """Poll every enabled source. Returns per-pattern counts for this window: [{fingerprint, template, count, ...}]."""
    window: dict[str, dict] = {}
    for src in db.q("SELECT * FROM sources WHERE project=? AND enabled=1", (project,)):
        cfg = json.loads(src["config"] or "{}")
        secret = staging._url(project, f"source:{src['id']}")
        try:
            events, cursor = FETCH[src["kind"]](src, cfg, secret)
            db.x("UPDATE sources SET status=?, last_polled=?, cursor=? WHERE id=?", (f"ok, {len(events)} new events", time.time(), cursor, src["id"]))
        except Exception as e:
            db.x("UPDATE sources SET status=?, last_polled=? WHERE id=?", (f"error: {_http_error(e)}", time.time(), src["id"]))
            continue
        for ev in events:
            if ev["level"].startswith("WARN"):
                continue
            tpl = template(ev["message"])
            fp = "log:" + hashlib.sha1(tpl.encode()).hexdigest()[:12]
            w = window.setdefault(fp, {"fingerprint": fp, "template": tpl, "count": 0, "sample": ev["message"][:500], "frames": ev["frames"],
                                       "link": ev.get("link"), "source": src["id"], "source_kind": src["kind"], "level": ev["level"]})
            w["count"] += 1
            if ev["frames"] and not w["frames"]:
                w["frames"] = ev["frames"]
    now = time.time()
    for fp, w in window.items():
        old = db.one("SELECT first_seen, total FROM patterns WHERE project=? AND fingerprint=?", (project, fp))
        w["new"] = old is None
        db.x("INSERT OR REPLACE INTO patterns VALUES(?,?,?,?,?,?,?,?,?,?,?)",
             (project, fp, w["source"], w["template"], w["sample"], w["level"], json.dumps(w["frames"]), old["first_seen"] if old else now, now,
              (old["total"] if old else 0) + w["count"], w["link"]))
    return list(window.values())


# ---------------- link frames to code ----------------

def frames_to_code(project: str, fr: list[list]) -> dict:
    """file:line -> file node (by path suffix) -> function whose line range contains it -> PRs -> people."""
    code, prs = [], {}
    for path, line in fr[::-1]:
        suffix = path.lstrip("./").split("/app/", 1)[-1] if "/app/" in path else path.lstrip("./")
        tail = "/".join(suffix.split("/")[-3:])
        f = db.one("SELECT id, label FROM nodes WHERE project=? AND type='file' AND (label=? OR label LIKE ?) ORDER BY length(label) LIMIT 1",
                   (project, suffix, f"%{tail}"))
        if not f:
            continue
        hit = None
        for s in graph.neighbors(project, f["id"], {"defines"}, "out"):
            n = graph.node(project, s["id"]) or {"props": {}}
            lo, hi = n["props"].get("line", 0), n["props"].get("end", 0)
            if line and lo <= line <= hi and (hit is None or lo > hit[1]):
                hit = (s["id"], lo)
        code.append(hit[0] if hit else f["id"])
        for pr in graph.neighbors(project, f["id"], {"touches"}, "in"):
            prs[pr["id"]] = prs.get(pr["id"], 0) + 1
    return {"code": list(dict.fromkeys(code))[:4], "prs": sorted(prs, key=lambda p: -prs[p])[:3], "tables": [], "hints": [], "filter_columns": []}
