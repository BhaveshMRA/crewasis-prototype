"""Ollama client for the three LLM steps (Plan, Synthesize, Frame).

The LLM only plans and words things. Every answer is parsed, validated and checked by the engine. A failed
call returns None and is logged; the app then refuses to show the analysis rather than fill it with template text.

Configuration (environment variables):
    OLLAMA_HOST      default http://localhost:11434 (Ollama Cloud: https://ollama.com)
    OLLAMA_MODEL     default nemotron-3-ultra
    OLLAMA_API_KEY   only for Ollama Cloud; sent as a Bearer token, never stored or logged
    OLLAMA_TIMEOUT   seconds per request, default 180
    CREWASIS_OFFLINE set to 1 to get no client (tests and development only; the app needs the LLM)
These can also go in a .env file next to app.py (see .env.example); .env is git-ignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

import requests

import db

try:  # optional: read OLLAMA_* settings from a .env file next to the app (git-ignored)
    from pathlib import Path

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)  # real environment variables win
except ImportError:
    pass

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "nemotron-3-ultra"
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json(text: str):
    """Pull a JSON object out of a model reply: drops <think> blocks and ``` fences, then finds the first object."""
    if not text:
        return None
    text = THINK_BLOCK.sub("", text).strip()
    text = FENCE.sub("", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                esc = (ch == "\\" and not esc)
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


class OllamaLLM:
    def __init__(self, con=None, host=None, model=None, api_key=None, timeout=None, retries=1):
        self.con = con
        self.host = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
        if not self.host.startswith(("http://", "https://")):
            self.host = "http://" + self.host
        self.model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL
        self._api_key = api_key if api_key is not None else os.environ.get("OLLAMA_API_KEY", "")
        self.timeout = float(timeout or os.environ.get("OLLAMA_TIMEOUT") or 180)
        self.retries = retries
        self.last_error = ""

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def ping(self) -> tuple[bool, str]:
        """Check the server answers and the model is available."""
        try:
            r = requests.get(f"{self.host}/api/tags", headers=self._headers(), timeout=10)
        except requests.RequestException as e:
            return False, f"Can't reach Ollama at {self.host} ({type(e).__name__})."
        if r.status_code in (401, 403):
            return False, "Ollama rejected the API key (check OLLAMA_API_KEY)."
        if r.status_code != 200:
            return False, f"Ollama returned HTTP {r.status_code}."
        names = {m.get("name", "") for m in r.json().get("models", [])}
        if self.model in names or any(n.split(":")[0] == self.model for n in names):
            return True, f"Connected to {self.host}, model {self.model} is available."
        return False, (f"Connected to {self.host}, but model {self.model} isn't listed. "
                       f"Run `ollama pull {self.model}` or set OLLAMA_MODEL.")

    def _cache_key(self, system, user, schema):
        blob = json.dumps([self.model, system, user, schema], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def chat_json(self, step: str, system: str, user: str, schema: dict, engagement_id=None):
        """One JSON-returning chat call. Returns a dict, or None on any failure (the caller falls back)."""
        key = self._cache_key(system, user, schema)
        if self.con is not None:
            cached = db.cache_get(self.con, key)
            if cached is not None:
                db.log_llm_call(self.con, step, self.model, True, True, 0, engagement_id=engagement_id)
                return json.loads(cached)
        body = {"model": self.model, "stream": False, "format": schema, "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        start, error = time.time(), ""
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(f"{self.host}/api/chat", json=body, headers=self._headers(), timeout=self.timeout)
            except requests.Timeout:
                error = f"timed out after {self.timeout:.0f}s"
                continue
            except requests.RequestException as e:
                error = f"connection failed ({type(e).__name__})"
                continue
            if r.status_code >= 500:
                error = f"HTTP {r.status_code}"
                continue
            if r.status_code != 200:
                try:
                    error = f"HTTP {r.status_code}: {r.json().get('error', '')}"
                except ValueError:
                    error = f"HTTP {r.status_code}"
                break  # 4xx won't get better on retry
            try:
                content = r.json().get("message", {}).get("content", "")
            except ValueError:
                error = "reply wasn't JSON"
                break
            data = parse_json(content)
            if isinstance(data, dict):
                ms = int((time.time() - start) * 1000)
                if self.con is not None:
                    db.cache_put(self.con, key, json.dumps(data))
                    db.log_llm_call(self.con, step, self.model, True, False, ms, engagement_id=engagement_id)
                self.last_error = ""
                return data
            error = "couldn't find a JSON object in the reply"
        self.last_error = error
        if self.con is not None:
            db.log_llm_call(self.con, step, self.model, False, False, int((time.time() - start) * 1000), error,
                            engagement_id=engagement_id)
        return None


def from_env(con=None):
    """The configured LLM, or None when CREWASIS_OFFLINE=1."""
    if os.environ.get("CREWASIS_OFFLINE") == "1":
        return None
    return OllamaLLM(con)
