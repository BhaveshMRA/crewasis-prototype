"""The Ollama client against a stub server that behaves like Ollama, including its failure modes."""
import json

import pytest

import db
import engine
import llm as llm_mod
from llm import OllamaLLM, parse_json

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


# ---------------------------------------------------------------- parsing model replies
@pytest.mark.parametrize("text, expected", [
    ('{"x": 1}', {"x": 1}),
    ('<think>let me think about {"x": 2}</think>{"x": 1}', {"x": 1}),
    ('```json\n{"x": 1}\n```', {"x": 1}),
    ('Sure! Here is the JSON: {"x": 1} Hope that helps.', {"x": 1}),
    ('{"a": "brace } in a string", "x": 1}', {"a": "brace } in a string", "x": 1}),
    ('noise {broken json} then {"x": 3}', {"x": 3}),
    ("", None),
    ("no json at all", None),
    ('{"x": 1', None),
])
def test_parse_json(text, expected):
    assert parse_json(text) == expected


# ---------------------------------------------------------------- HTTP behaviour
def test_success_sends_schema_and_logs(ollama, con):
    ollama.reply('{"x": 1}')
    client = OllamaLLM(con, host=ollama.url, model="nemotron-3-ultra", api_key="")
    assert client.chat_json("plan", "sys", "user", SCHEMA, engagement_id=7) == {"x": 1}
    _, path, headers, body = ollama.posts()[0]
    assert path == "/api/chat" and body["format"] == SCHEMA and body["stream"] is False
    assert body["model"] == "nemotron-3-ultra" and body["messages"][0] == {"role": "system", "content": "sys"}
    assert "Authorization" not in headers
    call = db.llm_calls(con, 7)[0]
    assert call["ok"] == 1 and call["cached"] == 0 and call["step"] == "plan"


def test_identical_call_is_served_from_cache(ollama, con):
    ollama.reply('{"x": 1}')
    client = OllamaLLM(con, host=ollama.url, api_key="")
    client.chat_json("plan", "s", "u", SCHEMA)
    assert client.chat_json("plan", "s", "u", SCHEMA) == {"x": 1}
    assert len(ollama.posts()) == 1
    assert [c["cached"] for c in db.llm_calls(con)] == [0, 1]


def test_cache_is_per_model(ollama, con):
    ollama.reply('{"x": 1}')
    ollama.reply('{"x": 2}')
    assert OllamaLLM(con, host=ollama.url, model="a", api_key="").chat_json("p", "s", "u", SCHEMA) == {"x": 1}
    assert OllamaLLM(con, host=ollama.url, model="b", api_key="").chat_json("p", "s", "u", SCHEMA) == {"x": 2}


def test_api_key_is_sent_as_bearer_and_never_logged(ollama, con):
    ollama.reply('{"x": 1}')
    OllamaLLM(con, host=ollama.url, api_key="secret-key-123").chat_json("plan", "s", "u", SCHEMA)
    assert ollama.posts()[0][2]["Authorization"] == "Bearer secret-key-123"
    dump = json.dumps([dict(r) for r in con.execute("SELECT * FROM llm_calls")] +
                      [dict(r) for r in con.execute("SELECT * FROM llm_cache")])
    assert "secret-key-123" not in dump


def test_server_error_is_retried_once(ollama, con):
    ollama.reply(status=500, raw={"error": "overloaded"})
    ollama.reply('{"x": 1}')
    assert OllamaLLM(con, host=ollama.url, api_key="").chat_json("plan", "s", "u", SCHEMA) == {"x": 1}
    assert len(ollama.posts()) == 2


def test_model_not_found_is_not_retried(ollama, con):
    ollama.reply(status=404, raw={"error": "model 'nemotron-3-ultra' not found"})
    client = OllamaLLM(con, host=ollama.url, api_key="")
    assert client.chat_json("plan", "s", "u", SCHEMA) is None
    assert len(ollama.posts()) == 1 and "not found" in client.last_error
    assert db.llm_calls(con)[0]["ok"] == 0


