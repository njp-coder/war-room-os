"""Where a user-supplied URL is allowed to point.

Anyone who signs in can connect a database, a log source and a staging app, and the server fetches whatever they
name. Without a check that reaches the server's own disk (sqlite:///…), its localhost, its cloud metadata endpoint
and anything else on the private network — and the results come back through the UI.

So: user URLs must use a network scheme and resolve to a public address. Every address behind the name is checked,
because a hostname can resolve to 127.0.0.1 just as easily as to a public IP.

WARROOM_ALLOW_LOCAL=1 turns this off for local development, where the staging database really is on localhost and
a log file really is on this disk. It must stay off on anything hosted.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

DB_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg", "mysql", "mysql+pymysql", "mssql", "sqlserver",
              "mssql+pymssql", "snowflake", "bigquery", "mongodb", "mongodb+srv", "dynamodb"}
LOCAL_ONLY_DB = {"sqlite"}
NO_HOST = {"bigquery", "dynamodb"}  # addressed by project/region, not a host


class Blocked(ValueError):
    """Raised with a message meant for the person who typed the URL."""


def allow_local() -> bool:
    return os.environ.get("WARROOM_ALLOW_LOCAL", "").strip().lower() in {"1", "true", "yes", "on"}


def _private(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast
            or a.is_unspecified or (a.version == 6 and a.ipv4_mapped is not None and _private(str(a.ipv4_mapped))))


def check_host(host: str | None):
    if allow_local():
        return
    if not host:
        raise Blocked("That address has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise Blocked(f"Couldn't resolve {host}") from None
    for info in infos:
        ip = info[4][0]
        if _private(ip):
            raise Blocked(f"{host} points inside the server's own network, which isn't allowed")


def safe_http(url: str, what: str = "URL") -> str:
    """An http(s) URL on a public host. Returns it unchanged, or raises Blocked."""
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https"):
        raise Blocked(f"The {what} has to start with http:// or https://")
    check_host(u.hostname)
    return url.strip()


def safe_db_url(url: str) -> str:
    """A database URL on a public host. Local files (sqlite) only in local development."""
    scheme = url.split("://", 1)[0].strip().lower()
    if scheme in LOCAL_ONLY_DB:
        if not allow_local():
            raise Blocked("A local database file can't be used on a hosted War Room. Connect a database over the network.")
        return url
    if scheme not in DB_SCHEMES:
        raise Blocked("Unsupported database. Use postgres://, mysql://, mssql://, snowflake://, bigquery://, mongodb:// or dynamodb://")
    if scheme in NO_HOST:
        return url
    host = urlsplit(url).hostname
    if not host and "@" in url:  # drivers that don't parse cleanly: take what follows the credentials
        host = url.split("@", 1)[1].split("/")[0].split(":")[0]
    check_host(host)
    return url


def safe_path(path: str, root_env: str = "WARROOM_LOG_ROOT") -> Path:
    """A file the server may read. Hosted, it must sit inside the directory the operator opened up."""
    p = Path(path).expanduser()
    if allow_local():
        return p
    root = os.environ.get(root_env, "").strip()
    if not root:
        raise Blocked("Reading log files off the server is turned off here. Use Sentry, Datadog, Loki or Vercel instead.")
    base = Path(root).expanduser().resolve()
    try:
        resolved = p.resolve()
        resolved.relative_to(base)
    except (ValueError, OSError):
        raise Blocked(f"Log files have to be inside {base}") from None
    return resolved
