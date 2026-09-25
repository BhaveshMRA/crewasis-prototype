"""The Streamlit app, driven headlessly against a stub Ollama: the full demo flow, the upgrades, and failures."""
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
    monkeypatch.delenv("CREWASIS_OFFLINE", raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()

    def start(host):
        monkeypatch.setenv("OLLAMA_HOST", host)
        at = AppTest.from_file(APP, default_timeout=180).run()
        assert not at.exception, at.exception
        return at
    yield start
    st.cache_resource.clear()


@pytest.fixture
def model(ollama):
    ollama.auto = True
    return ollama


def button(at, label):
    return next(b for b in at.button if b.label == label)


def markdown(at):
    return "\n".join(m.value for m in at.markdown)


def ask(at, problem=None):
    if problem is not None:
        at.text_area[0].set_value(problem)
    button(at, "Ask Winston").click().run()
    assert not at.exception, at.exception
    return at


def role(at, name):
    at.sidebar.selectbox(key="role").set_value(name).run()
    assert not at.exception, at.exception


def test_full_demo_flow_with_the_llm(app, model):
    at = app(model.url)
    assert "Ask Winston first (tab ①)." in [i.value for i in at.info]
    ask(at)
    md = markdown(at)
    assert "Brief · analysis #1" in [h.value for h in at.header]
    assert "LLM · checked" in md and "is the clearest problem" in md          # the LLM's own summary
    assert "Likely root causes" in [s.value for s in at.subheader]            # named by the LLM, scored by code
    assert "Agent trace" in [h.value for h in at.header]
    assert "Planner" in md and "Governance" in md and "Framer" in md
    # Marketing: the gated sugar card can't run until Strategy approves
    button(at, "Accept").click().run()
    assert any(b.label == "Awaiting Strategy approval" and b.disabled for b in at.button)
    role(at, "Strategy")
    assert len([b for b in at.button if b.label == "Approve"]) == 4
    button(at, "Approve").click().run()
    assert not at.exception
    assert len([b for b in at.button if b.label == "Approve"]) == 3


def test_not_relevant_and_restore(app, model):
    at = ask(app(model.url))
    button(at, "Not relevant ×").click().run()
    assert not at.exception
    assert "Only Marketing's ranking changed" in markdown(at) or "Marked not relevant by Marketing" in \
        "\n".join(e.label for e in at.expander)
    button(at, "Restore").click().run()
    assert not at.exception


def test_thin_evidence_confirm_releases_held_actions(app, model):
    at = ask(app(model.url))
    role(at, "Insights")
    assert "Evidence is thin" in markdown(at)
    accepts = [b for b in at.button if b.label == "Accept"]
    for b in accepts:
        b.click().run()
    button(at, "Confirm evidence").click().run()
    assert not at.exception
    role(at, "R&D")
    assert "Trial a foil-lined wrapper" in markdown(at)


def test_outcome_logging(app, model):
    at = ask(app(model.url))
    role(at, "Strategy")
    button(at, "Accept").click().run()
    button(at, "Decide").click().run()
    assert "Outcome to watch" in markdown(at)
    button(at, "Improved").click().run()
    assert not at.exception and "Improved" in markdown(at)


def test_problem_validation(app, model):
    at = ask(app(model.url), "hi")
    assert any("Describe the problem" in e.value for e in at.error)
    assert "Ask Winston first (tab ①)." in [i.value for i in at.info]


def test_history_and_reset(app, model):
    at = ask(app(model.url))
    ask(at, "Why is our whey protein powder not selling on marketplaces?")
    assert len(at.sidebar.selectbox(key="eid").options) == 2
    assert "Brief · analysis #2" in [h.value for h in at.header]
    at.sidebar.selectbox(key="eid").set_value(1).run()
    assert "Brief · analysis #1" in [h.value for h in at.header]
    button(at, "Reset demo").click().run()
    assert "Ask Winston first (tab ①)." in [i.value for i in at.info]


def test_llm_down_shows_an_error_not_template_text(app):
    at = ask(app("http://127.0.0.1:9"))
    assert any("Winston couldn't complete the analysis" in e.value for e in at.error)
    assert "Ask Winston first (tab ①)." in [i.value for i in at.info]   # nothing was shown instead


def test_connection_check(app, model):
    at = app(model.url)
    button(at, "Test connection").click().run()
    assert any("is available" in s.value for s in at.success)
