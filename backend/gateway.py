"""LLM gateway: every model call goes through llm_call(role, ...).

- Role -> model mapping lives in config/models.yaml (Gemini now, Claude later).
- Responses are cached by hash(role, model, system, prompt): re-runs cost 0 tokens.
- Token meter per role, shown in the UI.
- If no API key is set, raises Offline and callers use their deterministic path.
- Providers: gemini (Google AI Studio), anthropic, and openai_compatible (any /v1/chat/completions endpoint,
  e.g. the HiDevs gateway), whose base URL and key variable come from models.yaml.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path

import httpx
import yaml
from .paths import DATA

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
CACHE = sqlite3.connect(DATA / "llm_cache.db", check_same_thread=False)
CACHE.execute("CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, response TEXT)")
_lock = threading.Lock()

meter = {"calls": 0, "cached": 0, "prompt_tokens": 0, "output_tokens": 0, "by_role": {}, "budget": CONFIG.get("budget_tokens", 100000)}


class Offline(Exception):
    pass


def _key(*parts) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _key_var() -> str:
    provider = CONFIG["provider"]
    if provider == "openai_compatible":
        return CONFIG.get("api_key_env", "LLM_API_KEY")
    return {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider, "LLM_API_KEY")


def status() -> dict:
    provider = CONFIG["provider"]
    has_key = bool(os.getenv(_key_var()))
    used = meter["prompt_tokens"] + meter["output_tokens"]
    return {"provider": provider, "online": has_key, "used": used, "budget": meter["budget"], **{k: meter[k] for k in ("calls", "cached")}}


def llm_call(role: str, system: str, prompt: str, max_tokens: int = 400, json_out: bool = True) -> dict | str:
    cfg = CONFIG["roles"][role]
    model = cfg["model"]
    k = _key(role, model, system, prompt)
    with _lock:
        row = CACHE.execute("SELECT response FROM cache WHERE key=?", (k,)).fetchone()
    if row:
        meter["cached"] += 1
        text = row[0]
    else:
        text, usage = _call_provider(model, system, prompt, max_tokens, json_out)
        with _lock:
            CACHE.execute("INSERT OR REPLACE INTO cache VALUES(?,?)", (k, text))
            CACHE.commit()
        meter["calls"] += 1
        meter["prompt_tokens"] += usage[0]
        meter["output_tokens"] += usage[1]
        r = meter["by_role"].setdefault(role, {"calls": 0, "tokens": 0})
        r["calls"] += 1
        r["tokens"] += usage[0] + usage[1]
    if not json_out:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]) if start >= 0 else {}


def _call_provider(model: str, system: str, prompt: str, max_tokens: int, json_out: bool) -> tuple[str, tuple[int, int]]:
    provider = CONFIG["provider"]
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise Offline("GEMINI_API_KEY not set")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2,
                                 **({"responseMimeType": "application/json"} if json_out else {})},
        }
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       headers={"x-goog-api-key": key}, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        u = data.get("usageMetadata", {})
        return text, (u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0))
    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise Offline("ANTHROPIC_API_KEY not set")
        r = httpx.post("https://api.anthropic.com/v1/messages",
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                       json={"model": model, "max_tokens": max_tokens, "system": system,
                             "messages": [{"role": "user", "content": prompt}]}, timeout=60)
        r.raise_for_status()
        data = r.json()
        text = "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")
        u = data.get("usage", {})
        return text, (u.get("input_tokens", 0), u.get("output_tokens", 0))
    if provider == "openai_compatible":
        key = os.getenv(_key_var())
        if not key:
            raise Offline(f"{_key_var()} not set")
        # Thinking models spend hidden reasoning tokens out of max_tokens; give them headroom so the answer isn't cut off.
        headroom = int(CONFIG.get("reasoning_headroom", 0))
        body = {"model": model, "max_tokens": max_tokens + headroom, "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if json_out:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(CONFIG["base_url"].rstrip("/") + "/chat/completions",
                       headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
        if r.status_code == 400 and json_out:  # some gateways reject response_format; the prompt already asks for JSON
            body.pop("response_format")
            r = httpx.post(CONFIG["base_url"].rstrip("/") + "/chat/completions",
                           headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        u = data.get("usage", {})
        if choice.get("finish_reason") == "length":
            # A truncated answer is worse than none: callers fall back to their deterministic path.
            raise Offline(f"{model} ran out of tokens ({u.get('completion_tokens', 0)} used); raise reasoning_headroom in models.yaml")
        return text, (u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    raise Offline(f"unknown provider {provider}")


def llm_call_parts(role: str, system: str, parts: list[dict], max_tokens: int = 1200, json_out: bool = True) -> dict | str:
    """Multimodal call: parts are {"text": ...}, {"image": base64, "mime": "image/jpeg"} or {"audio": base64, "format": "mp3"}.
    Only the openai_compatible provider carries images and audio today; others raise Offline and callers skip the media."""
    if CONFIG["provider"] != "openai_compatible":
        raise Offline("media input needs the openai_compatible provider")
    key = os.getenv(_key_var())
    if not key:
        raise Offline(f"{_key_var()} not set")
    model = CONFIG["roles"][role]["model"]
    content = []
    for p in parts:
        if "text" in p:
            content.append({"type": "text", "text": p["text"]})
        elif "image" in p:
            content.append({"type": "image_url", "image_url": {"url": f"data:{p.get('mime', 'image/jpeg')};base64,{p['image']}"}})
        elif "audio" in p:
            content.append({"type": "input_audio", "input_audio": {"data": p["audio"], "format": p.get("format", "mp3")}})
    k = _key(role, model, system, json.dumps(content))
    with _lock:
        row = CACHE.execute("SELECT response FROM cache WHERE key=?", (k,)).fetchone()
    if row:
        meter["cached"] += 1
        text = row[0]
    else:
        body = {"model": model, "max_tokens": max_tokens + int(CONFIG.get("reasoning_headroom", 0)), "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        r = httpx.post(CONFIG["base_url"].rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=300)
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        u = data.get("usage", {})
        if choice.get("finish_reason") == "length":
            raise Offline(f"{model} ran out of tokens on a media call")
        meter["calls"] += 1
        meter["prompt_tokens"] += u.get("prompt_tokens", 0)
        meter["output_tokens"] += u.get("completion_tokens", 0)
        with _lock:
            CACHE.execute("INSERT OR REPLACE INTO cache VALUES(?,?)", (k, text))
            CACHE.commit()
    if not json_out:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        return json.loads(text[s:e + 1]) if s >= 0 else {}
