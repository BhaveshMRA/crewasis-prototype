"""The Streamlit app, driven headlessly: offline, with a stub Ollama, with Ollama down, and with bad input."""
import json
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import db

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()

    def start(offline=True, host=None):
        if offline:
            monkeypatch.setenv("CREWASIS_OFFLINE", "1")
        else:
            monkeypatch.delenv("CREWASIS_OFFLINE", raising=False)
        if host:
            monkeypatch.setenv("OLLAMA_HOST", host)
        at = AppTest.from_file(APP, default_timeout=120).run()
        assert not at.exception, at.exception
        return at
    yield start
    st.cache_resource.clear()


def button(at, label):
    return next(b for b in at.button if b.label == label)


def markdown(at):
    return "\n".join(m.value for m in at.markdown)


def run_fde(at, problem=None):
    if problem is not None:
        at.text_area[0].set_value(problem)
    button(at, "Run the FDE").click().run()
    assert not at.exception, at.exception
    return at


def test_offline_demo_flow(app):
    at = app(offline=True)
    assert "Run the FDE first (tab ①)." in [i.value for i in at.info]
    run_fde(at)
    md = markdown(at)
    assert "Brief · analysis #1" in [h.value for h in at.header]
    assert "Texture is the top complaint" in md and "Written offline from templates" in "\n".join(c.value for c in at.caption)
    # Marketing: accept the gated sugar card; it can't be executed yet
    next(b for b in at.button if b.label == "Accept").click().run()
    assert any(b.label == "Awaiting Strategy approval" and b.disabled for b in at.button)
    # Strategy approves it
    at.sidebar.selectbox(key="role").set_value("Strategy").run()
    assert len([b for b in at.button if b.label == "Approve"]) == 4
    button(at, "Approve").click().run()
    assert not at.exception
    assert len([b for b in at.button if b.label == "Approve"]) == 3


def test_problem_validation(app):
    at = run_fde(app(offline=True), "hi")
    assert any("Describe the problem" in e.value for e in at.error)
    assert "Run the FDE first (tab ①)." in [i.value for i in at.info]


def test_simulated_mistake_shows_warning(app):
    at = run_fde(app(offline=True))
    at.sidebar.checkbox[0].check().run()
    assert any("Check caught a bad sentence" in w.value for w in at.warning)


def test_history_and_reset(app):
    at = run_fde(app(offline=True))
    run_fde(at, "Why is our whey protein powder not selling on marketplaces?")
    assert len(at.sidebar.selectbox(key="eid").options) == 2
    assert "Brief · analysis #2" in [h.value for h in at.header]
    at.sidebar.selectbox(key="eid").set_value(1).run()
    assert "Brief · analysis #1" in [h.value for h in at.header]
    button(at, "Reset demo").click().run()
    assert "Run the FDE first (tab ①)." in [i.value for i in at.info]


def test_llm_flow_with_stub_ollama(app, ollama):
    ollama.reply(json.dumps({"lenses": ["product", "retention", "community"], "products": ["bar"],
                             "questions": ["Why do buyers stop?"]}))
    ollama.reply(json.dumps({"summary": "Texture is the main reason buyers leave [F1] [F8].",
                             "sentences": [{"key": "find:F1",
                                            "text": "34% of this quarter's bad reviews call the bar chalky or dry [F1]."}]}))
    ollama.reply(json.dumps({"actions": [{"key": "c0", "action": "Test a softer bar base with 20 testers."}]}))
    at = app(offline=False, host=ollama.url)
    run_fde(at)
    md = markdown(at)
    assert "LLM · checked" in md and "Texture is the main reason buyers leave" in md
    assert "planned by llm" in md
    assert [p[1] for p in ollama.posts()] == ["/api/chat"] * 3
    assert not any("failed call" in w.value for w in at.warning)


def test_ollama_down_falls_back_with_a_warning(app):
    at = app(offline=False, host="http://127.0.0.1:9")
    run_fde(at)
    assert any("failed call" in w.value for w in at.warning)
    assert "Texture is the top complaint" in markdown(at)


def test_connection_check(app, ollama):
    at = app(offline=False, host=ollama.url)
    button(at, "Test connection").click().run()
    assert any("is available" in s.value for s in at.success)
