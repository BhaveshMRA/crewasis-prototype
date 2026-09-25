"""Shared fixtures: an in-memory database, the dataset, a scriptable fake LLM and a stub Ollama server."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import engine  # noqa: E402


@pytest.fixture(scope="session")
def data():
    return engine.load()  # straight from the CSVs; each test's database is seeded separately


@pytest.fixture
def con():
    c = db.connect(":memory:")
    db.ensure_seeded(c)
    yield c
    c.close()


class FakeLLM:
    """Stands in for OllamaLLM. `responses` maps a step to a dict, a function of the request, or an exception."""
    name = "fake:test"
    model = "fake"

    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def chat_json(self, step, system, user, schema, engagement_id=None):
        req = json.loads(user)
        self.calls.append((step, req))
        r = self.responses.get(step)
        if isinstance(r, Exception):
            raise r
        return r(req) if callable(r) else r


@pytest.fixture
def fake_llm():
    return FakeLLM


class StubOllama:
    """A tiny HTTP server that behaves like Ollama. Queue replies with `reply(...)`; inspect `requests`."""

    def __init__(self):
        self.queue, self.requests = [], []
        self.auto = False  # when True, answer every step like a well-behaved model (from the data it is sent)
        self.tags = {"models": [{"name": "nemotron-3-ultra:latest"}]}
        self.tags_status = 200
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                stub.requests.append(("GET", self.path, dict(self.headers), None))
                self._send(stub.tags_status, stub.tags)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(("POST", self.path, dict(self.headers), body))
                if stub.queue:
                    status, payload, delay = stub.queue.pop(0)
                elif stub.auto:
                    status, payload, delay = 200, stub.model_reply(body), 0
                else:
                    status, payload, delay = 500, {"error": "no reply queued"}, 0
                if delay:
                    time.sleep(delay)
                self._send(status, payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def model_reply(body):
        """A deterministic stand-in for the model: it only rephrases what it is sent and cites real fact ids."""
        system, content = body["messages"][0]["content"], body["messages"][1]["content"]
        try:
            user = json.loads(content)
        except ValueError:
            user = None
        if user is None:  # a plain-text prompt, like check_llm.py's ping
            out = {"ok": True}
        elif "planning step" in system:
            out = {"lenses": ["product", "competitor", "customer", "channel", "retention", "community"],
                   "products": ["bar"], "questions": ["Where is the bar failing?", "Why do buyers stop?"]}
        elif "write the brief" in system:
            facts = user["facts"]
            out = {"summary": f"{facts[0]['title']} is the clearest problem [{facts[0]['id']}]. "
                              f"Start with the actions for it [{facts[0]['id']}].",
                   "sentences": [{"key": x["key"], "text": x["text"]} for x in user["sentences"]],
                   "root_causes": [{"cause": f["title"], "facts": [f["id"]]} for f in facts[:3]]}
        elif "broke a rule" in system and "items" in user and "sentences" in json.dumps(body["format"]):
            out = {"sentences": [{"key": x["key"], "text": x["text"]} for x in user["items"]]}
        elif "broke a rule" in system:
            out = {"actions": [{"key": x["key"], "action": x["example_action"]} for x in user["items"]]}
        else:
            out = {"actions": [{"key": c["key"], "action": c["example_action"]} for c in user["cards"]]}
        return {"model": body["model"], "done": True,
                "message": {"role": "assistant", "content": "<think>…</think>" + json.dumps(out)}}

    def reply(self, content=None, status=200, delay=0, raw=None):
        payload = raw if raw is not None else {"model": "nemotron-3-ultra", "done": True,
                                              "message": {"role": "assistant", "content": content}}
        self.queue.append((status, payload, delay))

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def ollama():
    s = StubOllama()
    yield s
    s.close()
