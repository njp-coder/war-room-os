"""GitHub App: how War Room reads someone else's repos without holding their personal token.

A person installs the App on the accounts and repos they choose. For each install GitHub hands out a token that
lasts an hour, scoped to exactly those repos and read-only, which this module fetches and caches. Nothing durable
belongs to a person: revoking the install in GitHub settings ends War Room's access immediately.

Two different tokens are in play:
  app JWT            proves "I am this App" — lists installs, mints installation tokens
  installation token acts on one install's repos — everything the sync does
  user token         the signed-in person's OAuth token, used only to ask which installs THEY can see, so one
                     person can't connect a repo another org installed. Encrypted at rest, same key as database URLs.

Env: GITHUB_APP_ID, plus the private key as GITHUB_APP_KEY_PATH (a .pem file) or GITHUB_APP_KEY (its contents, raw
or base64, for hosts without a filesystem). Without them, github.py falls back to
GITHUB_TOKEN or the local `gh` login, which is what local development uses.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import httpx
import jwt

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

_lock = threading.Lock()
_inst_tokens: dict[int, tuple[str, float]] = {}   # installation id -> (token, expires at)
_repo_inst: dict[str, tuple[int, float]] = {}     # "owner/repo" -> (installation id, cached until)
_meta: dict[str, object] = {}


def configured() -> bool:
    return bool(os.getenv("GITHUB_APP_ID") and (os.getenv("GITHUB_APP_KEY") or os.getenv("GITHUB_APP_KEY_PATH")))


def _key() -> str:
    """The private key as a file locally (GITHUB_APP_KEY_PATH) or inline for hosts with no filesystem
    (GITHUB_APP_KEY, raw PEM or base64 of it)."""
    inline = os.getenv("GITHUB_APP_KEY", "").strip()
    if inline:
        if inline.startswith("-----"):
            return inline
        import base64

        return base64.b64decode(inline).decode()
    p = Path(os.environ["GITHUB_APP_KEY_PATH"]).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"GitHub App key not found at {p}")
    return p.read_text()


def app_jwt() -> str:
    """Signed with the App's private key. GitHub allows at most 10 minutes; 9 leaves room for clock drift."""
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": os.environ["GITHUB_APP_ID"]}, _key(), algorithm="RS256")


def _get(path: str, token: str, params: dict | None = None) -> dict | list:
    r = httpx.get(API + path, headers={**HEADERS, "Authorization": f"Bearer {token}"}, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def app_meta() -> dict:
    """Name and slug, for the 'Install on GitHub' link. Cached: it never changes."""
    if not _meta:
        a = _get("/app", app_jwt())
        _meta.update({"slug": a.get("slug"), "name": a.get("name"), "install_url": f"https://github.com/apps/{a.get('slug')}/installations/new"})
    return dict(_meta)


def installation_token(installation_id: int) -> str:
    with _lock:
        hit = _inst_tokens.get(installation_id)
        if hit and hit[1] - 60 > time.time():
            return hit[0]
    r = httpx.post(f"{API}/app/installations/{installation_id}/access_tokens",
                   headers={**HEADERS, "Authorization": f"Bearer {app_jwt()}"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    expires = time.mktime(time.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
    with _lock:
        _inst_tokens[installation_id] = (data["token"], expires)
    return data["token"]


def installation_for_repo(full_name: str) -> int | None:
    """Which install covers this repo. None means the App isn't installed on it."""
    with _lock:
        hit = _repo_inst.get(full_name)
        if hit and hit[1] > time.time():
            return hit[0]
    try:
        inst = _get(f"/repos/{full_name}/installation", app_jwt())
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 404):
            return None
        raise
    with _lock:
        _repo_inst[full_name] = (inst["id"], time.time() + 600)
    return inst["id"]


def token_for_repo(full_name: str) -> str | None:
    iid = installation_for_repo(full_name)
    return installation_token(iid) if iid else None


def forget(full_name: str):
    with _lock:
        _repo_inst.pop(full_name, None)


# ---------------- what the signed-in person can connect ----------------

def installations(user_token: str) -> list[dict]:
    """The installs this person can see, each with the repos they can access through it."""
    out = []
    data = _get("/user/installations", user_token, {"per_page": 50})
    for inst in data.get("installations", []) if isinstance(data, dict) else []:
        acct = inst.get("account") or {}
        repos = []
        try:
            page = _get(f"/user/installations/{inst['id']}/repositories", user_token, {"per_page": 100})
            repos = [{"full_name": r["full_name"], "private": r["private"], "default_branch": r["default_branch"],
                      "pushed_at": r.get("pushed_at"), "language": r.get("language")}
                     for r in (page.get("repositories", []) if isinstance(page, dict) else [])]
        except httpx.HTTPError:
            pass  # a lost install still lists; it just has nothing to offer
        repos.sort(key=lambda r: r["pushed_at"] or "", reverse=True)
        out.append({"id": inst["id"], "account": acct.get("login"), "kind": acct.get("type"),
                    "avatar": acct.get("avatar_url"), "selection": inst.get("repository_selection"), "repos": repos})
    return out


def can_access(user_token: str, full_name: str) -> bool:
    """Only let someone connect a repo their own GitHub account reaches through an install."""
    return any(r["full_name"].lower() == full_name.lower() for inst in installations(user_token) for r in inst["repos"])
