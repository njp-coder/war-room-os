"""GitHub adapter. Auth, in order: a GitHub App installation token for the repo being read (see ghapp.py), else
GITHUB_TOKEN, else the local `gh` login. The repo is taken from the API path, so every call and clone uses the
token for that repo's install and nothing reaches repos the App wasn't installed on.

Code comes from a shallow clone (fast, blob SHAs from `git ls-tree` drive the
incremental cache). History comes from the REST API.
"""
from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import ghapp
from .paths import DATA

API = "https://api.github.com"
CLONES = DATA / "repos"
_token: str | None = None
REPO_PATH = re.compile(r"^/repos/([^/]+/[^/]+)")


def fallback_token() -> str | None:
    """Local development and single-tenant installs: one token for everything."""
    global _token
    if _token:
        return _token
    _token = os.getenv("GITHUB_TOKEN")
    if not _token:
        try:
            _token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip() or None
        except Exception:
            _token = None
    return _token


def token(repo: str | None = None) -> str | None:
    if repo and ghapp.configured():
        try:
            t = ghapp.token_for_repo(repo)
            if t:
                return t
        except Exception:
            pass  # App unreachable or not installed here: fall back rather than fail the sync
    return fallback_token()


def _client(repo: str | None = None) -> httpx.Client:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    t = token(repo)
    if t:
        h["Authorization"] = f"Bearer {t}"
    return httpx.Client(base_url=API, headers=h, timeout=30)


def get(path: str, params: dict | None = None, repo: str | None = None) -> dict | list:
    m = REPO_PATH.match(path)
    with _client(repo or (m.group(1) if m else None)) as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def paged(path: str, params: dict | None = None, limit: int = 100, repo: str | None = None) -> list:
    out, page = [], 1
    params = dict(params or {})
    while len(out) < limit:
        params.update(per_page=min(100, limit), page=page)
        batch = get(path, params, repo=repo)
        if not isinstance(batch, list) or not batch:
            break
        out += batch
        if len(batch) < params["per_page"]:
            break
        page += 1
    return out[:limit]


def status() -> dict:
    """How this deployment reads GitHub: as an App people install, or with one fallback token."""
    if ghapp.configured():
        try:
            return {"connected": True, "mode": "app", **ghapp.app_meta()}
        except Exception as e:
            return {"connected": False, "mode": "app", "error": str(e)[:160]}
    if not fallback_token():
        return {"connected": False, "mode": "token"}
    try:
        return {"connected": True, "mode": "token", "login": get("/user")["login"]}
    except Exception as e:
        return {"connected": False, "mode": "token", "error": str(e)[:120]}


def my_repos(limit: int = 100) -> list[dict]:
    repos = paged("/user/repos", {"sort": "pushed", "affiliation": "owner,collaborator,organization_member"}, limit)
    return [{"full_name": r["full_name"], "private": r["private"], "default_branch": r["default_branch"],
             "pushed_at": r["pushed_at"], "language": r["language"], "description": r["description"]} for r in repos]


def _git(args: list[str], timeout: int = 600, repo: str | None = None):
    """Run git with the token passed as a one-off header, so it is never written to .git/config."""
    auth = []
    t = token(repo)
    if t:
        import base64

        b64 = base64.b64encode(f"x-access-token:{t}".encode()).decode()
        auth = ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {b64}"]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *auth, *args], check=True, env=env, capture_output=True, timeout=timeout)


def clone(project: str, full_name: str) -> Path:
    dest = CLONES / project / full_name.replace("/", "__")
    if (dest / ".git").exists():
        _git(["-C", str(dest), "fetch", "--depth", "1", "origin"], repo=full_name)
        _git(["-C", str(dest), "reset", "--hard", "FETCH_HEAD"], repo=full_name)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--depth", "1", f"https://github.com/{full_name}.git", str(dest)], repo=full_name)
    return dest


def blob_shas(repo_dir: Path) -> dict[str, str]:
    out = subprocess.run(["git", "-C", str(repo_dir), "ls-tree", "-r", "HEAD"], capture_output=True, text=True).stdout
    shas = {}
    for line in out.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            shas[path] = parts[2]
    return shas


def pulls(full_name: str, limit: int = 60) -> list[dict]:
    """Recent PRs with the context that explains 'why': body, reviews, review comments, files."""
    prs = paged(f"/repos/{full_name}/pulls", {"state": "all", "sort": "updated", "direction": "desc"}, limit)

    def enrich(pr: dict) -> dict:
        n = pr["number"]
        try:
            files = paged(f"/repos/{full_name}/pulls/{n}/files", limit=100)
            reviews = paged(f"/repos/{full_name}/pulls/{n}/reviews", limit=30)
            comments = paged(f"/repos/{full_name}/pulls/{n}/comments", limit=30)
        except Exception:
            files, reviews, comments = [], [], []
        return {
            "number": n, "title": pr["title"], "body": (pr.get("body") or "")[:1500], "state": pr["state"],
            "merged_at": pr.get("merged_at"), "created_at": pr["created_at"], "author": pr["user"]["login"],
            "base": pr["base"]["ref"], "head": pr["head"]["ref"],
            "milestone": (pr.get("milestone") or {}).get("title"), "labels": [l["name"] for l in pr.get("labels", [])],
            "url": pr["html_url"],
            "files": [{"path": f["filename"], "status": f["status"], "additions": f["additions"], "deletions": f["deletions"],
                       "patch": (f.get("patch") or "")[:4000]} for f in files],
            "reviewers": sorted({r["user"]["login"] for r in reviews if r.get("user")}),
            "review_notes": [(r["user"]["login"], (r.get("body") or "")[:1500]) for r in reviews if r.get("body")] +
                            [(c["user"]["login"], (f"On {c.get('path')}: " if c.get("path") else "") + c["body"][:1500]) for c in comments if c.get("user")],
        }

    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(enrich, prs))


def deployments(full_name: str, limit: int = 30) -> list[dict]:
    try:
        deps = paged(f"/repos/{full_name}/deployments", limit=limit)
    except Exception:
        return []
    return [{"id": d["id"], "env": d["environment"], "sha": d["sha"][:7], "ref": d["ref"], "created_at": d["created_at"],
             "creator": (d.get("creator") or {}).get("login")} for d in deps]


def milestones(full_name: str) -> list[dict]:
    try:
        return [{"title": m["title"], "due_on": m.get("due_on"), "open": m["open_issues"], "closed": m["closed_issues"]}
                for m in paged(f"/repos/{full_name}/milestones", {"state": "all"}, 50)]
    except Exception:
        return []


def issues(full_name: str, limit: int = 60) -> list[dict]:
    try:
        items = paged(f"/repos/{full_name}/issues", {"state": "all", "sort": "updated"}, limit)
    except Exception:
        return []
    return [{"number": i["number"], "title": i["title"], "body": (i.get("body") or "")[:800], "state": i["state"],
             "labels": [l["name"] for l in i.get("labels", [])], "author": i["user"]["login"], "url": i["html_url"],
             "milestone": (i.get("milestone") or {}).get("title"), "created_at": i["created_at"]}
            for i in items if "pull_request" not in i]