def test_timeout_returns_none(ollama, con):
    ollama.reply('{"x": 1}', delay=1.5)
    ollama.reply('{"x": 1}', delay=1.5)
    client = OllamaLLM(con, host=ollama.url, api_key="", timeout=0.5)
    assert client.chat_json("plan", "s", "u", SCHEMA) is None
    assert "timed out" in client.last_error


def test_unreachable_server_returns_none(con):
    client = OllamaLLM(con, host="http://127.0.0.1:9", api_key="", timeout=2)
    assert client.chat_json("plan", "s", "u", SCHEMA) is None
    assert "connection failed" in client.last_error
    assert client.ping()[0] is False


def test_reply_without_json_returns_none(ollama, con):
    ollama.reply("I'd rather not answer in JSON today.")
    ollama.reply("Still no JSON.")
    client = OllamaLLM(con, host=ollama.url, api_key="")
    assert client.chat_json("plan", "s", "u", SCHEMA) is None
    assert "JSON" in client.last_error


def test_non_json_http_body(ollama, con):
    ollama.reply(raw=b"<html>proxy error</html>", status=200)
    assert OllamaLLM(con, host=ollama.url, api_key="").chat_json("plan", "s", "u", SCHEMA) is None


def test_failed_calls_are_not_cached(ollama, con):
    ollama.reply(status=404, raw={"error": "nope"})
    ollama.reply('{"x": 1}')
    client = OllamaLLM(con, host=ollama.url, api_key="")
    assert client.chat_json("plan", "s", "u", SCHEMA) is None
    assert client.chat_json("plan", "s", "u", SCHEMA) == {"x": 1}


def test_ping(ollama):
    assert OllamaLLM(host=ollama.url, model="nemotron-3-ultra", api_key="").ping()[0]
    ok, msg = OllamaLLM(host=ollama.url, model="llama-9", api_key="").ping()
    assert not ok and "ollama pull llama-9" in msg
    ollama.tags_status = 401
    ok, msg = OllamaLLM(host=ollama.url, api_key="bad").ping()
    assert not ok and "API key" in msg


def test_config_from_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "ollama.example.com:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "nemotron-3-ultra:cloud")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "42")
    c = OllamaLLM()
    assert c.host == "http://ollama.example.com:11434" and c.model == "nemotron-3-ultra:cloud" and c.timeout == 42
    monkeypatch.setenv("CREWASIS_OFFLINE", "1")
    assert llm_mod.from_env() is None


# ---------------------------------------------------------------- end to end through the stub
def test_full_run_through_stub_ollama(ollama, con, data):
    ollama.reply(json.dumps({"lenses": ["product", "retention"], "products": ["bar"],
                             "questions": ["Why do buyers leave?"]}))
    ollama.reply("<think>rewriting…</think>" + json.dumps({
        "summary": "Texture drives complaints [F1] and lapsed buyers [F8].",
        "sentences": [{"key": "find:F1", "text": "34% of recent bad reviews say the bar is chalky or dry [F1]."}]}))
    ollama.reply("```json\n" + json.dumps({"actions": [{"key": "c0", "action": "Test a softer bar base this week."}]})
                 + "\n```")
    client = OllamaLLM(con, host=ollama.url, api_key="")
    e = engine.run(con, engine.DEMO_PROBLEM, client, data)
    assert e.llm_mode == "ollama:nemotron-3-ultra" and e.plan["planned_by"] == "llm"
    by = {s.key: s for s in e.sentences}
    assert by["find:F1"].written_by == "llm" and by["summary"].written_by == "llm"
    assert e.cards[0]["suggested_action"] == "Test a softer bar base this week."
    assert [c["step"] for c in db.llm_calls(con, e.id)] == ["plan", "synthesize", "frame"]
    assert all(c["ok"] for c in db.llm_calls(con, e.id))


def test_run_survives_ollama_being_down(con, data):
    client = OllamaLLM(con, host="http://127.0.0.1:9", api_key="", timeout=2)
    e = engine.run(con, engine.DEMO_PROBLEM, client, data)
    assert len(e.cards) == 13 and all(s.written_by == "template" for s in e.sentences)
    assert [c["ok"] for c in db.llm_calls(con, e.id)] == [0, 0, 0]
